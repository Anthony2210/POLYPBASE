"""Planned weekday of a Box: constraint, admin-only edits, audit, payloads, transplant."""

from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.audit.box_lookup import box_audits
from apps.audit.models import AuditLog
from apps.audit.services import serialize_business_details
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from . import api_views
from .admin import BoxAdmin
from .models import Box, ThermalZone
from .polyp_state import resolve_current_polyp_state
from .services import create_subculture, set_box_planned_weekday

Role = OrganizationMembership.Role


class PlannedWeekdayTestBase(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.org = Organization.objects.create(name="Weekday laboratory")
        self.other_org = Organization.objects.create(name="Other weekday laboratory")
        self.admin = users.create_user(
            username="weekday_admin", email="weekday_admin@example.org"
        )
        self.technician = users.create_user(
            username="weekday_tech", email="weekday_tech@example.org"
        )
        self.viewer = users.create_user(
            username="weekday_viewer", email="weekday_viewer@example.org"
        )
        self.other_admin = users.create_user(
            username="weekday_other", email="weekday_other@example.org"
        )
        for user, org, role in (
            (self.admin, self.org, Role.ADMIN),
            (self.technician, self.org, Role.LAB_TECHNICIAN),
            (self.viewer, self.org, Role.VIEWER),
            (self.other_admin, self.other_org, Role.ADMIN),
        ):
            OrganizationMembership.objects.create(
                user=user, organization=org, role=role
            )
        self.zone = ThermalZone.objects.create(
            organization=self.org, name="Weekday zone"
        )
        species = Species.objects.create(
            scientific_name="Aurelia weekday", genus_species_code="AWD"
        )
        self.strain = Strain.objects.create(
            species=species, organization=self.org, code="AWD-QA-1"
        )
        self.box = self.make_box("AWD-QA-1.001")

    def make_box(self, code, **fields):
        return Box.objects.create(
            organization=self.org,
            strain=self.strain,
            thermal_zone=self.zone,
            global_code=code,
            box_number=code.rsplit(".", 1)[1],
            **fields,
        )

    def patch(self, user, value, *, box=None, org=None, raw=None):
        self.client.force_login(user)
        return self.client.patch(
            reverse("api_box_planned_weekday", args=[(box or self.box).pk]),
            data=raw if raw is not None else {"planned_weekday": value},
            content_type="application/json",
            HTTP_X_ORGANIZATION_ID=str((org or self.org).pk),
        )


class PlannedWeekdayConstraintTests(PlannedWeekdayTestBase):
    def test_database_accepts_null_and_iso_days_only(self):
        self.assertIsNone(self.box.planned_weekday)
        for value in (1, 7):
            Box.objects.filter(pk=self.box.pk).update(planned_weekday=value)
        for value in (0, 8):
            with (
                self.subTest(value=value),
                transaction.atomic(),
                self.assertRaises(IntegrityError),
            ):
                Box.objects.filter(pk=self.box.pk).update(planned_weekday=value)
        Box.objects.filter(pk=self.box.pk).update(planned_weekday=None)
        self.box.refresh_from_db()
        self.assertIsNone(self.box.planned_weekday)

    def test_service_rejects_out_of_range_without_writing(self):
        for value in (0, 8):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                set_box_planned_weekday(
                    box=self.box,
                    organization=self.org,
                    planned_weekday=value,
                    user=self.admin,
                )
        self.box.refresh_from_db()
        self.assertIsNone(self.box.planned_weekday)
        self.assertFalse(
            AuditLog.objects.filter(
                metadata__event="box_planned_weekday_updated"
            ).exists()
        )

    def raw_update(self, value):
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE cultures_box SET planned_weekday = %s WHERE id = %s",
                [value, self.box.pk],
            )
        return Box.objects.values_list("planned_weekday", flat=True).get(pk=self.box.pk)

    def test_database_never_stores_a_fractional_weekday(self):
        # SQLite keeps 1.5 as REAL, so only a membership check rejects it. A
        # PostgreSQL smallint rounds the assignment first (1.5 -> 2, 7.5 -> 8).
        if connection.vendor == "postgresql":
            rejected, stored = (7.5, 0.4), {1.5: 2, 6.6: 7}
        else:
            rejected, stored = (1.5, 6.6, 7.5, 0.4), {}
        for value in rejected:
            with (
                self.subTest(value=value),
                transaction.atomic(),
                self.assertRaises(IntegrityError),
            ):
                self.raw_update(value)
        for value, expected in stored.items():
            with self.subTest(value=value):
                self.assertEqual(self.raw_update(value), expected)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM cultures_box WHERE planned_weekday IS NOT NULL "
                "AND planned_weekday NOT IN (1, 2, 3, 4, 5, 6, 7)"
            )
            self.assertEqual(cursor.fetchone()[0], 0)


