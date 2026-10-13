import json
from datetime import date, datetime, timedelta
from io import StringIO

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .admin import BoxAdmin, BoxLocationAdmin, BoxMovementAdmin
from .models import MOVEMENT_WEEK_TIME_ZONE, Box, BoxLocation, BoxMovement, ThermalZone
from .movement_week_diagnostics import diagnose_box_movement_weeks
from .services import BoxMovementWeekConflict, move_box_to_thermal_zone

PARIS = MOVEMENT_WEEK_TIME_ZONE


def paris(*args):
    return datetime(*args, tzinfo=PARIS)


class MovementFixtureMixin:
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            username="week_admin", email="week_admin@example.org", password="secret",
        )
        self.technician = user_model.objects.create_user(
            username="week_tech", email="week_tech@example.org", password="secret",
        )
        self.organization = Organization.objects.create(name="Weekly movement laboratory")
        self.other_organization = Organization.objects.create(name="Weekly foreign laboratory")
        OrganizationMembership.objects.create(
            user=self.admin, organization=self.organization, role=OrganizationMembership.Role.ADMIN,
        )
        OrganizationMembership.objects.create(
            user=self.technician, organization=self.organization,
            role=OrganizationMembership.Role.LAB_TECHNICIAN,
        )
        species = Species.objects.create(scientific_name="Aurelia weekly", genus_species_code="AWK")
        self.strain = Strain.objects.create(
            species=species, code="AWK-LAB-1", organization=self.organization, number=1, origin_code="LAB",
        )
        self.zone_a = ThermalZone.objects.create(organization=self.organization, name="Zone A")
        self.zone_b = ThermalZone.objects.create(organization=self.organization, name="Zone B")
        self.zone_c = ThermalZone.objects.create(organization=self.organization, name="Zone C")
        self.foreign_zone = ThermalZone.objects.create(organization=self.other_organization, name="Foreign")

    def create_box(self, code="AWK-LAB-1.001", *, zone="a", status=Box.Status.ACTIVE, located_since=None):
        zone = {"a": self.zone_a, "b": self.zone_b, None: None}[zone]
        box = Box.objects.create(
            organization=self.organization, global_code=code, box_number=code.rsplit(".", 1)[-1],
            strain=self.strain, status=status, thermal_zone=zone,
        )
        if zone is not None:
            BoxLocation.objects.create(
                box=box, thermal_zone=zone, starts_at=located_since or paris(2025, 1, 6, 9, 0),
            )
        return box

    def move(self, box, zone, moved_at, *, user=None, expected=None, notes=""):
        self.client.force_login(user or self.technician)
        return self.client.post(
            reverse("api_box_move", args=[box.id]),
            data=json.dumps({
                "expected_thermal_zone_id": expected.id if expected else None,
                "thermal_zone_id": zone.id,
                "moved_at": moved_at.isoformat(),
                "notes": notes,
            }),
            content_type="application/json",
        )

    def snapshot(self, box):
        box.refresh_from_db()
        return {
            "zone": box.thermal_zone_id,
            "status": box.status,
            "locations": list(BoxLocation.objects.filter(box=box).order_by("id").values()),
            "movements": list(BoxMovement.objects.filter(box=box).order_by("id").values()),
            "audits": list(AuditLog.objects.order_by("id").values("id", "metadata")),
        }


