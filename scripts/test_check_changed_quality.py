"""Targeted tests for the changed-source quality gate, without Git writes."""

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from scripts import check_changed_quality as quality

BASE = "a" * 40


def record(name, *, mode=b"100644", status=b"M"):
    return b":100644 " + mode + b" aaaaaaa bbbbbbb " + status + b"\0" + name + b"\0"


class SelectionTests(unittest.TestCase):
    def test_git_diff_compares_the_pr_base_to_head_without_deletions(self):
        with patch.object(quality.subprocess, "run") as run:
            run.return_value.stdout = b""
            self.assertEqual(quality.git_changes(Path.cwd(), BASE), b"")
        command = run.call_args.args[0]
        self.assertIn("-z", command)
        self.assertIn("--no-renames", command)
        self.assertIn("--diff-filter=AMT", command)
        self.assertEqual(command[-3:], [f"{BASE}^{{commit}}", "HEAD", "--"])

    def test_only_regular_destination_sources_are_selected(self):
        raw_diff = b"".join(
            [
                record(b"scripts/with spaces.py", status=b"A", mode=b"100755"),
                record(b"frontend/scripts/helper.py"),
                record(b"frontend/src/component\nname.tsx"),
                record(b"frontend/src/styles/base.css"),
                record(b"frontend/index.html"),
                record(b"frontend/scripts/-renamed.mjs", status=b"A"),
                record(b"backend/gone.py", status=b"D", mode=b"000000"),
                record(b"backend/link.py", status=b"T", mode=b"120000"),
                record(b"frontend/submodule.ts", mode=b"160000"),
                record(b"frontend/dist/out.js"),
                record(b"frontend/node_modules/pkg/index.js"),
                record(b"backend/template.html"),
                record(b"frontend/biome.json"),
                record(b"docs/guide.md"),
                record(b"frontend"),
            ]
        )
        python, frontend = quality.source_paths(raw_diff)
        self.assertEqual(
            [path.as_posix() for path in python],
            ["frontend/scripts/helper.py", "scripts/with spaces.py"],
        )
        self.assertEqual(
            [path.as_posix() for path in frontend],
            [
                "frontend/index.html",
                "frontend/scripts/-renamed.mjs",
                "frontend/src/component\nname.tsx",
                "frontend/src/styles/base.css",
            ],
        )


class GateTests(unittest.TestCase):
    def test_no_sources_does_not_start_tools(self):
        with (
            patch.object(quality, "git_changes", return_value=record(b"docs/guide.md")),
            patch.object(quality, "run_checks") as checks,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(quality.main(["--base", BASE]), 0)
        checks.assert_not_called()

    def test_git_failure_is_not_a_successful_empty_diff(self):
        error = subprocess.CalledProcessError(128, ["git", "diff"])
        with (
            patch.object(quality, "git_changes", side_effect=error),
            redirect_stderr(io.StringIO()) as output,
        ):
            self.assertEqual(quality.main(["--base", BASE]), 1)
        self.assertIn("quality check failed", output.getvalue())

    def test_checks_report_failure_without_skipping_the_other_tools(self):
        python = [PurePosixPath("scripts/with spaces.py")]
        frontend = [PurePosixPath("frontend/-sample.ts")]
        with patch.object(quality.subprocess, "run") as run:
            run.side_effect = [
                subprocess.CompletedProcess([], code) for code in (1, 0, 0)
            ]
            self.assertEqual(quality.run_checks(Path.cwd(), python, frontend), 1)
        lint, format_check, biome = [call.args[0] for call in run.call_args_list]
        self.assertEqual(lint[1:4], ["-m", "ruff", "check"])
        self.assertEqual(format_check[1:5], ["-m", "ruff", "format", "--check"])
        self.assertEqual(lint[-2:], ["--", "./scripts/with spaces.py"])
        self.assertEqual(format_check[-2:], lint[-2:])
        self.assertEqual(biome[-2:], ["--", "./-sample.ts"])
        self.assertIn("--error-on-warnings", biome)
        self.assertFalse(any("--write" in args for args in (lint, format_check, biome)))
        self.assertEqual(run.call_args.kwargs["cwd"], Path.cwd() / "frontend")

    def test_length_and_duplicates_do_not_fail_the_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            lines = [f"value_{n} = {n}" for n in range(12)]
            (root / "long.py").write_text("\n".join(lines + [""] * 489) + "\n")
            (root / "second.py").write_text("\n\n  ".join(lines) + "\n")
            (root / "at_limit.py").write_text("\n" * 500)
            raw_diff = (
                record(b"long.py") + record(b"second.py") + record(b"at_limit.py")
            )
            with (
                patch.object(quality, "ROOT", root),
                patch.object(quality, "git_changes", return_value=raw_diff),
                patch.object(quality, "run_checks", return_value=0),
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(quality.main(["--base", BASE]), 0)
            self.assertIn("'long.py' has 501 lines", output.getvalue())
            self.assertNotIn("'at_limit.py' has", output.getvalue())
            self.assertIn("duplicate block", output.getvalue())
            self.assertIn("'long.py':1 and 'second.py':1", output.getvalue())

    def test_overlapping_windows_are_not_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "repeat.py").write_text("same_text()\n" * 23)
            with redirect_stdout(io.StringIO()) as output:
                quality.warn(root, [PurePosixPath("repeat.py")])
            self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
