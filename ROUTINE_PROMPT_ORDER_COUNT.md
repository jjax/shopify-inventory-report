# 定期実行ルーチン用プロンプト（香港ストア 週次配送注文数レポート）

このファイルは `scripts/report_delivery_orders.py` を毎週自動実行するための
Routine 用プロンプトです。**claude.ai の Routine 画面から作成すること**
（エージェントが作成する Routine は Slack コネクタを引き継げないため）。

## Routine 設定値

- 名前: 香港ストア 週次配送注文数レポート
- スケジュール（cron, UTC）: `0 1 * * 4`（毎週木曜 10:00 JST / 9:00 HKT）
- ソースリポジトリ: `jjax/shopify-inventory-report`
- 必要な環境変数: `SHOPIFY_STORE_DOMAIN` / `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET`
  （在庫レポート Routine と共通。読み取り専用スクリプトなので push 権限は不要）
- 必要なコネクタ: Slack

## プロンプト本文

```
shopify-inventory-report リポジトリのルートで作業する。無ければ
git clone https://github.com/jjax/shopify-inventory-report.git を実行する。

1. pip3 install -r requirements.txt
2. python3 scripts/report_delivery_orders.py を実行する。
   - 非ゼロ終了なら、標準エラーの内容を添えて Slack チャンネル
     #project_goabroad_hk_shopify（ID: C0B5A8DMQTS）に
     「[エラー] 香港ストア週次配送注文数レポートの取得に失敗しました」
     として原因とともに通知し、終了する。
3. 標準出力のうち `---JSON---` より前のテキスト部分（レポート本文）を、
   一字一句そのまま Slack チャンネル #project_goabroad_hk_shopify
   （ID: C0B5A8DMQTS）に投稿する。要約・省略・言い換えはしない
   （数字が変わると意味がないため）。
```
