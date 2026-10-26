"""Synthetic additive-baseline proofs on the leased disposable PG18 harness.

No production evidence is loaded. Only non-test workflow helpers are reused;
importing this module must not discover the original production test classes.
"""

import json
from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.db import DatabaseError, connection
from django.test import TransactionTestCase

from apps.audit.models import AuditLog
from apps.measurements import services as measurement_services
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from config import cca_eob_database_role as role_contract

from . import test_cca_eob_production as workflow
from .historical_2026 import cca_eob_correction as correction
from .historical_2026 import cca_eob_production as production
from .models import Box
from .test_cca_eob_correction import CcaEobFixtureMixin, json_snapshot


# Copy descriptors, not TestCase ancestry (and not setUp's zero-argument super).
WorkflowHelpers = type("WorkflowHelpers", (), {
    name: value for name, value in vars(workflow.ProductionWorkflowTests).items()
    if not name.startswith("test") and name not in {
        "setUp", "__dict__", "__weakref__", "__module__", "__doc__",
        "_classSetupFailed", "_class_cleanups", "__unittest_skip__", "__unittest_skip_why__",
    } and (callable(value) or isinstance(value, (classmethod, staticmethod)))
})

ADDITIONS = (
    (104688, 5, "2026-10-21T09:37:07.809036Z"),
    (104689, 6, "2026-10-21T09:44:03.511877Z"),
)


