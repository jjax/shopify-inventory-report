# 定期実行ルーチン用プロンプト

前提:
- 環境変数に `SHOPIFY_STORE_DOMAIN` / `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET`
  が登録されていること。
- スケジュール: 毎時（Routine の最小間隔が1時間のため、これが上限の細かさ）。

---

OMAKASE の残枠レポートを実行して。

【0. 準備】
1. `shopify-inventory-report` リポジトリ（RUNBOOK.md があるディレクトリ）が
   クローンされていればそのルートで作業する。無ければ
   `git clone https://github.com/jjax/shopify-inventory-report.git` を実行する。
   クローンに失敗したら、その旨を通知して終了する。
2. RUNBOOK.md を読む。スクリプトのゼロからの書き直しは禁止。
3. `pip3 install -r requirements.txt`

【1. 取得と整形】
1. `python3 scripts/fetch_inventory.py`
   - 非ゼロ終了なら標準エラーの内容をそのまま添えて **エラーとして通知し終了**。
     Slack には投稿しない。
2. `python3 scripts/format_report.py`
   - 標準出力が `NO_POST` なら変化なし。**何も投稿せず、何もコミットせず正常終了**。
   - 標準出力が `POST` なら次へ。

【2. Slack 投稿】
`output/slack_message.txt` の中身を **一字一句そのまま** 投稿する。
要約・省略・言い換えはしない（数字が変わると意味がないため）。
投稿先: #project_goabroad_hk_shopify （ID: C0B5A8DMQTS）

【3. 状態の永続化（投稿が成功した場合のみ）】
Slack への投稿が成功したことを確認してから、次を実行する:

```
git add data/baseline.json data/alerted.json
git commit -m "state: <YYYY-MM-DD HH:MM> JST"
git push -u origin main
```

- ネットワークエラー時は 2s/4s/8s/16s で最大4回リトライする。
- **投稿に失敗した回はコミットしない。** コミットしてしまうと、その警告が
  「通知済み」として記録され、二度と通知されなくなる。
- push できなかった場合はその旨を通知に含める（次回の差分基準がずれるため）。
- 差分がなく `NO_POST` だった回は、コミットするものが無いので何もしない。

【動作の要点】
- 朝9時（JST）の回だけ全件レポートを出し、同時にその時点をベースラインとして
  `data/baseline.json` に保存し、通知済み記録を白紙に戻す。
- それ以外の毎時は、ベースラインと比べて「満枠になった」「残り10枠以下に
  なった」「枠が空いた」項目のうち、**その日まだ通知していないものだけ**を出す。
- 同じ項目を何度も通知しないよう `data/alerted.json` で管理している。

【エラー時の扱い】
API 呼び出しまたは Slack 投稿に失敗した場合は、実行結果に **失敗した旨と
原因（標準エラーの内容）を明記** して終了する。成功したかのように終わらせない。
