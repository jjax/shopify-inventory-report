# RUNBOOK — OMAKASE 残枠レポート

## 1. 環境変数

Claude Code の環境設定（Environment）の環境変数に登録する。
**プロンプト・リポジトリ・チャットに書かないこと。**

| 変数 | 必須 | 既定値 | 説明 |
|---|---|---|---|
| `SHOPIFY_STORE_DOMAIN` | ✅ | — | `xxxx.myshopify.com`。管理画面のURLではなくmyshopifyドメイン |
| `SHOPIFY_CLIENT_ID` | ✅※ | — | 開発ダッシュボードのクライアントID |
| `SHOPIFY_CLIENT_SECRET` | ✅※ | — | クライアントシークレット（`shpss_`） |
| `SHOPIFY_ADMIN_TOKEN` | ※ | — | 固定アクセストークン（`shpat_`）。あればこちらを優先 |
| `SHOPIFY_API_VERSION` | | `2026-07` | 400が出たら1つ前の四半期版に下げる |
| `SHOPIFY_LOW_STOCK_THRESHOLD` | | `10` | この数以下を「残枠少」と判定 |
| `SHOPIFY_FULL_REPORT_HOUR_JST` | | `9` | auto モードで全件を出す時刻（JST） |
| `SLACK_WEBHOOK_URL` | | — | 設定すると `post_slack.py` が直接投稿する。未設定ならコネクタ経由 |

※ 認証は次のどちらか一方でよい。**本番（OMAKASE）はパターンB。**

- **パターンA**: `SHOPIFY_ADMIN_TOKEN` … 旧来のカスタムアプリ。固定 `shpat_` トークン
- **パターンB**: `SHOPIFY_CLIENT_ID` + `SHOPIFY_CLIENT_SECRET` … 新しい開発
  ダッシュボード製アプリ。固定トークンは存在せず、実行時に
  `POST /admin/oauth/access_token`（`grant_type=client_credentials`）で
  有効24時間の短期トークンを都度発行する。スクリプトが自動でやるので
  手動作業は不要。

### トークンの発行手順
Shopify管理画面 → 設定 → アプリと販売チャネル → アプリ開発 → アプリを作成
→ Admin API 統合を構成 → スコープに `read_inventory` / `read_products` /
`read_locations` を付与 → 保存 → インストール → Admin API アクセストークンを表示。
**トークンは一度しか表示されない。**

> ⚠️ **取り違え注意**: 「APIの資格情報」タブには3つ並んでいる。
> 必要なのは一番上の **Admin API アクセストークン（`shpat_`）** だけ。
> 下の **APIキー** と **APIシークレットキー（`shpss_`）** は用途が違い、
> `X-Shopify-Access-Token` に入れても 401 になる。

## 2. 実行手順

```bash
pip3 install -r requirements.txt
python3 scripts/fetch_inventory.py     # data/snapshot.json を更新
python3 scripts/format_report.py       # 標準出力に POST / NO_POST
```

`format_report.py` が `POST` を出したら `python3 scripts/post_slack.py` で投稿する。
`NO_POST` のときは投稿しない（変化なし）。

`post_slack.py` の終了コード: `0`=成功 / `1`=失敗 / `2`=本文なし /
`3`=`SLACK_WEBHOOK_URL` 未設定（Slack コネクタ経由で投稿すること）。

### 投稿経路が2つある理由

エージェントが作成した Routine には Slack コネクタを引き継げない
（`connectors` パラメータが組織で無効）。そのためスクリプトから直接
投稿できる Webhook 経路を用意している。claude.ai の Routine 画面から
作成した Routine はコネクタを持つため、そちらはコネクタ経由でよい。

## 3. 投稿ポリシー

毎時実行を前提とし、既定は `--mode auto`。**差分の基準は「直前の実行」では
なく「その日の朝9時のベースライン」**。毎時のスナップショットを永続化せずに
済ませるための設計。

- **`SHOPIFY_FULL_REPORT_HOUR_JST` の回（既定9時）**: 全件一覧＋残枠少強調を
  必ず投稿する。同時にその時点を `data/baseline.json` に保存し、
  `data/alerted.json`（通知済み記録）を白紙に戻す。
- **それ以外の毎時**: ベースラインと比較し、変化のうち **その日まだ通知して
  いないものだけ** を投稿する。
  - 🔴 満枠になった（1枠以上 → 0）
  - 🟡 しきい値を下回った（閾値超 → 閾値以下）
  - 🟢 枠が空いた（閾値以下 → 閾値超）
  - 該当がなければ **投稿しない**。

強制的に切り替えたいとき: `--mode full` / `--mode diff`。

### 通知済み記録がなぜ要るか

ベースライン比較だけだと、9時に25枠だった枠が10時に9枠へ落ちた場合、
11時も12時も「25枠→9枠」のままなので同じ警告が毎時繰り返される。
`alerted.json` に `カテゴリ|バリエーションID|ロケーション` を記録して抑止する。
カテゴリが違えば別扱いなので、「残少」を通知した後に満枠になれば改めて通知される。

### 永続化の約束

`data/baseline.json` と `data/alerted.json` はリポジトリにコミットする
（Routine は毎回新しいコンテナで動くため、コミットしないと引き継がれない）。
**投稿が成功した回だけコミットすること。** 投稿に失敗した回をコミットすると、
その警告が「通知済み」になって二度と出なくなる。
`data/snapshot.json` は毎回の作業ファイルなのでコミットしない（.gitignore 済み）。

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
| `アプリのスコープが不足しています` | アプリに `read_inventory` / `read_products` / `read_locations` が付いていない。開発ダッシュボードで追加し、**リリース／再インストールが必要**（設定変更だけでは反映されない） |
| `スコープ不足でアクセスを拒否されました` | 同上。トークン発行時のscopeには出ないが個別フィールドで拒否されるケース |
| `トークン発行に失敗` | `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET` の誤り |
| `Invalid API key or access token` (401) | `SHOPIFY_ADMIN_TOKEN` に `shpss_`（シークレットキー）を入れている。パターンBならこの変数は消して CLIENT_ID/SECRET を使う |
| `認証失敗 (HTTP 401/403)` | トークンが誤り、または期限切れ／スコープ不足 |
| `エンドポイントが見つかりません` | ドメインが `xxxx.myshopify.com` 形式か確認。APIバージョンも確認 |
| `GraphQL errors` に `available` | APIバージョンが古い。`SHOPIFY_API_VERSION` を上げる |
| 400 で `doesn't exist on type` | 逆にバージョンが新しすぎる。1四半期下げる |
| 0行しか取れない | `read_products` スコープ不足が最有力 |
| ロケーション50超の警告 | `shopify_lib.py` の `inventoryLevels(first: 50)` を増やす |
| Slack には届くが `state:` コミットが増えない | **Routine のソースリポジトリが未設定**。`git clone https://github.com/...` で取得したクローンは読み取り専用で push できない。Routine 設定のソースに `jjax/shopify-inventory-report` を指定すると push 権限つきでクローンされる |
| 同じ警告が毎時繰り返される | 上と同じ原因。`alerted.json` が push できず引き継がれていない |

## 7. スクリプト変更時

`python3 scripts/verify.py` を必ず通すこと。API を叩かず合成データで
整形・差分ロジックを検証する。全項目 OK でなければコミットしない。
