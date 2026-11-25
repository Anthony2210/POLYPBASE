import unittest
from unittest.mock import patch

import check_commit_messages as gate


class CommitMessageTests(unittest.TestCase):
    def test_headline_format(self):
        for headline in (
            "ci(ci): Validate commit subjects",
            "docs: Explain conventions",
            "fix(i18n): Preserve approved labels",
        ):
            with self.subTest(headline=headline):
                self.assertTrue(gate.valid_headline(headline))
        for headline in (
            "i18n: Fix labels",
            "feat(api): Add view",
            "ci:Missing space",
            "ci: Fix.",
            "ci:  Fix",
            "ci: Fix\nmore",
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
                "fix(boxes): Correct counts",
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
            event["title"] = "fix(boxes): Correct counts"
            self.assertEqual(
                gate.check_pull_request(event),
                [f"Commit {other[:12]} does not match the commit headline format."],
            )
        self.assertIn("--no-merges", git.call_args.args)
        self.assertIn(f"{base}..{head}", git.call_args.args)

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
