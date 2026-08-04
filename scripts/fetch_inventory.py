#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shopify の在庫スナップショットを取得して data/snapshot.json に保存する。

前回分は data/snapshot_prev.json に退避してから上書きする（差分検出のため）。
失敗時は非ゼロで終了し、標準エラーに理由を出す。
"""
import json
import os
import shutil
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shopify_lib as sl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SNAPSHOT = os.path.join(DATA, "snapshot.json")
PREV = os.path.join(DATA, "snapshot_prev.json")
JST = timezone(timedelta(hours=9))


def main():
    try:
        cfg = sl.config()
    except sl.ShopifyError as e:
        print(f"[設定エラー] {e}", file=sys.stderr)
        return 2

    # session() の中でトークン発行とスコープ検証が走るため、ここも捕捉する
    try:
        sess = sl.session(cfg)
    except sl.ShopifyError as e:
        print(f"[認証エラー] {e}", file=sys.stderr)
        return 2

    print(f"取得開始: {cfg['store']} (API {cfg['version']})")

    def progress(pages, rows):
        print(f"  ページ {pages} / 累計 {rows} 行", flush=True)

    try:
        rows, warnings = sl.fetch_inventory(sess, cfg, progress=progress)
    except sl.ShopifyError as e:
        print(f"[取得エラー] {e}", file=sys.stderr)
        return 1

    if not rows:
        print("[取得エラー] 0 行しか取れませんでした。スコープ設定を確認すること。",
              file=sys.stderr)
        return 1

    os.makedirs(DATA, exist_ok=True)
    if os.path.exists(SNAPSHOT):
        shutil.copyfile(SNAPSHOT, PREV)

    snapshot = {
        "fetched_at": datetime.now(JST).isoformat(),
        "store": cfg["store"],
        "shop_name": sl.fetch_shop_name(sess, cfg),
        "api_version": cfg["version"],
        "warnings": warnings,
        "rows": rows,
    }
    with open(SNAPSHOT, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)

    for w in warnings:
        print(f"[警告] {w}", file=sys.stderr)
    print(f"完了: {len(rows)} 行 -> {os.path.relpath(SNAPSHOT, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
