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
python3 scripts/commit_state.py         # 投稿成功後の状態永続化（生のgit pushは使わない）
python3 scripts/verify.py               # スクリプト変更時は必須
```

- レポートは日本語。時刻表記は JST。
- 認証情報は**すべて環境変数**。リポジトリにもプロンプトにも書かない。
  必須: `SHOPIFY_STORE_DOMAIN` と、`SHOPIFY_CLIENT_ID` + `SHOPIFY_CLIENT_SECRET`
  （本番の OMAKASE はこちら。実行時に短期トークンを自動発行する）。
  固定トークンがある場合は `SHOPIFY_ADMIN_TOKEN` でも可。
- 差分の基準は「直前の実行」ではなく「その日の朝9時のベースライン」。
  `data/baseline.json`（基準）と `data/alerted.json`（通知済み記録）は
  **コミットして永続化する**。`data/snapshot.json` は作業ファイルなので
  .gitignore 済み。
- Slack 投稿は Slack コネクタ経由。`format_report.py` が `POST` を出力した
  ときだけ投稿し、`NO_POST` のときは何もしない。
- **投稿が成功した回だけコミットすること。** 失敗した回をコミットすると
  その警告が「通知済み」扱いになり二度と出ない。
- 永続化は必ず `scripts/commit_state.py` を使う。このリポジトリは public な
  ので認証情報が無くても read は通り、**`git push` だけが静かに失敗する**。
  非ゼロ終了なら状態は残っていないので、必ず実行結果に明記すること。
