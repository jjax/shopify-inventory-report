#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""スナップショットから Slack 投稿用テキストを組み立てる。

差分の基準は「直前の実行」ではなく「その日の朝9時のベースライン」。
毎時のスナップショットを永続化せずに済ませるための設計。

モード:
  auto (既定) … FULL_REPORT_HOUR_JST の回は full、それ以外は diff
  full        … 全件＋残枠少強調。同時にベースラインを更新し通知済み記録を消す
  diff        … ベースラインと比較し、今日まだ通知していない変化だけを出す

投稿すべき内容があれば output/slack_message.txt に書き出して "POST" を出力。
投稿不要なら "NO_POST" を出力し、ファイルは作らない。

重要: 通知済み記録(alerted.json)は「投稿が成功したら」コミットすること。
投稿に失敗した回をコミットすると、その警告が二度と出なくなる。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "output")
SNAPSHOT = os.path.join(DATA, "snapshot.json")
BASELINE = os.path.join(DATA, "baseline.json")
ALERTED = os.path.join(DATA, "alerted.json")
MESSAGE = os.path.join(OUT, "slack_message.txt")
JST = timezone(timedelta(hours=9))

THRESHOLD = int(os.environ.get("SHOPIFY_LOW_STOCK_THRESHOLD", "10"))
FULL_HOUR = int(os.environ.get("SHOPIFY_FULL_REPORT_HOUR_JST", "9"))

# バリエーション名が曜日を含む場合は曜日順に並べる（配送枠の運用に合わせる）。
# アルファベット順だと Fri, Mon, Sat… となって読めないため。
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

SOLD_OUT, LOW, FREED = "満枠", "残少", "空き"


