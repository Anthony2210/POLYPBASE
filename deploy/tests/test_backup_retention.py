"""Broker retention on synthetic names and temporary directories; no VM or database."""

import datetime
import importlib.util
import inspect
import io
import os
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "trusted_backup_retention", ROOT / "deploy/scripts/trusted_deployment_backup.py"
)
broker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(broker)
UTC = datetime.UTC


def at(text):
    return datetime.datetime.fromisoformat(text).replace(tzinfo=UTC)


def nightly(moment):
    return f"nightly-{moment:%Y%m%dT%H%M%SZ}.dump"


def pre(sha, moment):
    return f"pre-{sha}-{moment:%Y%m%dT%H%M%SZ}.dump"


def daily(first, last, hour="02:40:00"):
    day, names = datetime.date.fromisoformat(first), []
    while day <= datetime.date.fromisoformat(last):
        names.append(nightly(at(f"{day}T{hour}")))
        day += datetime.timedelta(days=1)
    return names


def kept(decisions):
    return {name for name, keep, _ in decisions if keep}


def deleted(decisions):
    return {name for name, keep, _ in decisions if not keep}


class ClassificationTests(unittest.TestCase):
    def test_managed_names_take_time_from_the_name(self):
        self.assertEqual(
            broker.classify_backup("nightly-20261231T023000Z.dump"),
            ("nightly", at("2026-12-31T02:30:00")),
        )
        self.assertEqual(
            broker.classify_backup("pre-0123abcd-20270101T000000Z.dump"),
            ("pre-deploy", at("2027-01-01T00:00:00")),
        )

    def test_unknown_and_older_formats_are_ignored(self):
        for name in (
            "backup-20261101T000000Z.dump",
            "pre-deploy-20261101T000000Z.dump",
            "pre-" + "a" * 40 + "-20261101T000000Z.dump",
            "pre-ABCDEF12-20261101T000000Z.dump",
            "pre-0123abc-20261101T000000Z.dump",
            "Nightly-20261101T000000Z.dump",
            "nightly-20261101T000000.dump",
            "nightly-20261101-000000Z.dump",
            "nightly-20261101T000000Z.dump.tmp",
            "nightly-20261101T000000Z.dump\n",
            ".nightly-20261101T000000Z.dump",
            "nightly-20261301T000000Z.dump",
            "nightly-20260230T000000Z.dump",
            "nightly-20261101T240000Z.dump",
            "nightly-20261101T006000Z.dump",
            "nightly-٢٠٢٦" + "1101T000000Z.dump",
            "nightly.dump",
            "",
        ):
            with self.subTest(name=name):
                self.assertIsNone(broker.classify_backup(name))
        names = ["backup-20261101T000000Z.dump", "polypbase.sql", "nightly-x.dump"]
        self.assertEqual(broker.plan_retention(names, now=at("2026-11-27")), [])


