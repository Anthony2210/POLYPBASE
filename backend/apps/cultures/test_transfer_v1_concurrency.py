import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .api_views import BoxTransferImportAPIView
from .models import Box, BoxLocation, BoxTransferImport, ThermalZone


@skipUnless(
    connection.vendor == "postgresql",
    "Transfer import concurrency requires PostgreSQL locks and constraints.",
)
class TransferV1ConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.organization = Organization.objects.create(name="Transfer concurrency QA")
        self.zone = ThermalZone.objects.create(
            organization=self.organization,
            name="Transfer destination",
        )
        self.users = [
            get_user_model().objects.create_user(
                username=f"transfer-importer-{index}",
                email=f"transfer-importer-{index}@example.test",
            )
            for index in range(2)
        ]
        for user in self.users:
            OrganizationMembership.objects.create(
                user=user,
                organization=self.organization,
                role=OrganizationMembership.Role.ADMIN,
            )
        self.source = {
            "format": "polypbase.box_transfer.v1",
            "transfer_id": "concurrent-transfer-1",
            "source_organization_name": "Transfer source QA",
            "source_global_code": "SOURCE.001",
            "species_scientific_name": "Transfer concurrency species one",
            "strain_code": "CON-ONE",
            "strain_origin_code": "SRC",
            "transferred_polyp_count": "12",
        }

    def _post_import(self, user, source, barrier, synchronization):
        close_old_connections()
        observations = {
            "replay_selects": 0,
            "import_inserts": 0,
            "constraint_names": [],
        }
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '15s'")
                cursor.execute("SET statement_timeout = '20s'")
                cursor.execute("SELECT pg_backend_pid()")
                observations["backend_pid"] = cursor.fetchone()[0]

            def synchronize_queries(execute, sql, params, many, context):
                normalized = " ".join(sql.upper().replace('"', '').split())
                replay_select = (
                    normalized.startswith("SELECT ")
                    and " FROM CULTURES_BOXTRANSFERIMPORT " in f"{normalized} "
                )
                import_insert = normalized.startswith(
                    "INSERT INTO CULTURES_BOXTRANSFERIMPORT "
                )
                if replay_select:
                    observations["replay_selects"] += 1
                if import_insert:
                    observations["import_inserts"] += 1
                    if synchronization == "insert":
                        barrier.wait()
                try:
                    result = execute(sql, params, many, context)
                except Exception as exc:
                    # Execute wrappers run inside Django's error translation;
                    # the exception can be a raw psycopg IntegrityError.
                    diagnostic = getattr(exc, "diag", None)
                    if diagnostic is None:
                        diagnostic = getattr(exc.__cause__, "diag", None)
                    constraint_name = getattr(diagnostic, "constraint_name", None)
                    if constraint_name is not None:
                        observations["constraint_names"].append(constraint_name)
                    raise
                if (
                    synchronization == "precheck"
                    and replay_select
                    and observations["replay_selects"] == 1
                ):
                    # Both SELECT snapshots must be taken while no import exists,
                    # before either worker can proceed to the species lock.
                    barrier.wait()
                return result

            request = APIRequestFactory().post(
                "/api/box-transfer-imports/",
                {
                    "source_data": source,
                    "organization": self.organization.pk,
                    "thermal_zone": self.zone.pk,
                },
                format="json",
                HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
            )
            force_authenticate(request, user=user)
            with connection.execute_wrapper(synchronize_queries):
                response = BoxTransferImportAPIView.as_view()(request)
            response.render()

            # A translated conflict must leave the worker outside the failed
            # transaction, with its existing connection still usable.
            self.assertFalse(connection.in_atomic_block)
            self.assertFalse(connection.needs_rollback)
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1, pg_backend_pid()")
                usable, backend_pid = cursor.fetchone()
            self.assertEqual(usable, 1)
            self.assertEqual(backend_pid, observations["backend_pid"])
            self.assertEqual(BoxTransferImport.objects.count(), 1)
            return response, observations
        finally:
            connections.close_all()

    def _run_imports(self, sources, synchronization):
        barrier = threading.Barrier(2, timeout=10)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    self._post_import, user, source, barrier, synchronization
                )
                for user, source in zip(self.users, sources)
            ]
            results = [future.result(timeout=45) for future in futures]
        self.assertEqual(
            sorted(response.status_code for response, _ in results),
            [201, 400],
            [(response.status_code, response.data) for response, _ in results],
        )
        self.assertEqual(
            len({observations["backend_pid"] for _, observations in results}), 2
        )
        loser_index = next(
            index
            for index, (response, _) in enumerate(results)
            if response.status_code == 400
        )
        self.assertEqual(
            results[loser_index][0].data, ["Ce transfert a déjà été importé."]
        )
        return results, loser_index

    def _assert_single_import(self, response, source, user):
        for model in (
            Species,
            Strain,
            Box,
            BoxLocation,
            BiologicalMeasurement,
            BoxTransferImport,
            AuditLog,
        ):
            with self.subTest(model=model.__name__):
                self.assertEqual(model.objects.count(), 1)

        species = Species.objects.get()
        strain = Strain.objects.get()
        box = Box.objects.get()
        location = BoxLocation.objects.get()
        measurement = BiologicalMeasurement.objects.get()
        imported = BoxTransferImport.objects.get()
        audit = AuditLog.objects.get()
        self.assertEqual(species.scientific_name, source["species_scientific_name"])
        self.assertEqual(strain.species_id, species.pk)
        self.assertEqual(strain.code, source["strain_code"])
        self.assertEqual(strain.origin_code, source["strain_origin_code"])
        self.assertEqual(strain.organization_id, self.organization.pk)
        self.assertEqual(box.strain_id, strain.pk)
        self.assertEqual(box.organization_id, self.organization.pk)
        self.assertEqual(box.thermal_zone_id, self.zone.pk)
        self.assertEqual(response.data["id"], box.pk)
        self.assertEqual(location.box_id, box.pk)
        self.assertEqual(location.thermal_zone_id, self.zone.pk)
        self.assertIsNone(location.ends_at)
        self.assertEqual(measurement.box_id, box.pk)
        self.assertEqual(measurement.polyp_count, 12)
        self.assertEqual(measurement.ephyrae_count, 0)
        self.assertEqual(measurement.user_id, user.pk)
        self.assertEqual(imported.created_box_id, box.pk)
        self.assertEqual(imported.destination_organization_id, self.organization.pk)
        self.assertEqual(imported.imported_by_id, user.pk)
        self.assertEqual(imported.format_version, source["format"])
        self.assertEqual(imported.source_transfer_id, source["transfer_id"])
        self.assertEqual(
            imported.source_organization_name, source["source_organization_name"]
        )
        self.assertEqual(imported.source_global_code, source["source_global_code"])
        self.assertEqual(imported.source_data, source)
        self.assertEqual(audit.organization_id, self.organization.pk)
        self.assertEqual(audit.user_id, user.pk)
        self.assertEqual(audit.action, AuditLog.Action.IMPORT)
        self.assertEqual(audit.object_type, "box")
        self.assertEqual(audit.object_id, box.global_code)
        self.assertEqual(audit.metadata["transfer_import_id"], imported.pk)
        self.assertEqual(audit.metadata["created_box_id"], box.pk)

    def test_identical_imports_recheck_replay_after_species_lock(self):
        # Avoid serializing on species creation: both requests must contend on
        # the existing species row after their empty prechecks have executed.
        Species.objects.create(
            scientific_name=self.source["species_scientific_name"]
        )
        results, loser_index = self._run_imports(
            [dict(self.source), dict(self.source)], "precheck"
        )
        for _, observations in results:
            self.assertGreaterEqual(observations["replay_selects"], 2)
            self.assertEqual(observations["constraint_names"], [])
        self.assertEqual(results[loser_index][1]["import_inserts"], 0)
        winner_index = 1 - loser_index
        self.assertEqual(results[winner_index][1]["import_inserts"], 1)
        self._assert_single_import(
            results[winner_index][0], self.source, self.users[winner_index]
        )

    def test_different_species_replay_constraint_rolls_back_losing_import(self):
        # The legacy replay identity excludes species and strain. Distinct new
        # species avoid shared row locks and let both transactions reach INSERT.
        sources = [
            dict(self.source),
            {
                **self.source,
                "species_scientific_name": "Transfer concurrency species two",
                "strain_code": "CON-TWO",
                "source_global_code": "OTHER-SOURCE.001",
            },
        ]
        results, loser_index = self._run_imports(sources, "insert")
        for _, observations in results:
            self.assertGreaterEqual(observations["replay_selects"], 2)
            self.assertEqual(observations["import_inserts"], 1)
        self.assertEqual(
            results[loser_index][1]["constraint_names"],
            ["unique_imported_box_transfer"],
        )
        winner_index = 1 - loser_index
        self.assertEqual(results[winner_index][1]["constraint_names"], [])
        self._assert_single_import(
            results[winner_index][0], sources[winner_index], self.users[winner_index]
        )
        self.assertFalse(
            Species.objects.filter(
                scientific_name=sources[loser_index]["species_scientific_name"]
            ).exists()
        )
        self.assertFalse(
            Strain.objects.filter(code=sources[loser_index]["strain_code"]).exists()
        )
