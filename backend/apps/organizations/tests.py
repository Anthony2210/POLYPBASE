"""Tests for portable organization identity and unchanged API boundaries."""

import importlib
import uuid

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from config.migration_test_utils import MigrationExecutor, historical_targets
from django.forms import modelform_factory
from django.test import TestCase, TransactionTestCase
from django.urls import Resolver404, resolve, reverse
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient, APIRequestFactory

from apps.accounts.models import OrganizationMembership
from apps.accounts.permissions import (
    get_active_organization_from_request,
    get_authorized_organization_ids,
)

from .models import Organization, SharingAgreement
from .serializers import OrganizationCreateSerializer, OrganizationSummarySerializer


class OrganizationPortableIdentityTests(TestCase):
    def test_new_organizations_receive_distinct_uuids(self):
        first = Organization.objects.create(name="First institution")
        second = Organization.objects.create(name="Second institution")
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertIsInstance(first.portable_id, uuid.UUID)
        self.assertIsInstance(second.portable_id, uuid.UUID)
        self.assertEqual(first.portable_id.version, 4)
        self.assertNotEqual(first.portable_id, second.portable_id)

    def test_portable_id_is_unique_at_database_level(self):
        organization = Organization.objects.create(name="First institution")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Organization.objects.create(name="Duplicate identity", portable_id=organization.portable_id)

    def test_portable_id_cannot_be_null(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Organization.objects.create(name="Missing identity", portable_id=None)

    def test_rename_preserves_portable_id(self):
        organization = Organization.objects.create(name="Original institution")
        portable_id = organization.portable_id
        organization.name = "Renamed institution"
        organization.save()
        organization.refresh_from_db()
        self.assertEqual(organization.portable_id, portable_id)

    def test_slug_change_preserves_portable_id(self):
        organization = Organization.objects.create(name="Institution", slug="original")
        portable_id = organization.portable_id
        organization.slug = "renamed"
        organization.save(update_fields=["slug"])
        organization.refresh_from_db()
        self.assertEqual(organization.portable_id, portable_id)

    def test_forms_and_admin_do_not_offer_portable_id(self):
        self.assertFalse(Organization._meta.get_field("portable_id").editable)
        form = modelform_factory(Organization, fields="__all__")
        self.assertNotIn("portable_id", form.base_fields)
        request = APIRequestFactory().get("/")
        request.user = get_user_model().objects.create_superuser(
            username="platform", email="platform@example.test"
        )
        admin_form = admin.site._registry[Organization].get_form(request)
        self.assertNotIn("portable_id", admin_form.base_fields)

    def test_existing_serializers_do_not_expose_or_write_portable_id(self):
        organization = Organization.objects.create(name="Institution")
        portable_id = organization.portable_id
        for serializer_class in (OrganizationCreateSerializer, OrganizationSummarySerializer):
            with self.subTest(serializer=serializer_class.__name__):
                serializer = serializer_class(
                    organization, data={"portable_id": str(uuid.uuid4())}, partial=True
                )
                self.assertTrue(serializer.is_valid(), serializer.errors)
                self.assertNotIn("portable_id", serializer.validated_data)
                serializer.save()
                self.assertNotIn("portable_id", serializer.data)
        organization.refresh_from_db()
        self.assertEqual(organization.portable_id, portable_id)


class OrganizationPortableIdentityApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.superuser = get_user_model().objects.create_superuser(
            username="platform", email="platform@example.test"
        )
        self.organization = Organization.objects.create(name="Institution")
        self.other = Organization.objects.create(name="Other institution")
        self.member = get_user_model().objects.create_user(
            username="member", email="member@example.test"
        )
        OrganizationMembership.objects.create(
            user=self.member, organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )

    def test_legacy_organization_create_route_is_unregistered(self):
        with self.assertRaises(Resolver404):
            resolve("/api/organizations/")

    def test_organization_detail_is_read_only_for_superusers(self):
        self.client.force_authenticate(self.superuser)
        url = reverse("api_organization_detail", args=[self.organization.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(set(response.data), {
            "id", "name", "city", "country", "contact_email", "notes",
        })
        self.assertEqual(response.data["id"], self.organization.pk)
        portable_id = self.organization.portable_id
        for method in (self.client.post, self.client.patch, self.client.put, self.client.delete):
            with self.subTest(method=method.__name__):
                response = method(url, {"name": "Renamed institution"}, format="json")
                self.assertEqual(response.status_code, 405)
        self.organization.refresh_from_db()
        self.assertEqual((self.organization.name, self.organization.portable_id), ("Institution", portable_id))

    def test_membership_admin_cannot_read_organization_detail(self):
        self.client.force_authenticate(self.member)
        for organization in (self.organization, self.other):
            with self.subTest(organization=organization.pk):
                response = self.client.get(reverse("api_organization_detail", args=[organization.pk]))
                self.assertEqual(response.status_code, 403)

    def test_active_context_remains_integer_and_membership_scoped(self):
        self.assertEqual(get_authorized_organization_ids(self.member), [self.organization.pk])
        factory = APIRequestFactory()
        request = factory.get("/", HTTP_X_ORGANIZATION_ID=str(self.organization.pk))
        request.user = self.member
        self.assertEqual(get_active_organization_from_request(request), self.organization)
        for identity in (self.other.pk, self.organization.portable_id, self.other.portable_id):
            with self.subTest(identity=identity):
                request = factory.get("/", HTTP_X_ORGANIZATION_ID=str(identity))
                request.user = self.member
                with self.assertRaises(PermissionDenied):
                    get_active_organization_from_request(request)


class OrganizationPortableIdentityMigrationTests(TransactionTestCase):
    def test_existing_rows_keep_primary_keys_and_relationships(self):
        before = ("organizations", "0001_initial")

        portable = ("organizations", "0002_organization_portable_id")

        executor = MigrationExecutor(connection)
        graph = executor.loader.graph
        latest = graph.leaf_nodes()
        historical_apps = {"organizations", "cultures", "measurements"}
        historical = [
            before, ("cultures", "0014_box_movement_week_matches_moved_at"),
            ("measurements", "0013_historical_temperature_receipt"),
        ]
        before_targets = historical_targets(executor.loader, historical_apps, historical)
        allowed = {node for target in historical for node in graph.forwards_plan(target)}
        for leaf in latest:
            if leaf[0] in historical_apps or leaf in before_targets:
                continue
            # Keep dependent apps at their last compatible schema rather than
            # dropping their tables or pulling newer pinned apps back in.
            compatible = [
                node for node in graph.forwards_plan(leaf)
                if node[0] == leaf[0] and {
                    dependency for dependency in graph.forwards_plan(node)
                    if dependency[0] in historical_apps
                } <= allowed
            ]
            if compatible:
                before_targets.append(compatible[-1])
        projected = {node for target in before_targets for node in graph.forwards_plan(target)}
        self.assertNotIn(portable, projected)
        self.assertEqual({node for node in projected if node[0] in historical_apps},
                         {node for node in allowed if node[0] in historical_apps})
        try:
            executor.migrate(before_targets)
            old_apps = executor.loader.project_state(before_targets).apps
            OldOrganization = old_apps.get_model("organizations", "Organization")
            OldMembership = old_apps.get_model("accounts", "OrganizationMembership")
            OldAgreement = old_apps.get_model("organizations", "SharingAgreement")
            user = get_user_model().objects.create_user(
                username="legacy-member", email="legacy-member@example.test"
            )
            first = OldOrganization.objects.create(name="Historical institution", slug="historical")
            second = OldOrganization.objects.create(name="Other historical institution")
            membership = OldMembership.objects.create(user_id=user.pk, organization_id=first.pk)
            agreement = OldAgreement.objects.create(
                owner_organization_id=first.pk, partner_organization_id=second.pk
            )
            original_values = list(OldOrganization.objects.order_by("pk").values())

            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            new_apps = executor.loader.project_state(latest).apps
            NewOrganization = new_apps.get_model("organizations", "Organization")
            rows = list(NewOrganization.objects.order_by("pk").values())
            identities = [row.pop("portable_id") for row in rows]
            self.assertEqual(rows, original_values)
            self.assertEqual(len(set(identities)), 2)
            self.assertTrue(all(isinstance(value, uuid.UUID) and value.version == 4 for value in identities))
            self.assertFalse(NewOrganization.objects.filter(portable_id__isnull=True).exists())
            self.assertEqual(OrganizationMembership.objects.get(pk=membership.pk).organization_id, first.pk)
            restored_agreement = SharingAgreement.objects.get(pk=agreement.pk)
            self.assertEqual(restored_agreement.owner_organization_id, first.pk)
            self.assertEqual(restored_agreement.partner_organization_id, second.pk)

            migration = importlib.import_module(
                "apps.organizations.migrations.0002_organization_portable_id"
            )
            with connection.schema_editor() as schema_editor:
                migration.populate_portable_ids(new_apps, schema_editor)
            self.assertEqual(
                list(NewOrganization.objects.order_by("pk").values_list("portable_id", flat=True)),
                identities,
            )

        finally:
            MigrationExecutor(connection).migrate(latest)