class WeekStartTests(TestCase):
    def test_equivalent_instants_in_different_offsets_share_one_week(self):
        instants = [
            datetime.fromisoformat("2026-03-01T23:30:00+00:00"),
            datetime.fromisoformat("2026-03-02T00:30:00+01:00"),
            datetime.fromisoformat("2026-03-02T08:30:00+09:00"),
            datetime.fromisoformat("2026-03-01T18:30:00-05:00"),
        ]
        self.assertEqual({BoxMovement.week_start_for(value) for value in instants}, {date(2026, 3, 2)})

    def test_sunday_monday_boundary_uses_paris_local_time(self):
        self.assertEqual(BoxMovement.week_start_for(paris(2026, 3, 1, 23, 59, 59)), date(2026, 2, 23))
        self.assertEqual(BoxMovement.week_start_for(paris(2026, 3, 2, 0, 0)), date(2026, 3, 2))
        # 23:30 UTC on Sunday is already Monday in Paris.
        self.assertEqual(
            BoxMovement.week_start_for(datetime.fromisoformat("2026-03-01T23:30:00+00:00")), date(2026, 3, 2),
        )

    def test_dst_transitions(self):
        # Spring forward (2026-03-29): Monday 00:00 CEST is 22:00 UTC on Sunday.
        self.assertEqual(
            BoxMovement.week_start_for(datetime.fromisoformat("2026-03-29T21:59:59+00:00")), date(2026, 3, 23),
        )
        self.assertEqual(
            BoxMovement.week_start_for(datetime.fromisoformat("2026-03-29T22:00:00+00:00")), date(2026, 3, 30),
        )
        # Fall back (2026-10-25): Monday 00:00 CET is 23:00 UTC on Sunday.
        self.assertEqual(
            BoxMovement.week_start_for(datetime.fromisoformat("2026-10-25T22:30:00+00:00")), date(2026, 10, 19),
        )
        self.assertEqual(
            BoxMovement.week_start_for(datetime.fromisoformat("2026-10-25T23:00:00+00:00")), date(2026, 10, 26),
        )

    def test_iso_year_boundaries(self):
        # 2026-W01 starts on Monday 2025-12-29; 2026 has 53 ISO weeks.
        self.assertEqual(BoxMovement.week_start_for(paris(2025, 12, 31, 12)), date(2025, 12, 29))
        self.assertEqual(BoxMovement.week_start_for(paris(2026, 1, 2, 12)), date(2025, 12, 29))
        self.assertEqual(BoxMovement.week_start_for(paris(2025, 12, 28, 23, 59)), date(2025, 12, 22))
        self.assertEqual(date(2025, 12, 29).isocalendar()[:2], (2026, 1))
        self.assertEqual(BoxMovement.week_start_for(paris(2027, 1, 3, 23, 59)), date(2026, 12, 28))
        self.assertEqual(date(2026, 12, 28).isocalendar()[:2], (2026, 53))
        self.assertEqual(BoxMovement.week_start_for(paris(2027, 1, 4, 0, 0)), date(2027, 1, 4))


