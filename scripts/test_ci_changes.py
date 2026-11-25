"""Exercise CI routing without modifying the Git checkout."""

import contextlib
import io
import subprocess
import unittest
from unittest.mock import patch

import ci_changes


HEAD, BASE, PR_HEAD = (digit * 40 for digit in "abc")


class PathTests(unittest.TestCase):
    def test_path_scopes(self):
        cases = {
            b"docs/operations/example.md": set(),
            b"frontend/src/example.ts": {"frontend"},
            b"docs/translation-review/japanese/manifest.csv": {"frontend"},
            b"docs/translation-review/application/register.csv": {"frontend", "backend"},
            b"backend/apps/example.py": {"backend", "backend_postgres", "deploy_scripts"},
            b"uv.lock": {"backend", "backend_postgres", "deploy_scripts"},
            b"scripts/run_backend_postgres_ci.py": {"backend", "backend_postgres"},
            b"deploy/deploy_vm.ps1": {"deploy_scripts"},
            b".github/workflows/ci.yml": set(ci_changes.CHECKS),
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(ci_changes.classify([path]), expected)


class EventTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_SHA": HEAD,
            "PR_BASE_SHA": BASE,
            "PR_HEAD_SHA": PR_HEAD,
            "PUSH_BEFORE_SHA": BASE,
        }

    def test_pr_uses_verified_merge_result(self):
        parents = f"{HEAD} {BASE} {PR_HEAD}\n".encode()
        with patch.object(ci_changes, "git", side_effect=[HEAD.encode(), parents, b"frontend/\xff\nfile\0deploy/old\0"]) as git:
            self.assertEqual(ci_changes.select_checks(self.env), {"frontend", "deploy_scripts"})
        self.assertEqual(git.call_args_list[1].args, ("rev-list", "--parents", "-n", "1", HEAD))
        args = git.call_args.args
        self.assertIn("-z", args)
        self.assertIn("--no-renames", args)
        self.assertEqual(args[-3:], (f"{BASE}^{{commit}}", f"{HEAD}^{{commit}}", "--"))

    def test_push_compares_before_and_after_trees_without_ancestry_check(self):
        self.env["GITHUB_EVENT_NAME"] = "push"
        with patch.object(ci_changes, "git", side_effect=[HEAD.encode(), b"docs/example.md\0"]) as git:
            self.assertEqual(ci_changes.select_checks(self.env), set())
        self.assertEqual(git.call_count, 2)
        self.assertEqual(git.call_args.args[-3:], (f"{BASE}^{{commit}}", f"{HEAD}^{{commit}}", "--"))

    def test_dispatch_needs_no_git(self):
        with patch.object(ci_changes, "git") as git:
            self.assertEqual(ci_changes.select_checks({"GITHUB_EVENT_NAME": "workflow_dispatch"}), set(ci_changes.CHECKS))
        git.assert_not_called()

    def test_unverified_comparisons_run_all_checks(self):
        cases = (
            ({"GITHUB_EVENT_NAME": "push", "PUSH_BEFORE_SHA": "0" * 40}, [HEAD.encode()]),
            ({"PR_BASE_SHA": "--bad-option"}, [HEAD.encode()]),
            ({}, [BASE.encode()]),
            ({}, [HEAD.encode(), f"{HEAD} {PR_HEAD} {BASE}".encode()]),
            ({"GITHUB_EVENT_NAME": "push"}, [HEAD.encode(), subprocess.CalledProcessError(128, "git diff")]),
            ({"GITHUB_EVENT_NAME": "push"}, [subprocess.TimeoutExpired("git rev-parse", 60)]),
        )
        for overrides, replies in cases:
            with self.subTest(overrides=overrides, replies=replies), contextlib.redirect_stderr(io.StringIO()):
                with patch.object(ci_changes, "git", side_effect=replies):
                    self.assertEqual(ci_changes.select_checks(self.env | overrides), set(ci_changes.CHECKS))


if __name__ == "__main__":
    unittest.main()
