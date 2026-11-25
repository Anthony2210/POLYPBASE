import unittest
from unittest.mock import patch

import check_commit_messages as gate


class CommitMessageTests(unittest.TestCase):
    def test_headline_format(self):
        for headline in (
            "ci: validate commit subjects",
            "docs: explain conventions",
            "fix(i18n): preserve approved labels",
            "fix: x",
        ):
            with self.subTest(headline=headline):
                self.assertTrue(gate.valid_headline(headline))
        for headline in (
            "i18n: fix labels",
            "feat(api): add view",
            "ci:missing space",
            "ci: fix.",
            "ci:  fix",
            "ci: fix\nmore",
            "ci: Fix",
            "ci: 2 fixes",
            "docs(docs): update guidance",
            "ci(ci): validate commits",
            "fix: " + "x" * 68,
        ):
            with self.subTest(headline=headline):
                self.assertFalse(gate.valid_headline(headline))

    def test_pr_title_and_all_non_merge_subjects(self):
        base, head, other, bot = (digit * 40 for digit in "abcd")
        event = {
            "title": "Bad title",
            "user": {"login": "person"},
            "base": {"sha": base},
            "head": {"sha": head},
        }
        commits = "\0".join(
            (
                head,
                "Author",
                "author@example.test",
                "fix(boxes): correct counts",
                other,
                "Author",
                "author@example.test",
                "Bad commit",
                bot,
                gate.DEPENDABOT,
                gate.DEPENDABOT_EMAIL,
                "Bump packages",
                "",
            )
        )
        with patch.object(gate, "git", return_value=commits.encode()) as git:
            self.assertEqual(len(gate.check_pull_request(event)), 2)
            event["title"] = "fix(boxes): correct counts"
            self.assertEqual(
                gate.check_pull_request(event),
                [f"Commit {other[:12]} does not match the commit headline format."],
            )
        self.assertIn("--no-merges", git.call_args.args)
        self.assertIn(f"{base}..{head}", git.call_args.args)

    def test_legacy_commit_is_exempt_only_for_pr_52(self):
        number, oid = gate.LEGACY_HEADLINE
        event = {
            "number": number,
            "title": "docs: establish contributor conventions",
            "user": {"login": "person"},
            "base": {"sha": "a" * 40},
            "head": {"sha": "b" * 40},
        }
        subject = "docs(docs): Establish contributor conventions"
        for pr, commit, expected in (
            (number, oid, 0),
            (number + 1, oid, 1),
            (number, "b" * 40, 1),
        ):
            with self.subTest(pr=pr, commit=commit):
                commits = "\0".join(
                    (commit, "Author", "author@example.test", subject, "")
                )
                with patch.object(gate, "git", return_value=commits.encode()):
                    self.assertEqual(
                        len(gate.check_pull_request(event | {"number": pr})), expected
                    )

    def test_dependabot_title_is_exempt(self):
        event = {
            "title": "Bump packages",
            "user": {"login": gate.DEPENDABOT},
            "base": {"sha": "a" * 40},
            "head": {"sha": "b" * 40},
        }
        with patch.object(gate, "git", return_value=b""):
            self.assertEqual(gate.check_pull_request(event), [])

    def test_invalid_revision_never_reaches_git(self):
        with patch.object(gate, "git") as git, self.assertRaises(ValueError):
            gate.check_pull_request(
                {"base": {"sha": "--all"}, "head": {"sha": "b" * 40}}
            )
        git.assert_not_called()