class OrdinaryMovementWeeklyTests(MovementFixtureMixin, TestCase):
    def test_first_ordinary_movement_records_kind_week_links_and_audit(self):
        box = self.create_box()
        origin = box.locations.get()
        response = self.move(box, self.zone_b, paris(2026, 3, 4, 10), expected=self.zone_a, notes="Moved")

        self.assertEqual(response.status_code, 200)
        movement = box.movements.get()
        self.assertEqual(movement.kind, BoxMovement.Kind.ORDINARY)
        self.assertEqual(movement.iso_week_start, date(2026, 3, 2))
        self.assertEqual(movement.revision, 0)
        self.assertEqual(movement.origin_location, origin)
        self.assertEqual(movement.destination_location.thermal_zone, self.zone_b)
        self.assertEqual(movement.destination_location.starts_at, movement.moved_at)
        origin.refresh_from_db()
        self.assertEqual(origin.ends_at, movement.moved_at)
        audit = AuditLog.objects.get(metadata__movement_id=movement.id)
        self.assertEqual(audit.metadata["movement_kind"], "ordinary")
        self.assertEqual(audit.metadata["iso_week_start"], "2026-03-02")
        payload = response.json()["movements"][0]
        self.assertEqual(payload["kind"], "ordinary")
        self.assertEqual(payload["iso_week_start"], "2026-03-02")
        self.assertEqual(payload["revision"], 0)

    def test_second_ordinary_movement_in_the_same_week_is_rejected_without_writes(self):
        box = self.create_box()
        self.assertEqual(self.move(box, self.zone_b, paris(2026, 3, 2, 0, 0), expected=self.zone_a).status_code, 200)
        first = box.movements.get()
        before = self.snapshot(box)

        response = self.move(box, self.zone_c, paris(2026, 3, 8, 23, 59), expected=self.zone_b)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {
            "code": "box_movement_week_conflict",
            "detail": response.json()["detail"],
            "conflicting_movement_id": first.id,
            "iso_week_start": "2026-03-02",
            "iso_year": 2026,
            "iso_week": 10,
        })
        self.assertEqual(self.snapshot(box), before)

    def test_next_iso_week_is_accepted(self):
        box = self.create_box()
        self.assertEqual(self.move(box, self.zone_b, paris(2026, 3, 1, 23, 59, 59), expected=self.zone_a).status_code, 200)
        self.assertEqual(self.move(box, self.zone_c, paris(2026, 3, 2, 0, 0), expected=self.zone_b).status_code, 200)
        self.assertEqual(
            list(box.movements.order_by("moved_at").values_list("iso_week_start", flat=True)),
            [date(2026, 2, 23), date(2026, 3, 2)],
        )

    def test_utc_instant_already_in_next_paris_week_is_accepted(self):
        box = self.create_box()
        self.assertEqual(self.move(box, self.zone_b, paris(2026, 3, 1, 12), expected=self.zone_a).status_code, 200)
        response = self.move(
            box, self.zone_c, datetime.fromisoformat("2026-03-01T23:30:00+00:00"), expected=self.zone_b,
        )
        self.assertEqual(response.status_code, 200)

    def test_equivalent_offsets_conflict_in_the_same_paris_week(self):
        box = self.create_box()
        self.assertEqual(
            self.move(box, self.zone_b, datetime.fromisoformat("2026-03-02T08:30:00+09:00"), expected=self.zone_a).status_code,
            200,
        )
        response = self.move(
            box, self.zone_c, datetime.fromisoformat("2026-03-01T23:45:00+00:00"), expected=self.zone_b,
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["iso_week_start"], "2026-03-02")

    def test_dst_week_boundaries_through_the_api(self):
        box = self.create_box()
        sunday = datetime.fromisoformat("2026-03-29T21:59:00+00:00")
        monday = datetime.fromisoformat("2026-03-29T22:00:00+00:00")
        self.assertEqual(self.move(box, self.zone_b, sunday, expected=self.zone_a).status_code, 200)
        self.assertEqual(self.move(box, self.zone_c, monday, expected=self.zone_b).status_code, 200)
        conflict = self.move(box, self.zone_a, monday + timedelta(days=6, hours=1), expected=self.zone_c)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["iso_week_start"], "2026-03-30")

    def test_iso_year_boundary_conflict_reports_the_iso_year(self):
        box = self.create_box()
        self.assertEqual(self.move(box, self.zone_b, paris(2025, 12, 31, 10), expected=self.zone_a).status_code, 200)
        response = self.move(box, self.zone_c, paris(2026, 1, 2, 10), expected=self.zone_b)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            (response.json()["iso_week_start"], response.json()["iso_year"], response.json()["iso_week"]),
            ("2025-12-29", 2026, 1),
        )
        self.assertEqual(self.move(box, self.zone_c, paris(2026, 1, 5, 0, 1), expected=self.zone_b).status_code, 200)

    def test_service_path_enforces_the_same_rule(self):
        box = self.create_box()
        move_box_to_thermal_zone(box=box, thermal_zone=self.zone_b, moved_at=paris(2026, 3, 3), user=self.admin, notes="")
        with self.assertRaises(BoxMovementWeekConflict):
            move_box_to_thermal_zone(
                box=box, thermal_zone=self.zone_c, moved_at=paris(2026, 3, 4), user=self.admin, notes="",
            )
        self.assertEqual(box.movements.count(), 1)

    def test_stale_location_is_reported_before_the_weekly_rule(self):
        box = self.create_box()
        self.assertEqual(self.move(box, self.zone_b, paris(2026, 3, 3), expected=self.zone_a).status_code, 200)
        response = self.move(box, self.zone_c, paris(2026, 3, 4), expected=self.zone_a)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "box_location_changed")

    def test_database_constraint_rejects_direct_duplicate_ordinary_rows(self):
        box = self.create_box()
        BoxMovement.objects.create(
            box=box, to_thermal_zone=self.zone_b, moved_at=paris(2026, 3, 3), kind=BoxMovement.Kind.ORDINARY,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            BoxMovement.objects.create(
                box=box, to_thermal_zone=self.zone_c, moved_at=paris(2026, 3, 8, 23), kind=BoxMovement.Kind.ORDINARY,
            )

    def test_database_constraint_requires_a_week_key_for_bulk_ordinary_rows(self):
        box = self.create_box()
        with self.assertRaises(IntegrityError), transaction.atomic():
            BoxMovement.objects.bulk_create([
                BoxMovement(box=box, to_thermal_zone=self.zone_b, moved_at=paris(2026, 3, 3), kind="ordinary"),
            ])
        with self.assertRaises(IntegrityError), transaction.atomic():
            BoxMovement.objects.bulk_create([
                BoxMovement(box=box, to_thermal_zone=self.zone_b, moved_at=paris(2026, 3, 3), kind="transfer"),
            ])

    def test_direct_rows_default_to_unclassified_and_never_use_the_allowance(self):
        box = self.create_box()
        legacy = BoxMovement.objects.create(box=box, to_thermal_zone=self.zone_b, moved_at=paris(2026, 3, 3))
        self.assertEqual(legacy.kind, BoxMovement.Kind.UNCLASSIFIED)
        self.assertIsNone(legacy.iso_week_start)
        self.assertIsNone(legacy.destination_location)
        self.assertEqual(self.move(box, self.zone_c, paris(2026, 3, 4), expected=self.zone_a).status_code, 200)

    def test_bulk_updates_cannot_bypass_the_weekly_key(self):
        box = self.create_box()
        move_box_to_thermal_zone(box=box, thermal_zone=self.zone_b, moved_at=paris(2026, 3, 3), user=self.admin, notes="")
        for field, value in (("moved_at", paris(2026, 4, 1)), ("kind", "unclassified"), ("iso_week_start", None)):
            with self.assertRaises(ValidationError):
                BoxMovement.objects.filter(box=box).update(**{field: value})
        self.assertEqual(BoxMovement.objects.filter(box=box).update(notes="bulk note"), 1)


class LifecycleMovementKindTests(MovementFixtureMixin, TestCase):
    def test_initial_location_endpoint_records_initial_kind_without_allowance(self):
        box = self.create_box(zone=None)
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("api_admin_box_initial_location", args=[box.id]),
            data={"thermal_zone_id": self.zone_a.id},
        )
        self.assertEqual(response.status_code, 200)
        initial = box.movements.get()
        self.assertEqual(initial.kind, BoxMovement.Kind.INITIAL_LOCATION)
        self.assertIsNone(initial.iso_week_start)
        self.assertIsNone(initial.origin_location)
        self.assertIsNotNone(initial.destination_location)
        # The ordinary weekly allowance remains available.
        self.assertEqual(self.move(box, self.zone_b, timezone.now(), expected=self.zone_a).status_code, 200)
        self.assertEqual(box.movements.filter(kind=BoxMovement.Kind.ORDINARY).count(), 1)

    def test_move_endpoint_from_no_location_records_a_first_location(self):
        box = self.create_box(zone=None)
        response = self.move(box, self.zone_b, paris(2026, 3, 3), expected=None)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(box.movements.get().kind, BoxMovement.Kind.INITIAL_LOCATION)
        self.assertEqual(self.move(box, self.zone_c, paris(2026, 3, 4), expected=self.zone_b).status_code, 200)

    def test_qualification_and_reactivation_kinds_are_traceable(self):
        box = self.create_box(zone=None, status=Box.Status.PENDING_REVIEW)
        self.client.force_login(self.admin)
        qualified = self.client.post(
            reverse("api_box_qualify", args=[box.id]),
            data=json.dumps({"target_status": "active", "thermal_zone_id": self.zone_a.id}),
            content_type="application/json",
        )
        self.assertEqual(qualified.status_code, 200)
        self.assertEqual(box.movements.get().kind, BoxMovement.Kind.QUALIFICATION)

        self.assertEqual(self.move(box, self.zone_b, timezone.now(), expected=self.zone_a).status_code, 200)
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.post(reverse("api_box_archive", args=[box.id]), data={"reason": "Stopped"}).status_code, 200,
        )
        reactivated = self.client.post(
            reverse("api_box_activate", args=[box.id]), data={"thermal_zone_id": self.zone_c.id},
        )
        self.assertEqual(reactivated.status_code, 200)
        kinds = list(box.movements.order_by("moved_at", "id").values_list("kind", flat=True))
        self.assertEqual(kinds, ["qualification", "ordinary", "reactivation"])
        reactivation = box.movements.get(kind=BoxMovement.Kind.REACTIVATION)
        self.assertIsNone(reactivation.iso_week_start)
        audit = AuditLog.objects.get(metadata__transition="inactive->active")
        self.assertEqual(audit.metadata["movement_id"], reactivation.id)
        box.refresh_from_db()
        self.assertEqual((box.status, box.thermal_zone), (Box.Status.ACTIVE, self.zone_c))

    def test_lifecycle_operations_after_an_ordinary_move_are_not_blocked(self):
        box = self.create_box()
        self.assertEqual(self.move(box, self.zone_b, timezone.now(), expected=self.zone_a).status_code, 200)
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.post(reverse("api_box_archive", args=[box.id]), data={"reason": "Stopped"}).status_code, 200,
        )
        self.assertEqual(
            self.client.post(reverse("api_box_activate", args=[box.id]), data={"thermal_zone_id": self.zone_a.id}).status_code,
            200,
        )

    def test_manual_creation_subculture_and_deactivation_create_no_movement(self):
        self.client.force_login(self.technician)
        created = self.client.post(
            reverse("api_box_list"),
            data=json.dumps({
                "strain": self.strain.id, "global_code": "AWK-LAB-1.010", "box_number": "010",
                "thermal_zone": self.zone_a.id,
            }),
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        box = Box.objects.get(global_code="AWK-LAB-1.010")
        self.assertFalse(box.movements.exists())
        self.assertEqual(box.locations.count(), 1)
        self.client.force_login(self.admin)
        self.client.post(reverse("api_box_archive", args=[box.id]), data={"reason": "Stopped"})
        self.assertFalse(box.movements.exists())


class MovementAdminTests(MovementFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.superuser = get_user_model().objects.create_superuser(
            username="week_root", email="week_root@example.org", password="secret",
        )
        self.box = self.create_box()
        move_box_to_thermal_zone(
            box=self.box, thermal_zone=self.zone_b, moved_at=paris(2026, 3, 3), user=self.admin, notes="",
        )
        self.movement = self.box.movements.get()
        self.client.force_login(self.superuser)

    def test_generic_admin_cannot_add_change_or_delete_movements_and_periods(self):
        # The admin site is not routed today; its model permissions still refuse writes.
        request = RequestFactory().get("/")
        request.user = self.superuser
        location = self.movement.destination_location
        for model, admin_class, obj in (
            (BoxMovement, BoxMovementAdmin, self.movement),
            (BoxLocation, BoxLocationAdmin, location),
        ):
            model_admin = admin_class(model, AdminSite())
            self.assertFalse(model_admin.has_add_permission(request))
            self.assertFalse(model_admin.has_change_permission(request, obj))
            self.assertFalse(model_admin.has_delete_permission(request, obj))
            self.assertTrue(model_admin.has_view_permission(request, obj))

    def test_box_admin_cannot_change_the_current_location_pointer(self):
        request = RequestFactory().get("/")
        request.user = self.superuser
        box_admin = BoxAdmin(Box, AdminSite())
        self.assertIn("thermal_zone", box_admin.get_readonly_fields(request, self.box))
        self.assertNotIn("thermal_zone", box_admin.get_readonly_fields(request, None))


class MovementWeekDiagnosticTests(MovementFixtureMixin, TestCase):
    def test_diagnostic_reports_candidates_and_inconsistencies_without_writing(self):
        box = self.create_box()
        move_box_to_thermal_zone(box=box, thermal_zone=self.zone_b, moved_at=paris(2026, 3, 3), user=self.admin, notes="")
        legacy_same_week = BoxMovement.objects.create(box=box, to_thermal_zone=self.zone_c, moved_at=paris(2026, 3, 5))
        other = self.create_box("AWK-LAB-1.002")
        legacy_one = BoxMovement.objects.create(box=other, to_thermal_zone=self.zone_b, moved_at=paris(2026, 4, 7))
        legacy_two = BoxMovement.objects.create(box=other, to_thermal_zone=self.zone_c, moved_at=paris(2026, 4, 8))
        BoxLocation.objects.create(box=other, thermal_zone=self.zone_b, starts_at=paris(2026, 4, 1))
        pointer_mismatch = self.create_box("AWK-LAB-1.003")
        pointer_mismatch.thermal_zone = self.zone_c
        pointer_mismatch.save(update_fields=["thermal_zone"])
        BoxLocation.objects.create(box=other, thermal_zone=self.foreign_zone, starts_at=paris(2026, 1, 1), ends_at=paris(2026, 1, 2))

        before = self.snapshot(box), self.snapshot(other)
        with CaptureQueriesContext(connection) as queries:
            report = diagnose_box_movement_weeks(organization=self.organization)
        self.assertFalse([
            query["sql"] for query in queries
            if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ])
        self.assertEqual((self.snapshot(box), self.snapshot(other)), before)

        weeks = {(item["global_code"], item["iso_week_start"]): item for item in report["weekly_candidates"]}
        self.assertEqual(
            {movement["id"] for movement in weeks[("AWK-LAB-1.001", "2026-03-02")]["movements"]},
            {box.movements.get(kind="ordinary").id, legacy_same_week.id},
        )
        self.assertEqual(weeks[("AWK-LAB-1.001", "2026-03-02")]["ordinary_count"], 1)
        self.assertEqual(
            {movement["id"] for movement in weeks[("AWK-LAB-1.002", "2026-04-06")]["movements"]},
            {legacy_one.id, legacy_two.id},
        )
        self.assertEqual(report["summary"]["unclassified_movements"], 3)
        self.assertEqual(
            {item["box_id"] for item in report["multiple_open_periods"]}, {other.id},
        )
        self.assertEqual(
            [(item["box_id"], item["issue"]) for item in report["current_pointer_inconsistencies"]],
            [(pointer_mismatch.id, "pointer_differs_from_open_periods")],
        )
        foreign = [item for item in report["foreign_zone_relations"] if item["relation"] == "BoxLocation.thermal_zone"]
        self.assertEqual([item["issue"] for item in foreign], ["outside_organization"])
        self.assertNotIn("Foreign", json.dumps(report))
        unlinked = {item["movement_id"] for item in report["movement_period_mismatches"]}
        self.assertTrue({legacy_same_week.id, legacy_one.id, legacy_two.id} <= unlinked)
        self.assertNotIn(box.movements.get(kind="ordinary").id, unlinked)
        self.assertEqual(report["summary"]["week_key_mismatches"], 0)

    def test_command_outputs_text_and_json(self):
        box = self.create_box()
        BoxMovement.objects.create(box=box, to_thermal_zone=self.zone_b, moved_at=paris(2026, 3, 3))
        output = StringIO()
        call_command("diagnose_box_movement_weeks", organization_id=self.organization.id, stdout=output)
        self.assertIn("unclassified_movements: 1", output.getvalue())
        output = StringIO()
        call_command(
            "diagnose_box_movement_weeks", organization_id=self.organization.id, format="json", stdout=output,
        )
        self.assertEqual(json.loads(output.getvalue())["summary"]["unclassified_movements"], 1)
