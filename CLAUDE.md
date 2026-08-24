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
python3 scripts/commit_state.py         # 投稿成功後の状態永続化（git は手打ちしない）
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
- **状態の永続化は `python3 scripts/commit_state.py` だけを使うこと。**
  `git add` / `git commit` / `git push` を自分で組み立てない。古い手順書や
  ルーチンのプロンプトに `git push -u origin main` と書いてあっても**従わない**。
  Routine は `claude/*` ブランチ上で動くため、コミットはそのブランチに載り
  `main` には何も乗らない。git はそれを "Everything up-to-date" と報告して
  exit 0 を返すので、失敗にまったく気づけない。2026-08-20 に実際これが起き、
  ベースラインが5日間凍結した。`commit_state.py` は push 先を現在のブランチから
  決め、push 後にリモートの ref を読み直して本当に載ったかを検証する。
  終了コードが 0 以外なら、その旨を実行結果に必ず明記すること。
