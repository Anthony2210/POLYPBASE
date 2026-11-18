"""The launcher must clean up plain TransactionTestCase without weakening guards."""

from unittest import skipUnless

from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase

from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain
from config.disposable_postgres import require_disposable_cluster

from .models import Box, BoxGlobalIdentity, BoxLocalIdAlias


@skipUnless(connection.vendor == "postgresql", "Requires the disposable PostgreSQL launcher")
class DisposablePostgresCleanupTests(TransactionTestCase):
    # Deliberately no flush mixin: ordinary Django transaction suites need the
    # runner-level cleanup as well as tests explicitly opting into the mixin.
    def setUp(self):
        require_disposable_cluster()
        with connection.cursor() as cursor:
            cursor.execute("SHOW session_replication_role")
            self.assertEqual(cursor.fetchone(), ("origin",))
        self.assertFalse(BoxLocalIdAlias.objects.exists())
        self.assertFalse(BoxGlobalIdentity.objects.exists())

    def create_fixture(self):
        organization = Organization.objects.create(name="Disposable cleanup laboratory")
        species = Species.objects.create(scientific_name="Disposable cleanup species")
        strain = Strain.objects.create(organization=organization, species=species, code="DCL-LAB-1")
        identity = BoxGlobalIdentity.objects.create(allocation_reason="automatic_creation")
        box = Box.objects.create(organization=organization, strain=strain,
                                 global_code="DCL-LAB-1.001", box_number="001", global_identity=identity)
        alias = BoxLocalIdAlias.objects.create(box=box, code="Retained-cleanup-alias")
        return box, alias

    def assert_guards_active(self):
        box, alias = self.create_fixture()
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM cultures_boxlocalidalias WHERE id=%s", [alias.pk])
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("UPDATE cultures_box SET global_id=NULL WHERE id=%s", [box.pk])
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("TRUNCATE measurements_historicaltemperatureimportreceipt CASCADE")
        self.assertTrue(BoxLocalIdAlias.objects.filter(pk=alias.pk).exists())
        box.refresh_from_db()
        self.assertIsNotNone(box.global_identity_id)

    def test_guards_are_active_before_standard_teardown(self):
        self.assert_guards_active()

    def test_guards_and_normal_role_survive_the_previous_standard_teardown(self):
        self.assert_guards_active()
