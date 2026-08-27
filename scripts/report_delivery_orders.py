#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""香港ストアの週次締め切りバッチで確定した注文数を、配送曜日ごとに集計する。

背景（毎週の運用フロー）:
  - 毎週水曜 23:59 に、来週分の配送注文が締め切られる。
  - 締め切り後、香港時間の木曜 8:00〜9:00 ごろに決済バッチが走り、
    その回で確定した注文がすべて作成される（実測: 数秒〜数十秒で一括作成）。
  - 各注文には customAttributes に「配送予定日」(YYYY-MM-DD) が入っている。

このスクリプトは「今日（香港時間）の決済バッチ時間帯に作成された注文」を
created_at で絞り込み、キャンセルされておらず決済が成立している注文だけを
「配送予定日」ごとにカウントする。日付レンジを勝手に仮定しない
（曜日オフセットの決め打りをしない）ことで、繁忙期のずれや祝日による
リードタイム変動があっても正しく動く。

使い方:
  python3 scripts/report_delivery_orders.py
  python3 scripts/report_delivery_orders.py --date 2026-08-27   # 香港時間での対象日を指定（テスト/再実行用）
"""
import argparse
import collections
import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shopify_lib as sl

HKT = timezone(timedelta(hours=8))
JST = timezone(timedelta(hours=9))

# 決済バッチは実測で香港時間 8:06 台に数十秒で完了する。前後にゆとりを持たせる。
WINDOW_START_HOUR = 6
WINDOW_END_HOUR = 11

CONFIRMED_STATUSES = {"PAID", "PARTIALLY_PAID"}
DELIVERY_DATE_KEY = "配送予定日"
WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]

ORDERS_QUERY = """
query OrdersInWindow($cursor: String, $q: String!) {
  orders(first: 100, after: $cursor, query: $q, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      name
      createdAt
      cancelledAt
      displayFinancialStatus
      customAttributes { key value }
    }
  }
}
"""


def fetch_orders_in_window(sess, cfg, start_utc, end_utc):
    q = (f"created_at:>='{start_utc.isoformat()}' "
         f"AND created_at:<='{end_utc.isoformat()}'")
    out = []
    cursor = None
    while True:
        data = sl.graphql(sess, cfg, ORDERS_QUERY, {"cursor": cursor, "q": q})
        conn = data["orders"]
        out.extend(conn["nodes"])
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]
    return out


def summarize(orders):
    """注文リストから配送予定日ごとの確定件数を集計する。

    戻り値: (by_date: {date: count}, excluded_count: int, no_attr_count: int)
    """
    by_date = collections.Counter()
    excluded = 0
    no_attr = 0
    for o in orders:
        confirmed = (o.get("cancelledAt") is None
                     and o.get("displayFinancialStatus") in CONFIRMED_STATUSES)
        if not confirmed:
            excluded += 1
            continue
        attrs = {a["key"]: a["value"] for a in o.get("customAttributes", [])}
        dd = attrs.get(DELIVERY_DATE_KEY)
        if not dd:
            no_attr += 1
            continue
        by_date[dd] += 1
    return by_date, excluded, no_attr


def format_report(target_date_hkt, by_date, excluded, no_attr, shop_name):
    lines = []
    now_jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    lines.append(f"【{shop_name}】香港ストア 週次配送注文数レポート（{now_jst} 集計）")
    lines.append(f"対象決済バッチ: 香港時間 {target_date_hkt.isoformat()} "
                 f"{WINDOW_START_HOUR}:00〜{WINDOW_END_HOUR}:00")
    lines.append("")

    dates = sorted(by_date.keys())
    total = sum(by_date.values())
    if not dates:
        lines.append("対象時間帯に確定した注文はありませんでした。")
    else:
        lines.append("配送曜日ごとの確定注文数:")
        for d in dates:
            dt = datetime.strptime(d, "%Y-%m-%d")
            wd = WEEKDAY_JA[dt.weekday()]
            lines.append(f"  {d}（{wd}）: {by_date[d]} 件")
        lines.append(f"  合計: {total} 件")

        first = datetime.strptime(dates[0], "%Y-%m-%d")
        last = datetime.strptime(dates[-1], "%Y-%m-%d")
        expected_span = (last - first).days + 1
        if first.weekday() != 0 or last.weekday() != 5 or expected_span != len(dates):
            lines.append("")
            lines.append("[注意] 配送日が月〜土のきれいな6日間になっていません。"
                          "手動で確認してください。")

    if no_attr:
        lines.append("")
        lines.append(f"[警告] 「{DELIVERY_DATE_KEY}」属性が無い注文が {no_attr} 件"
                      "ありました（集計対象外）。")
    if excluded:
        lines.append(f"[情報] 決済未確定/キャンセル済みのため除外した注文: {excluded} 件")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="香港時間での対象日 (YYYY-MM-DD)。省略時は実行時点の香港時間の日付")
    args = parser.parse_args()

    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        target_date = datetime.now(HKT).date()

    try:
        cfg = sl.config()
        sess = sl.session(cfg)
    except sl.ShopifyError as e:
        print(f"[設定/認証エラー] {e}", file=sys.stderr)
        return 2

    start_utc = datetime(target_date.year, target_date.month, target_date.day,
                          WINDOW_START_HOUR, 0, 0, tzinfo=HKT).astimezone(timezone.utc)
    end_utc = datetime(target_date.year, target_date.month, target_date.day,
                        WINDOW_END_HOUR, 0, 0, tzinfo=HKT).astimezone(timezone.utc)

    try:
        orders = fetch_orders_in_window(sess, cfg, start_utc, end_utc)
    except sl.ShopifyError as e:
        print(f"[取得エラー] {e}", file=sys.stderr)
        return 1

    by_date, excluded, no_attr = summarize(orders)
    shop_name = sl.fetch_shop_name(sess, cfg) or cfg["store"]
    report = format_report(target_date, by_date, excluded, no_attr, shop_name)

    print(report)

    result = {
        "target_date_hkt": target_date.isoformat(),
        "window_utc": [start_utc.isoformat(), end_utc.isoformat()],
        "by_date": dict(by_date),
        "excluded": excluded,
        "no_delivery_attr": no_attr,
        "total_orders_seen": len(orders),
    }
    print("\n---JSON---")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
