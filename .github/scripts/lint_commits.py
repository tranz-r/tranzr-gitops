#!/usr/bin/env python3
"""Lint commit messages with opensource-nepal commitlint CLI.

Validates every commit in the trusted event revision range via
``commitlint --hash``. One exact historical SHA already accepted on
``origin/main`` may be skipped after an ancestor audit check; a new commit
with the same title is still rejected.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import Iterable, List, Optional, Sequence

# Exact published main tip title Develop (#44). Not overridable via env/CLI.
GRANDFATHERED_SHA = "7c91ac878c344c53bb385c57bc8c8e75ac5eef06"
ZERO_SHA = "0" * 40
MAIN_REF = "origin/main"


def run_git(args: Sequence[str], *, cwd: Optional[str] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def is_ancestor(commit: str, ref: str, *, cwd: Optional[str] = None) -> bool:
    result = run_git(["merge-base", "--is-ancestor", commit, ref], cwd=cwd)
    return result.returncode == 0


def list_commits(base: str, head: str, *, cwd: Optional[str] = None) -> List[str]:
    result = run_git(["rev-list", "--reverse", f"{base}..{head}"], cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(
            f"git rev-list failed for {base}..{head}: {result.stderr.strip()}"
        )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def resolve_range(
    event_name: str,
    *,
    pr_base: str,
    pr_head: str,
    push_before: str,
    push_after: str,
) -> tuple[str, str]:
    if event_name == "pull_request":
        if not pr_base or not pr_head:
            raise RuntimeError("pull_request requires PR_BASE_SHA and PR_HEAD_SHA")
        return pr_base, pr_head
    if event_name == "push":
        if not push_before or not push_after:
            raise RuntimeError("push requires PUSH_BEFORE and PUSH_AFTER")
        if push_before == ZERO_SHA or push_after == ZERO_SHA:
            raise RuntimeError(
                "push range contains zero SHA; fail-closed (ref create/delete unsupported)"
            )
        return push_before, push_after
    raise RuntimeError(f"unsupported event: {event_name}")


def should_grandfather(sha: str, *, cwd: Optional[str] = None) -> bool:
    if sha != GRANDFATHERED_SHA:
        return False
    if not is_ancestor(GRANDFATHERED_SHA, MAIN_REF, cwd=cwd):
        print(
            f"AUDIT: {GRANDFATHERED_SHA} is not an ancestor of {MAIN_REF}; "
            "not grandfathered, will lint",
            file=sys.stderr,
        )
        return False
    print(
        f"AUDIT: skipping exact grandfathered SHA {GRANDFATHERED_SHA} "
        f"(confirmed ancestor of {MAIN_REF}; historical Develop (#44) already on main)",
        file=sys.stderr,
    )
    return True


def lint_hash(
    sha: str,
    *,
    commitlint_bin: str,
    cwd: Optional[str] = None,
) -> int:
    result = subprocess.run(
        [commitlint_bin, "--hash", sha],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode


def lint_commits(
    commits: Iterable[str],
    *,
    commitlint_bin: str,
    cwd: Optional[str] = None,
) -> int:
    failed = 0
    for sha in commits:
        if should_grandfather(sha, cwd=cwd):
            continue
        code = lint_hash(sha, commitlint_bin=commitlint_bin, cwd=cwd)
        if code != 0:
            print(f"FAIL: commitlint --hash {sha} exited {code}", file=sys.stderr)
            failed = 1
    return failed


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Lint commits for GitHub push/pull_request events."
    )
    parser.add_argument(
        "--commitlint-bin",
        default=os.environ.get("COMMITLINT_BIN", "commitlint"),
        help="Path to commitlint CLI (test injection). Exception SHA is not overridable.",
    )
    parser.add_argument("--cwd", default=None, help="Git working directory")
    parser.add_argument(
        "--event-name",
        default=os.environ.get("EVENT_NAME", ""),
        help="GitHub event name (pull_request or push)",
    )
    parser.add_argument(
        "--pr-base-sha",
        default=os.environ.get("PR_BASE_SHA", ""),
        help="pull_request base SHA from trusted event payload",
    )
    parser.add_argument(
        "--pr-head-sha",
        default=os.environ.get("PR_HEAD_SHA", ""),
        help="pull_request head SHA from trusted event payload",
    )
    parser.add_argument(
        "--push-before",
        default=os.environ.get("PUSH_BEFORE", ""),
        help="push before SHA from trusted event payload",
    )
    parser.add_argument(
        "--push-after",
        default=os.environ.get("PUSH_AFTER", ""),
        help="push after SHA from trusted event payload",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        base, head = resolve_range(
            args.event_name,
            pr_base=args.pr_base_sha,
            pr_head=args.pr_head_sha,
            push_before=args.push_before,
            push_after=args.push_after,
        )
        commits = list_commits(base, head, cwd=args.cwd)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Linting {len(commits)} commit(s) in {base}..{head}", file=sys.stderr)
    return lint_commits(commits, commitlint_bin=args.commitlint_bin, cwd=args.cwd)


if __name__ == "__main__":
    sys.exit(main())
