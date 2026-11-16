"""Fail-closed settings for the disposable history-revision Actions service only."""

from contextlib import ExitStack, contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured


REQUIRED_ENVIRONMENT = {
    "GITHUB_ACTIONS": "true",
    "RUNNER_ENVIRONMENT": "github-hosted",
    "RUNNER_OS": "Linux",
    "GITHUB_EVENT_NAME": "push",
    "GITHUB_REF": "refs/heads/test/history-revision-postgres-qa",
    "HR_CI_POSTGRES": "1",
    "PYTHON_DOTENV_DISABLED": "1",
    "POLYPBASE_TEST_POSTGRES": "0",
    "POSTGRES_DB": "",
}
DATABASE_NAME = "history_revision_ci"
TEST_DATABASE_NAME = "test_history_revision_ci"
SERVICE_MARKER = "history-revision-actions-disposable"


def check_environment():
    for name, expected in REQUIRED_ENVIRONMENT.items():
        if os.environ.get(name) != expected:
            raise ImproperlyConfigured(f"History revision CI requires the dedicated workflow value for {name}.")
    if (Path(__file__).resolve().parent.parent / ".env").exists():
        raise ImproperlyConfigured("History revision CI refuses backend/.env before importing application settings.")
    for name, value in os.environ.items():
        if value and (name == "DATABASE_URL" or name.startswith(("POSTGRES_", "PG", "DB_"))):
            # Never echo inherited values, which may contain credentials.
            raise ImproperlyConfigured(f"History revision CI refuses inherited database configuration: {name}.")


check_environment()

from .test_settings import *  # noqa: E402,F403
from django.db import connections  # noqa: E402
from django.test.runner import DiscoverRunner  # noqa: E402


# Trust authentication is allowed only on the disposable hosted runner service.
_DATABASE_CONFIG = {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": DATABASE_NAME,
    "USER": DATABASE_NAME,
    "PASSWORD": "",
    "HOST": "127.0.0.1",
    "PORT": "55440",
    "TEST": {"NAME": TEST_DATABASE_NAME},
    "OPTIONS": {
        "connect_timeout": 5,
        "options": "-c lock_timeout=5000 -c statement_timeout=20000 -c idle_in_transaction_session_timeout=20000",
    },
    "CONN_MAX_AGE": 0,
}
DATABASES = {"default": deepcopy(_DATABASE_CONFIG)}
DATABASE_ROUTERS = []
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
EMAIL_DELIVERY_ENABLED = False
TEST_RUNNER = "config.history_revision_ci_settings.HistoryRevisionCIRunner"


def check_database_config(connection, *, test_database=False):
    """Check the endpoint before connecting, including every libpq option."""
    check_environment()
    if connection.vendor != "postgresql":
        raise ImproperlyConfigured("History revision CI requires the PostgreSQL backend.")
    expected = deepcopy(_DATABASE_CONFIG)
    expected["NAME"] = TEST_DATABASE_NAME if test_database else DATABASE_NAME
    for name, value in expected.items():
        if name == "TEST":
            continue
        if connection.settings_dict.get(name) != value:
            raise ImproperlyConfigured(f"History revision CI refuses unexpected database {name}.")
    test = connection.settings_dict.get("TEST", {})
    allowed_test = {
        "NAME": TEST_DATABASE_NAME, "CHARSET": None, "COLLATION": None,
        "MIGRATE": True, "MIRROR": None,
    }
    if test.get("NAME") != TEST_DATABASE_NAME or any(
        name not in allowed_test or value != allowed_test[name] for name, value in test.items()
    ):
        raise ImproperlyConfigured("History revision CI refuses alternate test database configuration.")


