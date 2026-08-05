#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""output/slack_message.txt を Slack Incoming Webhook へ投稿する。

エージェントが作成した Routine には Slack コネクタを引き継げないため、
スクリプトから直接投稿できる経路を用意している。

終了コード:
  0 … 投稿成功
  1 … 投稿失敗（コミットしてはいけない）
  2 … 投稿するメッセージが無い
  3 … SLACK_WEBHOOK_URL 未設定（コネクタ経由で投稿すること）
"""
import os
import sys

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MESSAGE = os.path.join(ROOT, "output", "slack_message.txt")
CA_BUNDLE = "/root/.ccr/ca-bundle.crt"


def main():
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        print("[情報] SLACK_WEBHOOK_URL が未設定。コネクタ経由で投稿すること。",
              file=sys.stderr)
        return 3

    if not os.path.exists(MESSAGE):
        print(f"[エラー] {MESSAGE} がありません。先に format_report.py を実行すること。",
              file=sys.stderr)
        return 2
    with open(MESSAGE, encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        print("[エラー] メッセージが空です。", file=sys.stderr)
        return 2

    kwargs = {"json": {"text": text}, "timeout": 30}
    if os.path.exists(CA_BUNDLE):
        kwargs["verify"] = CA_BUNDLE

    delay = 2
    for attempt in range(5):
        try:
            r = requests.post(url.strip(), **kwargs)
        except requests.RequestException as e:
            if attempt < 4:
                import time
                time.sleep(delay)
                delay *= 2
                continue
            print(f"[エラー] Slack 投稿の通信エラー: {e}", file=sys.stderr)
            return 1

        if r.status_code == 200 and r.text.strip() == "ok":
            print("投稿成功")
            return 0
        # 4xx は再試行しても直らない（URL 失効・チャンネル削除など）
        if 400 <= r.status_code < 500:
            print(f"[エラー] Slack 投稿に失敗 (HTTP {r.status_code}): {r.text[:200]}\n"
                  "SLACK_WEBHOOK_URL が失効しているか、投稿先チャンネルが無い可能性。",
                  file=sys.stderr)
            return 1
        if attempt < 4:
            import time
            time.sleep(delay)
            delay *= 2
            continue
        print(f"[エラー] Slack 投稿に失敗 (HTTP {r.status_code}): {r.text[:200]}",
              file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
