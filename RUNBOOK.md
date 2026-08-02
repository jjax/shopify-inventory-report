# RUNBOOK — Shopify 在庫レポート

## 1. 環境変数

Claude Code の環境設定（Environment）の環境変数に登録する。
**プロンプト・リポジトリ・チャットに書かないこと。**

| 変数 | 必須 | 既定値 | 説明 |
|---|---|---|---|
| `SHOPIFY_STORE_DOMAIN` | ✅ | — | `xxxx.myshopify.com`。管理画面のURLではなくmyshopifyドメイン |
| `SHOPIFY_ADMIN_TOKEN` | ✅ | — | カスタムアプリのアクセストークン（`shpat_...`） |
| `SHOPIFY_API_VERSION` | | `2026-07` | 400が出たら1つ前の四半期版に下げる |
| `SHOPIFY_LOW_STOCK_THRESHOLD` | | `5` | この数以下を「在庫少」と判定 |
| `SHOPIFY_FULL_REPORT_HOUR_JST` | | `9` | auto モードで全件を出す時刻（JST） |

### トークンの発行手順
Shopify管理画面 → 設定 → アプリと販売チャネル → アプリ開発 → アプリを作成
→ Admin API 統合を構成 → スコープに `read_inventory` / `read_products` /
`read_locations` を付与 → 保存 → インストール → Admin API アクセストークンを表示。
**トークンは一度しか表示されない。**

## 2. 実行手順

```bash
pip3 install -r requirements.txt
python3 scripts/fetch_inventory.py     # data/snapshot.json を更新（前回は snapshot_prev.json へ退避）
python3 scripts/format_report.py       # 標準出力に POST / NO_POST
```

`format_report.py` が `POST` を出したら `output/slack_message.txt` の中身を
そのまま Slack に投稿する。`NO_POST` のときは投稿しない（差分なし）。

## 3. 投稿ポリシー

毎時実行を前提とし、既定は `--mode auto`:

- **毎時**: 前回スナップショットとの差分だけを投稿する。
  - 🔴 売り切れ（1個以上 → 0）
  - 🟡 しきい値を下回った（閾値超 → 閾値以下）
  - 🟢 補充された（閾値以下 → 閾値超）
  - 変化がなければ **投稿しない**。チャンネルを埋めないための設計。
- **`SHOPIFY_FULL_REPORT_HOUR_JST` の回（既定9時）**: 差分に関係なく
  全件一覧＋在庫少強調を必ず投稿する。

強制的に切り替えたいとき: `--mode full` / `--mode diff`。

## 4. 集計の定義

- 対象は **tracked=true かつ product.status=ACTIVE** の行のみ。
  在庫管理していない商品（tracked=false）は数量を持たないため除外する。
  これを入れると「在庫0」と区別がつかなくなる。
- 1行 = バリエーション × ロケーション。同じバリエーションでも
  ロケーションが複数あれば複数行になる。
- 数量は `InventoryLevel.quantities(names: ["available"])`。
  旧 `available` フィールドは 2024-07 で削除済み。

## 5. API 仕様メモ

- エンドポイント: `https://{store}/admin/api/{version}/graphql.json`
- ヘッダー: `X-Shopify-Access-Token: {token}`
- ページネーションは二重。外側 `productVariants(after:)` は必ず全ページ回す。
  内側 `inventoryLevels(first: 50)` は50超で警告を出す。
- スロットリングはコストベース。`extensions.cost.throttleStatus` を見て待つ。
  `THROTTLED` はHTTP 200のbody内エラーで来ることがある。

## 6. トラブルシュート

| 症状 | 原因と対処 |
|---|---|
| `認証失敗 (HTTP 401/403)` | トークンが誤り、または期限切れ／スコープ不足。3つのスコープを確認 |
| `エンドポイントが見つかりません` | ドメインが `xxxx.myshopify.com` 形式か確認。APIバージョンも確認 |
| `GraphQL errors` に `available` | APIバージョンが古い。`SHOPIFY_API_VERSION` を上げる |
| 400 で `doesn't exist on type` | 逆にバージョンが新しすぎる。1四半期下げる |
| 0行しか取れない | `read_products` スコープ不足が最有力 |
| ロケーション50超の警告 | `shopify_lib.py` の `inventoryLevels(first: 50)` を増やす |

## 7. スクリプト変更時

`python3 scripts/verify.py` を必ず通すこと。API を叩かず合成データで
整形・差分ロジックを検証する。全項目 OK でなければコミットしない。