class PlannedWeekdayEditTests(PlannedWeekdayTestBase):
    def audits(self):
        return list(
            AuditLog.objects.filter(
                metadata__event="box_planned_weekday_updated"
            ).order_by("pk")
        )

    def test_admin_sets_changes_and_clears_with_audited_before_and_after(self):
        for value in (3, 7, None):
            response = self.patch(self.admin, value)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["planned_weekday"], value)
        self.box.refresh_from_db()
        self.assertIsNone(self.box.planned_weekday)
        audits = self.audits()
        self.assertEqual(
            [audit.metadata["modifications"]["planned_weekday"] for audit in audits],
            [
                {"avant": None, "apres": 3},
                {"avant": 3, "apres": 7},
                {"avant": 7, "apres": None},
            ],
        )
        for audit in audits:
            self.assertEqual(
                (
                    audit.organization_id,
                    audit.user_id,
                    audit.action,
                    audit.object_type,
                    audit.object_id,
                ),
                (
                    self.org.pk,
                    self.admin.pk,
                    AuditLog.Action.UPDATE,
                    "box",
                    self.box.global_code,
                ),
            )
            self.assertEqual(audit.metadata["box_id"], self.box.pk)

    def test_audit_appears_in_box_history_with_before_and_after(self):
        self.patch(self.admin, 2)
        history = list(box_audits(organization_id=self.org.pk, box_ids=[self.box.pk]))
        self.assertEqual(len(history), 1)
        details = serialize_business_details(history[0])
        self.assertEqual(details["type"], "box")
        self.assertEqual(
            details["changes"], {"planned_weekday": {"before": None, "after": 2}}
        )

    def test_unchanged_value_writes_no_audit(self):
        self.patch(self.admin, 4)
        response = self.patch(self.admin, 4)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.audits()), 1)

    def test_rejects_values_outside_iso_range_and_non_integers(self):
        for raw in (
            {"planned_weekday": 0},
            {"planned_weekday": 8},
            {"planned_weekday": -1},
            {"planned_weekday": "3"},
            {"planned_weekday": True},
            {"planned_weekday": 2.5},
            {},
        ):
            with self.subTest(raw=raw):
                response = self.patch(self.admin, None, raw=raw)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("planned_weekday", response.json())
        self.box.refresh_from_db()
        self.assertIsNone(self.box.planned_weekday)
        self.assertEqual(self.audits(), [])

    def test_technician_and_viewer_are_refused_like_other_admin_box_edits(self):
        self.make_box("AWD-QA-1.002")
        for user in (self.technician, self.viewer):
            with self.subTest(user=user.username):
                response = self.patch(user, 5)
                self.assertEqual(response.status_code, 403)
                # Same refusal as the existing admin-only deactivation.
                self.client.force_login(user)
                deactivation = self.client.post(
                    reverse("api_box_deactivate", args=[self.box.pk]),
                    data={"reason": "test"},
                    content_type="application/json",
                    HTTP_X_ORGANIZATION_ID=str(self.org.pk),
                )
                self.assertEqual(deactivation.status_code, 403)
        self.box.refresh_from_db()
        self.assertIsNone(self.box.planned_weekday)
        self.assertEqual(self.audits(), [])

    def test_other_institution_admin_cannot_find_the_box(self):
        response = self.patch(self.other_admin, 5, org=self.other_org)
        self.assertEqual(response.status_code, 404)
        # Claiming the box's institution without membership is refused too.
        self.assertIn(self.patch(self.other_admin, 5).status_code, {403, 404})
        self.box.refresh_from_db()
        self.assertIsNone(self.box.planned_weekday)
        self.assertEqual(self.audits(), [])

    def test_inactive_and_pending_boxes_follow_admin_box_edit_rule(self):
        inactive = self.make_box("AWD-QA-1.003", status=Box.Status.INACTIVE)
        Box.objects.filter(pk=inactive.pk).update(thermal_zone=None)
        pending = self.make_box("AWD-QA-1.004", status=Box.Status.PENDING_REVIEW)
        for box in (inactive, pending):
            with self.subTest(status=box.status):
                response = self.patch(self.admin, 6, box=box)
                self.assertEqual(response.status_code, 200, response.content)
                box.refresh_from_db()
                self.assertEqual(box.planned_weekday, 6)
                self.assertEqual(
                    box.status,
                    Box.Status.INACTIVE
                    if box is inactive
                    else Box.Status.PENDING_REVIEW,
                )

    def test_merged_box_is_not_editable(self):
        target = self.make_box("AWD-QA-1.005")
        Box.objects.filter(pk=self.box.pk).update(
            merged_into=target, status=Box.Status.INACTIVE, thermal_zone=None
        )
        self.assertEqual(self.patch(self.admin, 1).status_code, 404)
        self.assertEqual(self.audits(), [])

    def move_to_other_institution(self):
        Box.objects.filter(pk=self.box.pk).update(organization=self.other_org)

    def test_box_moved_after_authorization_is_a_404_without_write_or_audit(self):
        real_check = api_views.get_active_admin_organization_ids

        def authorize_then_move(request):
            # Authorization succeeds, then the box leaves the institution before the write.
            authorized = real_check(request)
            self.move_to_other_institution()
            return authorized

        with patch.object(
            api_views, "get_active_admin_organization_ids", authorize_then_move
        ):
            response = self.patch(self.admin, 4)
        self.assertEqual(response.status_code, 404)
        self.box.refresh_from_db()
        self.assertEqual(
            (self.box.organization_id, self.box.planned_weekday),
            (self.other_org.pk, None),
        )
        self.assertEqual(self.audits(), [])

    def test_service_locks_the_box_within_the_authorized_institution(self):
        self.move_to_other_institution()
        with self.assertRaises(Box.DoesNotExist):
            set_box_planned_weekday(
                box=self.box, organization=self.org, planned_weekday=4, user=self.admin
            )
        self.box.refresh_from_db()
        self.assertIsNone(self.box.planned_weekday)
        self.assertEqual(self.audits(), [])


