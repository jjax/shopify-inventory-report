# Shopify 在庫レポート（HKストア）

Shopify HKストアの在庫残数を取得し、Slack に通知するツール一式。
**レポート実行を頼まれたら、まず `RUNBOOK.md` を読むこと。**
API仕様・環境変数・投稿ポリシー・トラブルシュートがすべて書いてある。
スクリプトはそのまま使う。ゼロから書き直さない。

クイックリファレンス（リポジトリルートで実行）:
```bash
pip3 install -r requirements.txt        # 初回のみ
python3 scripts/fetch_inventory.py      # 在庫取得 -> data/snapshot.json
python3 scripts/format_report.py        # Slack本文生成 -> output/slack_message.txt
python3 scripts/verify.py               # スクリプト変更時は必須
```

- レポートは日本語。時刻表記は JST。
- 認証情報は**すべて環境変数**。リポジトリにもプロンプトにも書かない。
  必須: `SHOPIFY_STORE_DOMAIN` / `SHOPIFY_ADMIN_TOKEN`
- `data/snapshot*.json` と `output/slack_message.txt` は .gitignore 済み
  （在庫データはコミットしない）。
- Slack 投稿は Slack コネクタ経由。`format_report.py` が `POST` を出力した
  ときだけ投稿し、`NO_POST` のときは何もしない。
