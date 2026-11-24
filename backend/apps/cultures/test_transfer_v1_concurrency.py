import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase


from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import Box, BoxLocation, BoxTransferImport, ThermalZone
from .transfer_v1 import TransferV1ValidationError, import_transfer_v1


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

    def _import(self, user, source, barrier):
        close_old_connections()
        observations = {
            "replay_selects": 0,
            "import_inserts": 0,
            "constraint_names": [],
            "registry_acquisitions": 0,
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
                if normalized.startswith("SELECT PG_ADVISORY_XACT_LOCK(192837, 19)"):
                    if observations["registry_acquisitions"] == 0:
                        # Start both imports before the gate serializes their bodies.
                        barrier.wait()
                    observations["registry_acquisitions"] += 1
                if replay_select:
                    observations["replay_selects"] += 1
                if import_insert:
                    observations["import_inserts"] += 1
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

                return result

            with connection.execute_wrapper(synchronize_queries):
                try:
                    outcome = import_transfer_v1(
                        source=source, organization=self.organization, zone=self.zone, user=user,
                    )
                except TransferV1ValidationError as error:
                    outcome = error

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
            return outcome, observations
        finally:
            connections.close_all()

    def _run_imports(self, sources):
        barrier = threading.Barrier(2, timeout=10)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    self._import, user, source, barrier
                )
                for user, source in zip(self.users, sources)
            ]
            results = [future.result(timeout=45) for future in futures]
        self.assertCountEqual([type(outcome) for outcome, _ in results], [Box, TransferV1ValidationError])
        self.assertEqual(
            len({observations["backend_pid"] for _, observations in results}), 2
        )
        loser_index = next(
            index
            for index, (outcome, _) in enumerate(results)
            if isinstance(outcome, TransferV1ValidationError)
        )
        self.assertEqual(
            results[loser_index][0].detail, "Ce transfert a déjà été importé."
        )
        return results, loser_index

    def _assert_single_import(self, created_box, source, user):
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
        self.assertEqual(created_box.pk, box.pk)
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

    def test_identical_imports_serialize_before_species_and_reject_replay(self):
        # Both requests reach the registry together, before any Species row lock.
        Species.objects.create(
            scientific_name=self.source["species_scientific_name"]
        )
        results, loser_index = self._run_imports(
            [dict(self.source), dict(self.source)]
        )
        for _, observations in results:
            self.assertGreaterEqual(observations["registry_acquisitions"], 1)
            self.assertGreaterEqual(observations["replay_selects"], 1)
            self.assertEqual(observations["constraint_names"], [])
        self.assertEqual(results[loser_index][1]["import_inserts"], 0)
        winner_index = 1 - loser_index
        self.assertGreaterEqual(results[winner_index][1]["replay_selects"], 2)
        self.assertEqual(results[winner_index][1]["import_inserts"], 1)
        self._assert_single_import(
            results[winner_index][0], self.source, self.users[winner_index]
        )

    def test_different_species_replay_creates_no_losing_identity(self):
        # Replay identity excludes Species and Strain. Registry coordination must
        # reject the losing request before either of its identities is created.
        sources = [
            dict(self.source),
            {
                **self.source,
                "species_scientific_name": "Transfer concurrency species two",
                "strain_code": "CON-TWO",
                "source_global_code": "OTHER-SOURCE.001",
            },
        ]
        results, loser_index = self._run_imports(sources)
        for _, observations in results:
            self.assertGreaterEqual(observations["registry_acquisitions"], 1)
            self.assertGreaterEqual(observations["replay_selects"], 1)
            self.assertEqual(observations["constraint_names"], [])
        self.assertEqual(results[loser_index][1]["import_inserts"], 0)
        winner_index = 1 - loser_index
        self.assertGreaterEqual(results[winner_index][1]["replay_selects"], 2)
        self.assertEqual(results[winner_index][1]["import_inserts"], 1)
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