@skipUnless(workflow._ENABLED, "Requires the leased disposable PG18 harness")
class AdditiveBaselineTests(WorkflowHelpers, CcaEobFixtureMixin, TransactionTestCase):
    reset_sequences = True
    call_writer = workflow.WriterCapabilityTests.call_writer
    clear_approval = workflow.WriterCapabilityTests.clear_approval
    assert_writer_denied = workflow.WriterCapabilityTests.assert_writer_denied
    pending_writer = workflow.WriterCapabilityTests.pending_writer

    def setUp(self):
        from config import historical_temperature_pgqa as guard

        guard.require_owner(connection)
        guard.require_run_lease()
        self.install_boundary()
        create_box, create_org = Box.objects.create, Organization.objects.create

        def exact_box(**values):
            ids = {code: pk for pk, code in production.BOXES.items()}
            if values.get("global_code") in ids:
                values["pk"] = ids[values["global_code"]]
            return create_box(**values)

        def exact_org(**values):
            values["pk"] = {"cca-qa": 1, "other-cca-qa": 2}[values["slug"]]
            return create_org(**values)

        with patch.object(Box.objects, "create", side_effect=exact_box), \
                patch.object(Organization.objects, "create", side_effect=exact_org):
            CcaEobFixtureMixin.setUp(self)
        self.old_audit = AuditLog.objects.create(
            organization=self.org, user=self.writer, action="entry", object_type="box",
            object_id=self.boxes[5].global_code, metadata={"synthetic_legacy": True},
        )

    def prepare_additive(self):
        BiologicalMeasurement.objects.all().delete()
        self.source["entries"].clear()
        self.manifest["measurements"].clear()
        self.measurements = []
        for week in range(1, 41):
            for old, target in (((5, 4), (6, 5)) if week <= 36 else ((4, 4), (5, 5))):
                polyps = 0 if week == 1 and target == 4 else week * 10 + target
                row = BiologicalMeasurement.objects.create(
                    box=self.boxes[old], measured_on=date.fromisocalendar(2026, week, 3),
                    polyp_count=polyps, ephyrae_count=0, strobila_count=0,
                    salinity_psu="0.00", user=self.writer, original_observer=self.writer,
                    observer_status="known", notes="Preserve synthetic provenance",
                )
                self.measurements.append(row)
                self.source["entries"].append({
                    "sheet": "Semaestomeae", "source_identifier": f"CCA-EOB-2.{target:02}",
                    "target_code": self.boxes[target].global_code, "iso_year": 2026, "iso_week": week,
                    "polyp_cell": f"QA{week}_{target}", "ephyrae_cell": f"QB{week}_{target}",
                    "polyps": polyps, "ephyrae": 0,
                })
                self.manifest["measurements"].append({
                    "box": self.boxes[old].global_code, "year": 2026, "iso_week": week,
                    "polyps": polyps, "ephyrae": 0,
                })
        outside = []
        for week in range(41, 45):
            outside.append(BiologicalMeasurement.objects.create(
                box=self.boxes[4], measured_on=date.fromisocalendar(2026, week, 3),
                polyp_count=0, ephyrae_count=0,
            ))
        # Revisions/sequences are synthetic, fixed before authenticating history.
        BiologicalMeasurement.objects.all().update(polyp_state_sequence=24)
        Box.objects.filter(pk__in=production.BOXES).update(polyp_state_revision=24)
        original = correction._build_plan(1, workbook_path=self.workbook_path)
        self.assertEqual(original["counts"], {
            correction.MOVE: 72, correction.MATCH: 8, correction.OUTSIDE: 4,
        })
        self.historical_witness = workflow.synthetic_historical_witness(original, actor_id=self.admin.pk)
        self.enterContext(patch.object(
            role_contract, "HISTORICAL_PLAN_SHA256", self.historical_witness["original_plan_hash"], create=True,
        ))
        self.enterContext(patch.object(production, "_root_document", return_value=self.historical_witness))
        self.add_historical_link(outside[1])
        self.additions = []
        for pk, number, timestamp in ADDITIONS:
            row = BiologicalMeasurement.objects.create(
                pk=pk, box=self.boxes[number], measured_on=date(2026, 10, 21),
                user=self.writer, original_observer=self.writer, observer_status="known",
                origin="direct_entry", temporal_precision="exact_date", polyp_count=0,
                ephyrae_count=0, strobila_count=None, salinity_psu="0.00", notes="Synthetic addition",
            )
            BiologicalMeasurement.objects.filter(pk=pk).update(
                created_at=datetime.fromisoformat(timestamp.replace("Z", "+00:00")),
            )
            row.refresh_from_db()
            self.assertEqual((row.week_start, row.polyp_state_sequence), (date(2026, 10, 19), 25))
            audit = measurement_services._record_measurement_audit(
                box=self.boxes[number], measurement=row, user=self.writer, action="entry",
                metadata={"measurement_id": pk, "valeurs": measurement_services._measurement_audit_values(row)},
            )
            self.additions.append(SimpleNamespace(row=row, audit=audit))
        # Re-emit the real capability with the test-only historical pin.
        self.install_boundary()
        return self.forged_document()

    def forged_document(self):
        """Owner-reviewed envelope without invoking the Python thermal gate.

        Used deliberately for invalid cases: rebuild science, recipe AND raw
        snapshot, then seed both admin decisions so SQL must defend itself.
        """
        with self.production_runtime():
            payload = production._read_scope(self.admin)
            data = production._planner_data(payload)
            plan = correction._build_plan(1, workbook_path=self.workbook_path, data=data)
            guards = production._schema_state()
        return correction._json({
            "schema": production.SCHEMA, "target": self.target, "actor_id": self.admin.pk,
            "plan": {
                "correction": plan, "deployed_guards": guards, "database_snapshot": payload,
                "write_recipe": correction.write_recipe(plan, data["measurements"], self.admin.pk),
                "thermal_exception": self.historical_witness,
                "historical_document_text": json.dumps(
                    self.historical_witness["original_document"], sort_keys=True, default=str,
                ),
            },
        })

    def validate_python(self, document):
        plan = document["plan"]
        return role_contract.validate_thermal_exception(
            plan["thermal_exception"], plan["correction"], snapshot=plan["database_snapshot"],
        )

    @workflow.runtime_workflow
    def test_accepts_exact_delta_and_preserves_complete_rows_and_audits(self):
        forged = self.prepare_additive()
        original_witness = deepcopy(self.historical_witness)
        self.validate_python(forged)
        document = self.production_plan()
        self.assertEqual(document["plan"], forged["plan"])
        snapshot = document["plan"]["database_snapshot"]
        self.assertEqual(len(snapshot["addition_audits"]), 2)
        self.assertEqual(document["plan"]["correction"]["counts"], {
            correction.MOVE: 72, correction.MATCH: 8, correction.OUTSIDE: 6,
        })
        before = self.database_snapshot()
        rows = {m.pk: json_snapshot(m) for m in BiologicalMeasurement.objects.all()}
        boxes = {b.pk: json_snapshot(b) for b in Box.objects.all()}
        with self.synthetic_release_io(document):
            self.production_apply(document)
        after = self.database_snapshot()
        targets = {m["id"]: m["target_box_id"] for m in document["plan"]["write_recipe"]["moves"]}
        self.assertEqual(len(targets), 72)
        revisions = {change["box_id"]: change["after"]
                     for change in document["plan"]["correction"]["revision_changes"]}
        for box in Box.objects.all():
            self.assertEqual(json_snapshot(box), {
                **boxes[box.pk], "polyp_state_revision": revisions.get(box.pk, boxes[box.pk]["polyp_state_revision"]),
            })
        for row in BiologicalMeasurement.objects.all():
            self.assertEqual(json_snapshot(row), {**rows[row.pk], "box_id": targets.get(row.pk, rows[row.pk]["box_id"])})
        for addition in self.additions:
            addition.row.refresh_from_db()
            self.assertEqual(json_snapshot(addition.row), rows[addition.row.pk])
        for table in before.keys() - {Box._meta.db_table, BiologicalMeasurement._meta.db_table, AuditLog._meta.db_table}:
            self.assertEqual(before[table], after[table], table)
        for audit in before[AuditLog._meta.db_table]:
            self.assertIn(audit, after[AuditLog._meta.db_table])
        self.assertEqual(len(after[AuditLog._meta.db_table]) - len(before[AuditLog._meta.db_table]), 73)
        self.assertIsNotNone(self.approval_consumed_at())
        self.assertEqual(self.historical_witness, original_witness)

    @workflow.runtime_workflow
    def test_valid_document_can_be_applied_by_direct_sql(self):
        document = self.prepare_additive()
        with self.synthetic_release_io(document):
            self.call_writer(correction.fingerprint(document))
        self.assertIsNotNone(self.approval_consumed_at())
        for move in document["plan"]["write_recipe"]["moves"]:
            self.assertEqual(BiologicalMeasurement.objects.get(pk=move["id"]).box_id, move["target_box_id"])

    @workflow.runtime_workflow
    def test_snapshot_is_required_for_additive_but_not_original_identity(self):
        document = self.prepare_additive()
        with self.assertRaises(ValueError):
            role_contract.validate_thermal_exception(self.historical_witness, document["plan"]["correction"])
        role_contract.validate_thermal_exception(
            self.historical_witness, self.historical_witness["original_document"]["plan"]["correction"],
        )

    @workflow.runtime_workflow
    def test_reader_exposes_all_actions_and_foreign_audits_as_complete_rows(self):
        self.prepare_additive()
        foreign = AuditLog.objects.create(
            organization=self.other_org, user=self.admin, action="correction", object_type="box",
            object_id="foreign", metadata={"measurement_id": ADDITIONS[0][0], "unexpected": 0},
        )
        document = self.forged_document()
        actual = document["plan"]["database_snapshot"]["addition_audits"]
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_jsonb(a) FROM audit_auditlog a WHERE metadata->>'measurement_id' IN ('104688','104689') ORDER BY id")
            expected = [json.loads(row[0]) if isinstance(row[0], str) else row[0] for row in cursor.fetchall()]
        self.assertEqual(sorted(actual, key=lambda row: row["id"]), expected)
        self.assertIn(foreign.pk, [row["id"] for row in actual])

    @workflow.runtime_workflow
    def test_late_receipt_failure_rolls_back_additions_moves_audits_and_approval(self):
        document = self.prepare_additive()
        self.validate_python(document)
        with connection.cursor() as cursor:
            cursor.execute("""CREATE FUNCTION public.cca_qa_additive_failure() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF NEW.object_type='historical_correction' THEN
                    RAISE EXCEPTION 'Synthetic additive receipt failure'; END IF; RETURN NEW; END $$;
                CREATE TRIGGER cca_qa_additive_failure BEFORE INSERT ON public.audit_auditlog
                FOR EACH ROW EXECUTE FUNCTION public.cca_qa_additive_failure()""")
        try:
            with self.synthetic_release_io(document):
                error = self.assert_writer_denied(correction.fingerprint(document))
            self.assertIn("Synthetic additive receipt failure", str(error))
            self.assertIsNotNone(self.final_authorization_state()[0])
        finally:
            with connection.cursor() as cursor:
                cursor.execute("DROP TRIGGER cca_qa_additive_failure ON public.audit_auditlog; DROP FUNCTION public.cca_qa_additive_failure()")

    @workflow.runtime_workflow
    def test_plan_approval_never_substitutes_for_final_authorization(self):
        document = self.prepare_additive()
        self.seed_approval(document)
        error = self.assert_writer_denied(correction.fingerprint(document))
        self.assertIn("final", str(error).lower())

    def assert_document_rejected(self, document):
        with self.subTest(boundary="Python"), self.assertRaises(ValueError):
            self.validate_python(document)
        with self.subTest(boundary="SQL"), self.synthetic_release_io(document):
            error = self.assert_writer_denied(correction.fingerprint(document))
            self.assertNotIn("snapshot changed", str(error).lower())

    def rebuild_recipe(self, document):
        plan = document["plan"]
        data = production._planner_data(plan["database_snapshot"])
        plan["write_recipe"] = correction.write_recipe(plan["correction"], data["measurements"], self.admin.pk)

    def refresh_raw_snapshot(self, document):
        with self.production_runtime():
            document["plan"]["database_snapshot"] = production._read_scope(self.admin)

    def assert_split_evidence_rejected(self, document):
        with self.subTest(boundary="Python"), self.assertRaisesMessage(ValueError, "baseline differs"):
            self.validate_python(document)
        with self.subTest(boundary="SQL"), self.synthetic_release_io(document):
            error = self.assert_writer_denied(correction.fingerprint(document))
            self.assertIn("baseline differs", str(error))

    @workflow.runtime_workflow
    def test_rejects_split_old_row_raw_notes_and_unchanged_candidate(self):
        document = self.prepare_additive()
        self.validate_python(document)
        candidate = deepcopy(document["plan"]["correction"])
        BiologicalMeasurement.objects.filter(pk=self.measurements[0].pk).update(notes="Changed raw historical note")
        self.refresh_raw_snapshot(document)
        self.assertEqual(document["plan"]["correction"], candidate)
        self.assert_split_evidence_rejected(document)

    @workflow.runtime_workflow
    def test_rejects_split_extra_raw_row_and_unchanged_candidate(self):
        document = self.prepare_additive()
        self.validate_python(document)
        candidate = deepcopy(document["plan"]["correction"])
        extra = BiologicalMeasurement.objects.create(
            box=self.boxes[6], measured_on=date(2026, 11, 4), polyp_count=0, ephyrae_count=0,
        )
        # Isolate the extra row from the separately tested revision mismatch.
        Box.objects.filter(pk=self.boxes[6].pk).update(polyp_state_revision=25)
        self.refresh_raw_snapshot(document)
        self.assertEqual(document["plan"]["correction"], candidate)
        self.assertIn(extra.pk, [row["id"] for row in document["plan"]["database_snapshot"]["measurements"]])
        self.assertNotIn(extra.pk, [row["id"] for row in candidate["baseline"]["measurements"]])
        self.assert_split_evidence_rejected(document)

    @workflow.runtime_workflow
    def test_rejects_split_raw_revision_26_with_candidate_baseline_25(self):
        document = self.prepare_additive()
        self.validate_python(document)
        baseline = deepcopy(document["plan"]["correction"]["baseline"])
        Box.objects.filter(pk=self.boxes[5].pk).update(polyp_state_revision=26)
        current = self.forged_document()
        document["plan"]["database_snapshot"] = current["plan"]["database_snapshot"]
        document["plan"]["correction"]["revision_changes"] = current["plan"]["correction"]["revision_changes"]
        self.rebuild_recipe(document)
        self.assertEqual(document["plan"]["correction"]["baseline"], baseline)
        revision = next(r for r in document["plan"]["write_recipe"]["revisions"] if r["box_id"] == self.boxes[5].pk)
        self.assertEqual(revision["before"], 26)
        box = next(b for b in baseline["boxes"] if b["id"] == self.boxes[5].pk)
        self.assertEqual(box["polyp_state_revision"], 25)
        self.assert_split_evidence_rejected(document)

    @workflow.runtime_workflow
    def test_rejects_historical_envelope_tamper_with_original_canonical_text(self):
        document = self.prepare_additive()
        self.validate_python(document)
        document = deepcopy(document)
        witness = document["plan"]["thermal_exception"]
        witness["original_document"]["plan"]["historical_evidence"]["synthetic"] = "Tampered envelope"
        # SQL's text hash still matches its pin, but parsing must bind that text
        # to the complete original_document, not only its scientific correction.
        self.assertEqual(correction.fingerprint(json.loads(document["plan"]["historical_document_text"])),
                         role_contract.HISTORICAL_PLAN_SHA256)
        self.assert_document_rejected(document)
        with self.assertRaisesMessage(ValueError, "authentication"):
            role_contract.validate_thermal_exception(witness, witness["original_document"]["plan"]["correction"])

    @workflow.runtime_workflow
    def test_rejects_rehashed_historical_envelope_replacement(self):
        document = deepcopy(self.prepare_additive())
        self.validate_python(document)
        witness = document["plan"]["thermal_exception"]
        witness["original_document"]["target"] = {"synthetic": True, "replacement": True}
        witness["original_plan_hash"] = correction.fingerprint(witness["original_document"])
        document["plan"]["historical_document_text"] = json.dumps(
            witness["original_document"], sort_keys=True, default=str,
        )
        self.assertNotEqual(witness["original_plan_hash"], role_contract.HISTORICAL_PLAN_SHA256)
        self.assert_document_rejected(document)
        with self.assertRaisesMessage(ValueError, "authentication"):
            role_contract.validate_thermal_exception(witness, witness["original_document"]["plan"]["correction"])

    @workflow.runtime_workflow
    def test_sql_rejects_historical_document_text_with_changed_bytes(self):
        document = self.prepare_additive()
        document["plan"]["historical_document_text"] += " "
        self.assertEqual(json.loads(document["plan"]["historical_document_text"]),
                         document["plan"]["thermal_exception"]["original_document"])
        # The witness itself remains valid; only the independently hashed SQL
        # representation has changed. A semantic JSON comparison is insufficient.
        self.validate_python(document)
        with self.synthetic_release_io(document):
            error = self.assert_writer_denied(correction.fingerprint(document))
        self.assertIn("historical document authentication", str(error).lower())

    @workflow.runtime_workflow
    def test_rejects_changed_move_order_with_rebuilt_recipe(self):
        document = self.prepare_additive()
        order = document["plan"]["correction"]["move_order"]
        order[0], order[1] = order[1], order[0]
        self.rebuild_recipe(document)
        self.assert_document_rejected(document)

    @workflow.runtime_workflow
    def test_rejects_changed_destination_with_rebuilt_recipe(self):
        document = self.prepare_additive()
        plan = document["plan"]["correction"]
        entry = next(e for e in plan["entries"] if e["classification"] == correction.MOVE)
        entry.update(target_box_pk=self.boxes[6].pk, target_box=self.boxes[6].global_code,
                     target_code=self.boxes[6].global_code)
        self.rebuild_recipe(document)
        self.assert_document_rejected(document)

    @workflow.runtime_workflow
    def test_rejects_new_row_baseline_science_not_matching_raw_snapshot(self):
        original = self.prepare_additive()
        self.validate_python(original)
        for pk, _, _ in ADDITIONS:
            for field, value in (("polyp_count", 17), ("salinity_psu", "1.25"), ("notes", "Forged baseline")):
                with self.subTest(pk=pk, field=field):
                    document = deepcopy(original)
                    plan = document["plan"]["correction"]
                    row = next(m for m in plan["baseline"]["measurements"] if m["id"] == pk)
                    entry = next(e for e in plan["entries"] if e["measurement_pk"] == pk)
                    row[field] = value
                    entry["before"] = deepcopy(row)
                    if field == "polyp_count":
                        entry["current_value"][0] = value
                    self.rebuild_recipe(document)
                    self.clear_approval()
                    self.assert_document_rejected(document)

    @workflow.runtime_workflow
    def test_writer_rechecks_audit_edit_committed_while_waiting_for_share_lock(self):
        document = self.prepare_additive()
        self.validate_python(document)
        self.seed_approval(document)
        self.seed_final_authorization(document)
        final_before = self.final_authorization_state()
        expected = self.database_snapshot()
        table = AuditLog._meta.db_table
        with self.owner_session() as editor, self.production_runtime():
            editor.set_autocommit(False)
            try:
                with editor.cursor() as cursor:
                    cursor.execute("SELECT * FROM audit_auditlog WHERE id=%s", [self.additions[0].audit.pk])
                    old_audit = cursor.fetchone()
                    cursor.execute("UPDATE audit_auditlog SET description='Concurrent synthetic edit' WHERE id=%s RETURNING *",
                                   [self.additions[0].audit.pk])
                    new_audit = cursor.fetchone()
                expected[table].remove(old_audit)
                expected[table] = sorted([*expected[table], new_audit], key=repr)
                with self.pending_writer(correction.fingerprint(document)) as (result, wait_blocked):
                    try:
                        wait_blocked()
                        with editor.cursor() as cursor:
                            cursor.execute("""SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=%s
                                AND relation='public.audit_auditlog'::regclass
                                AND mode='ShareLock' AND NOT granted)""", [result["pid"]])
                            self.assertEqual(cursor.fetchone(), (True,))
                    finally:
                        editor.commit()
            finally:
                editor.rollback()
        self.assertNotIn("applied", result)
        self.assertIn("stale", result.get("error", "").lower(), result)
        self.assertEqual(self.database_snapshot(), expected)
        self.assertIsNone(self.approval_consumed_at())
        self.assertEqual(self.final_authorization_state(), final_before)

    @workflow.runtime_workflow
    def test_writer_audit_share_lock_excludes_updates_and_phantom_events(self):
        from config import historical_temperature_pgqa as guard

        document = self.prepare_additive()
        self.validate_python(document)
        self.seed_approval(document)
        self.seed_final_authorization(document)
        before = self.database_snapshot()
        with self.owner_session() as editor, self.production_runtime():
            writer = connection.copy(alias="cca_additive_open_writer")
            try:
                guard.assert_disposable(writer)
                writer.set_autocommit(False)
                with writer.cursor() as cursor:
                    cursor.execute("SELECT public.cca_eob_apply_v1(%s)", [correction.fingerprint(document)])
                with editor.cursor() as cursor:
                    cursor.execute("SET lock_timeout='200ms'")
                statements = (
                    ("UPDATE audit_auditlog SET description='Blocked synthetic edit' WHERE id=%s",
                     [self.additions[0].audit.pk]),
                    ("""INSERT INTO audit_auditlog
                        (organization_id,user_id,action,object_type,object_id,description,metadata,
                         created_at,edited_at,edited_by_id,original_values)
                        SELECT organization_id,user_id,action,object_type,object_id,description,metadata,
                               clock_timestamp(),NULL,NULL,'{}'::jsonb FROM audit_auditlog WHERE id=%s""",
                     [self.additions[1].audit.pk]),
                )
                for statement, params in statements:
                    with self.subTest(statement=statement.split()[0]):
                        with self.assertRaises(DatabaseError) as caught, editor.cursor() as cursor:
                            cursor.execute(statement, params)
                        self.assertEqual(getattr(caught.exception.__cause__, "sqlstate", None), "55P03")
            finally:
                writer.rollback()
                writer.close()
        self.assertEqual(self.database_snapshot(), before)
        self.assertIsNone(self.approval_consumed_at())

    def mutate(self, case):
        row, audit = self.additions[0].row, self.additions[0].audit
        biology = BiologicalMeasurement.objects.filter(pk=row.pk)
        audits = AuditLog.objects.filter(pk=audit.pk)
        if case == "third_addition":
            BiologicalMeasurement.objects.create(box=self.boxes[6], measured_on=date(2026, 11, 4), polyp_count=0, ephyrae_count=0)
        elif case == "missing_addition":
            biology.delete()
        elif case == "missing_old_row":
            BiologicalMeasurement.objects.filter(pk=self.measurements[0].pk).delete()
        elif case == "changed_old_row":
            BiologicalMeasurement.objects.filter(pk=self.measurements[0].pk).update(notes="changed old provenance")
        elif case == "missing_audit":
            audits.delete()
        elif case in {"duplicate_audit", "foreign_audit", "other_action_audit"}:
            AuditLog.objects.create(
                organization=self.other_org if case == "foreign_audit" else self.org,
                user=self.writer, action="correction" if case == "other_action_audit" else "entry",
                object_type="box", object_id=row.box.global_code, metadata=audit.metadata,
            )
        elif case.startswith("audit_"):
            changes = {
                "audit_actor": {"user_id": self.admin.pk}, "audit_org": {"organization_id": self.other_org.pk},
                "audit_box": {"object_id": self.boxes[6].global_code}, "audit_action": {"action": "correction"},
                "audit_type": {"object_type": "measurement"},
                "audit_edited_at": {"edited_at": datetime.now(timezone.utc)},
                "audit_edited_by": {"edited_by_id": self.admin.pk},
                "audit_original_values": {"original_values": {"note": "before"}},
                "audit_values": {"metadata": {**audit.metadata, "valeurs": {**audit.metadata["valeurs"], "polypes": None}}},
                "audit_extra_metadata": {"metadata": {**audit.metadata, "extra": True}},
            }
            audits.update(**changes[case])
        elif case == "revision":
            Box.objects.filter(pk=row.box_id).update(polyp_state_revision=26)
        elif case in {"thermal_addition", "thermal_move"}:
            self.add_historical_link(row if case == "thermal_addition" else self.measurements[0])
        else:
            changes = {
                "sequence": {"polyp_state_sequence": 26},
                "origin": {"origin": "file_import", "import_format": "csv", "imported_by_id": self.writer.pk},
                "precision": {"temporal_precision": "legacy_unverified"}, "author": {"user_id": self.admin.pk},
                "created_at": {"created_at": datetime(2026, 10, 21, 9, 37, 8, tzinfo=timezone.utc)},
                "measured_on": {"measured_on": date(2026, 10, 22)},
                "zero_to_null": {"salinity_psu": None}, "null_to_zero": {"strobila_count": 0},
            }
            biology.update(**changes[case])


def rejection_test(case):
    @workflow.runtime_workflow
    def test(self):
        self.prepare_additive()
        self.mutate(case)
        document = self.forged_document()
        with self.subTest(boundary="Python"), self.assertRaises(ValueError):
            self.validate_python(document)
        # Neither Python approval validation nor an obsolete snapshot can mask
        # missing SQL defenses: the owner deliberately approves this forged state.
        with self.subTest(boundary="SQL"), self.synthetic_release_io(document):
            error = self.assert_writer_denied(correction.fingerprint(document))
            self.assertNotIn("snapshot changed", str(error).lower())
    return test


for _case in (
    "third_addition", "missing_addition", "missing_old_row", "changed_old_row", "missing_audit",
    "duplicate_audit", "foreign_audit", "other_action_audit", "audit_actor", "audit_org", "audit_box",
    "audit_action", "audit_type", "audit_edited_at", "audit_edited_by", "audit_original_values",
    "audit_values", "audit_extra_metadata", "revision", "thermal_addition", "thermal_move",
    "sequence", "origin", "precision", "author", "created_at", "measured_on", "zero_to_null", "null_to_zero",
):
    setattr(AdditiveBaselineTests, f"test_rejects_{_case}_in_python_and_sql", rejection_test(_case))
