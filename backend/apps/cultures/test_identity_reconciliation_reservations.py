"""Ordinary-save reservations for the two exact approved CLA aliases."""

from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase
from django.utils.translation import override

from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from . import box_codes
from .models import Box, BoxCodeNamespace


class IdentityReconciliationReservationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Reservation QA", slug="reservation-qa")
        cls.other_org = Organization.objects.create(name="Foreign QA", slug="foreign-reservation-qa")
        species = Species.objects.create(scientific_name="Reservation species")
        cls.source = Strain.objects.create(organization=cls.org, species=species, code="CLA-JKA-1")
        cls.target = Strain.objects.create(organization=cls.org, species=species, code="CLC-JKA-1")

    def create_box(self, code, *, organization=None):
        return Box.objects.create(
            organization=organization or self.org,
            strain=self.target if code.startswith("CLC-") else self.source,
            global_code=code, box_number=code.rsplit(".", 1)[1],
        )

    def snapshot(self):
        return (
            list(Box.objects.order_by("pk").values()),
            list(BoxCodeNamespace.objects.order_by("pk").values()),
        )

    def test_recreation_after_queryset_correction_is_rejected_without_previous_code(self):
        for suffix in ("009", "010"):
            with self.subTest(suffix=suffix):
                old_code, target_code = f"CLA-JKA-1.{suffix}", f"CLC-JKA-1.{suffix}"
                box = self.create_box(old_code)
                # The reviewed corrector preserves the PK and bypasses save();
                # previous_global_code belongs to merge semantics, not this rename.
                Box.objects.filter(pk=box.pk).update(global_code=target_code, strain=self.target)
                box.refresh_from_db()
                self.assertEqual(box.previous_global_code, "")
                before = self.snapshot()
                with self.assertRaises(ValidationError):
                    self.create_box(old_code)
                self.assertEqual(self.snapshot(), before)

    def test_stale_instance_cannot_restore_the_old_code_after_correction(self):
        for suffix in ("009", "010"):
            with self.subTest(suffix=suffix):
                stale = self.create_box(f"CLA-JKA-1.{suffix}")
                Box.objects.filter(pk=stale.pk).update(
                    global_code=f"CLC-JKA-1.{suffix}", strain=self.target,
                )
                before = self.snapshot()
                stale.notes = "A stale save must not undo the correction"
                with self.assertRaises(ValidationError):
                    stale.save()
                self.assertEqual(self.snapshot(), before)

    def test_same_and_foreign_counterparts_block_creation_in_both_directions(self):
        for suffix in ("009", "010"):
            for existing, proposed in (("CLA", "CLC"), ("CLC", "CLA")):
                for organization in (self.org, self.other_org):
                    with self.subTest(suffix=suffix, existing=existing, organization=organization.pk):
                        counterpart = self.create_box(f"{existing}-JKA-1.{suffix}", organization=organization)
                        before = self.snapshot()
                        with override("en"), self.assertRaises(ValidationError) as error:
                            self.create_box(f"{proposed}-JKA-1.{suffix}")
                        self.assertEqual(error.exception.messages, [
                            "This code is reserved by a historical culture identity.",
                        ])
                        self.assertEqual(self.snapshot(), before)
                        counterpart.delete()

    def test_rename_is_rejected_in_both_directions_with_full_or_explicit_save(self):
        for suffix in ("009", "010"):
            for existing, proposed in (("CLA", "CLC"), ("CLC", "CLA")):
                counterpart = self.create_box(f"{existing}-JKA-1.{suffix}")
                candidate = self.create_box(f"{proposed}-JKA-1.1{suffix}")
                before = self.snapshot()
                candidate.global_code = f"{proposed}-JKA-1.{suffix}"
                for kwargs in ({}, {"update_fields": ["global_code"]}):
                    with self.subTest(suffix=suffix, proposed=proposed, kwargs=kwargs):
                        with self.assertRaises(ValidationError):
                            candidate.save(**kwargs)
                        self.assertEqual(self.snapshot(), before)
                candidate.delete()
                counterpart.delete()

    def test_ordinary_rename_of_the_counterpart_itself_is_rejected(self):
        for suffix in ("009", "010"):
            for existing, proposed in (("CLA", "CLC"), ("CLC", "CLA")):
                box = self.create_box(f"{existing}-JKA-1.{suffix}")
                before = self.snapshot()
                box.global_code = f"{proposed}-JKA-1.{suffix}"
                for kwargs in ({}, {"update_fields": ["global_code"]}):
                    with self.subTest(suffix=suffix, proposed=proposed, kwargs=kwargs):
                        with self.assertRaises(ValidationError):
                            box.save(**kwargs)
                        self.assertEqual(self.snapshot(), before)
                box.delete()

    def test_existing_collisions_remain_editable_without_running_the_guard(self):
        for suffix in ("009", "010"):
            old = self.create_box(f"CLA-JKA-1.{suffix}")
            target = self.create_box(f"CLC-JKA-1.1{suffix}")
            # Deliberately reconstruct a collision predating ordinary-save guards.
            Box._base_manager.filter(pk=target.pk).update(global_code=f"CLC-JKA-1.{suffix}")
            target.refresh_from_db()
            for box, counterpart in ((old, target), (target, old)):
                with self.subTest(suffix=suffix, code=box.global_code):
                    counterpart_before = Box.objects.filter(pk=counterpart.pk).values().get()
                    with patch.object(box_codes, "reserve_reconciled_box_identity") as guard:
                        box.notes = "Historical collision remains inspectable"
                        box.save()
                        box.notes = "Explicit unchanged code is also allowed"
                        box.save(update_fields=["notes", "global_code"])
                        guard.assert_not_called()
                    box.refresh_from_db()
                    self.assertEqual(box.notes, "Explicit unchanged code is also allowed")
                    self.assertEqual(Box.objects.filter(pk=counterpart.pk).values().get(), counterpart_before)
                    self.assertEqual(box.previous_global_code, "")

    def test_unpersisted_code_change_does_not_run_the_guard(self):
        self.create_box("CLC-JKA-1.009")
        old = self.create_box("CLA-JKA-1.099")
        old.global_code = "CLA-JKA-1.009"
        old.notes = "Only notes are persisted"
        with patch.object(box_codes, "reserve_reconciled_box_identity") as guard:
            old.save(update_fields=["notes"])
            guard.assert_not_called()
        old.refresh_from_db()
        self.assertEqual(old.global_code, "CLA-JKA-1.099")
        self.assertEqual(old.notes, "Only notes are persisted")

    def test_guard_is_exact_and_does_not_reserve_other_identities(self):
        for existing, proposed in (("CLA", "CLC"), ("CLC", "CLA")):
            for suffix in ("008", "009", "010", "011"):
                self.create_box(f"{existing}-JKA-1.{suffix}")
            for code in (
                f"{proposed}-JKA-1.008", f"{proposed}-JKA-1.011", f"{proposed}-JKA-1.09",
                f"{proposed}-JKA-1.009-extra", f"{proposed}-JKA-1.010-extra",
                f"{proposed}-JKA-2.009", f"{proposed}-OTHER-1.009", "SME-JKA-1.009",
            ):
                with self.subTest(code=code):
                    self.assertEqual(self.create_box(code).global_code, code)
                    with patch.object(box_codes, "locked_namespace") as lock:
                        box_codes.reserve_reconciled_box_identity(code)
                        lock.assert_not_called()
            Box.objects.all().delete()

    def test_each_exact_side_locks_cla_then_clc_before_ordinary_registration(self):
        for code in ("CLA-JKA-1.009", "CLA-JKA-1.010", "CLC-JKA-1.009", "CLC-JKA-1.010"):
            with self.subTest(code=code):
                events = []
                lock = box_codes._lock_namespace_row
                register = box_codes.register_box_code

                def traced_lock(namespace):
                    events.append(("lock", namespace))
                    return lock(namespace)

                def traced_register(value, **kwargs):
                    events.append(("register", value))
                    return register(value, **kwargs)

                with patch.object(box_codes, "_lock_namespace_row", side_effect=traced_lock), patch.object(
                    box_codes, "register_box_code", side_effect=traced_register,
                ):
                    box = self.create_box(code)
                self.assertEqual(events[:3], [
                    ("lock", "CLA-JKA-1"), ("lock", "CLC-JKA-1"), ("register", code),
                ])
                box.delete()

    def test_existing_box_rename_to_target_uses_the_same_lock_order(self):
        box = self.create_box("CLA-JKA-1.099")
        with patch.object(box_codes, "_lock_namespace_row", wraps=box_codes._lock_namespace_row) as lock:
            box.global_code = "CLC-JKA-1.009"
            box.strain = self.target
            box.save(update_fields=["global_code", "strain"])
        self.assertEqual([call.args[0] for call in lock.call_args_list], [
            "CLA-JKA-1", "CLC-JKA-1", "CLA-JKA-1", "CLC-JKA-1",
        ])

    def test_allocation_locks_pair_before_save_and_only_reconciles_requested_counter(self):
        for namespace in ("CLA-JKA-1", "CLC-JKA-1"):
            with self.subTest(namespace=namespace):
                BoxCodeNamespace.objects.update_or_create(namespace="CLA-JKA-1", defaults={"high_water": 8})
                BoxCodeNamespace.objects.update_or_create(namespace="CLC-JKA-1", defaults={"high_water": 8})
                with transaction.atomic(), patch.object(
                    box_codes, "_lock_namespace_row", wraps=box_codes._lock_namespace_row,
                ) as lock:
                    codes = box_codes.allocate_box_codes(namespace, 1)
                    self.assertEqual([call.args[0] for call in lock.call_args_list], [
                        "CLA-JKA-1", "CLC-JKA-1",
                    ])
                    box = self.create_box(codes[0][0])
                    self.assertEqual([call.args[0] for call in lock.call_args_list], [
                        "CLA-JKA-1", "CLC-JKA-1",
                        "CLA-JKA-1", "CLC-JKA-1",
                        "CLA-JKA-1", "CLC-JKA-1",
                    ])
                other = "CLC-JKA-1" if namespace == "CLA-JKA-1" else "CLA-JKA-1"
                self.assertEqual(BoxCodeNamespace.objects.get(namespace=other).high_water, 8)
                self.assertEqual(BoxCodeNamespace.objects.get(namespace=namespace).high_water, 9)
                box.delete()

    def test_unrelated_namespace_allocation_and_save_only_lock_that_namespace(self):
        namespace = "CLA-JKA-2"
        with transaction.atomic(), patch.object(
            box_codes, "_lock_namespace_row", wraps=box_codes._lock_namespace_row,
        ) as lock:
            code, _ = box_codes.allocate_box_codes(namespace, 1)[0]
            self.create_box(code)
        self.assertEqual([call.args[0] for call in lock.call_args_list], [namespace, namespace])
        self.assertFalse(BoxCodeNamespace.objects.filter(namespace__in=["CLA-JKA-1", "CLC-JKA-1"]).exists())

    def test_stale_identity_writes_fail_for_all_exact_reviewed_codes(self):
        codes = (
            [f"CLA-JKA-1.{number:03}" for number in range(1, 11)]
            + [f"SME-JKA-1.{number:03}" for number in (1, 4, 5, 6, 7, 8, 9, 10)]
            + ["CLC-JKA-1.009", "CLC-JKA-1.010"]
        )
        for code in codes:
            with self.subTest(code=code):
                box = self.create_box(code)
                stale = Box.objects.get(pk=box.pk)
                corrected_strain = self.source if code.startswith("CLC-") else self.target
                Box.objects.filter(pk=box.pk).update(strain=corrected_strain)
                before = self.snapshot()
                stale.notes = "Must not restore historical attribution"
                for kwargs in ({}, {"update_fields": ["notes", "strain"]}, {"update_fields": ["strain_id"]}):
                    with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                        stale.save(**kwargs)
                    self.assertEqual(self.snapshot(), before)
                stale.save(update_fields=["notes"])
                box.refresh_from_db()
                self.assertEqual(box.strain_id, corrected_strain.pk)
                self.assertEqual(box.notes, stale.notes)
                # A successful notes-only save must not bless the stale identity.
                with self.assertRaises(ValidationError):
                    stale.save(update_fields=["strain"])
                stale.refresh_from_db()
                stale.notes = "Reloaded identity is writable"
                stale.save()
                self.assertEqual(stale._loaded_identity, (code, corrected_strain.pk))
                box.delete()

    def test_stale_code_and_strain_correction_allows_notes_only(self):
        stale = self.create_box("CLA-JKA-1.009")
        stale = Box.objects.get(pk=stale.pk)
        Box.objects.filter(pk=stale.pk).update(global_code="CLC-JKA-1.009", strain=self.target)
        for kwargs in ({}, {"update_fields": ["notes", "strain"]}, {"update_fields": ["global_code"]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                stale.save(**kwargs)
        stale.notes = "Preserve corrected attribution"
        stale.save(update_fields=["notes"])
        persisted = Box.objects.get(pk=stale.pk)
        self.assertEqual((persisted.global_code, persisted.strain_id), ("CLC-JKA-1.009", self.target.pk))
        self.assertEqual(persisted.notes, stale.notes)
        self.assertEqual(persisted.previous_global_code, "")

    def test_deferred_load_does_not_recurse_or_trust_missing_identity(self):
        box = self.create_box("SME-JKA-1.001")
        with self.assertNumQueries(1):
            deferred = Box.objects.only("pk", "notes").get(pk=box.pk)
        self.assertIsNone(deferred._loaded_identity)
        Box.objects.filter(pk=box.pk).update(strain=self.target)
        deferred.strain = self.source
        with self.assertRaises(ValidationError):
            deferred.save(update_fields=["strain"])
        deferred.notes = "Deferred notes remain writable"
        deferred.save(update_fields=["notes"])
        self.assertIsNone(deferred._loaded_identity)
        # Django's default refresh keeps still-deferred fields deferred.
        deferred.refresh_from_db(fields=["global_code", "strain_id"])
        self.assertEqual(deferred._loaded_identity, (box.global_code, self.target.pk))
        deferred.save()

    def test_deferred_identity_reads_do_not_refresh_a_stale_snapshot(self):
        box = self.create_box("CLA-JKA-1.001")
        loaded = Box.objects.only("pk", "global_code", "strain_id").get(pk=box.pk)
        Box.objects.filter(pk=box.pk).update(strain=self.target)
        # Loading an unrelated deferred field must not reset optimistic tracking.
        self.assertEqual(loaded.notes, "")
        with self.assertRaises(ValidationError):
            loaded.save(update_fields=["strain"])

    def test_constructed_existing_pk_cannot_modify_reviewed_identity(self):
        box = self.create_box("SME-JKA-1.001")
        Box.objects.filter(pk=box.pk).update(strain=self.target)
        constructed = Box(pk=box.pk, global_code=box.global_code, strain=self.source, organization=self.org)
        for kwargs in ({}, {"update_fields": ["strain"]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                constructed.save(**kwargs)
        constructed.notes = "Constructed notes-only write"
        constructed.save(update_fields=["notes"])
        box.refresh_from_db()
        self.assertEqual(box.strain_id, self.target.pk)
        self.assertEqual(box.notes, constructed.notes)

    def test_constructed_pk_code_only_save_does_not_bless_stale_strain(self):
        for code in ("SME-JKA-1.001", "CLA-JKA-1.001", "CLC-JKA-1.009"):
            with self.subTest(code=code):
                box = self.create_box(code)
                stale_strain_id = box.strain_id
                corrected_strain = self.source if code.startswith("CLC-") else self.target
                Box.objects.filter(pk=box.pk).update(strain=corrected_strain)
                constructed = Box(
                    pk=box.pk, global_code=code, strain_id=stale_strain_id, organization=self.org,
                )
                before = self.snapshot()
                constructed.save(update_fields=["global_code"])
                self.assertEqual(constructed.strain_id, stale_strain_id)
                self.assertIsNone(constructed._loaded_identity)
                self.assertEqual(self.snapshot(), before)
                with self.assertRaises(ValidationError):
                    constructed.save(update_fields=["strain"])
                self.assertEqual(self.snapshot(), before)
                constructed.refresh_from_db()
                self.assertEqual(constructed._loaded_identity, (code, corrected_strain.pk))
                constructed.save(update_fields=["strain"])

    def test_deferred_code_only_save_does_not_bless_stale_strain(self):
        for code in ("SME-JKA-1.001", "CLA-JKA-1.001", "CLC-JKA-1.009"):
            with self.subTest(code=code):
                box = self.create_box(code)
                deferred = Box.objects.only("pk", "strain_id").get(pk=box.pk)
                self.assertIsNone(deferred._loaded_identity)
                corrected_strain = self.source if code.startswith("CLC-") else self.target
                Box.objects.filter(pk=box.pk).update(strain=corrected_strain)
                before = self.snapshot()
                deferred.save(update_fields=["global_code"])
                self.assertEqual(deferred.strain_id, box.strain_id)
                self.assertIsNone(deferred._loaded_identity)
                self.assertEqual(self.snapshot(), before)
                with self.assertRaises(ValidationError):
                    deferred.save(update_fields=["strain"])
                self.assertEqual(self.snapshot(), before)
                deferred.refresh_from_db(fields=["global_code", "strain_id"])
                self.assertEqual(deferred._loaded_identity, (code, corrected_strain.pk))
                deferred.save(update_fields=["strain"])

    def test_successful_save_captures_identity_for_later_conflicts(self):
        box = self.create_box("SME-JKA-1.001")
        self.assertEqual(box._loaded_identity, (box.global_code, self.source.pk))
        box.strain = self.target
        box.save(update_fields=["strain"])
        self.assertEqual(box._loaded_identity, (box.global_code, self.target.pk))
        Box.objects.filter(pk=box.pk).update(strain=self.source)
        with self.assertRaises(ValidationError):
            box.save()

    def test_unrelated_code_retains_existing_save_behavior(self):
        for code in ("CLA-JKA-1.011", "SME-JKA-1.002", "CLC-JKA-1.008", "CLA-JKA-2.001"):
            with self.subTest(code=code):
                stale = self.create_box(code)
                Box.objects.filter(pk=stale.pk).update(strain=self.target)
                stale.notes = "Outside the exact reviewed scope"
                stale.save()
                stale.refresh_from_db()
                self.assertEqual(stale.strain_id, self.source.pk if not code.startswith("CLC-") else self.target.pk)

    def test_each_side_creation_without_counterpart_is_allowed(self):
        for suffix in ("009", "010"):
            for prefix in ("CLA", "CLC"):
                with self.subTest(suffix=suffix, prefix=prefix):
                    box = self.create_box(f"{prefix}-JKA-1.{suffix}")
                    self.assertEqual(box.previous_global_code, "")
                    box.delete()
