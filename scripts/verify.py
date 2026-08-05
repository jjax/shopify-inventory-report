#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""スクリプト変更時の自己検証。API を叩かず、合成データで整形ロジックを確認する。

実行: python3 scripts/verify.py
全項目 OK なら終了コード 0。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

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


def snap(rows, at="2026-08-05T09:00:00+09:00"):
    return {"fetched_at": at, "store": "x.myshopify.com", "shop_name": "TESTSHOP",
            "api_version": "2026-07", "warnings": [], "rows": rows}


def run_cli(workdir, mode):
    r = subprocess.run([sys.executable, os.path.join(workdir, "scripts", "format_report.py"),
                        "--mode", mode], capture_output=True, text=True)
    return r.stdout.strip().splitlines()[0] if r.stdout.strip() else f"(rc={r.returncode})"


def main():
    import format_report as fr
    T = fr.THRESHOLD

    # --- 対象行の絞り込み ---
    rows = [row(1, "A", "HK", 3), row(2, "B", "HK", 100),
            row(3, "C", "HK", None, tracked=False),
            row(4, "D", "HK", 7, status="ARCHIVED")]
    active = fr.active_tracked(rows)
    check("tracked=false を除外", all(r["tracked"] for r in active))
    check("available=None を除外", all(r["available"] is not None for r in active))
    check("ARCHIVED 商品を除外", all(r["status"] == "ACTIVE" for r in active))
    check("残るのは2行", len(active) == 2, f"実際 {len(active)}")

    # --- ベースラインとの比較 ---
    base = [row(1, "A", "HK", 30), row(2, "B", "HK", 5), row(5, "E", "HK", 2),
            row(7, "G", "HK", 2)]
    cur = [row(1, "A", "HK", 0), row(2, "B", "HK", 25), row(5, "E", "HK", 2),
           row(7, "G", "HK", 4)]
    alerts = fr.build_alerts(cur, base)
    cats = {a[0]: a[1]["product"] for a in alerts}
    check("満枠を検出", cats.get(fr.SOLD_OUT) == "商品1")
    check("枠が空いたことを検出", cats.get(fr.FREED) == "商品2")
    check("変化なしは無視", all(a[1]["product"] != "商品5" for a in alerts))
    check("しきい値を超えない増加は無視", all(a[1]["product"] != "商品7" for a in alerts))

    low = fr.build_alerts([row(6, "F", "HK", T - 1)], [row(6, "F", "HK", T + 10)])
    check("しきい値割れを検出", len(low) == 1 and low[0][0] == fr.LOW, f"実際 {low}")

    # --- 全件整形 ---
    text = "\n".join(fr.build_full(fr.active_tracked(
        [row(1, "A", "HK", 2), row(2, "B", "HK", 50)])))
    check("全件に残枠少セクションが出る", "残り" in text and "商品1" in text)
    check("全件に一覧が出る", "残枠一覧（" in text and "商品2" in text)
    check("数量に枠が付く", "50枠" in text)
    check("在庫0は満枠と表示", "満枠" in "\n".join(fr.build_full(
        fr.active_tracked([row(1, "A", "HK", 0)]))))
    check("残枠少ゼロ件なら✅表示",
          "✅" in "\n".join(fr.build_full(fr.active_tracked([row(2, "B", "HK", 50)]))))

    # --- 曜日ソート（配送枠運用）---
    days = ["Fri", "Mon", "Sat", "Thu", "Tue", "Wed"]
    ordered = [r["variant"] for r in sorted(
        [row(9, f"Delivery on {d}", "HK", 30) for d in days], key=fr.variant_sort_key)]
    check("曜日は Mon→Sat の順に並ぶ",
          ordered == [f"Delivery on {d}" for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]],
          f"実際 {ordered}")
    check("曜日を含まない名前は後ろに回る", fr.variant_sort_key(row(1, "Zzz", "HK", 1))[0] == 1)

    # --- ロケーションのまとめ ---
    check("拠点が1つなら名前を返す",
          fr.single_location([row(1, "A", "HK", 1), row(2, "B", "HK", 1)]) == "HK")
    check("拠点が複数なら None",
          fr.single_location([row(1, "A", "HK", 1), row(2, "B", "SG", 1)]) is None)
    one = "\n".join(fr.build_full(fr.active_tracked(
        [row(1, "A", "HK", 30), row(2, "B", "HK", 30)])))
    check("拠点1つなら各行に拠点名を繰り返さない", one.count("HK") == 1,
          f"実際 {one.count('HK')}回")
    check("拠点が複数なら各行に出す", "SG" in "\n".join(fr.build_full(
        fr.active_tracked([row(1, "A", "HK", 30), row(2, "B", "SG", 30)]))))

    # --- ラベル ---
    plain = row(1, "Default Title", "HK", 1)
    plain["sku"] = "SKU-1"
    check("Default Title は表示しない", "Default Title" not in fr.label(plain))
    check("商品名とSKUは残る", "商品1" in fr.label(plain) and "SKU-1" in fr.label(plain))
    check("ロケーションを含む", "@ HK" in fr.label(row(1, "A", "HK", 1)))

    # --- 通知の重複抑止（実際にCLIを回す） ---
    with tempfile.TemporaryDirectory() as td:
        shutil.copytree(SCRIPTS, os.path.join(td, "scripts"))
        data = os.path.join(td, "data")
        os.makedirs(data)

        def write(name, obj):
            with open(os.path.join(data, name), "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False)

        write("snapshot.json", snap([row(1, "A", "HK", 30)]))
        check("ベースライン未作成なら投稿しない", run_cli(td, "diff") == "NO_POST")

        check("full はベースラインを作って投稿する", run_cli(td, "full") == "POST")
        check("baseline.json が作られる", os.path.exists(os.path.join(data, "baseline.json")))
        check("alerted.json が作られる", os.path.exists(os.path.join(data, "alerted.json")))

        write("snapshot.json", snap([row(1, "A", "HK", 30)]))
        check("変化がなければ投稿しない", run_cli(td, "diff") == "NO_POST")

        write("snapshot.json", snap([row(1, "A", "HK", T - 1)]))
        check("しきい値を割ったら投稿する", run_cli(td, "diff") == "POST")

        write("snapshot.json", snap([row(1, "A", "HK", T - 2)]))
        check("同じ項目は二度通知しない", run_cli(td, "diff") == "NO_POST")

        write("snapshot.json", snap([row(1, "A", "HK", 0)]))
        check("残少の後に満枠になったら改めて通知する", run_cli(td, "diff") == "POST")

        with open(os.path.join(data, "alerted.json"), encoding="utf-8") as f:
            st = json.load(f)
        check("通知済み記録に日付が入る", st.get("date"))
        check("通知済み記録が2件たまる", len(st.get("keys") or []) == 2, f"実際 {st}")

        check("full を回すと通知済み記録が消える", run_cli(td, "full") == "POST")
        with open(os.path.join(data, "alerted.json"), encoding="utf-8") as f:
            check("記録が白紙に戻る", json.load(f)["keys"] == [])

    # --- ライブラリの構文と定数 ---
    import shopify_lib as sl
    check("available クエリを使っている", 'names: ["available"]' in sl.VARIANTS_QUERY)
    check("外側ページネーションがある", "hasNextPage" in sl.VARIANTS_QUERY)
    check("トークンをコードに埋めていない", "shpat_" not in sl.VARIANTS_QUERY)

    # --- 認証設定の解決（API は叩かない） ---
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("SHOPIFY_")}

    def cfg_with(**env):
        saved = dict(os.environ)
        os.environ.clear(); os.environ.update(base_env); os.environ.update(env)
        try:
            return sl.config()
        finally:
            os.environ.clear(); os.environ.update(saved)

    c = cfg_with(SHOPIFY_STORE_DOMAIN="x.myshopify.com", SHOPIFY_ADMIN_TOKEN="shpat_dummy")
    check("パターンA: 固定トークンを読む", c["token"] == "shpat_dummy")
    c = cfg_with(SHOPIFY_STORE_DOMAIN="x.myshopify.com",
                 SHOPIFY_CLIENT_ID="cid", SHOPIFY_CLIENT_SECRET="shpss_dummy")
    check("パターンB: client 資格情報を読む", c["token"] is None and c["client_id"] == "cid")
    c = cfg_with(SHOPIFY_STORE_DOMAIN="https://x.myshopify.com/",
                 SHOPIFY_ADMIN_TOKEN="shpat_dummy")
    check("ドメインのURL形式を正規化", c["store"] == "x.myshopify.com")

    for name, env in (("認証情報なしはエラー", {"SHOPIFY_STORE_DOMAIN": "x.myshopify.com"}),
                      ("ドメインなしはエラー", {"SHOPIFY_ADMIN_TOKEN": "shpat_dummy"})):
        try:
            cfg_with(**env)
            check(name, False, "例外が出なかった")
        except sl.ShopifyError:
            check(name, True)

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