def server_identity(connection, *, test_database=False):
    """Validate identity read-only; the preflight alone provisions the base marker."""
    check_database_config(connection, test_database=test_database)
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT current_database(), current_user, session_user,
                   inet_server_port(), current_setting('server_version_num')::integer,
                   shobj_description(oid, 'pg_database')
            FROM pg_catalog.pg_database WHERE datname = current_database()
        """)
        row = cursor.fetchone()
    expected_name = TEST_DATABASE_NAME if test_database else DATABASE_NAME
    if not row or len(row) != 6:
        raise ImproperlyConfigured("History revision CI could not verify server identity.")
    database, user, session_user, port, version, marker = row
    # Docker publishes 55440 on the host; PostgreSQL itself listens on 5432.
    if (database, user, session_user, port) != (expected_name, DATABASE_NAME, DATABASE_NAME, 5432):
        raise ImproperlyConfigured("History revision CI server database, role or service port does not match.")
    if not isinstance(version, int) or not 180000 <= version < 190000:
        raise ImproperlyConfigured("History revision CI requires PostgreSQL 18.")
    if not test_database and marker != SERVICE_MARKER:
        raise ImproperlyConfigured("History revision CI requires the preflight disposable-service marker.")
    return {"database": database, "user": user, "server_port": port, "server_version_num": version}


# Frozen coverage contract: update deliberately, never infer it during collection.
REQUIRED_TEST_IDS = frozenset(
    f"{class_name}.{method}"
    for class_name, methods in (
        ("apps.cultures.test_history_revision_models.HistoryRevisionStorageTests", (
            "test_creation_is_inert_and_snapshots_fresh_author",
            "test_author_must_be_stored_and_currently_active",
            "test_orm_immutability",
            "test_sql_guards_cover_every_immutable_column_and_delete",
            "test_box_and_author_deletion_preserve_storage",
            "test_staged_chain_head_advances_only_one_step_in_same_stream",
            "test_normal_head_advancement_and_no_rollback",
            "test_chain_validation_sql_and_unique_keys",
            "test_scope_immutable_in_sql_and_orm",
            "test_identity_shapes_and_source_uniqueness",
            "test_derived_identity_key_is_canonical_server_computed_and_scoped",
            "test_derived_identity_sql_uniqueness_and_key_shape",
            "test_raw_sql_head_recovers_through_preinserted_successors",
            "test_conservative_json_validation",
            "test_detachment_with_unpublished_successor_and_no_reattachment",
            "test_staged_root_can_initialize_without_skipping_and_insert_scope_is_guarded",
            "test_author_nulling_is_the_only_permitted_sql_update",
            "test_exact_and_uncertain_per_event_snapshots_round_trip",
            "test_snapshot_temporal_and_source_shape_rejections",
            "test_phase_three_temporal_rejections_prevent_storage",
            "test_paris_dst_week_and_equivalent_utc_bounds_are_preserved",
            "test_locator_preserves_names_but_rejects_credentials_and_untyped_values",
            "test_phase_four_operations_with_backend_identity_keys",
            "test_operation_shape_and_uuid_rejections",
            "test_operation_limit_in_model_and_database",
            "test_justifications_default_and_required_for_insertions",
            "test_justification_exact_shape_indices_and_reason_validation",
            "test_justification_limit_and_technical_byte_cap",
            "test_sql_justification_container_and_count_guards",
            "test_detached_stream_still_reserves_original_box_identity",
            "test_new_stream_requires_live_box_in_model_and_database",
            "test_guards_are_installed_and_reversible",
            "test_postgresql_guard_generation_without_connection",
        )),
        ("apps.cultures.test_history_revision_codec.HistoryRevisionCodecTests", (
            "test_all_five_operations_roundtrip_without_collapsing_unset_and_null",
            "test_all_temporal_precisions_and_event_kinds_roundtrip",
            "test_minimal_refs_preserve_source_roles_and_categories_not_thermal_values",
            "test_codec_and_composition_leave_zero_negative_null_and_all_raw_evidence_intact",
            "test_missing_optional_snapshot_and_ref_fields_decode_to_typed_defaults",
            "test_unknown_fields_are_rejected_at_every_boundary",
            "test_malformed_shapes_scalars_kinds_dates_fingerprints_and_refs_are_rejected",
            "test_uncertain_temporal_shapes_are_validated_not_coerced",
            "test_reasons_must_reference_unique_operation_indices_and_insertion_requires_reason",
            "test_durable_identifiers_require_canonical_uuid_strings",
            "test_review_targets_and_local_chain_shape_are_strict",
            "test_decode_and_reencode_do_not_share_mutable_json_with_caller",
        )),
        ("apps.cultures.test_history_revision_codec.HistoryRevisionCodecStorageTests", (
            "test_real_source_r1_r2_r3_store_decode_compose_and_preview_never_reads_overlay",
            "test_registry_assigned_insert_identity_survives_storage_and_later_review",
        )),
        ("apps.cultures.test_history_revision_composition.HistoryRevisionCompositionTests", (
            "test_empty_composition_keeps_source_and_engine_baseline_without_publication_policy",
            "test_r1_r2_r3_restore_is_a_new_attributed_decision_not_erasure",
            "test_review_captures_previous_effective_values_and_original_content_fingerprint",
            "test_fingerprint_hashes_original_complete_content_deterministically",
            "test_candidate_is_separate_from_source_and_published_history_and_thermal",
            "test_candidate_can_be_first_revision",
            "test_inserted_backend_identity_can_be_reviewed_and_corrected_later",
            "test_browser_and_noncanonical_insertion_ids_are_rejected",
            "test_insertions_require_phase3_justification",
            "test_phase3_rejects_malformed_operations_and_justifications",
            "test_canonical_anchor_identity_survives_multiple_revisions_and_derived_key_changes",
            "test_changed_anchor_evidence_rejects_composition",
            "test_orphan_anchor_rejects_composition",
            "test_ambiguous_derived_evidence_rejects_composition",
            "test_two_anchors_cannot_claim_one_derived_event",
            "test_anchor_locator_mismatch_rejects_composition",
            "test_anchor_ids_types_duplicates_and_foreign_scopes_are_rejected",
            "test_persisted_event_origin_sources_and_original_references_are_preserved",
            "test_invalid_source_and_scope_are_rejected",
            "test_non_revision_objects_are_rejected",
            "test_revision_and_predecessor_ids_must_be_canonical_uuid_strings",
            "test_revision_chain_rejects_gaps_reordering_duplicate_ids_and_wrong_predecessors",
            "test_candidate_must_be_the_next_revision_of_the_complete_chain",
            "test_foreign_revision_and_candidate_scopes_are_rejected",
            "test_thermal_source_foreign_org_box_or_zone_scope_is_rejected",
            "test_each_noninsert_target_requires_exactly_one_review",
            "test_multiple_operations_on_one_target_share_review_and_keep_full_operation_indices",
            "test_review_of_original_values_is_stale_after_previous_revision",
            "test_changed_review_fields_and_original_source_content_are_stale",
            "test_missing_target_with_review_rejects_whole_revision_atomically",
            "test_engine_rejection_rejects_whole_revision_and_candidate_atomically",
            "test_noop_is_explicit_rejection_not_a_silent_decision",
            "test_exact_and_uncertain_positions_are_not_coerced_across_revisions",
            "test_dst_fold_edit_and_restore_preserve_elapsed_instants_and_thermal_assignment",
            "test_zero_negative_null_and_all_temperature_source_categories_stay_distinct",
            "test_provisional_scientific_status_survives_restore_and_unrelated_revision",
            "test_semantic_conflicts_are_reported_not_used_as_publication_policy",
            "test_each_revision_allows_100_operations_without_lifetime_100_operation_cap",
            "test_more_than_100_revisions_preserve_every_decision_without_truncation",
            "test_101_operations_in_one_revision_fail_even_when_an_earlier_revision_is_valid",
            "test_inputs_remain_immutable_on_success_failure_and_repeated_composition",
            "test_composition_reuses_existing_project_and_provisional_engines_with_effective_fold",
            "test_history_budget_fails_explicitly_before_any_engine_projection",
            "test_insertions_count_toward_history_budget_before_revision_projection",
            "test_thermal_overlap_budget_fails_before_history_or_thermal_allocation",
            "test_published_thermal_attribution_budget_fails_explicitly_before_projection",
            "test_candidate_thermal_attribution_budget_is_checked_independently",
        )),
        ("apps.cultures.test_history_revision_postgres_ci.HistoryRevisionPostgresCITests", (
            "test_concurrent_successors_commit_exactly_one_and_persist_its_head",
            "test_failed_first_successor_releases_unique_slots_for_waiting_writer",
            "test_concurrent_head_updates_advance_against_committed_predecessor",
            "test_stale_head_update_cannot_rewind_after_waiting_for_commit",
            "test_failure_rolls_back_registry_revision_and_head_then_allows_retry",
            "test_deferred_revision_stream_fk_fails_at_commit_and_rolls_back_other_writes",
            "test_deferred_identity_fk_can_be_repaired_before_commit",
            "test_deferred_fk_does_not_defer_structural_predecessor_guard",
        )),
        ("apps.cultures.test_history_revision_postgres_ci.HistoryRevisionSourcePreservationPostgresCITests", (
            "test_committed_revision_replay_preserves_scientific_and_operational_sources",
        )),
    )
    for method in methods
)


class HistoryRevisionCIRunner(DiscoverRunner):
    """Guard creation, migrations, test connections and teardown against DB drift."""

    def build_suite(self, test_labels=None, **kwargs):
        self._history_revision_collected_ids = frozenset()
        suite = super().build_suite(test_labels, **kwargs)

        def cases(tests):
            for test in tests:
                if isinstance(test, unittest.TestSuite):
                    yield from cases(test)
                else:
                    yield test

        collected = list(cases(suite))
        identities = frozenset(test.id() for test in collected)
        missing = REQUIRED_TEST_IDS - identities
        if self.test_loader.errors or any(
            isinstance(test, unittest.loader._FailedTest) for test in collected
        ) or missing:
            raise ImproperlyConfigured(
                "History revision CI collection failed; missing required tests: "
                + ", ".join(sorted(missing))
            )
        self._history_revision_collected_ids = identities
        return suite

    def get_test_runner_kwargs(self):
        kwargs = super().get_test_runner_kwargs()
        base_result = kwargs.get("resultclass") or self.test_runner.resultclass

        class IdentityResult(base_result):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.history_revision_started_ids = set()
                self.history_revision_stopped_ids = set()
                self.history_revision_successful_ids = set()

            def startTest(self, test):
                self.history_revision_started_ids.add(test.id())
                super().startTest(test)

            def stopTest(self, test):
                super().stopTest(test)
                self.history_revision_stopped_ids.add(test.id())

            def addSuccess(self, test):
                super().addSuccess(test)
                self.history_revision_successful_ids.add(test.id())

        kwargs["resultclass"] = IdentityResult
        return kwargs

    def suite_result(self, suite, result, **kwargs):
        counts = {
            "run": result.testsRun,
            "failed": len(result.failures),
            "errors": len(result.errors),
            "skipped": len(result.skipped),
            "expectedFailures": len(result.expectedFailures),
            "unexpectedSuccesses": len(result.unexpectedSuccesses),
        }
        counts["passed"] = max(0, counts["run"] - sum(
            counts[name] for name in ("failed", "errors", "skipped", "expectedFailures", "unexpectedSuccesses")
        ))
        collected = getattr(self, "_history_revision_collected_ids", frozenset())
        started = getattr(result, "history_revision_started_ids", set())
        stopped = getattr(result, "history_revision_stopped_ids", set())
        successful = getattr(result, "history_revision_successful_ids", set())
        missing_collected = REQUIRED_TEST_IDS - collected
        missing_executed = REQUIRED_TEST_IDS - (started & stopped)
        missing_successful = REQUIRED_TEST_IDS - successful
        identity_failure = bool(
            missing_collected or missing_executed or missing_successful
            or collected != started or collected != stopped or collected != successful
            or result.testsRun != len(collected)
        )
        directory = os.environ.get("HR_CI_EVIDENCE_DIR")
        if directory:
            evidence = {
                **counts,
                "collected_test_ids": sorted(collected),
                "started_test_ids": sorted(started),
                "stopped_test_ids": sorted(stopped),
                "successful_test_ids": sorted(successful),
                "missing_collected_test_ids": sorted(missing_collected),
                "missing_executed_test_ids": sorted(missing_executed),
                "missing_successful_test_ids": sorted(missing_successful),
                "failing_test_ids": [test.id() for test, _ in result.failures + result.errors],
                "skipped_test_ids": [test.id() for test, _ in result.skipped],
                "expected_failure_test_ids": [test.id() for test, _ in result.expectedFailures],
                "unexpected_success_test_ids": [test.id() for test in result.unexpectedSuccesses],
            }
            path = Path(directory) / "tests.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        # Dedicated PostgreSQL coverage must not silently shrink or accept known failures.
        return (
            super().suite_result(suite, result, **kwargs)
            + counts["skipped"] + counts["expectedFailures"] + int(identity_failure)
        )

    def _guard_connection(self, stack, connection, *, maintenance=False):
        original_connect = connection.connect

        def guarded_connect():
            test_database = connection.settings_dict.get("NAME") == TEST_DATABASE_NAME
            if maintenance and test_database:
                raise ImproperlyConfigured("History revision CI maintenance must use the marked base database.")
            check_database_config(connection, test_database=test_database)
            original_connect()
            try:
                server_identity(connection, test_database=test_database)
            except BaseException:
                connection.close()
                raise

        stack.enter_context(patch.object(connection, "connect", guarded_connect))

    def _create_scoped_database(self, creation, original_create, *args, **kwargs):
        from django.core import management
        from .history_revision_ci_migrations import migrate_scoped

        original_call = management.call_command
        migration_calls = 0

        def scoped_call(command, *command_args, **options):
            nonlocal migration_calls
            if command == "migrate":
                if command_args or migration_calls or options != {
                    "verbosity": max(kwargs.get("verbosity", 1) - 1, 0),
                    "interactive": False, "database": "default", "run_syncdb": True,
                }:
                    raise ImproperlyConfigured("History revision CI refuses unexpected test migration dispatch.")
                server_identity(creation.connection, test_database=True)
                migration_calls += 1
                result = migrate_scoped(creation.connection, verbosity=options["verbosity"], fresh=True)
                directory = os.environ.get("HR_CI_EVIDENCE_DIR")
                if directory:
                    (Path(directory) / "test-database-migrations.json").write_text(
                        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8",
                    )
                return None
            if command != "createcachetable":
                raise ImproperlyConfigured("History revision CI refuses unexpected database creation command.")
            return original_call(command, *command_args, **options)

        # BaseDatabaseCreation imports call_command locally. Intercept only its
        # creation-time dispatch; normal migrate signals and later flush are intact.
        with patch.object(management, "call_command", scoped_call):
            result = original_create(*args, **kwargs)
        if migration_calls != 1:
            raise ImproperlyConfigured("History revision CI test setup did not execute its scoped migrations.")
        return result

    def setup_databases(self, **kwargs):
        if self.keepdb or self.parallel > 1:
            raise ImproperlyConfigured("History revision CI forbids keepdb and parallel database clones.")
        if set(connections) != {"default"}:
            raise ImproperlyConfigured("History revision CI permits only the default database alias.")
        connection = connections["default"]
        check_database_config(connection)
        # Discard any pre-existing session so identity cannot be cached across setup.
        connection.close()
        stack = self._history_revision_guards = ExitStack()
        maintenance = connection.copy(alias="__history_revision_maintenance__")
        stack.callback(maintenance.close)
        self._guard_connection(stack, connection)
        self._guard_connection(stack, maintenance, maintenance=True)

        @contextmanager
        def marked_base_cursor():
            # Django normally uses the unmarked 'postgres' maintenance database.
            # Keep CREATE/DROP DATABASE on the already identified marked base.
            server_identity(maintenance)
            with maintenance.cursor() as cursor:
                yield cursor

        creation = connection.creation

        def create_test_db_once(verbosity, autoclobber, keepdb=False):
            # Do not delegate to Django's _create_test_db(): its duplicate-create
            # recovery may DROP an existing database when interactive=False.
            if keepdb:
                raise ImproperlyConfigured("History revision CI refuses test database reuse.")
            check_database_config(connection)
            test_database_name = creation._get_test_db_name()
            if test_database_name != TEST_DATABASE_NAME:
                raise ImproperlyConfigured("History revision CI refuses an unexpected test database name.")
            parameters = {
                "dbname": connection.ops.quote_name(test_database_name),
                "suffix": creation.sql_table_creation_suffix(),
            }
            with marked_base_cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s",
                    [test_database_name],
                )
                if cursor.fetchone() is not None:
                    raise ImproperlyConfigured("History revision CI refuses a pre-existing test database.")
                try:
                    creation._execute_create_test_db(cursor, parameters, keepdb=False)
                except Exception:
                    # A database can appear after the SELECT. Never retry, prompt,
                    # or DROP it, regardless of Django's autoclobber argument.
                    raise ImproperlyConfigured(
                        "History revision CI test database creation failed; no retry or DROP is allowed."
                    ) from None
            return test_database_name

        stack.enter_context(patch.object(creation, "_nodb_cursor", marked_base_cursor))
        stack.enter_context(patch.object(creation, "_create_test_db", create_test_db_once))
        original_create = creation.create_test_db
        try:
            server_identity(connection)
            # Restore command interception immediately, not at teardown.
            with patch.object(creation, "create_test_db", lambda *args, **options:
                              self._create_scoped_database(creation, original_create, *args, **options)):
                old_config = super().setup_databases(**kwargs)
            # The connect guard also checks the test identity BEFORE its migrations.
            server_identity(connection, test_database=True)
            return old_config
        except BaseException:
            stack.close()
            raise

    def teardown_databases(self, old_config, **kwargs):
        try:
            server_identity(connections["default"], test_database=True)
            return super().teardown_databases(old_config, **kwargs)
        finally:
            self._history_revision_guards.close()
