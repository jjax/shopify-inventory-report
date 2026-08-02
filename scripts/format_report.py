#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""スナップショットから Slack 投稿用テキストを組み立てる。

モード:
  auto (既定) … FULL_REPORT_HOUR_JST の回だけ全件、それ以外は差分のみ
  full        … 常に全件＋在庫少強調
  diff        … 常に差分のみ

投稿すべき内容があれば output/slack_message.txt に書き出して "POST" を出力。
投稿不要（差分なし）なら "NO_POST" を出力し、ファイルは作らない。
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
PREV = os.path.join(DATA, "snapshot_prev.json")
MESSAGE = os.path.join(OUT, "slack_message.txt")
JST = timezone(timedelta(hours=9))

THRESHOLD = int(os.environ.get("SHOPIFY_LOW_STOCK_THRESHOLD", "5"))
FULL_HOUR = int(os.environ.get("SHOPIFY_FULL_REPORT_HOUR_JST", "9"))


def load(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def key(row):
    return (row.get("variant_id"), row.get("location"))


def label(row):
    """商品名 / バリエーション / ロケーション の表示名。"""
    name = row.get("product") or "(商品名なし)"
    variant = row.get("variant")
    if variant and variant != "Default Title":
        name = f"{name} / {variant}"
    sku = row.get("sku")
    if sku:
        name = f"{name} [{sku}]"
    loc = row.get("location")
    return f"{name} @ {loc}" if loc else name


def active_tracked(rows):
    """在庫管理対象かつ販売中の行だけを対象にする。"""
    return [r for r in rows
            if r.get("tracked") and r.get("available") is not None
            and (r.get("status") or "ACTIVE").upper() == "ACTIVE"]


def build_diff(cur_rows, prev_rows):
    prev = {key(r): r for r in prev_rows}
    sold_out, newly_low, restocked = [], [], []
    for r in cur_rows:
        p = prev.get(key(r))
        if p is None or p.get("available") is None:
            continue
        before, after = p["available"], r["available"]
        if before == after:
            continue
        if after <= 0 < before:
            sold_out.append((r, before, after))
        elif after <= THRESHOLD < before:
            newly_low.append((r, before, after))
        elif before <= THRESHOLD < after:
            restocked.append((r, before, after))
    return sold_out, newly_low, restocked


def fmt_changes(title, changes):
    if not changes:
        return []
    lines = [f"*{title}*"]
    for r, before, after in sorted(changes, key=lambda c: c[0]["available"]):
        lines.append(f"• {label(r)} … {before} → *{after}*")
    lines.append("")
    return lines


def build_full(snapshot, rows):
    low = sorted([r for r in rows if r["available"] <= THRESHOLD],
                 key=lambda r: r["available"])
    lines = []
    if low:
        lines.append(f"*⚠️ 残り{THRESHOLD}個以下（{len(low)}件）*")
        for r in low:
            mark = "🔴" if r["available"] <= 0 else "🟡"
            lines.append(f"{mark} {label(r)} … *{r['available']}*")
        lines.append("")
    else:
        lines.append(f"✅ 残り{THRESHOLD}個以下の商品はありません")
        lines.append("")

    lines.append(f"*在庫一覧（{len(rows)}件）*")
    by_product = {}
    for r in rows:
        by_product.setdefault(r.get("product") or "(商品名なし)", []).append(r)
    for product in sorted(by_product):
        lines.append(f"*{product}*")
        for r in sorted(by_product[product], key=lambda x: (x.get("variant") or "")):
            variant = r.get("variant")
            name = variant if variant and variant != "Default Title" else "—"
            loc = r.get("location")
            suffix = f" @ {loc}" if loc else ""
            lines.append(f"    {name}{suffix} … {r['available']}")
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
    mode = args.mode
    if mode == "auto":
        mode = "full" if now.hour == FULL_HOUR else "diff"

    header = [f"📦 *Shopify 在庫レポート* — {snapshot['store']}",
              f"_{now.strftime('%Y-%m-%d %H:%M')} JST_", ""]
    body = []

    if mode == "full":
        body = build_full(snapshot, rows)
    else:
        prev = load(PREV)
        if not prev:
            print("NO_POST")
            print("[情報] 前回スナップショットがないため差分なし。", file=sys.stderr)
            return 0
        sold_out, newly_low, restocked = build_diff(rows, active_tracked(prev["rows"]))
        if not (sold_out or newly_low or restocked):
            print("NO_POST")
            return 0
        body += fmt_changes("🔴 売り切れ", sold_out)
        body += fmt_changes(f"🟡 残り{THRESHOLD}個以下になりました", newly_low)
        body += fmt_changes("🟢 補充されました", restocked)

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