class PlannedWeekdayPayloadTests(PlannedWeekdayTestBase):
    def get(self, name, *args):
        self.client.force_login(self.viewer)
        return self.client.get(
            reverse(name, args=args), HTTP_X_ORGANIZATION_ID=str(self.org.pk)
        )

    def test_list_detail_and_overview_expose_the_field_read_only(self):
        Box.objects.filter(pk=self.box.pk).update(planned_weekday=1)
        empty = self.make_box("AWD-QA-1.002")
        listed = {
            row["id"]: row["planned_weekday"]
            for row in self.get("api_box_list").json()["results"]
        }
        self.assertEqual(listed, {self.box.pk: 1, empty.pk: None})
        self.assertEqual(
            self.get("api_box_detail", self.box.pk).json()["planned_weekday"], 1
        )
        overview = {
            row["id"]: row["planned_weekday"]
            for row in self.get("api_overview_active_boxes").json()["results"]
        }
        self.assertEqual(overview, {self.box.pk: 1, empty.pk: None})

    def query_count(self, name):
        self.client.force_login(self.viewer)
        url = reverse(name)
        self.client.get(
            url, HTTP_X_ORGANIZATION_ID=str(self.org.pk)
        )  # Warm session and caches.
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(
                self.client.get(
                    url, HTTP_X_ORGANIZATION_ID=str(self.org.pk)
                ).status_code,
                200,
            )
        return len(queries)

    def test_list_and_overview_query_counts_do_not_grow_with_planned_boxes(self):
        for offset, name in ((2, "api_box_list"), (5, "api_overview_active_boxes")):
            with self.subTest(endpoint=name):
                Box.objects.update(planned_weekday=None)
                baseline = self.query_count(name)
                for number in range(offset, offset + 3):
                    self.make_box(f"AWD-QA-1.{number:03}", planned_weekday=number)
                Box.objects.update(planned_weekday=3)
                self.assertEqual(self.query_count(name), baseline)


