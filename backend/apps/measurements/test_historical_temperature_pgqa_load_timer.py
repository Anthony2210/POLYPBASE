"""Connection-free checks of the synthetic load timer, deadline and progress."""

from io import StringIO
from contextlib import redirect_stdout
from time import perf_counter

from django.test import SimpleTestCase

from . import historical_temperature_pgqa_load as load


def _execute(sql, params, many, context):
    return "ok"


class StatementTimerTests(SimpleTestCase):
    def test_categories_and_progress(self):
        timer = load._StatementTimer(None, perf_counter(), 4, progress_every=2)
        out = StringIO()
        with redirect_stdout(out):
            for sql in (
                'INSERT INTO "measurements_historicaltemperaturereading" (a) VALUES (1)',
                'INSERT INTO "measurements_historicaltemperatureevidence" (a) VALUES (1)',
                'INSERT INTO "measurements_historicaltemperaturereading" (a) VALUES (1)',
                'SELECT 1',
            ):
                self.assertEqual(timer(_execute, sql, None, False, None), "ok")
        metrics = timer.metrics()["by_category"]
        self.assertEqual(metrics["reading_insert"]["statements"], 2)
        self.assertEqual(metrics["evidence_insert"]["statements"], 1)
        self.assertEqual(metrics["other"]["statements"], 1)
        self.assertEqual(timer.count, 4)
        self.assertIn("readings inserted 2/4", out.getvalue())

    def test_deadline_raises_before_executing(self):
        calls = []
        timer = load._StatementTimer(perf_counter() - 1)
        with self.assertRaisesMessage(load._LoadError, "max_duration_exceeded"):
            timer(lambda *args: calls.append(args), "SELECT 1", None, False, None)
        self.assertEqual(calls, [])

    def test_no_deadline_means_unbounded(self):
        load._check_deadline(None)
        with self.assertRaises(load._LoadError):
            load._check_deadline(perf_counter() - 1)
