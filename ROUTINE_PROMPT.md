# 定期実行ルーチン用プロンプト

前提:
- 環境のソースリポジトリに `jjax/shopify-inventory-report` を設定しておくこと
  （コンテナ起動時に自動クローンされ、CLAUDE.md が読み込まれる）。
- 環境変数に `SHOPIFY_STORE_DOMAIN` / `SHOPIFY_ADMIN_TOKEN` を登録しておくこと。
- スケジュール: 毎時（Routine の最小間隔が1時間のため、これが上限の細かさ）。

---

Shopify の在庫レポートを実行して。

【0. 前提確認】
1. shopify-inventory-report リポジトリ（RUNBOOK.md があるディレクトリ）が
   クローンされていることを確認し、そのルートで作業する。
   見つからない場合は環境のソースリポジトリ設定を見直すよう通知して終了。
2. RUNBOOK.md を読む。スクリプトのゼロからの書き直しは禁止。
3. `pip3 install -r requirements.txt`

【1. 取得と整形】
1. `python3 scripts/fetch_inventory.py`
   - 非ゼロ終了なら、標準エラーの内容をそのまま添えて **エラーとして通知し終了**。
     Slack には投稿しない。
2. `python3 scripts/format_report.py`
   - 標準出力が `NO_POST` なら、在庫に変化なし。**何も投稿せず正常終了**。
   - 標準出力が `POST` なら次へ。

【2. Slack 投稿】
`output/slack_message.txt` の中身を **一字一句そのまま** 指定チャンネルへ投稿する。
要約・省略・言い換えはしない（数字が変わると意味がないため）。
投稿先チャンネル: <ここに実際のチャンネル名を記入>

【3. 永続化】
`data/snapshot*.json` と `output/slack_message.txt` は .gitignore 済みなので
通常はコミット不要。スクリプトを修正した場合のみ:
- `python3 scripts/verify.py` を通す
- `git add` → `git commit` → `git push -u origin main`
  （ネットワークエラー時は 2s/4s/8s/16s で最大4回リトライ）

【エラー時の扱い】
API 呼び出しまたは Slack 投稿に失敗した場合は、実行結果に **失敗した旨と
原因（標準エラーの内容）を明記** して終了する。成功したかのように終わらせない。