class PlannedWeekdayTransplantTests(PlannedWeekdayTestBase):
    def setUp(self):
        super().setUp()
        Box.objects.filter(pk=self.box.pk).update(planned_weekday=4)
        self.box.refresh_from_db()
        BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=timezone.localdate(),
            polyp_count=10,
            ephyrae_count=0,
            user=self.technician,
        )

    def revision(self):
        self.box.refresh_from_db()
        return resolve_current_polyp_state(self.box)["revision"]

    def test_service_inherits_by_default_and_opt_out_gives_null(self):
        _, (inherited, opted_out) = create_subculture(
            parent_box=self.box,
            user=self.technician,
            organization=self.org,
            expected_current_state_revision=self.revision(),
            children=[
                {"thermal_zone": self.zone, "allocated_polyps": 2},
                {
                    "thermal_zone": self.zone,
                    "allocated_polyps": 0,
                    "copy_planned_weekday": False,
                },
            ],
        )
        inherited.refresh_from_db()
        opted_out.refresh_from_db()
        self.assertEqual(inherited.planned_weekday, 4)
        self.assertIsNone(opted_out.planned_weekday)
        self.box.refresh_from_db()
        self.assertEqual(self.box.planned_weekday, 4)

    def test_api_accepts_the_opt_out_flag_and_leaves_the_parent_unchanged(self):
        self.client.force_login(self.technician)
        response = self.client.post(
            reverse("api_box_subcultures", args=[self.box.pk]),
            data={
                "expected_current_state_revision": self.revision(),
                "children": [
                    {"thermal_zone_id": self.zone.pk, "allocated_polyps": 1},
                    {
                        "thermal_zone_id": self.zone.pk,
                        "allocated_polyps": 1,
                        "copy_planned_weekday": True,
                    },
                    {
                        "thermal_zone_id": self.zone.pk,
                        "allocated_polyps": 1,
                        "copy_planned_weekday": False,
                    },
                ],
            },
            content_type="application/json",
            HTTP_X_ORGANIZATION_ID=str(self.org.pk),
        )
        self.assertEqual(response.status_code, 201, response.content)
        children = Box.objects.filter(parent_lineages__parent_box=self.box).order_by(
            "global_code"
        )
        self.assertEqual([child.planned_weekday for child in children], [4, 4, None])
        self.box.refresh_from_db()
        self.assertEqual(self.box.planned_weekday, 4)
        # The transplant is not a planned weekday edit: no weekday audit is written.
        self.assertFalse(
            AuditLog.objects.filter(
                metadata__event="box_planned_weekday_updated"
            ).exists()
        )

    def test_parent_without_planned_weekday_gives_null_children(self):
        Box.objects.filter(pk=self.box.pk).update(planned_weekday=None)
        _, (child,) = create_subculture(
            parent_box=self.box,
            user=self.technician,
            organization=self.org,
            expected_current_state_revision=self.revision(),
            children=[{"thermal_zone": self.zone, "allocated_polyps": 1}],
        )
        child.refresh_from_db()
        self.assertIsNone(child.planned_weekday)


class PlannedWeekdayDjangoAdminTests(PlannedWeekdayTestBase):
    """The generic admin never writes the planned weekday; only the audited service does."""

    def setUp(self):
        super().setUp()
        self.superuser = get_user_model().objects.create_superuser(
            username="weekday_superuser",
            email="weekday_superuser@example.org",
            password="secret",
        )
        self.request = RequestFactory().get("/")
        self.request.user = self.superuser
        self.box_admin = BoxAdmin(Box, AdminSite())

    def test_field_is_read_only_on_the_add_and_change_forms(self):
        for obj in (None, self.box):
            with self.subTest(form="change" if obj else "add"):
                self.assertIn(
                    "planned_weekday",
                    self.box_admin.get_readonly_fields(self.request, obj),
                )
                form_class = self.box_admin.get_form(self.request, obj)
                self.assertNotIn("planned_weekday", form_class.base_fields)

    def test_add_form_cannot_set_the_planned_weekday(self):
        # The admin is not routed in config/urls.py: drive its own add form and save.
        form = self.box_admin.get_form(self.request, None)(
            data={
                "organization": self.org.pk,
                "global_code": "AWD-QA-1.009",
                "local_code": "",
                "box_number": "009",
                "strain": self.strain.pk,
                "status": Box.Status.ACTIVE,
                "stop_reason": "",
                "notes": "",
                "planned_weekday": "3",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        created = form.save(commit=False)
        self.box_admin.save_model(self.request, created, form, change=False)
        created.refresh_from_db()
        self.assertIsNone(created.planned_weekday)
        self.assertFalse(
            AuditLog.objects.filter(
                metadata__event="box_planned_weekday_updated"
            ).exists()
        )

    def test_stale_change_form_save_keeps_the_newer_audited_value(self):
        set_box_planned_weekday(
            box=self.box, organization=self.org, planned_weekday=3, user=self.admin
        )
        stale = Box.objects.get(
            pk=self.box.pk
        )  # Loaded by the admin while the value is 3.
        set_box_planned_weekday(
            box=self.box, organization=self.org, planned_weekday=5, user=self.admin
        )
        stale.notes = "Edited in the Django admin"
        self.box_admin.save_model(self.request, stale, form=None, change=True)
        self.box.refresh_from_db()
        self.assertEqual(self.box.planned_weekday, 5)
        self.assertEqual(self.box.notes, "Edited in the Django admin")
        self.assertEqual(
            AuditLog.objects.filter(
                metadata__event="box_planned_weekday_updated"
            ).count(),
            2,
        )
