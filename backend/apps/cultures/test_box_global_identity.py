"""Box Global ID foundation: allocation, permanence, scoping and compatibility."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from importlib import import_module
from threading import Event
from unittest import skipUnless

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import (
    DatabaseError,
    IntegrityError,
    close_old_connections,
    connection,
    transaction,
)
from django.db.migrations.exceptions import IrreversibleError
from config.disposable_postgres import DisposablePostgresFlushMixin
from config.migration_test_utils import MigrationExecutor, historical_targets
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import (
    Box,
    BoxGlobalIdentity,
    BoxLineage,
    SubcultureAllocation,
    ThermalZone,
)
from .qr import box_scan_url
from .services import create_subculture

Reason = BoxGlobalIdentity.AllocationReason


@override_settings(SECURE_SSL_REDIRECT=False)
class BoxGlobalIdentityTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Global ID laboratory")
        self.other_org = Organization.objects.create(name="Other Global ID laboratory")
        self.user = get_user_model().objects.create_user(
            username="global-id-tech", email="global@example.org"
        )
        OrganizationMembership.objects.create(
            organization=self.org, user=self.user, role="lab_technician"
        )
        self.species = Species.objects.create(
            scientific_name="Global identity species", genus_species_code="GID"
        )
        self.strain = Strain.objects.create(
            organization=self.org, species=self.species, code="GID-LAB-1"
        )
        self.other_strain = Strain.objects.create(
            organization=self.other_org, species=self.species, code="GID-OTH-1"
        )
        self.zone = ThermalZone.objects.create(
            organization=self.org, name="Global ID zone"
        )
        self.client.force_login(self.user)

    def headers(self, organization=None):
        return {"HTTP_X_ORGANIZATION_ID": str((organization or self.org).pk)}

    def create_automatic(self, request_id=None):
        return self.client.post(
            reverse("api_box_automatic"),
            {
                "strain": self.strain.pk,
                "thermal_zone": self.zone.pk,
                "entered_on": "2026-10-01",
                "initial_polyp_count": 12,
                "request_id": request_id or str(uuid.uuid4()),
            },
            content_type="application/json",
            **self.headers(),
        )

    def plain_box(
        self, code, *, organization=None, strain=None, identity=None, **extra
    ):
        return Box.objects.create(
            organization=organization or self.org,
            strain=strain or self.strain,
            global_code=code,
            box_number=code.rsplit(".", 1)[-1],
            global_identity=identity,
            **extra,
        )

    def resolve(self, global_id, organization=None):
        return self.client.get(
            reverse("api_box_global_id_resolve", args=[global_id]),
            **self.headers(organization),
        )

    # Allocation -----------------------------------------------------------

    def test_automatic_creation_allocates_one_stable_unique_global_id(self):
        request_id = str(uuid.uuid4())
        first = self.create_automatic(request_id)
        replay = self.create_automatic(request_id)
        second = self.create_automatic()
        self.assertEqual(
            (first.status_code, replay.status_code, second.status_code), (201, 200, 201)
        )
        global_id = first.json()["global_id"]
        self.assertEqual(str(uuid.UUID(global_id)), global_id)
        self.assertEqual(replay.json()["global_id"], global_id)
        self.assertNotEqual(second.json()["global_id"], global_id)
        self.assertEqual(BoxGlobalIdentity.objects.count(), 2)
        box = Box.objects.get(pk=first.json()["id"])
        self.assertEqual(str(box.global_identity_id), global_id)
        self.assertEqual(
            box.global_identity.allocation_reason, Reason.AUTOMATIC_CREATION
        )
        log = AuditLog.objects.get(object_id=box.global_code, action="creation")
        self.assertEqual(log.metadata["global_id"], global_id)
        # The Local ID allocation is unchanged by the Global ID.
        self.assertEqual(box.global_code, "GID-LAB-1.001")

    def test_subculture_children_receive_distinct_new_global_ids(self):
        parent_identity = BoxGlobalIdentity.objects.create(
            allocation_reason=Reason.AUTOMATIC_CREATION
        )
        parent = self.plain_box(
            "GID-LAB-1.001",
            identity=parent_identity,
            thermal_zone=self.zone,
            volume_liters=Decimal("0.30"),
        )
        BiologicalMeasurement.objects.create(
            box=parent,
            measured_on=timezone.localdate(),
            polyp_count=100,
            user=self.user,
        )
        parent.refresh_from_db()
        from .polyp_state import resolve_current_polyp_state

        event, children = create_subculture(
            parent_box=parent,
            organization=self.org,
            user=self.user,
            expected_current_state_revision=resolve_current_polyp_state(parent)[
                "revision"
            ],
            children=[
                {"thermal_zone": self.zone, "allocated_polyps": 10},
                {"thermal_zone": self.zone, "allocated_polyps": 0},
            ],
        )
        ids = [child.global_identity_id for child in children]
        self.assertNotIn(None, ids)
        self.assertEqual(len(set(ids)), 2)
        self.assertNotIn(parent_identity.global_id, ids)
        parent.refresh_from_db()
        self.assertEqual(parent.global_identity_id, parent_identity.global_id)
        for child in children:
            child.refresh_from_db()
            self.assertEqual(child.global_identity.allocation_reason, Reason.SUBCULTURE)
        log = AuditLog.objects.get(action=AuditLog.Action.SUBCULTURE)
        self.assertEqual(
            log.metadata["child_global_ids"], [str(value) for value in ids]
        )
        self.assertEqual(SubcultureAllocation.objects.filter(event=event).count(), 2)
        self.assertEqual(BoxLineage.objects.filter(parent_box=parent).count(), 2)

    def test_other_creation_paths_leave_the_global_id_uninitialized(self):
        box = self.plain_box("GID-LAB-1.005")
        self.assertIsNone(box.global_identity_id)
        self.assertFalse(BoxGlobalIdentity.objects.exists())

    # Permanence -------------------------------------------------------------

    def test_strain_reassignment_and_ordinary_saves_keep_the_global_id(self):
        identity = BoxGlobalIdentity.objects.create(
            allocation_reason=Reason.AUTOMATIC_CREATION
        )
        box = self.plain_box("GID-LAB-1.002", identity=identity)
        replacement = Strain.objects.create(
            organization=self.org, species=self.species, code="GID-LAB-2"
        )
        box.strain = replacement
        box.save(update_fields=["strain"])
        box.notes = "Edited"
        box.save()
        box.refresh_from_db()
        self.assertEqual(
            (box.strain_id, box.global_identity_id),
            (replacement.pk, identity.global_id),
        )

    def test_stale_or_explicit_writes_never_clear_or_replace_a_global_id(self):
        identity = BoxGlobalIdentity.objects.create(
            allocation_reason=Reason.AUTOMATIC_CREATION
        )
        box = self.plain_box("GID-LAB-1.003", identity=identity)
        stale = Box.objects.get(pk=box.pk)
        stale.global_identity = None
        stale.notes = "Stale write"
        stale.save()
        box.refresh_from_db()
        self.assertEqual(
            (box.notes, box.global_identity_id), ("Stale write", identity.global_id)
        )
        other = BoxGlobalIdentity.objects.create(
            allocation_reason=Reason.AUTOMATIC_CREATION
        )
        for value in (other, None):
            box.global_identity = value
            with self.subTest(value=value), self.assertRaises(ValidationError):
                box.save(update_fields=["global_identity"])
        box.refresh_from_db()
        self.assertEqual(box.global_identity_id, identity.global_id)

    def test_database_guards_protect_assigned_ids_and_the_registry(self):
        identity = BoxGlobalIdentity.objects.create(
            allocation_reason=Reason.AUTOMATIC_CREATION
        )
        other = BoxGlobalIdentity.objects.create(
            allocation_reason=Reason.AUTOMATIC_CREATION
        )
        box = self.plain_box("GID-LAB-1.004", identity=identity)
        for value in (other.global_id, None):
            with (
                self.subTest(value=value),
                self.assertRaises(DatabaseError),
                transaction.atomic(),
            ):
                Box.objects.filter(pk=box.pk).update(global_identity=value)
        with self.assertRaises(ValidationError):
            BoxGlobalIdentity.objects.filter(pk=identity.pk).update(
                allocation_reason=Reason.SUBCULTURE
            )
        with self.assertRaises(ValidationError):
            identity.save()
        with (
            self.assertRaises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE cultures_boxglobalidentity SET global_id = %s WHERE id = %s",
                [uuid.uuid4().hex, identity.pk],
            )
        box.refresh_from_db()
        identity.refresh_from_db()
        self.assertEqual(box.global_identity_id, identity.global_id)
        # An uninitialized Box may still be initialized once.
        later = self.plain_box("GID-LAB-1.006")
        Box.objects.filter(pk=later.pk).update(global_identity=other.global_id)
        later.refresh_from_db()
        self.assertEqual(later.global_identity_id, other.global_id)

    def test_one_live_record_per_identity_and_institution(self):
        identity = BoxGlobalIdentity.objects.create(
            allocation_reason=Reason.AUTOMATIC_CREATION
        )
        self.plain_box("GID-LAB-1.010", identity=identity)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.plain_box("GID-LAB-1.011", identity=identity)
        # Another institution may hold its own record of the same physical Box.
        self.plain_box(
            "GID-OTH-1.001",
            organization=self.other_org,
            strain=self.other_strain,
            identity=identity,
        )
        self.assertEqual(identity.boxes.count(), 2)

    # Resolution and exposure ----------------------------------------------

    def test_resolution_is_scoped_to_the_active_institution(self):
        identity = BoxGlobalIdentity.objects.create(
            allocation_reason=Reason.AUTOMATIC_CREATION
        )
        own = self.plain_box("GID-LAB-1.020", identity=identity)
        foreign_identity = BoxGlobalIdentity.objects.create(
            allocation_reason=Reason.AUTOMATIC_CREATION
        )
        self.plain_box(
            "GID-OTH-1.020",
            organization=self.other_org,
            strain=self.other_strain,
            identity=foreign_identity,
        )
        response = self.resolve(identity.global_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "id": own.pk,
                "global_id": str(identity.global_id),
                "global_code": own.global_code,
            },
        )
        # The API accepts the canonical lowercase form; the SPA route lowercases.
        upper = self.client.get(
            f"/api/boxes/global/{str(identity.global_id).upper()}/", **self.headers()
        )
        self.assertEqual(upper.status_code, 404)
        # Foreign and unknown identities are indistinguishable.
        foreign = self.resolve(foreign_identity.global_id)
        unknown = self.resolve(uuid.uuid4())
        self.assertEqual((foreign.status_code, unknown.status_code), (404, 404))
        self.assertEqual(foreign.json(), unknown.json())
        # A Global ID never grants access to an institution without membership.
        self.assertIn(
            self.resolve(foreign_identity.global_id, self.other_org).status_code,
            {403, 404},
        )
        missing_context = self.client.get(
            reverse("api_box_global_id_resolve", args=[identity.global_id])
        )
        self.assertEqual(missing_context.status_code, 403)
        self.client.logout()
        self.assertIn(self.resolve(identity.global_id).status_code, {401, 403})
        self.assertFalse(AuditLog.objects.exists())

    def test_shared_identity_resolves_each_institutions_own_record(self):
        OrganizationMembership.objects.create(
            organization=self.other_org, user=self.user, role="viewer"
        )
        identity = BoxGlobalIdentity.objects.create(
            allocation_reason=Reason.AUTOMATIC_CREATION
        )
        own = self.plain_box("GID-LAB-1.030", identity=identity)
        other = self.plain_box(
            "GID-OTH-1.030",
            organization=self.other_org,
            strain=self.other_strain,
            identity=identity,
        )
        self.assertEqual(self.resolve(identity.global_id).json()["id"], own.pk)
        self.assertEqual(
            self.resolve(identity.global_id, self.other_org).json()["id"], other.pk
        )

    def test_merged_alias_resolves_to_its_terminal_record(self):
        identity = BoxGlobalIdentity.objects.create(
            allocation_reason=Reason.AUTOMATIC_CREATION
        )
        canonical = self.plain_box("GID-LAB-1.040", identity=identity)
        alias = self.plain_box("GID-LAB-1.041", status=Box.Status.INACTIVE)
        Box.objects.filter(pk=alias.pk).update(
            merged_into=canonical, global_identity=identity.global_id
        )
        self.assertEqual(self.resolve(identity.global_id).json()["id"], canonical.pk)

    def test_uuid_route_rejects_non_uuid_values(self):
        for value in ("42", "GID-LAB-1.001", "not-a-uuid"):
            with self.subTest(value=value):
                self.assertEqual(
                    self.client.get(
                        f"/api/boxes/global/{value}/", **self.headers()
                    ).status_code,
                    404,
                )

    def test_existing_routes_qr_and_payloads_remain_compatible(self):
        identity = BoxGlobalIdentity.objects.create(
            allocation_reason=Reason.AUTOMATIC_CREATION
        )
        box = self.plain_box("GID-LAB-1.050", identity=identity)
        legacy = self.plain_box("GID-LAB-1.051")
        detail = self.client.get(
            reverse("api_box_detail", args=[box.pk]), **self.headers()
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(
            (detail.json()["global_code"], detail.json()["global_id"]),
            (box.global_code, str(identity.global_id)),
        )
        self.assertIsNone(
            self.client.get(
                reverse("api_box_detail", args=[legacy.pk]), **self.headers()
            ).json()["global_id"]
        )
        listed = {
            row["id"]: row
            for row in self.client.get(
                reverse("api_box_list"), **self.headers()
            ).json()["results"]
        }
        self.assertEqual(listed[box.pk]["global_id"], str(identity.global_id))
        self.assertIsNone(listed[legacy.pk]["global_id"])
        searched = self.client.get(
            reverse("api_box_list"), {"q": box.global_code}, **self.headers()
        ).json()
        self.assertEqual([row["id"] for row in searched["results"]], [box.pk])
        # Printed QR codes and their numeric handoff are unchanged.
        self.assertEqual(reverse("scan_boite", args=[box.pk]), f"/bac/{box.pk}/")
        self.assertTrue(box_scan_url(box).endswith(f"/bac/{box.pk}/"))
        self.assertNotIn(str(identity.global_id), box_scan_url(box))
        scan = self.client.post(
            reverse("api_box_scan", args=[box.pk]), **self.headers()
        )
        self.assertEqual(scan.status_code, 201)
        self.assertEqual(scan.json(), {"id": box.pk, "global_code": box.global_code})

    def test_global_id_is_not_writable_through_box_mutations(self):
        identity = BoxGlobalIdentity.objects.create(
            allocation_reason=Reason.AUTOMATIC_CREATION
        )
        response = self.client.post(
            reverse("api_box_automatic"),
            {
                "strain": self.strain.pk,
                "thermal_zone": self.zone.pk,
                "entered_on": "2026-10-01",
                "request_id": str(uuid.uuid4()),
                "global_id": str(identity.global_id),
            },
            content_type="application/json",
            **self.headers(),
        )
        # Clients never choose identifiers: an unexpected Global ID is refused.
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("global_id", response.json())
        self.assertFalse(Box.objects.exists())
        self.assertEqual(BoxGlobalIdentity.objects.count(), 1)


class BoxGlobalIdentityMigrationTests(
    DisposablePostgresFlushMixin, TransactionTestCase
):
    # Flush truncates append-only tables (historical temperature, Local ID
    # aliases) whose guards forbid TRUNCATE. The mixin bypasses them only on a
    # verified disposable cluster, for fixture cleanup only.
    def test_initializes_only_verified_new_boxes_without_rewriting_records(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        before = historical_targets(
            executor.loader,
            {"cultures"},
            [
                ("cultures", "0021_merge_dna_and_history"),
            ],
        )
        executor.migrate(before)
        # Rollback removes the guards and the column without residue.
        self.assertEqual(self.guards(), set())
        with connection.cursor() as cursor:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor, "cultures_box"
                )
            }
            self.assertNotIn("global_id", columns)
            self.assertNotIn(
                "cultures_boxglobalidentity",
                connection.introspection.table_names(cursor),
            )
        apps = executor.loader.project_state(before).apps
        org = apps.get_model("organizations", "Organization").objects.create(
            name="Global ID migration"
        )
        user = apps.get_model(settings.AUTH_USER_MODEL).objects.create(
            username="global-migration", email="global-migration@example.org"
        )
        species = apps.get_model("taxonomy", "Species").objects.create(
            scientific_name="Migration species"
        )
        strain = apps.get_model("taxonomy", "Strain").objects.create(
            species=species, code="MIG-LAB-1"
        )
        Box = apps.get_model("cultures", "Box")

        def box(number, **extra):
            return Box.objects.create(
                organization=org,
                strain=strain,
                global_code=f"MIG-LAB-1.{number:03}",
                box_number=f"{number:03}",
                **extra,
            )

        automatic, child, parent, historical, imported, canonical = (
            box(n) for n in range(1, 7)
        )
        alias = box(7, status="inactive")
        Box.objects.filter(pk=alias.pk).update(merged_into=canonical.pk)
        Request = apps.get_model("cultures", "AutomaticBoxCreationRequest")
        for target in (automatic, canonical):
            Request.objects.create(
                organization=org,
                actor=user,
                request_id=uuid.uuid4(),
                intent={},
                box=target,
            )
        event = apps.get_model("cultures", "SubcultureEvent").objects.create(
            parent_box=parent,
            event_date=date(2026, 1, 1),
            occurred_at=timezone.now(),
            parent_state_sequence=1,
            parent_polyp_count_before=10,
            allocated_polyp_count=4,
            parent_polyp_count_after=6,
            parent_state_snapshot={"polyp_count": 10},
        )
        apps.get_model("cultures", "SubcultureAllocation").objects.create(
            event=event,
            child_box=child,
            position=0,
            allocated_polyps=4,
            child_global_code=child.global_code,
        )
        apps.get_model("cultures", "BoxTransferImport").objects.create(
            format_version="v1",
            source_transfer_id="T-1",
            source_organization_name="Source",
            source_global_code="SRC-LAB-1.001",
            destination_organization=org,
            created_box=imported,
        )
        rows = {row["id"]: row for row in Box.objects.values()}

        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        apps = executor.loader.project_state(latest).apps
        Box = apps.get_model("cultures", "Box")
        after = {row["id"]: row for row in Box.objects.values()}
        for pk, row in rows.items():
            self.assertEqual(
                {
                    key: value
                    for key, value in after[pk].items()
                    if key not in {"global_identity_id", "planned_weekday"}
                },
                row,
            )
            self.assertIsNone(after[pk]["planned_weekday"])
        identities = {pk: row["global_identity_id"] for pk, row in after.items()}
        Identity = apps.get_model("cultures", "BoxGlobalIdentity")
        self.assertEqual(
            Identity.objects.get(global_id=identities[automatic.pk]).allocation_reason,
            "initialized_automatic_creation",
        )
        self.assertEqual(
            Identity.objects.get(global_id=identities[child.pk]).allocation_reason,
            "initialized_subculture",
        )
        self.assertNotEqual(identities[automatic.pk], identities[child.pk])
        # Unproven continuity stays uninitialized: no invented physical identity.
        for target in (parent, historical, imported, canonical, alias):
            self.assertIsNone(identities[target.pk], target.global_code)
        self.assertEqual(Identity.objects.count(), 2)
        self.assertEqual(
            self.guards(),
            {"protect_box_global_identity", "protect_box_global_identity_registry"},
        )
        with self.assertRaises(DatabaseError), transaction.atomic():
            Box.objects.filter(pk=automatic.pk).update(global_identity=None)
        with self.assertRaises(DatabaseError), transaction.atomic():
            Identity.objects.filter(global_id=identities[child.pk]).update(
                allocation_reason="subculture"
            )
        # The registry column itself is unique in the database.
        with self.assertRaises(IntegrityError), transaction.atomic():
            Identity.objects.create(
                global_id=identities[child.pk], allocation_reason="subculture"
            )

        # A populated rollback is refused before any guard, column or registry
        # row is dropped; nothing is regenerated afterwards.
        boxes = dict(Box.objects.values_list("pk", "global_identity_id"))
        registry = sorted(
            Identity.objects.values_list("global_id", "allocation_reason", "created_at")
        )
        with self.assertRaisesRegex(IrreversibleError, "Global IDs are permanent"):
            MigrationExecutor(connection).migrate(before)
        self.assert_identity_schema_intact()
        self.assertEqual(
            dict(Box.objects.values_list("pk", "global_identity_id")), boxes
        )
        self.assertEqual(
            sorted(
                Identity.objects.values_list(
                    "global_id", "allocation_reason", "created_at"
                )
            ),
            registry,
        )
        MigrationExecutor(connection).migrate(latest)
        self.assertEqual(
            dict(Box.objects.values_list("pk", "global_identity_id")), boxes
        )
        self.assertEqual(
            sorted(
                Identity.objects.values_list(
                    "global_id", "allocation_reason", "created_at"
                )
            ),
            registry,
        )

    def test_unassigned_registry_identity_also_blocks_rollback(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        before = historical_targets(
            executor.loader, {"cultures"}, [("cultures", "0021_merge_dna_and_history")]
        )
        identity = BoxGlobalIdentity.objects.create(
            allocation_reason=Reason.AUTOMATIC_CREATION
        )
        with self.assertRaisesRegex(IrreversibleError, "Global IDs are permanent"):
            MigrationExecutor(connection).migrate(before)
        self.assert_identity_schema_intact()
        self.assertEqual(
            list(BoxGlobalIdentity.objects.values_list("global_id", flat=True)),
            [identity.global_id],
        )

    @skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL table locks")
    def test_rollback_preflight_fences_writers_and_refuses_busy_tables(self):
        migration = import_module("apps.cultures.migrations.0022_box_global_identity")
        org = Organization.objects.create(name="Global ID rollback fence")
        species = Species.objects.create(scientific_name="Global ID fence species")
        strain = Strain.objects.create(
            organization=org, species=species, code="GIF-LAB-1"
        )

        def blocked(write):
            close_old_connections()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '200ms'")
                    write()
            except DatabaseError as error:
                cause = error.__cause__
                return getattr(cause, "sqlstate", None) or getattr(
                    cause, "pgcode", None
                )
            finally:
                connection.close()
            return "committed"

        def preflight():
            with connection.schema_editor(atomic=False) as editor:
                migration.refuse_identity_loss(django_apps, editor)

        # An empty preflight keeps its locks until the DROPs: no identity or
        # Box can be written between the emptiness check and the rollback.
        with transaction.atomic():
            preflight()
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertEqual(
                    pool.submit(
                        blocked,
                        lambda: BoxGlobalIdentity.objects.create(
                            allocation_reason=Reason.AUTOMATIC_CREATION
                        ),
                    ).result(timeout=15),
                    "55P03",
                )
                self.assertEqual(
                    pool.submit(
                        blocked,
                        lambda: Box.objects.create(
                            organization=org,
                            strain=strain,
                            global_code="GIF-LAB-1.001",
                            box_number="001",
                        ),
                    ).result(timeout=15),
                    "55P03",
                )
        self.assertFalse(BoxGlobalIdentity.objects.exists())
        self.assertFalse(Box.objects.exists())

        # An uncommitted competing allocation makes the preflight refuse at
        # once instead of missing it or waiting with a partial lock set.
        holding, release = Event(), Event()

        def hold_allocation():
            close_old_connections()
            try:
                with transaction.atomic():
                    BoxGlobalIdentity.objects.create(
                        allocation_reason=Reason.AUTOMATIC_CREATION
                    )
                    holding.set()
                    if not release.wait(timeout=10):
                        raise AssertionError("Competing allocation was not released")
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(hold_allocation)
            try:
                self.assertTrue(holding.wait(timeout=10))
                with self.assertRaises(DatabaseError) as caught, transaction.atomic():
                    preflight()
                cause = caught.exception.__cause__
                self.assertEqual(
                    getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None),
                    "55P03",
                )
            finally:
                release.set()
            future.result(timeout=15)
        self.assertEqual(BoxGlobalIdentity.objects.count(), 1)
        with (
            self.assertRaisesRegex(IrreversibleError, "Global IDs are permanent"),
            transaction.atomic(),
        ):
            preflight()

    def assert_identity_schema_intact(self):
        self.assertIn(
            ("cultures", "0022_box_global_identity"),
            MigrationExecutor(connection).loader.applied_migrations,
        )
        self.assertEqual(
            self.guards(),
            {"protect_box_global_identity", "protect_box_global_identity_registry"},
        )
        with connection.cursor() as cursor:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor, "cultures_box"
                )
            }
            self.assertIn("global_id", columns)
            self.assertIn(
                "cultures_boxglobalidentity",
                connection.introspection.table_names(cursor),
            )

    def guards(self):
        with connection.cursor() as cursor:
            if connection.vendor == "postgresql":
                cursor.execute("""SELECT t.tgname FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
                    WHERE NOT t.tgisinternal AND t.tgname LIKE 'protect_box_global_identity%%'""")
                triggers = {row[0] for row in cursor.fetchall()}
                cursor.execute(
                    "SELECT proname FROM pg_proc WHERE proname LIKE 'protect_box_global_identity%%'"
                )
                self.assertEqual({row[0] for row in cursor.fetchall()}, triggers)
                return triggers
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND name LIKE 'protect_box_global_identity%'"
            )
            return {row[0] for row in cursor.fetchall()}
