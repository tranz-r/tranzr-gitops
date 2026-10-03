#!/usr/bin/env python3
"""Unit tests for .github/scripts/lint_commits.py using fixture git repos."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = ROOT / ".github" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import lint_commits  # noqa: E402


def run(cmd, cwd):
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )


def git(cwd, *args):
    return run(["git", *args], cwd)


def write_fake_commitlint(path: Path, fail_on_substr: str = "Develop (#44)") -> Path:
    path.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import sys
            import subprocess
            if len(sys.argv) < 3 or sys.argv[1] != "--hash":
                sys.exit(2)
            sha = sys.argv[2]
            msg = subprocess.check_output(
                ["git", "log", "-1", "--format=%B", sha], text=True
            )
            open({str(path.parent / "calls.log")!r}, "a").write(sha + "\\n")
            if {fail_on_substr!r} in msg.splitlines()[0]:
                print("fake fail:", msg.splitlines()[0], file=sys.stderr)
                sys.exit(1)
            print("fake ok:", sha)
            sys.exit(0)
            """
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


class LintCommitsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init")
        git(self.repo, "config", "user.email", "test@example.com")
        git(self.repo, "config", "user.name", "Test")
        self.bin_dir = Path(self.tmp.name) / "bin"
        self.bin_dir.mkdir()
        self.fake_cli = write_fake_commitlint(self.bin_dir / "commitlint")

    def tearDown(self):
        self.tmp.cleanup()

    def _commit(self, message: str) -> str:
        # Unique blob each call so identical subjects still create new commits.
        self._n = getattr(self, "_n", 0) + 1
        (self.repo / "f.txt").write_text(f"{message}\n{self._n}\n", encoding="utf-8")
        git(self.repo, "add", "f.txt")
        git(self.repo, "commit", "-m", message)
        return git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def _set_origin_main(self, sha: str) -> None:
        git(self.repo, "update-ref", "refs/remotes/origin/main", sha)

    def test_resolve_pull_request_range(self):
        base, head = lint_commits.resolve_range(
            "pull_request",
            pr_base="aaa",
            pr_head="bbb",
            push_before="",
            push_after="",
        )
        self.assertEqual((base, head), ("aaa", "bbb"))

    def test_push_zero_sha_fail_closed(self):
        with self.assertRaises(RuntimeError):
            lint_commits.resolve_range(
                "push",
                pr_base="",
                pr_head="",
                push_before=lint_commits.ZERO_SHA,
                push_after="abc",
            )

    def test_good_new_commit_passes(self):
        base = self._commit("chore: base")
        head = self._commit("fix(gitops): good new commit")
        self._set_origin_main(base)
        code = lint_commits.lint_commits(
            lint_commits.list_commits(base, head, cwd=str(self.repo)),
            commitlint_bin=str(self.fake_cli),
            cwd=str(self.repo),
        )
        self.assertEqual(code, 0)
        calls = (self.bin_dir / "calls.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(calls, [head])

    def test_bad_new_commit_fails(self):
        base = self._commit("chore: base")
        head = self._commit("Develop (#44)")
        self._set_origin_main(base)
        code = lint_commits.lint_commits(
            [head],
            commitlint_bin=str(self.fake_cli),
            cwd=str(self.repo),
        )
        self.assertEqual(code, 1)

    def test_historic_exact_sha_grandfathered_when_main_ancestor(self):
        # Build a commit, then rewrite its object identity is hard; instead
        # monkeypatch the production constant to the fixture SHA.
        base = self._commit("chore: base")
        historic = self._commit("Develop (#44)")
        head = self._commit("fix(gitops): reconcile")
        self._set_origin_main(historic)
        original = lint_commits.GRANDFATHERED_SHA
        lint_commits.GRANDFATHERED_SHA = historic
        try:
            code = lint_commits.lint_commits(
                lint_commits.list_commits(base, head, cwd=str(self.repo)),
                commitlint_bin=str(self.fake_cli),
                cwd=str(self.repo),
            )
        finally:
            lint_commits.GRANDFATHERED_SHA = original
        self.assertEqual(code, 0)
        calls = (self.bin_dir / "calls.log").read_text(encoding="utf-8").splitlines()
        # Historic skipped; only the new reconcile commit linted.
        self.assertEqual(calls, [head])

    def test_same_title_new_commit_not_grandfathered(self):
        base = self._commit("chore: base")
        historic = self._commit("Develop (#44)")
        twin = self._commit("Develop (#44)")
        self._set_origin_main(historic)
        original = lint_commits.GRANDFATHERED_SHA
        lint_commits.GRANDFATHERED_SHA = historic
        try:
            code = lint_commits.lint_commits(
                [historic, twin],
                commitlint_bin=str(self.fake_cli),
                cwd=str(self.repo),
            )
        finally:
            lint_commits.GRANDFATHERED_SHA = original
        self.assertEqual(code, 1)
        calls = (self.bin_dir / "calls.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(calls, [twin])

    def test_exact_sha_without_main_ancestor_still_linted(self):
        base = self._commit("chore: base")
        historic = self._commit("Develop (#44)")
        # origin/main points at base only — historic is NOT an ancestor of main.
        self._set_origin_main(base)
        original = lint_commits.GRANDFATHERED_SHA
        lint_commits.GRANDFATHERED_SHA = historic
        try:
            code = lint_commits.lint_commits(
                [historic],
                commitlint_bin=str(self.fake_cli),
                cwd=str(self.repo),
            )
        finally:
            lint_commits.GRANDFATHERED_SHA = original
        self.assertEqual(code, 1)

    def test_exception_constant_not_env_overridable(self):
        os.environ["GRANDFATHERED_SHA"] = "deadbeef" * 5
        self.assertEqual(
            lint_commits.GRANDFATHERED_SHA,
            "7c91ac878c344c53bb385c57bc8c8e75ac5eef06",
        )
        del os.environ["GRANDFATHERED_SHA"]

    def test_cli_injection_path_used(self):
        base = self._commit("chore: base")
        head = self._commit("chore: uses injected cli")
        self._set_origin_main(base)
        marker = self.bin_dir / "marker"
        custom = self.bin_dir / "custom-commitlint"
        custom.write_text(
            textwrap.dedent(
                f"""\
                #!/bin/sh
                echo injected > {marker}
                exit 0
                """
            ),
            encoding="utf-8",
        )
        custom.chmod(0o755)
        code = lint_commits.main(
            [
                "--event-name",
                "pull_request",
                "--pr-base-sha",
                base,
                "--pr-head-sha",
                head,
                "--commitlint-bin",
                str(custom),
                "--cwd",
                str(self.repo),
            ]
        )
        self.assertEqual(code, 0)
        self.assertTrue(marker.exists())

    def test_production_sha_constant_is_exact(self):
        self.assertEqual(
            lint_commits.GRANDFATHERED_SHA,
            "7c91ac878c344c53bb385c57bc8c8e75ac5eef06",
        )


if __name__ == "__main__":
    unittest.main()
