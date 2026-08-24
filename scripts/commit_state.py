#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""baseline.json / alerted.json をコミットして push する。

Routine は毎回新しいコンテナで動くため、push が通らないと差分の基準が
次の回に引き継がれない。この経路は過去に二度、**無言で** 壊れている:

  2026-08-06 … クローンが読み取り専用で push が拒否された
  2026-08-20 … Routine が claude/* ブランチ上で動くようになったのに、
               手順が `git push -u origin main` を決め打ちしていた。
               コミットは claude/* に載り main には何も乗らないため、
               git は "Everything up-to-date" と言って exit 0 を返す。
               ベースラインは 8/20 09:40 のまま5日間凍結した。

どちらも「push したつもり」で正常終了するのが厄介なので、このスクリプトは

  1. push 先を **現在のブランチ** から決める（決め打ちしない）
  2. push 後にリモートの ref を読み直し、コミットが本当に載ったか確認する

の2点で silent failure を潰す。載っていなければ非ゼロで終わる。

push 先を明示したい場合だけ環境変数 `STATE_BRANCH` で上書きできる。

終了コード:
  0 … push 成功、またはコミットするものが無い
  1 … コミットまたは push に失敗（次回の差分基準がずれる）
"""
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JST = timezone(timedelta(hours=9))
FILES = ["data/baseline.json", "data/alerted.json"]
RETRY_DELAYS = [2, 4, 8, 16]

# 再試行しても直らない push 失敗（リモートが進んでいる／権限が無い）
FATAL = ("non-fast-forward", "fetch first", "rejected", "denied", "403", "authentication")


class GitError(RuntimeError):
    pass


def git(*args, check=True):
    r = subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise GitError(f"git {' '.join(args)} が失敗しました:\n{(r.stderr or r.stdout).strip()}")
    return r


def target_branch():
    """push 先のブランチ名。STATE_BRANCH があればそれ、無ければ現在のブランチ。"""
    override = os.environ.get("STATE_BRANCH", "").strip()
    if override:
        return override
    name = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if name == "HEAD":
        raise GitError("detached HEAD です。ブランチをチェックアウトしてから実行すること。")
    return name


def push(branch, head):
    """push して、リモートに head が載ったことを確認する。載れば True。"""
    for attempt, delay in enumerate([None, *RETRY_DELAYS], start=1):
        if delay:
            time.sleep(delay)
        r = git("push", "origin", f"HEAD:refs/heads/{branch}", check=False)
        if r.returncode == 0:
            break
        err = (r.stderr or r.stdout).strip()
        print(f"[警告] push 失敗 (試行{attempt}): {err}", file=sys.stderr)
        if any(w in err.lower() for w in FATAL):
            print("[エラー] 再試行しても直らない失敗です。権限とリモートの状態を確認すること。",
                  file=sys.stderr)
            return False
    else:
        print("[エラー] push できませんでした（通信エラー）。", file=sys.stderr)
        return False

    # git が成功と言っても信用しない。リモートを読み直して確認する。
    # 2026-08-20 の事故は、まさにここが exit 0 のまま素通りしていた。
    out = git("ls-remote", "origin", f"refs/heads/{branch}", check=False).stdout.split()
    remote = out[0] if out else None
    if remote != head:
        print(f"[エラー] push は成功と報告されましたが、origin/{branch} に {head[:7]} が "
              f"載っていません（リモート: {remote[:7] if remote else 'なし'}）。",
              file=sys.stderr)
        return False

    print(f"push 完了: origin/{branch} = {head[:7]}")
    return True


def main():
    try:
        branch = target_branch()
    except GitError as e:
        print(f"[エラー] {e}", file=sys.stderr)
        return 1

    existing = [f for f in FILES if os.path.exists(os.path.join(ROOT, f))]
    if not existing:
        print(f"[エラー] {' / '.join(FILES)} が見つかりません。"
              "先に format_report.py を実行すること。", file=sys.stderr)
        return 1

    try:
        git("add", "--", *existing)
        if not git("diff", "--cached", "--name-only").stdout.strip():
            print("[情報] 変化なし。コミットするものはありません。")
            return 0
        stamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
        git("commit", "-m", f"state: {stamp} JST")
        head = git("rev-parse", "HEAD").stdout.strip()
    except GitError as e:
        print(f"[エラー] {e}", file=sys.stderr)
        return 1

    print(f"コミット {head[:7]} をブランチ {branch} へ push します")
    if not push(branch, head):
        print("[エラー] 状態を永続化できませんでした。次回の差分基準がずれます。",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
