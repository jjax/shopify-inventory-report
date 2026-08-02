# -*- coding: utf-8 -*-
"""Shopify Admin GraphQL API アクセス共通ライブラリ。

重要な実装上の知見（変更・削除しないこと）:
1. 在庫数は `InventoryLevel.quantities(names: ["available"])` で取る。
   旧 `InventoryLevel.available` は 2024-07 で削除済みで、使うと 400 になる。
2. productVariants を親にして inventoryItem.inventoryLevels を辿る。
   Shopify では全 product に最低1つ variant があるため、これで全件をカバーできる。
3. ページネーションは二重にある。外側 productVariants(after:) は必ず回すこと。
   内側 inventoryLevels は first: 50 で取り、hasNextPage が立ったら
   警告を出す（ロケーションが50を超える運用は想定していない）。
4. GraphQL はコストベースのスロットリング。extensions.cost.throttleStatus の
   currentlyAvailable が閾値を切ったら restoreRate から必要秒数を計算して待つ。
   429 は throttled エラーとして body 200 で返ることがあるため、
   HTTP ステータスだけを見ていると取りこぼす。
5. egress プロキシ環境では CA バンドルを明示指定しないと SSL 検証に失敗する。
   verify を無効化するのは禁止（session() 参照）。
6. tracked=false の inventoryItem は在庫管理対象外。数量は None として扱い、
   「在庫0」と混同しないこと。
"""
import json
import os
import time

import requests

CA_BUNDLE = "/root/.ccr/ca-bundle.crt"
DEFAULT_API_VERSION = "2026-07"

# スロットル制御: 残コストがこれを下回ったら回復を待つ
COST_FLOOR = 200


class ShopifyError(RuntimeError):
    """Shopify API が userErrors / errors を返した、または設定が不正。"""


def config():
    """環境変数から設定を読む。トークンはリポジトリにも会話にも置かない。"""
    store = os.environ.get("SHOPIFY_STORE_DOMAIN")
    token = os.environ.get("SHOPIFY_ADMIN_TOKEN")
    missing = [n for n, v in (("SHOPIFY_STORE_DOMAIN", store),
                              ("SHOPIFY_ADMIN_TOKEN", token)) if not v]
    if missing:
        raise ShopifyError(
            "環境変数が未設定: " + ", ".join(missing) + "\n"
            "Claude Code の環境設定（Environment）の環境変数に登録すること。"
            "プロンプトやリポジトリに直書きしない。")
    store = store.strip()
    if store.startswith("http"):
        store = store.split("//", 1)[1]
    store = store.rstrip("/")
    return {
        "store": store,
        "token": token.strip(),
        "version": os.environ.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION).strip(),
    }


def endpoint(cfg):
    return f"https://{cfg['store']}/admin/api/{cfg['version']}/graphql.json"


def session(cfg):
    s = requests.Session()
    s.headers.update({
        "X-Shopify-Access-Token": cfg["token"],
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    if os.path.exists(CA_BUNDLE):
        s.verify = CA_BUNDLE
    return s


def _throttle(extensions):
    """残コストが少なければ回復を待つ。"""
    st = (extensions or {}).get("cost", {}).get("throttleStatus")
    if not st:
        return
    available = st.get("currentlyAvailable", 0)
    rate = st.get("restoreRate", 50) or 50
    if available < COST_FLOOR:
        time.sleep(min((COST_FLOOR - available) / rate, 10))


def graphql(sess, cfg, query, variables=None, retries=4):
    """GraphQL を叩く。throttled と一時エラーはバックオフして再試行する。"""
    payload = {"query": query, "variables": variables or {}}
    delay = 2
    last = None
    for attempt in range(retries + 1):
        try:
            r = sess.post(endpoint(cfg), data=json.dumps(payload), timeout=60)
        except requests.RequestException as e:
            last = f"通信エラー: {e}"
            if attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise ShopifyError(last)

        if r.status_code == 401 or r.status_code == 403:
            raise ShopifyError(
                f"認証失敗 (HTTP {r.status_code})。SHOPIFY_ADMIN_TOKEN と "
                f"スコープ(read_inventory/read_products/read_locations)を確認すること。")
        if r.status_code == 404:
            raise ShopifyError(
                f"エンドポイントが見つかりません: {endpoint(cfg)}\n"
                f"SHOPIFY_STORE_DOMAIN（xxxx.myshopify.com）と "
                f"SHOPIFY_API_VERSION を確認すること。")
        if r.status_code == 429 or r.status_code >= 500:
            last = f"HTTP {r.status_code}: {r.text[:200]}"
            if attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise ShopifyError(last)
        if r.status_code != 200:
            raise ShopifyError(f"HTTP {r.status_code}: {r.text[:500]}")

        body = r.json()
        errors = body.get("errors")
        if errors:
            codes = {(e.get("extensions") or {}).get("code") for e in errors}
            if "THROTTLED" in codes and attempt < retries:
                _throttle(body.get("extensions"))
                time.sleep(delay)
                delay *= 2
                continue
            raise ShopifyError("GraphQL errors: " + json.dumps(errors, ensure_ascii=False)[:500])

        _throttle(body.get("extensions"))
        return body["data"]

    raise ShopifyError(last or "不明なエラー")


VARIANTS_QUERY = """
query InventorySnapshot($cursor: String) {
  productVariants(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      title
      sku
      displayName
      product { id title status handle }
      inventoryItem {
        id
        tracked
        inventoryLevels(first: 50) {
          pageInfo { hasNextPage }
          nodes {
            location { id name }
            quantities(names: ["available"]) { name quantity }
          }
        }
      }
    }
  }
}
"""


def fetch_inventory(sess, cfg, progress=None):
    """全商品・全バリエーション・全ロケーションの在庫を取得して行のリストで返す。

    戻り値の各行: product/variant/sku/location/available/tracked/status
    available は tracked=false のとき None。
    """
    rows = []
    warnings = []
    cursor = None
    pages = 0
    while True:
        data = graphql(sess, cfg, VARIANTS_QUERY, {"cursor": cursor})
        conn = data["productVariants"]
        for v in conn["nodes"]:
            item = v.get("inventoryItem") or {}
            tracked = bool(item.get("tracked"))
            levels = (item.get("inventoryLevels") or {})
            if (levels.get("pageInfo") or {}).get("hasNextPage"):
                warnings.append(
                    f"ロケーションが50件を超えています: {v.get('displayName')}")
            nodes = levels.get("nodes") or []
            if not nodes:
                rows.append(_row(v, None, None, tracked))
                continue
            for lv in nodes:
                qty = None
                if tracked:
                    for q in lv.get("quantities") or []:
                        if q.get("name") == "available":
                            qty = q.get("quantity")
                rows.append(_row(v, (lv.get("location") or {}).get("name"), qty, tracked))
        pages += 1
        if progress:
            progress(pages, len(rows))
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]

    return rows, warnings


def _row(variant, location, available, tracked):
    product = variant.get("product") or {}
    return {
        "product_id": product.get("id"),
        "product": product.get("title"),
        "status": product.get("status"),
        "variant_id": variant.get("id"),
        "variant": variant.get("title"),
        "sku": variant.get("sku") or "",
        "display_name": variant.get("displayName"),
        "location": location,
        "available": available,
        "tracked": tracked,
    }
