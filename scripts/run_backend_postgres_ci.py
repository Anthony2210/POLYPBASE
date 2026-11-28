"""Run the live backend PostgreSQL regressions on the hosted Actions service."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys

from validate_historical_movements_ci import schema_findings
from validate_history_revision_ci import TEST_LABELS as HISTORY_LABELS, installed_schema


ROOT = Path(__file__).resolve().parents[1]
SETTINGS = "config.backend_postgres_ci_settings"
SPECIES_LABELS = (
    "apps.taxonomy.test_species_resolution_retirement",
    "apps.taxonomy.test_existing_species_actions",
)
MOVEMENT_LABELS = (
    "apps.cultures.test_historical_movements_postgres_ci",
    "apps.cultures.test_history_audit_lookup",
    "apps.cultures.test_box_detail_organization_scoping",
)
BOX_LABELS = ("apps.cultures.test_box_1902_correction_v2_postgres",)
SPECIES_REQUIRED = {
    f"{SPECIES_LABELS[0]}.RetiredResolutionTests.test_retired_resolve_never_creates_profile_code_or_audit",
    *(
        f"{SPECIES_LABELS[1]}.ExistingSpeciesConcurrencyTests.{method}"
        for method in (
            "test_concurrent_first_association_creates_one_profile_and_one_registration",
            "test_concurrent_retries_of_one_request_create_one_strain",
            "test_concurrent_edit_and_strain_creation_never_overwrite_or_duplicate",
        )
    ),
}
BOX_REQUIRED = {
    f"{BOX_LABELS[0]}.Box1902CorrectionV2PostgreSQLTests.{method}"
    for method in (
        "test_installation_on_populated_history_rejects_runtime_owner_then_preserves_rows",
        "test_exact_correction_preserves_all_41_confirmations",
    )
}
SQLITE_ONLY = (
    "apps.cultures.test_history_audit_lookup.HistoryAuditLookupTests."
    "test_synthetic_before_after_sqlite_full_identity_index_plans"
)


def prepare_environment():
    # Keep the service identity for repeated checks, but never import settings
    # with inherited application configuration, credentials or opt-ins.
    from config.backend_postgres_ci import SERVICE_ENV

    for name in list(os.environ):
        if name not in SERVICE_ENV and name.upper().startswith(
            (
                "DJANGO_",
                "EMAIL_",
                "DEMO_",
                "POLYPBASE_",
                "IMINILIDE_",
                "BOX_SHARING_",
                "PUBLIC_BASE_URL",
            )
        ):
            del os.environ[name]
    os.environ.update(
        DJANGO_SETTINGS_MODULE=SETTINGS,
        PYTHON_DOTENV_DISABLED="1",
        POLYPBASE_TEST_POSTGRES="0",
        POLYPBASE_DEMO="0",
        EMAIL_DELIVERY_ENABLED="0",
    )
    sys.argv.append(
        "test"
    )  # Application settings distinguish test from production mode.


def write_evidence(directory, name, data):
    (directory / f"{name}.json").write_text(
        json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8"
    )


@contextmanager
def database(service, suffix, *, owner="postgres", template=None):
    from django.db import connections

    name = service.prefix + suffix
    service.create_database(name, owner=owner, template=template)
    try:
        yield name
    finally:
        connections.close_all()
        service.drop_database(name)


def migrate_full_graph(service, name):
    from django.core.management import call_command
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    from apps.measurements import box_1902_sql as contract
    from config.migration_test_utils import historical_targets

    service.use_django_database(name, contract.RUNTIME)
    executor = MigrationExecutor(connection)
    executor.migrate(
        historical_targets(
            executor.loader,
            {"measurements"},
            [
                ("measurements", "0017_bucket_probe_last_measured_index"),
            ],
        )
    )
    connection.close()
    service.provision_box_roles(name)
    service.use_django_database(name, contract.MIGRATOR, role=contract.OWNER)
    call_command("migrate", interactive=False, verbosity=1)
    contract.attest(connection, allow_retired=False)
    service.use_django_database(name)


def rehearse_history(evidence_dir):
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.recorder import MigrationRecorder

    from config.backend_postgres_ci import require
    from config.history_revision_ci_migrations import APPROVED, TARGETS
    from validate_history_revision_ci import M19, M20, MERGE

    def plan(executor, targets):
        return [
            ((migration.app_label, migration.name), backwards)
            for migration, backwards in executor.migration_plan(targets)
        ]

    def applied():
        return set(MigrationRecorder(connection).applied_migrations())

    executor = MigrationExecutor(connection)
    forward = plan(executor, TARGETS)
    require(
        len(forward) == len(APPROVED)
        and set(forward) == {(node, False) for node in APPROVED},
        "The historical rehearsal must apply exactly the approved closure, not later descendants.",
    )
    executor.migrate(TARGETS)
    require(applied() == APPROVED, "Unexpected initial historical migration state.")
    initial = installed_schema(connection, guarded=True)
    write_evidence(evidence_dir, "history-schema-initial", initial)

    executor = MigrationExecutor(connection)
    require(
        plan(executor, [M19]) == [(MERGE, True), (M20, True)],
        "History reversal must remove only the empty merge and the guard migration.",
    )
    merge = executor.loader.disk_migrations[MERGE]
    require(
        not merge.operations and not merge.run_before and not merge.replaces,
        "The history merge must remain empty and independent of later migrations.",
    )
    executor.migrate([M19])
    require(
        applied() == APPROVED - {MERGE, M20},
        "History reversal modified storage migrations.",
    )
    write_evidence(
        evidence_dir,
        "history-schema-reversed",
        installed_schema(connection, guarded=False),
    )

    executor = MigrationExecutor(connection)
    require(
        plan(executor, TARGETS) == [(M20, False), (MERGE, False)],
        "Unexpected history reapplication plan.",
    )
    executor.migrate(TARGETS)
    restored = installed_schema(connection, guarded=True)
    require(
        applied() == APPROVED and restored == initial,
        "History reapplication did not restore the catalogs.",
    )
    write_evidence(evidence_dir, "history-schema-restored", restored)


def test_suite(
    service, labels, *, template=None, suffix=None, required=(), allowed_skips=()
):
    from django.core.management import call_command

    service.test_template = template
    service.required_tests = set(required)
    service.allowed_skips = set(allowed_skips)
    print(f"\nPostgreSQL regression suite: {', '.join(labels)}", flush=True)
    if template is None:
        call_command("test", *labels, interactive=False, parallel=1, verbosity=2)
        return
    try:
        with database(service, suffix, template=template) as name:
            service.test_database = name
            service.use_django_database(name)
            call_command("test", *labels, interactive=False, parallel=1, verbosity=2)
    finally:
        service.test_template = None


def run(service, evidence_dir):
    import django
    from django.db import connections

    from config.history_revision_ci_coverage import REQUIRED_TEST_IDS

    with database(service, "bootstrap") as bootstrap:
        django.setup()
        try:
            # Rehearse the historical merge in isolation. Runtime tests below
            # use the current full schema, including every later migration.
            with database(service, "history_rehearsal") as name:
                service.use_django_database(name)
                rehearse_history(evidence_dir)
            service.create_runtime_role()
            with database(service, "full_template", owner="polypbase") as template:
                migrate_full_graph(service, template)
                write_evidence(
                    evidence_dir,
                    "historical-movements-schema",
                    schema_findings(connections["default"]),
                )
                connections.close_all()
                test_suite(
                    service,
                    SPECIES_LABELS,
                    template=template,
                    suffix="species",
                    required=SPECIES_REQUIRED,
                )
                test_suite(
                    service,
                    MOVEMENT_LABELS,
                    template=template,
                    suffix="movements",
                    allowed_skips=[SQLITE_ONLY],
                )
                test_suite(
                    service,
                    HISTORY_LABELS,
                    template=template,
                    suffix="revisions",
                    required=REQUIRED_TEST_IDS,
                )
            service.drop_roles()
            service.use_django_database(bootstrap)
            test_suite(service, BOX_LABELS, required=BOX_REQUIRED)
        finally:
            connections.close_all()


def main():
    if len(sys.argv) != 1:
        raise SystemExit("This runner accepts no database overrides or test labels.")
    sys.path.insert(0, str(ROOT / "backend"))
    from config.backend_postgres_ci import Service, check_environment

    check_environment()  # Before importing Django or psycopg.
    prepare_environment()
    evidence_dir = Path(os.environ["RUNNER_TEMP"]) / "backend-postgres-evidence"
    evidence_dir.mkdir(exist_ok=False)
    os.environ["HR_CI_EVIDENCE_DIR"] = str(evidence_dir)
    os.environ["HISTORICAL_MOVEMENTS_EVIDENCE_DIR"] = str(evidence_dir)
    with Service() as service:
        run(service, evidence_dir)


if __name__ == "__main__":
    main()