def load(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def key(row):
    return f"{row.get('variant_id')}|{row.get('location')}"


def variant_sort_key(row):
    name = (row.get("variant") or "").lower()
    for i, d in enumerate(WEEKDAYS):
        if d in name:
            return (0, i, name)
    return (1, 0, name)


def single_location(rows):
    """ロケーションが1種類だけならその名前を返す。複数なら None。"""
    locs = {r.get("location") for r in rows if r.get("location")}
    return locs.pop() if len(locs) == 1 else None


def label(row, omit_location=False):
    """商品名 / バリエーション / ロケーション の表示名。

    omit_location=True のときはロケーションを省く（拠点が1つのとき、
    全行に同じ名前が並ぶのを避けるため。拠点名はヘッダーに出す）。
    """
    name = row.get("product") or "(商品名なし)"
    variant = row.get("variant")
    if variant and variant != "Default Title":
        name = f"{name} / {variant}"
    sku = row.get("sku")
    if sku:
        name = f"{name} [{sku}]"
    loc = row.get("location")
    return f"{name} @ {loc}" if loc and not omit_location else name


def active_tracked(rows):
    """在庫管理対象かつ販売中の行だけを対象にする。"""
    return [r for r in rows
            if r.get("tracked") and r.get("available") is not None
            and (r.get("status") or "ACTIVE").upper() == "ACTIVE"]


def qty(n):
    return "満枠" if n <= 0 else f"{n}枠"


def build_alerts(cur_rows, base_rows):
    """ベースラインと比べて状態が変わった項目を返す。

    各要素: (category, row, before, after)
    同じ項目でも category が違えば別の通知として扱う
    （残少になった後に満枠になったら、両方通知したいため）。
    """
    base = {key(r): r for r in base_rows}
    alerts = []
    for r in cur_rows:
        b = base.get(key(r))
        if b is None or b.get("available") is None:
            continue
        before, after = b["available"], r["available"]
        if after <= 0 < before:
            alerts.append((SOLD_OUT, r, before, after))
        elif after <= THRESHOLD < before:
            alerts.append((LOW, r, before, after))
        elif before <= THRESHOLD < after:
            alerts.append((FREED, r, before, after))
    return alerts


TITLES = {
    SOLD_OUT: "🔴 満枠になりました",
    LOW: f"🟡 残り{THRESHOLD}枠以下になりました",
    FREED: "🟢 枠が空きました",
}


def fmt_alerts(alerts, omit_location):
    lines = []
    for cat in (SOLD_OUT, LOW, FREED):
        group = [a for a in alerts if a[0] == cat]
        if not group:
            continue
        lines.append(f"*{TITLES[cat]}*")
        for _, r, before, after in sorted(group, key=lambda a: a[3]):
            lines.append(f"• {label(r, omit_location)} … {qty(before)} → *{qty(after)}*")
        lines.append("")
    return lines


def build_full(rows):
    only_loc = single_location(rows)
    low = sorted([r for r in rows if r["available"] <= THRESHOLD],
                 key=lambda r: r["available"])
    lines = []
    if low:
        lines.append(f"*⚠️ 残り{THRESHOLD}枠以下（{len(low)}件）*")
        for r in low:
            mark = "🔴" if r["available"] <= 0 else "🟡"
            lines.append(f"{mark} {label(r, bool(only_loc))} … *{qty(r['available'])}*")
        lines.append("")
    else:
        lines.append(f"✅ 残り{THRESHOLD}枠以下はありません")
        lines.append("")

    header = f"*残枠一覧（{len(rows)}件）*"
    if only_loc:
        header += f"　_{only_loc}_"
    lines.append(header)
    by_product = {}
    for r in rows:
        by_product.setdefault(r.get("product") or "(商品名なし)", []).append(r)
    for product in sorted(by_product):
        lines.append(f"*{product}*")
        for r in sorted(by_product[product], key=variant_sort_key):
            variant = r.get("variant")
            name = variant if variant and variant != "Default Title" else "—"
            suffix = "" if only_loc else (f" @ {r['location']}" if r.get("location") else "")
            mark = " 🔴" if r["available"] <= 0 else (" 🟡" if r["available"] <= THRESHOLD else "")
            lines.append(f"    {name}{suffix} … {qty(r['available'])}{mark}")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["auto", "full", "diff"], default="auto")
    args = ap.parse_args()

    snapshot = load(SNAPSHOT)
    if not snapshot:
        print(f"[エラー] {SNAPSHOT} がありません。先に fetch_inventory.py を実行すること。",
              file=sys.stderr)
        return 2

    rows = active_tracked(snapshot["rows"])
    now = datetime.now(JST)
    today = now.strftime("%Y-%m-%d")
    mode = args.mode
    if mode == "auto":
        mode = "full" if now.hour == FULL_HOUR else "diff"

    shop = snapshot.get("shop_name") or snapshot["store"]
    header = [f"📦 *残枠レポート* — {shop}",
              f"_{now.strftime('%Y-%m-%d %H:%M')} JST_", ""]

    if mode == "full":
        # 今日のベースラインを張り替え、通知済み記録を白紙に戻す
        save(BASELINE, snapshot)
        save(ALERTED, {"date": today, "keys": []})
        body = build_full(rows)
        print(f"[情報] ベースラインを更新（{len(rows)}行）", file=sys.stderr)
    else:
        baseline = load(BASELINE)
        if not baseline:
            print("NO_POST")
            print(f"[情報] ベースライン未作成。朝{FULL_HOUR}時の全件実行を待つこと。",
                  file=sys.stderr)
            return 0

        state = load(ALERTED) or {}
        if state.get("date") != today:
            state = {"date": today, "keys": []}
        seen = set(state["keys"])

        alerts = build_alerts(rows, active_tracked(baseline["rows"]))
        fresh = [a for a in alerts if f"{a[0]}|{key(a[1])}" not in seen]
        if not fresh:
            print("NO_POST")
            if alerts:
                print(f"[情報] {len(alerts)}件の変化はすべて通知済み。", file=sys.stderr)
            return 0

        seen.update(f"{a[0]}|{key(a[1])}" for a in fresh)
        save(ALERTED, {"date": today, "keys": sorted(seen)})

        base_time = (baseline.get("fetched_at") or "")[11:16]
        header[1] = f"_{now.strftime('%Y-%m-%d %H:%M')} JST — 本日{base_time}時点との比較_"
        body = fmt_alerts(fresh, bool(single_location(rows)))
        print(f"[情報] 新規{len(fresh)}件 / 変化{len(alerts)}件", file=sys.stderr)

    for w in snapshot.get("warnings") or []:
        body.append(f"_⚠️ {w}_")

    os.makedirs(OUT, exist_ok=True)
    with open(MESSAGE, "w", encoding="utf-8") as f:
        f.write("\n".join(header + body).rstrip() + "\n")
    print("POST")
    print(f"[情報] mode={mode} 対象{len(rows)}行 -> {os.path.relpath(MESSAGE, ROOT)}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