class PlanTests(unittest.TestCase):
    def test_fewer_files_than_keep_counts_are_all_kept(self):
        names = daily("2026-11-20", "2026-11-27") + [
            pre("0123abcd", at("2026-11-21T10:00")),
            pre("89abcdef", at("2026-11-25T10:00")),
        ]
        decisions = broker.plan_retention(names, now=at("2026-11-27T03:00"))
        self.assertEqual(kept(decisions), set(names))

    def test_several_months_across_the_year_boundary(self):
        names = daily("2026-09-01", "2027-01-03")
        decisions = broker.plan_retention(names, now=at("2027-01-03T02:40"))
        weekly = {
            nightly(at(f"{day}T02:40"))
            for day in (
                "2026-11-15",
                "2026-11-22",
                "2026-11-29",
                "2026-12-06",
                "2026-12-13",
                "2026-12-20",
            )
        }
        recent = set(daily("2026-12-21", "2027-01-03"))
        self.assertEqual(len(recent), broker.KEEP_NIGHTLY)
        self.assertEqual(kept(decisions), recent | weekly)
        self.assertEqual(deleted(decisions), set(names) - recent - weekly)
        reasons = dict((name, reason) for name, _, reason in decisions)
        self.assertEqual(
            reasons[nightly(at("2027-01-03T02:40"))],
            "nightly: one of the 14 most recent; nightly: newest of ISO week 2026-W53",
        )
        self.assertEqual(
            reasons[nightly(at("2026-11-15T02:40"))],
            "nightly: newest of ISO week 2026-W46",
        )
        self.assertEqual(
            reasons[nightly(at("2026-11-14T02:40"))], "nightly: no keep rule"
        )

    def test_dec_31_and_jan_1_share_iso_week_2026_w53(self):
        older = [nightly(at("2026-12-31T02:30")), nightly(at("2027-01-01T02:30"))]
        names = older + daily("2027-01-04", "2027-01-17")
        decisions = broker.plan_retention(names, now=at("2027-01-17T02:40"))
        reasons = dict((name, reason) for name, _, reason in decisions)
        self.assertEqual(deleted(decisions), {older[0]})
        self.assertEqual(reasons[older[1]], "nightly: newest of ISO week 2026-W53")

    def test_duplicates_within_one_week_keep_only_the_newest(self):
        week = [
            nightly(at("2026-10-05T02:30")),
            nightly(at("2026-10-07T02:30")),
            nightly(at("2026-10-07T14:00")),
        ]
        names = week + daily("2026-10-12", "2026-10-25")
        decisions = broker.plan_retention(names, now=at("2026-10-25T03:00"))
        self.assertEqual(deleted(decisions), set(week[:2]))

    def test_weekly_window_starts_on_the_monday_seven_weeks_back(self):
        first_day = nightly(at("2026-10-12T00:00:00"))
        day_before = nightly(at("2026-10-11T23:59:59"))
        names = [first_day, day_before] + daily("2026-11-17", "2026-11-30")
        decisions = broker.plan_retention(names, now=at("2026-11-30T03:00"))
        self.assertIn(first_day, kept(decisions))
        self.assertEqual(deleted(decisions), {day_before})

    def test_pre_deploy_keeps_the_ten_most_recent(self):
        names = [
            pre(f"{index:08x}", at("2026-10-01T09:00") + datetime.timedelta(days=index))
            for index in range(15)
        ]
        decisions = broker.plan_retention(names, now=at("2026-11-27"))
        self.assertEqual(kept(decisions), set(names[5:]))
        self.assertTrue(all(reason for _, _, reason in decisions))

    def test_never_below_a_keep_count(self):
        start = at("2026-01-01T02:30")
        for count in range(0, 40, 3):
            names = [nightly(start + datetime.timedelta(days=i)) for i in range(count)]
            names += [
                pre("0123abcd", start + datetime.timedelta(days=i))
                for i in range(count)
            ]
            decisions = broker.plan_retention(names, now=at("2027-06-01"))
            with self.subTest(count=count):
                kept_names = kept(decisions)
                self.assertGreaterEqual(
                    sum(name.startswith("nightly-") for name in kept_names),
                    min(broker.KEEP_NIGHTLY, count),
                )
                self.assertGreaterEqual(
                    sum(name.startswith("pre-") for name in kept_names),
                    min(broker.KEEP_PRE_DEPLOY, count),
                )

    def test_just_written_is_kept_and_limits_the_category(self):
        names = daily("2026-09-01", "2026-11-27") + [
            pre(f"{index:08x}", at("2026-09-01T09:00") + datetime.timedelta(days=index))
            for index in range(20)
        ]
        written = nightly(at("2026-11-27T02:40"))
        decisions = broker.plan_retention(
            names,
            now=at("2026-11-27T02:40"),
            categories=("nightly",),
            just_written=written,
        )
        self.assertIn((written, True), {(name, keep) for name, keep, _ in decisions})
        self.assertTrue(
            dict((n, r) for n, _, r in decisions)[written].startswith("just written")
        )
        self.assertFalse(any(name.startswith("pre-") for name, _, _ in decisions))

    def test_just_written_must_be_the_newest_of_its_category(self):
        names = daily("2026-11-01", "2026-11-27")
        for written in (
            nightly(at("2026-11-20T02:40")),
            nightly(at("2026-11-28T02:40")),
            "backup-20261127T024000Z.dump",
        ):
            with self.subTest(written=written), self.assertRaises(ValueError):
                broker.plan_retention(
                    names,
                    now=at("2026-11-27"),
                    categories=("nightly",),
                    just_written=written,
                )


class ApplyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.owner = self.directory.lstat().st_uid
        self.nightlies = daily("2026-09-01", "2026-11-27")
        self.pres = [
            pre(f"{index:08x}", at("2026-09-01T09:00") + datetime.timedelta(days=index))
            for index in range(15)
        ]
        self.unmanaged = [
            "backup-20260801T000000Z.dump",
            "pre-deploy-20260801T000000Z.dump",
            "nightly-20260801T000000Z.dump.tmp",
        ]
        for name in self.nightlies + self.pres + self.unmanaged:
            (self.directory / name).write_bytes(b"fixture")
        self.lines = []

    def run_retention(self, **options):
        options.setdefault("now", at("2026-11-27T02:40"))
        return broker.apply_retention(
            self.directory, self.owner, log=self.lines.append, **options
        )

    def present(self):
        return set(os.listdir(self.directory))

    def test_dry_run_deletes_nothing_and_logs_the_plan(self):
        before = self.present()
        self.assertEqual(self.run_retention(dry_run=True), [])
        self.assertEqual(self.present(), before)
        managed = len(self.nightlies) + len(self.pres)
        decisions = [line for line in self.lines if line.startswith("RETENTION keep")]
        planned = [
            line for line in self.lines if line.startswith("RETENTION would-delete")
        ]
        self.assertEqual(len(decisions) + len(planned), managed)
        # 14 recent + 5 older ISO weeks (W41 to W45) + 10 pre-deploy.
        self.assertEqual(len(decisions), 14 + 5 + 10)
        self.assertIn(
            f"RETENTION ignore {len(self.unmanaged)} entries with unmanaged names",
            self.lines,
        )
        self.assertEqual(
            self.lines[-1], f"RETENTION_DRY_RUN kept=29 would_delete={managed - 29}"
        )

    def test_real_run_deletes_only_planned_files_of_the_written_category(self):
        written = self.nightlies[-1]
        removed = self.run_retention(just_written=written)
        self.assertEqual(len(removed), len(self.nightlies) - 19)
        self.assertEqual(
            self.present(),
            set(self.nightlies) - set(removed) | set(self.pres) | set(self.unmanaged),
        )
        self.assertIn(written, self.present())
        for name in removed:
            self.assertTrue(
                any(
                    line.startswith(f"RETENTION delete {name} (") for line in self.lines
                )
            )
        self.assertEqual(self.lines[-1], f"RETENTION_OK kept=19 deleted={len(removed)}")

    def test_directories_links_and_other_owners_are_never_touched(self):
        trap = self.directory / "nightly-20200101T000000Z.dump"
        trap.mkdir()
        (trap / "inside").write_bytes(b"fixture")
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        target = Path(outside.name) / "target.dump"
        target.write_bytes(b"fixture")
        link = self.directory / "nightly-20200102T000000Z.dump"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            link = None
        self.run_retention(just_written=self.nightlies[-1])
        self.assertTrue((trap / "inside").is_file())
        if link is not None:
            self.assertTrue(link.is_symlink())
        self.assertTrue(target.is_file())
        self.assertIn(
            "RETENTION ignore nightly-20200101T000000Z.dump "
            "(not a regular file of the backup account)",
            self.lines,
        )

        before = self.present()
        self.lines.clear()
        broker.apply_retention(
            self.directory, self.owner + 1, now=at("2026-11-27"), log=self.lines.append
        )
        self.assertEqual(self.present(), before)

    def test_entry_changed_since_the_scan_is_skipped(self):
        before = self.present()
        real_scan = broker.scan_backups

        def scan(*args):
            found = real_scan(*args)
            return {name: (0, 0) for name in found}

        with patch.object(broker, "scan_backups", side_effect=scan):
            self.assertEqual(self.run_retention(just_written=self.nightlies[-1]), [])
        self.assertEqual(self.present(), before)

    def test_failed_plan_deletes_nothing(self):
        before = self.present()
        with self.assertRaises(ValueError):
            self.run_retention(just_written=self.nightlies[0])
        self.assertEqual(self.present(), before)

    def test_deletion_is_per_file_without_shell_glob_or_recursion(self):
        source = inspect.getsource(broker.apply_retention) + inspect.getsource(
            broker.scan_backups
        )
        for forbidden in (
            "glob",
            "rmtree",
            "subprocess",
            "os.system",
            "shell",
            "rmdir",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("path.unlink()", source)


class MainTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.file_owner = self.directory.lstat().st_uid
        self.identity = types.SimpleNamespace(pw_uid=1234, pw_gid=1234)
        self.names = daily("2026-09-01", "2026-11-26")
        for name in self.names:
            (self.directory / name).write_bytes(b"fixture")

    def patches(self, stack, args):
        uid = self.identity.pw_uid
        for target, name, value in (
            (sys, "flags", types.SimpleNamespace(isolated=1)),
            (sys, "argv", ["broker", *args]),
            (broker, "OUTPUT", self.directory),
            (broker, "backup_identity", lambda: self.identity),
            (broker, "require_output", lambda identity: None),
            (broker.os, "chdir", lambda path: None),
            (broker.os, "umask", lambda mask: None),
        ):
            stack.enter_context(patch.object(target, name, value))
        stack.enter_context(
            patch.multiple(
                broker.os,
                getuid=lambda: uid,
                geteuid=lambda: uid,
                getgid=lambda: uid,
                getegid=lambda: uid,
                getgroups=lambda: [uid],
                create=True,
            )
        )
        real_retention = broker.apply_retention

        def retention(directory, owner, **options):
            # The broker passes its identity; fixture files belong to the test user.
            self.assertEqual(owner, uid)
            return real_retention(directory, self.file_owner, **options)

        stack.enter_context(patch.object(broker, "apply_retention", retention))
        stack.enter_context(patch.dict(os.environ, {}, clear=True))
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        stack.enter_context(redirect_stdout(self.stdout))
        stack.enter_context(redirect_stderr(self.stderr))

    def test_failed_backup_deletes_nothing(self):
        with ExitStack() as stack:
            self.patches(stack, [])
            load = stack.enter_context(
                patch.object(broker, "load_control", return_value=({}, {}))
            )
            stack.enter_context(
                patch.object(broker, "run_backup", side_effect=ValueError("failed"))
            )
            retention = stack.enter_context(
                patch.object(broker, "apply_retention", wraps=broker.apply_retention)
            )
            self.assertEqual(broker.main(), 1)
        load.assert_called_once_with()
        retention.assert_not_called()
        self.assertEqual(sorted(os.listdir(self.directory)), sorted(self.names))
        self.assertNotIn("RETENTION", self.stderr.getvalue())

    def test_verified_backup_then_prunes_its_category_to_stderr(self):
        written = nightly(at("2026-11-27T02:40"))

        def backup(*args):
            (self.directory / written).write_bytes(b"fixture")
            print("BACKUP_OK " + str(self.directory / written))
            return self.directory / written

        with ExitStack() as stack:
            self.patches(stack, [])
            stack.enter_context(
                patch.object(broker, "load_control", return_value=({}, {}))
            )
            stack.enter_context(patch.object(broker, "run_backup", side_effect=backup))
            self.assertEqual(broker.main(), 0)
        self.assertEqual(
            self.stdout.getvalue(), f"BACKUP_OK {self.directory / written}\n"
        )
        present = set(os.listdir(self.directory))
        self.assertIn(written, present)
        self.assertEqual(len(present), 19)
        self.assertIn("RETENTION_OK kept=19 deleted=69", self.stderr.getvalue())

    def run_refused_retention(self, args, written, future):
        """Back up `written` while a future-dated name makes retention refuse."""
        (self.directory / future).write_bytes(b"fixture")
        before = set(os.listdir(self.directory)) | {written}

        def backup(*arguments):
            (self.directory / written).write_bytes(b"fixture")
            print("BACKUP_OK " + str(self.directory / written))
            return self.directory / written

        with ExitStack() as stack:
            self.patches(stack, args)
            stack.enter_context(
                patch.object(broker, "load_control", return_value=({}, {}))
            )
            stack.enter_context(patch.object(broker, "run_backup", side_effect=backup))
            code = broker.main()
        self.assertEqual(set(os.listdir(self.directory)), before)
        self.assertEqual(
            self.stdout.getvalue(), f"BACKUP_OK {self.directory / written}\n"
        )
        self.assertTrue(
            self.stderr.getvalue().endswith(
                "RETENTION_ERROR retention refused or failed\n"
            )
        )
        return code

    def test_nightly_retention_error_fails_the_unit_but_keeps_the_backup(self):
        code = self.run_refused_retention(
            [], nightly(at("2026-11-27T02:40")), nightly(at("2030-01-01T00:00"))
        )
        self.assertEqual(code, broker.RETENTION_FAILED_EXIT)
        self.assertNotIn(code, (0, 1))

    def test_pre_deploy_retention_error_never_fails_the_backup(self):
        code = self.run_refused_retention(
            ["abcdef01" * 5],
            pre("abcdef01", at("2026-11-27T10:00")),
            pre("01234567", at("2030-01-01T00:00")),
        )
        self.assertEqual(code, 0)

    def test_retention_error_hides_details_for_both_labels(self):
        for args, written, expected in (
            ([], nightly(at("2026-11-27T02:40")), broker.RETENTION_FAILED_EXIT),
            (["a" * 40], pre("aaaaaaaa", at("2026-11-27T10:00")), 0),
        ):
            with self.subTest(args=args), ExitStack() as stack:
                self.patches(stack, args)
                stack.enter_context(
                    patch.object(broker, "load_control", return_value=({}, {}))
                )
                stack.enter_context(
                    patch.object(
                        broker, "run_backup", return_value=self.directory / written
                    )
                )
                stack.enter_context(
                    patch.object(
                        broker, "apply_retention", side_effect=OSError("secret-fixture")
                    )
                )
                self.assertEqual(broker.main(), expected)
                self.assertEqual(
                    self.stderr.getvalue(),
                    "RETENTION_ERROR retention refused or failed\n",
                )

    def test_dry_run_flag_needs_no_control_and_deletes_nothing(self):
        with ExitStack() as stack:
            self.patches(stack, [broker.DRY_RUN_FLAG])
            load = stack.enter_context(patch.object(broker, "load_control"))
            backup = stack.enter_context(patch.object(broker, "run_backup"))
            self.assertEqual(broker.main(), 0)
        load.assert_not_called()
        backup.assert_not_called()
        self.assertEqual(sorted(os.listdir(self.directory)), sorted(self.names))
        self.assertIn("RETENTION would-delete", self.stdout.getvalue())
        self.assertIn("RETENTION_DRY_RUN kept=", self.stdout.getvalue())

    def test_dry_run_variants_are_refused(self):
        for args in (
            [broker.DRY_RUN_FLAG, "a" * 40],
            [broker.DRY_RUN_FLAG + "=1"],
            ["--retention-dry-run "],
        ):
            with ExitStack() as stack:
                self.patches(stack, args)
                retention = stack.enter_context(patch.object(broker, "apply_retention"))
                with self.subTest(args=args):
                    self.assertEqual(broker.main(), 1)
                retention.assert_not_called()
        self.assertEqual(sorted(os.listdir(self.directory)), sorted(self.names))


if __name__ == "__main__":
    unittest.main()
