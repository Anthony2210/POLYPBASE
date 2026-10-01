"""Real row-lock regressions; run only against an isolated PostgreSQL test DB."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.db.models import F
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.organizations.models import Organization

from .api_views import StrainReferenceDetailAPIView
from .models import LocalStrainIdentity, OrganizationSpeciesCode, Species, Strain
from .services import create_local_strain_identity


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row locks are required")
class StrainSpeciesConcurrencyTests(TransactionTestCase):
    wait_timeout = 10

    def setUp(self):
        self.organization = Organization.objects.create(name="Strain species concurrency QA")
        self.admin = get_user_model().objects.create_user(
            username="strain-concurrency-admin",
            email="strain-concurrency-admin@example.org",
        )
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        self.species_one = Species.objects.create(scientific_name="Concurrency species one")
        self.species_two = Species.objects.create(scientific_name="Concurrency species two")
        self.assignment = OrganizationSpeciesCode.objects.create(
            organization=self.organization, species=self.species_one, code="AAA"
        )
        self.strain = Strain.objects.create(
            organization=self.organization,
            species=self.species_one,
            code="ISSUED-CONCURRENT",
        )

    def _patch_species(self):
        request = APIRequestFactory().patch(
            reverse("api_taxonomy_strains_detail", args=[self.strain.pk]),
            {"species": self.species_two.pk},
            format="json",
            HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
        )
        force_authenticate(request, user=self.admin)
        response = StrainReferenceDetailAPIView.as_view()(request, pk=self.strain.pk)
        response.render()
        return response

    def _create_identity(self):
        # Keep the S1 instances supplied by setUp: the service must re-read after locking.
        try:
            identity = create_local_strain_identity(
                strain=self.strain,
                organization=self.organization,
                species_code_assignment=self.assignment,
            )
        except ValidationError as error:
            return error.code
        return identity.pk

    def _run_in_lock_order(self, first_operation, second_operation):
        first_locked = Event()
        second_attempting = Event()
        release_first = Event()
        backend_pids = {}
        strain_table = connection.ops.quote_name(Strain._meta.db_table)

        def worker(operation, *, first):
            close_old_connections()
            try:
                # SQL timeouts also bound executor shutdown if a locking regression occurs.
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '15s'")
                    cursor.execute("SET statement_timeout = '20s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    backend_pids[first] = cursor.fetchone()[0]

                intercepted = False

                def synchronize_lock(execute, sql, params, many, context):
                    nonlocal intercepted
                    is_strain_lock = (
                        not intercepted
                        and sql.lstrip().upper().startswith("SELECT")
                        and f"FROM {strain_table}" in sql
                        and "FOR UPDATE" in sql.upper()
                    )
                    if not is_strain_lock:
                        return execute(sql, params, many, context)
                    intercepted = True
                    if not first:
                        second_attempting.set()
                        return execute(sql, params, many, context)

                    result = execute(sql, params, many, context)
                    # The SELECT has acquired the lock, but its atomic block is still open.
                    first_locked.set()
                    if not release_first.wait(timeout=self.wait_timeout):
                        raise AssertionError("Timed out waiting to release the first strain lock")
                    return result

                with connection.execute_wrapper(synchronize_lock):
                    return operation()
            finally:
                connections.close_all()

        def wait_for_event(event, future, message):
            if not event.wait(timeout=self.wait_timeout):
                if future.done():
                    future.result()  # Surface worker exceptions instead of hiding them.
                self.fail(message)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(worker, first_operation, first=True)
            try:
                wait_for_event(
                    first_locked, first_future,
                    "The first operation never acquired a Strain SELECT FOR UPDATE lock",
                )
                second_future = executor.submit(worker, second_operation, first=False)
                wait_for_event(
                    second_attempting, second_future,
                    "The second operation never attempted a Strain SELECT FOR UPDATE lock",
                )
                # An event before execute alone does not prove contention. Observe the
                # real database wait before allowing the first transaction to commit.
                deadline = monotonic() + self.wait_timeout
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT %s = ANY(pg_blocking_pids(%s))",
                            [backend_pids[True], backend_pids[False]],
                        )
                        blocked = cursor.fetchone()[0]
                    if blocked:
                        break
                    if second_future.done():
                        second_future.result()
                        self.fail("The second operation completed without waiting for the strain lock")
                    second_attempting.clear()
                    second_attempting.wait(timeout=0.02)
                else:
                    self.fail("PostgreSQL did not report the second worker blocked by the first")
            finally:
                release_first.set()
            return first_future.result(timeout=25), second_future.result(timeout=25)

    def _assert_no_species_mismatch(self):
        self.assertFalse(
            LocalStrainIdentity.objects.filter(strain=self.strain).exclude(
                species_code_assignment__species_id=F("strain__species_id")
            ).exists()
        )

    def test_identity_first_rejects_species_patch_without_audit(self):
        identity_pk, response = self._run_in_lock_order(
            self._create_identity, self._patch_species
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("species", response.data)
        self.strain.refresh_from_db()
        self.assertEqual(self.strain.species_id, self.species_one.pk)
        identity = LocalStrainIdentity.objects.get(strain=self.strain)
        self.assertEqual(identity.pk, identity_pk)
        self.assertEqual(identity.species_code_assignment_id, self.assignment.pk)
        self.assertFalse(AuditLog.objects.exists())
        self._assert_no_species_mismatch()

    def test_patch_first_rejects_stale_species_assignment_and_audits_patch(self):
        response, identity_result = self._run_in_lock_order(
            self._patch_species, self._create_identity
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(identity_result, "species_mismatch")
        self.strain.refresh_from_db()
        self.assertEqual(self.strain.species_id, self.species_two.pk)
        self.assertFalse(LocalStrainIdentity.objects.filter(strain=self.strain).exists())
        self.assertEqual(AuditLog.objects.count(), 1)
        audit = AuditLog.objects.get()
        self.assertEqual(audit.action, AuditLog.Action.UPDATE)
        self.assertEqual(audit.object_type, "strain")
        self.assertEqual(audit.object_id, str(self.strain.pk))
        self.assertEqual(audit.organization_id, self.organization.pk)
        self.assertEqual(audit.user_id, self.admin.pk)
        self._assert_no_species_mismatch()
