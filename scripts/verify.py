#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""スクリプト変更時の自己検証。API を叩かず、合成データで整形ロジックを確認する。

実行: python3 scripts/verify.py
全項目 OK なら終了コード 0。
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

FAILS = []


def check(name, cond, detail=""):
    print(f"{'OK  ' if cond else 'NG  '} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def row(pid, variant, loc, avail, tracked=True, status="ACTIVE"):
    return {"product_id": f"gid://P/{pid}", "product": f"商品{pid}", "status": status,
            "variant_id": f"gid://V/{pid}-{variant}", "variant": variant,
            "sku": f"SKU-{pid}{variant}", "display_name": f"商品{pid} - {variant}",
            "location": loc, "available": avail, "tracked": tracked}


def main():
    import format_report as fr

    # --- 対象行の絞り込み ---
    rows = [row(1, "A", "HK", 3), row(2, "B", "HK", 100),
            row(3, "C", "HK", None, tracked=False),
            row(4, "D", "HK", 7, status="ARCHIVED")]
    active = fr.active_tracked(rows)
    check("tracked=false を除外", all(r["tracked"] for r in active))
    check("available=None を除外", all(r["available"] is not None for r in active))
    check("ARCHIVED 商品を除外", all(r["status"] == "ACTIVE" for r in active))
    check("残るのは2行", len(active) == 2, f"実際 {len(active)}")

    # --- 差分検出 ---
    # 商品2 は 3→20 でしきい値(5)を上抜けするので「補充」。
    # 商品7 は 2→4 でしきい値を超えないため、増えていても通知しない。
    prev = [row(1, "A", "HK", 10), row(2, "B", "HK", 3),
            row(5, "E", "HK", 2), row(7, "G", "HK", 2)]
    cur = [row(1, "A", "HK", 0), row(2, "B", "HK", 20),
           row(5, "E", "HK", 2), row(7, "G", "HK", 4)]
    sold_out, newly_low, restocked = fr.build_diff(cur, prev)
    check("売り切れを検出", len(sold_out) == 1 and sold_out[0][0]["product"] == "商品1")
    check("補充を検出", len(restocked) == 1 and restocked[0][0]["product"] == "商品2")
    check("低在庫内での増加は通知しない",
          all(c[0]["product"] != "商品7" for c in sold_out + newly_low + restocked))
    check("変化なしは無視", all(c[0]["product"] != "商品5"
                                for c in sold_out + newly_low + restocked))

    prev2 = [row(6, "F", "HK", 20)]
    cur2 = [row(6, "F", "HK", 4)]
    _, nl, _ = fr.build_diff(cur2, prev2)
    check("しきい値割れを検出", len(nl) == 1, f"実際 {len(nl)}")

    # --- 全件整形 ---
    lines = fr.build_full({"store": "x"}, fr.active_tracked(
        [row(1, "A", "HK", 2), row(2, "B", "HK", 50)]))
    text = "\n".join(lines)
    check("全件に在庫少セクションが出る", "残り" in text and "商品1" in text)
    check("全件に一覧が出る", "在庫一覧" in text and "商品2" in text)

    lines_ok = fr.build_full({"store": "x"}, fr.active_tracked([row(2, "B", "HK", 50)]))
    check("在庫少ゼロ件なら✅表示", "✅" in "\n".join(lines_ok))

    # --- ラベル ---
    plain = row(1, "Default Title", "HK", 1)
    plain["sku"] = "SKU-1"
    check("Default Title は表示しない", "Default Title" not in fr.label(plain))
    check("商品名とSKUは残る", "商品1" in fr.label(plain) and "SKU-1" in fr.label(plain))
    check("ロケーションを含む", "@ HK" in fr.label(row(1, "A", "HK", 1)))

    # --- CLI: スナップショット無しで終了コード2 ---
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, SHOPIFY_LOW_STOCK_THRESHOLD="5")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "format_report.py"),
                            "--mode", "diff"],
                           capture_output=True, text=True, env=env, cwd=td)
        check("差分モードは前回なしなら NO_POST か、スナップショット無しなら2",
              r.returncode in (0, 2))

    # --- ライブラリの構文と定数 ---
    import shopify_lib as sl
    check("available クエリを使っている", 'names: ["available"]' in sl.VARIANTS_QUERY)
    check("外側ページネーションがある", "hasNextPage" in sl.VARIANTS_QUERY)
    check("トークンをコードに埋めていない", "shpat_" not in sl.VARIANTS_QUERY)

    # --- 認証設定の解決（API は叩かない） ---
    base = {k: v for k, v in os.environ.items() if not k.startswith("SHOPIFY_")}
    def cfg_with(**env):
        saved = dict(os.environ)
        os.environ.clear(); os.environ.update(base); os.environ.update(env)
        try:
            return sl.config()
        finally:
            os.environ.clear(); os.environ.update(saved)

    c = cfg_with(SHOPIFY_STORE_DOMAIN="x.myshopify.com", SHOPIFY_ADMIN_TOKEN="shpat_dummy")
    check("パターンA: 固定トークンを読む", c["token"] == "shpat_dummy")

    c = cfg_with(SHOPIFY_STORE_DOMAIN="x.myshopify.com",
                 SHOPIFY_CLIENT_ID="cid", SHOPIFY_CLIENT_SECRET="shpss_dummy")
    check("パターンB: client 資格情報を読む",
          c["token"] is None and c["client_id"] == "cid")

    c = cfg_with(SHOPIFY_STORE_DOMAIN="https://x.myshopify.com/",
                 SHOPIFY_ADMIN_TOKEN="shpat_dummy")
    check("ドメインのURL形式を正規化", c["store"] == "x.myshopify.com")

    try:
        cfg_with(SHOPIFY_STORE_DOMAIN="x.myshopify.com")
        check("認証情報なしはエラー", False, "例外が出なかった")
    except sl.ShopifyError:
        check("認証情報なしはエラー", True)

    try:
        cfg_with(SHOPIFY_ADMIN_TOKEN="shpat_dummy")
        check("ドメインなしはエラー", False, "例外が出なかった")
    except sl.ShopifyError:
        check("ドメインなしはエラー", True)

    check("必要スコープを3つ定義している",
          set(sl.REQUIRED_SCOPES) == {"read_inventory", "read_products", "read_locations"})

    print()
    if FAILS:
        print(f"NG {len(FAILS)} 件: " + ", ".join(FAILS))
        return 1
    print("すべて OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
