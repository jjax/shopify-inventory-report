#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""baseline.json / alerted.json をコミットして push する。

Routine のコンテナは毎回作り直されるので、push できなければ状態は失われる。
やっかいなのは push が **静かに失敗する** こと。リポジトリが public だと
認証なしでも fetch/clone は通るため、「読めているから書けるはず」と
勘違いしやすい。実際 2026-08-06 と 2026-08-12 はどちらも
「Slack 投稿は成功、push だけ失敗」で、差分の基準が古いまま凍結した。

そのためこのスクリプトは:
  - 認証を明示的に用意する（GITHUB_TOKEN / GH_TOKEN を credential helper 経由で渡す）
  - push 後にリモートの ref を読み直して、実際に反映されたことを確認する
  - 少しでも怪しければ非ゼロで終了する（呼び出し側が握りつぶせないように）

トークンは URL にも argv にも git config にも書かない。helper には環境変数名
だけを渡し、値は環境変数で渡す（argv は ps で他プロセスから見えるため）。

終了コード:
  0 … push 成功、またはコミットするものが無かった
  1 … commit / push に失敗した（状態は永続化されていない）
  2 … 認証トークンが無い
"""
import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JST = timezone(timedelta(hours=9))

STATE_FILES = ["data/baseline.json", "data/alerted.json"]
# verify.py から回すときは待たない（リトライ回数は変えない）
RETRY_WAITS = [0, 0, 0, 0] if os.environ.get("COMMIT_STATE_FAST") else [2, 4, 8, 16]

# 値ではなく環境変数名だけを argv に載せる
CRED_HELPER = '!f(){ echo username=x-access-token; echo "password=$GIT_PUSH_TOKEN"; };f'
TOKEN_ENV = ("GITHUB_TOKEN", "GH_TOKEN")


def redact(text, secret):
    """トークンらしきものを出力から消す。"""
    if secret:
        text = text.replace(secret, "***REDACTED***")
    return re.sub(r"//[^@/\s]*@", "//***@", text)


def git(args, repo, token=None, capture=True):
    cmd = ["git", "-C", repo]
    if token:
        cmd += ["-c", f"credential.helper={CRED_HELPER}"]
    cmd += args
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if token:
        env["GIT_PUSH_TOKEN"] = token
    return subprocess.run(cmd, cwd=repo, capture_output=capture, text=True, env=env)


def out(r):
    return ((r.stdout or "") + (r.stderr or "")).strip()


def needs_auth(url):
    """ローカルパス（テスト用のベアリポジトリなど）は認証不要。"""
    return url.startswith("http://") or url.startswith("https://")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=ROOT)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--message", default=None)
    args = ap.parse_args()
    repo = os.path.abspath(args.repo)

    r = git(["config", "--get", f"remote.{args.remote}.url"], repo)
    if r.returncode != 0:
        print(f"[エラー] リモート {args.remote} が無い", file=sys.stderr)
        return 1
    url = r.stdout.strip()

    token = None
    if needs_auth(url):
        for name in TOKEN_ENV:
            if os.environ.get(name):
                token = os.environ[name]
                break
        if not token:
            print(f"[エラー] {' / '.join(TOKEN_ENV)} が未設定のため push できない。"
                  "状態は永続化されないので、次回の差分基準がずれる。", file=sys.stderr)
            return 2

    # --- コミット ---
    present = [f for f in STATE_FILES if os.path.exists(os.path.join(repo, f))]
    if not present:
        print(f"[エラー] 状態ファイルが無い: {', '.join(STATE_FILES)}", file=sys.stderr)
        return 1

    r = git(["add", "--"] + present, repo)
    if r.returncode != 0:
        print(f"[エラー] git add 失敗\n{out(r)}", file=sys.stderr)
        return 1

    if git(["diff", "--cached", "--quiet"], repo).returncode == 0:
        print("NOTHING")
        print("[情報] 状態に変化なし。コミットするものが無い。", file=sys.stderr)
        return 0

    msg = args.message or f"state: {datetime.now(JST).strftime('%Y-%m-%d %H:%M')} JST"
    r = git(["commit", "-m", msg], repo)
    if r.returncode != 0:
        print(f"[エラー] commit 失敗\n{out(r)}", file=sys.stderr)
        return 1

    head = git(["rev-parse", "HEAD"], repo).stdout.strip()

    # --- push（ネットワーク失敗はリトライ）---
    refspec = f"HEAD:refs/heads/{args.branch}"
    last = ""
    for i, wait in enumerate([0] + RETRY_WAITS):
        if wait:
            time.sleep(wait)
        r = git(["push", args.remote, refspec], repo, token=token)
        if r.returncode == 0:
            break
        last = redact(out(r), token)
        print(f"[警告] push 失敗 ({i + 1}回目)\n{last}", file=sys.stderr)
    else:
        print(f"[エラー] push を {len(RETRY_WAITS) + 1} 回試して失敗。"
              f"コミット {head[:7]} はローカルにしか無い。"
              "次回の差分基準がずれるので手当てが必要。", file=sys.stderr)
        return 1

    # --- 反映確認（「成功したように見えて実は無反映」を潰す）---
    r = git(["ls-remote", args.remote, f"refs/heads/{args.branch}"], repo, token=token)
    remote_sha = (r.stdout.split()[0] if r.returncode == 0 and r.stdout.split() else "")
    if remote_sha != head:
        print(f"[エラー] push は成功を返したがリモートに反映されていない "
              f"(local={head[:7]} remote={remote_sha[:7] or 'なし'})", file=sys.stderr)
        return 1

    print("PUSHED")
    print(f"[情報] {args.branch} に {head[:7]} を反映: {msg}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
