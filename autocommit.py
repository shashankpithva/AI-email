#!/usr/bin/env python3
"""
Auto-commit watcher
-------------------
Watches this git repository folder and, whenever files change (added, edited,
or deleted), automatically runs:  git add -A  ->  git commit  ->  git push.

- No external dependencies (uses only the Python standard library + git).
- Cross-platform (Windows, macOS, Linux).
- Respects your .gitignore automatically (so .env is never committed).
- Debounces: waits until changes settle before committing, so dropping several
  files at once results in a single tidy commit.

------------------------------------------------------------------------------
SETUP (one time)
------------------------------------------------------------------------------
1. Put this file inside your git repo folder (next to ai_email_writer.py).
2. Make sure git is set up and the remote works:
       git remote -v          # should show your GitHub URL
       git push               # do one manual push first to confirm auth works

------------------------------------------------------------------------------
RUN
------------------------------------------------------------------------------
    python3 autocommit.py

    # options:
    python3 autocommit.py --interval 3      # check every 3 seconds (default 3)
    python3 autocommit.py --no-push         # commit locally but don't push
    python3 autocommit.py --path /repo/dir  # watch a specific folder

Stop it anytime with Ctrl+C.
"""

import os
import sys
import time
import argparse
import subprocess
from datetime import datetime


def run_git(args, cwd):
    """Run a git command and return (exit_code, stdout+stderr)."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def ensure_git_repo(path):
    code, out = run_git(["rev-parse", "--is-inside-work-tree"], path)
    if code != 0 or out.strip() != "true":
        sys.exit(
            f"[error] '{path}' is not a git repository.\n"
            f"        cd into your repo folder, or pass --path /path/to/repo."
        )


def has_changes(path):
    """Return the porcelain status output if there are changes, else ''."""
    code, out = run_git(["status", "--porcelain"], path)
    if code != 0:
        print(f"[warn] git status failed: {out}")
        return ""
    return out.strip()


def summarize(status_text):
    """Turn porcelain status into a short human summary for the commit message."""
    files = []
    for line in status_text.splitlines():
        # porcelain format: 'XY filename'
        name = line[3:].strip()
        if name:
            files.append(name)
    if not files:
        return "changes"
    if len(files) <= 3:
        return ", ".join(files)
    return f"{files[0]}, {files[1]} and {len(files) - 2} more"


def commit_and_push(path, do_push):
    status = has_changes(path)
    if not status:
        return False

    # Stage everything (respects .gitignore).
    code, out = run_git(["add", "-A"], path)
    if code != 0:
        print(f"[warn] git add failed: {out}")
        return False

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message = f"auto: {summarize(status)} ({stamp})"

    code, out = run_git(["commit", "-m", message], path)
    if code != 0:
        # Nothing to commit can happen if changes were only ignored files.
        print(f"[info] nothing committed: {out}")
        return False
    print(f"[commit] {message}")

    if do_push:
        code, out = run_git(["push"], path)
        if code != 0:
            print(f"[warn] git push failed:\n{out}")
        else:
            print("[push]   pushed to remote")
    return True


def parse_args():
    parser = argparse.ArgumentParser(description="Auto-commit & push on file changes.")
    parser.add_argument("--path", default=".", help="Repo folder to watch (default: current dir).")
    parser.add_argument("--interval", type=float, default=3.0, help="Seconds between checks (default: 3).")
    parser.add_argument("--debounce", type=float, default=2.0,
                        help="Seconds of quiet before committing (default: 2).")
    parser.add_argument("--no-push", action="store_true", help="Commit locally but do not push.")
    return parser.parse_args()


def main():
    args = parse_args()
    path = os.path.abspath(args.path)
    ensure_git_repo(path)

    do_push = not args.no_push
    print(f"[watching] {path}")
    print(f"[config]   interval={args.interval}s  debounce={args.debounce}s  push={do_push}")
    print("[ready]    drop or edit files -- I'll commit them. Press Ctrl+C to stop.\n")

    last_status = has_changes(path)
    stable_since = None

    try:
        while True:
            time.sleep(args.interval)
            current = has_changes(path)

            if not current:
                stable_since = None
                last_status = ""
                continue

            if current != last_status:
                # Something changed since last check -- reset the debounce timer.
                last_status = current
                stable_since = time.time()
                print(f"[detected] {summarize(current)}")
                continue

            # Status is unchanged since last check. If it's been quiet long
            # enough, commit. This avoids committing mid-copy of large drops.
            if stable_since and (time.time() - stable_since) >= args.debounce:
                commit_and_push(path, do_push)
                last_status = has_changes(path)
                stable_since = None
    except KeyboardInterrupt:
        print("\n[stopped] auto-commit watcher shut down.")


if __name__ == "__main__":
    main()
