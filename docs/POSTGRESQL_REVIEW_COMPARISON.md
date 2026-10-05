# PostgreSQL baseline/worktree comparison

## Finding

**PRE-EXISTING:** the previously reported 8 failures and 36 errors were reproduced on canonical main and the current worktree using the same 182 Transfer v2/portable-lineage tests and equivalent fresh disposable PostgreSQL environments. All failing test/subtest IDs and full tracebacks match after normalizing repository paths and one AnonymousUser object memory address. Every failure/error traceback contains `Portable lineage requires its own transaction.` No worktree-only failure was observed in this scope.

This is a real canonical-main comparison, not an in-memory replacement of changed model save methods. No application methods, tests, migrations or tracked code were edited by this review. This does not establish that the full PostgreSQL suite is green, nor prove the absence of regressions outside the tested scope.

## Trees and results

Both trees had HEAD `c3948c26ea4e75e450f1bd9a11a02c44beb8dc65` at inspection. Canonical main was clean before and after the runs. The worktree contained substantial concurrent implementation changes; those were preserved.

| Tree | Location | Tests run | Failures | Errors | Skips | Process exit | Test runner time | Total child time |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Main | `C:/Users/antoc/POLYPBASE` | 182 | 8 | 36 | 0 | 1 | 38.106 s | 52.14 s |
| Worktree | `C:/Users/antoc/worktrees/POLYPBASE/subculture-popup-overhaul/POLYPBASE` | 182 | 8 | 36 | 0 | 1 | 47.421 s | 60.67 s |

Counts are unittest failure/error entries, including subtests; do not calculate passed test methods by subtracting 44 from 182. Both Django system checks reported no issues. Both test databases were destroyed by Django.

The lineage guard and all eight selected test files were byte-identical between the trees at inspection. PostgreSQL migrations were allowed to differ naturally with each tree's implementation; all were applied only to disposable test databases.

## Safety and environment

- Consulted both trees' `AGENTS.md` and local backend instructions in `docs/context/development-deployment.md`, plus the worktree `polypbase-qa` skill.
- Consulted prior evidence in `docs/SUBCULTURE_IMPLEMENTATION_REPORT.md` and `backend/QUANTITATIVE_SUBCULTURE.md`. Those reported 8 failures/36 errors on PostgreSQL 16 Alpine but only a representative in-memory save-path neutralization. This review independently attributes the same failures on canonical main using PostgreSQL 17.4; it is not a rerun on PostgreSQL 16.
- Used installed `C:/Program Files/PostgreSQL/17/bin` binaries: **PostgreSQL 17.4**, x86_64 Windows, MSVC 19.43.34808.
- Used the exact same existing worktree `.venv/Scripts/python.exe` for both trees: **Django 5.2.17**, **psycopg 3.3.2**. No packages were installed and no dependencies or virtual environments were changed.
- Each tree got its own newly initialized empty cluster (`--encoding=UTF8 --locale=C`), started sequentially on **127.0.0.1:55439**. Port vacancy was checked before each start. Connected to `postgres` only on this disposable endpoint to verify server version, data directory and loopback binding, then created the empty QA database.
- Public disposable role/database `subculture_qa`, empty password; test database `test_subculture_qa`. Trust authentication was confined to the disposable instance with loopback-only TCP listening. No copied data, Docker shared container, default/shared DB, Neon or production access.
- Child environment was allowlisted to OS/runtime variables plus explicit safe test settings. No inherited application credentials. `dotenv.load_dotenv` was disabled before importing either tree's settings: **no `.env` file was read**.
- Imported each tree's `config.test_settings`. Worktree's opt-in DB dictionary was asserted to match the expected safe dictionary, then both trees received the identical DB dictionary in memory before Django setup. This is necessary because main's test settings only support SQLite. Only settings/bootstrap were adapted; no model/service behavior was replaced.
- Explicit `POSTGRES_DB=''`, `DJANGO_DEBUG=1`, `POLYPBASE_TEST_POSTGRES=1`, `DJANGO_SETTINGS_MODULE=config.test_settings`; lock timeout 10 s, statement timeout 30 s.
- `-B` and `PYTHONDONTWRITEBYTECODE=1` prevented bytecode writes to main. Test outputs, runner and clusters were stored exclusively in the disposable temporary directory, except this report.
- Both clusters stopped successfully using `pg_ctl -m fast -w stop`. Final connect to 127.0.0.1:55439 returned Windows 10061 (connection refused). Cluster directories were removed after shutdown; text/JSON evidence retained.

## Scope and classification

Exact labels, same order on both trees:

```text
apps.cultures.test_transfer_v2
apps.cultures.test_transfer_v2_destination
apps.cultures.test_transfer_v2_lineage_protocol
apps.cultures.test_transfer_v2_lineage_source
apps.cultures.test_portable_lineage
apps.cultures.test_portable_lineage_models
apps.cultures.test_portable_lineage_migrations
apps.cultures.test_portable_lineage_concurrency
```

**PRE-EXISTING:** 36 uncaught DRF `ValidationError` entries and 8 assertion-failure entries caused by the same guard in both trees. The assertion failures expect later validation text (`Cycles`, `Source Strain`, `limit`), but receive the earlier transaction-ownership conflict instead.

The guard in `backend/apps/cultures/portable_lineage.py:54-57` rejects PostgreSQL calls inside an existing atomic transaction or with autocommit disabled. These affected service/package tests use Django `TestCase`, which supplies an outer transaction. Calls therefore fail before the service can establish its own SERIALIZABLE transaction and before the intended deeper assertions. This is a PostgreSQL-specific test/service transaction contract mismatch already present on main. No fix was attempted, and the guard was not bypassed.

**REGRESSION:** none observed in the selected comparison scope.

**INCONCLUSIVE / NOT RUN at initial baseline comparison:** full PostgreSQL suite; final concurrent quantitative/code-preview implementation. No implementation-ready signal had been received at that stage. The first implementation-ready quantitative run is recorded below: 77 tests, 76 passed, one code-preview fixture error, no skips. The subsequent test-only fixture correction and fresh PostgreSQL rerun passed 77/77, with no skips; both sets of evidence are retained below. Prior 268/268 affected-scope results remain prior evidence, not new validation by this review. PostgreSQL 16-specific equivalence is not claimed.

## Commands and artifacts

Temporary artifact root:

```text
C:/Users/antoc/AppData/Local/Temp/polypbase-pg-comparison-bpzr2uxw
```

Retained: `compare.py`, `main-tests.log`, `worktree-tests.log`, `main-results.json`, `worktree-results.json`, `main-server.json`, `worktree-server.json`, each tree's infrastructure and PostgreSQL logs. JSON includes every failing test/subtest ID and complete traceback. The runner source below preserves the complete safe bootstrap so it is not necessary to rely on temporary-file retention.

Executed orchestration command from the worktree root:

```sh
./.venv/Scripts/python.exe -B C:/Users/antoc/AppData/Local/Temp/polypbase-pg-comparison-bpzr2uxw/compare.py
```

The runner executed each test child from the corresponding tree's `backend/`, using `DiscoverRunner(verbosity=2, interactive=False).run_tests(LABELS)`. It set `sys.argv` to `manage.py test --settings=config.test_settings --noinput` before importing settings. This is a settings-safe equivalent of the labeled manage.py test command, not a default-settings invocation. Each initdb/start/test/stop command is preserved in the source below. Do not rerun the existing script against its old cluster directories; create a fresh temporary directory for any new run.

Normalization used only path separators, the two repository-root prefixes, and hexadecimal object addresses (`0x[0-9A-Fa-f]+`). No exception messages, stack frames, line numbers or test parameters were dropped. Normalized IDs and complete tracebacks matched for all entries.

## Every matched failure/error entry

| Outcome | Test/subtest ID (both trees) | Classification |
|---|---|---|
| failure | `apps.cultures.test_transfer_v2_lineage_source.TransferV21SourcePackageTests.test_cycle_failure_rolls_back_package_and_all_new_bridges` | PRE-EXISTING |
| failure | `apps.cultures.test_transfer_v2_lineage_source.TransferV21SourcePackageTests.test_foreign_owned_source_strain_is_rejected_only_for_21` | PRE-EXISTING |
| failure | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_default_node_limit_rejects_251_nodes_atomically` | PRE-EXISTING |
| failure | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_local_cycle_fails_atomically_instead_of_returning_partial_graph` | PRE-EXISTING |
| failure | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_node_and_edge_bounds_fail_without_truncation_or_partial_bridges (limits={'max_nodes': 2})` | PRE-EXISTING |
| failure | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_node_and_edge_bounds_fail_without_truncation_or_partial_bridges (limits={'max_edges': 1})` | PRE-EXISTING |
| failure | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_node_and_edge_bounds_fail_without_truncation_or_partial_bridges (limits={'max_nodes': 0})` | PRE-EXISTING |
| failure | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_node_and_edge_bounds_fail_without_truncation_or_partial_bridges (limits={'max_edges': 0})` | PRE-EXISTING |
| error | `apps.cultures.test_transfer_v2_lineage_source.TransferV21SourcePackageTests.test_21_allows_active_superuser_but_rechecks_source_organization_activity` | PRE-EXISTING |
| error | `apps.cultures.test_transfer_v2_lineage_source.TransferV21SourcePackageTests.test_21_denies_anonymous_inactive_actors_and_wrong_organization_context (actor=None)` | PRE-EXISTING |
| error | `apps.cultures.test_transfer_v2_lineage_source.TransferV21SourcePackageTests.test_21_denies_anonymous_inactive_actors_and_wrong_organization_context (actor=<django.contrib.auth.models.AnonymousUser object at 0xADDRESS>)` | PRE-EXISTING |
| error | `apps.cultures.test_transfer_v2_lineage_source.TransferV21SourcePackageTests.test_21_denies_anonymous_inactive_actors_and_wrong_organization_context` | PRE-EXISTING |
| error | `apps.cultures.test_transfer_v2_lineage_source.TransferV21SourcePackageTests.test_21_package_has_no_culture_location_stock_or_measurement_lifecycle_effects` | PRE-EXISTING |
| error | `apps.cultures.test_transfer_v2_lineage_source.TransferV21SourcePackageTests.test_21_privacy_allowlist_zero_measurement_and_attributed_audit_are_exact` | PRE-EXISTING |
| error | `apps.cultures.test_transfer_v2_lineage_source.TransferV21SourcePackageTests.test_21_requires_active_source_admin_before_any_lineage_projection (role=OrganizationMembership.Role.VIEWER)` | PRE-EXISTING |
| error | `apps.cultures.test_transfer_v2_lineage_source.TransferV21SourcePackageTests.test_21_requires_active_source_admin_before_any_lineage_projection (role=OrganizationMembership.Role.LAB_TECHNICIAN)` | PRE-EXISTING |
| error | `apps.cultures.test_transfer_v2_lineage_source.TransferV21SourcePackageTests.test_21_requires_active_source_admin_before_any_lineage_projection` | PRE-EXISTING |
| error | `apps.cultures.test_transfer_v2_lineage_source.TransferV21SourcePackageTests.test_late_audit_failure_rolls_back_audit_as_well_as_bridges_envelope_and_items` | PRE-EXISTING |
| error | `apps.cultures.test_transfer_v2_lineage_source.TransferV21SourcePackageTests.test_late_item_constraint_failure_rolls_back_new_bridges_envelope_items_and_audit` | PRE-EXISTING |
| error | `apps.cultures.test_transfer_v2_lineage_source.TransferV21SourcePackageTests.test_legacy_unowned_strain_already_used_by_source_is_eligible_for_21` | PRE-EXISTING |
| error | `apps.cultures.test_transfer_v2_lineage_source.TransferV21SourcePackageTests.test_onward_package_exports_prior_transfer_but_no_current_outgoing_transfer_edge` | PRE-EXISTING |
| error | `apps.cultures.test_transfer_v2_lineage_source.TransferV21SourcePackageTests.test_repeated_packages_keep_graph_ids_but_generate_new_transfer_and_item_ids` | PRE-EXISTING |
| error | `apps.cultures.test_transfer_v2_lineage_source.TransferV21SourcePackageTests.test_saved_package_is_frozen_after_lineage_labels_status_codes_and_identity_change` | PRE-EXISTING |
| error | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_ancestry_excludes_siblings_descendants_and_disconnected_same_strain_codes` | PRE-EXISTING |
| error | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_changed_box_organization_invalidates_existing_bridge` | PRE-EXISTING |
| error | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_cycle_in_union_of_local_and_portable_edges_fails_atomically` | PRE-EXISTING |
| error | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_default_edge_limit_rejects_1001_edges_instead_of_slicing_snapshot` | PRE-EXISTING |
| error | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_edge_bridge_can_attach_once_and_omission_preserves_it` | PRE-EXISTING |
| error | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_established_edge_cannot_rebind_to_another_local_lineage` | PRE-EXISTING |
| error | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_explicit_multigeneration_graph_preserves_every_biological_relationship` | PRE-EXISTING |
| error | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_foreign_ancestor_prior_transfer_and_local_descendant_need_no_foreign_operational_lookup` | PRE-EXISTING |
| error | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_malformed_foreign_node_or_edge_fk_fails_closed_and_rolls_back_new_projection` | PRE-EXISTING |
| error | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_mutated_local_lineage_bridge_fails_closed_without_rewriting_edge` | PRE-EXISTING |
| error | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_node_and_edge_bounds_fail_without_truncation_or_partial_bridges` | PRE-EXISTING |
| error | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_node_bridge_cannot_rebind_or_give_box_a_second_identity` | PRE-EXISTING |
| error | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_repeated_local_projection_and_assertions_keep_node_and_edge_identities` | PRE-EXISTING |
| error | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_same_edge_identity_rejects_contradictory_endpoints_and_relationship` | PRE-EXISTING |
| error | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_same_uuids_in_two_tenants_have_independent_bridges_and_traversal` | PRE-EXISTING |
| error | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_service_rejects_non_uuid_node_edge_and_provenance_without_writes` | PRE-EXISTING |
| error | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_service_rejects_self_edges_unknown_types_and_invalid_provenance` | PRE-EXISTING |
| error | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_shared_subculture_event_produces_one_edge_per_box_lineage` | PRE-EXISTING |
| error | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_transfer_edge_preserves_provenance_and_rejects_same_id_changes` | PRE-EXISTING |
| error | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_unbridged_foreign_identity_stays_knowledge_only_without_fake_box` | PRE-EXISTING |
| error | `apps.cultures.test_portable_lineage.PortableLineageServiceTests.test_unbridged_node_can_attach_once_and_omitted_bridge_preserves_it` | PRE-EXISTING |

## Representative complete tracebacks

These are main traces; normalized worktree traces are identical.

### Uncaught transaction conflict

```text
Traceback (most recent call last):
  File "C:\Users\antoc\POLYPBASE\backend\apps\cultures\test_transfer_v2_lineage_source.py", line 141, in test_21_allows_active_superuser_but_rechecks_source_organization_activity
    envelope = self.lineage_package()
  File "C:\Users\antoc\POLYPBASE\backend\apps\cultures\test_transfer_v2_lineage_source.py", line 33, in lineage_package
    return self.package(selections, protocol_version=(2, 1), **kwargs)
           ~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\antoc\POLYPBASE\backend\apps\cultures\test_transfer_v2.py", line 98, in package
    return create_source_package(
        actor=kwargs.pop("actor", self.actor),
    ...<4 lines>...
        **kwargs,
    )
  File "C:\Users\antoc\POLYPBASE\backend\apps\cultures\transfer_v2.py", line 58, in create_source_package
    return builder(
        actor=actor, source_organization=source_organization, selections=selections,
    ...<2 lines>...
        protocol_version=protocol_version,
    )
  File "C:\Users\antoc\POLYPBASE\backend\apps\cultures\portable_lineage.py", line 57, in run
    _conflict("Portable lineage requires its own transaction.")
    ~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\antoc\POLYPBASE\backend\apps\cultures\portable_lineage.py", line 23, in _conflict
    raise ValidationError({"lineage": message}, code="source_lineage_conflict")
rest_framework.exceptions.ValidationError: {'lineage': ErrorDetail(string='Portable lineage requires its own transaction.', code='source_lineage_conflict')}
```

### Deeper assertion preempted by the transaction guard

```text
Traceback (most recent call last):
  File "C:\Users\antoc\POLYPBASE\backend\apps\cultures\test_transfer_v2_lineage_source.py", line 358, in test_cycle_failure_rolls_back_package_and_all_new_bridges
    self.assertIn("Cycles", str(caught.exception.detail))
    ~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 'Cycles' not found in "{'lineage': ErrorDetail(string='Portable lineage requires its own transaction.', code='source_lineage_conflict')}"
```

## Exact runner source

```python
import os, sys, json, subprocess, socket, time, hashlib
from pathlib import Path
ROOT=Path(__file__).parent
PY=Path("C:/Users/antoc/worktrees/POLYPBASE/subculture-popup-overhaul/POLYPBASE/.venv/Scripts/python.exe")
BIN=Path("C:/Program Files/PostgreSQL/17/bin")
TREES={"main":Path("C:/Users/antoc/POLYPBASE"),"worktree":Path("C:/Users/antoc/worktrees/POLYPBASE/subculture-popup-overhaul/POLYPBASE")}
LABELS=["apps.cultures."+n for n in ["test_transfer_v2","test_transfer_v2_destination","test_transfer_v2_lineage_protocol","test_transfer_v2_lineage_source","test_portable_lineage","test_portable_lineage_models","test_portable_lineage_migrations","test_portable_lineage_concurrency"]]
DB={"ENGINE":"django.db.backends.postgresql","NAME":"subculture_qa","USER":"subculture_qa","PASSWORD":"","HOST":"127.0.0.1","PORT":"55439","TEST":{"NAME":"test_subculture_qa"},"OPTIONS":{"options":"-c lock_timeout=10000 -c statement_timeout=30000"}}
ENV={k:v for k,v in os.environ.items() if k.upper() in {"SYSTEMROOT","WINDIR","TEMP","TMP","PATH","COMSPEC","PATHEXT","APPDATA","LOCALAPPDATA","USERPROFILE"}}
ENV.update(POSTGRES_DB="",DJANGO_DEBUG="1",POLYPBASE_TEST_POSTGRES="1",DJANGO_SETTINGS_MODULE="config.test_settings",PYTHONDONTWRITEBYTECODE="1",PYTHONUTF8="1")
def child(name):
    os.chdir(TREES[name]/"backend")
    sys.path.insert(0,str(Path.cwd()))
    sys.argv=["manage.py","test","--settings=config.test_settings","--noinput"]
    import dotenv
    dotenv.load_dotenv=lambda *a,**kw: False
    import config.test_settings as cfg
    if name=="worktree":
        assert cfg.DATABASES["default"]==DB, "Worktree DB opt-in changed; stop"
    cfg.DATABASES={"default":dict(DB)}
    import django, psycopg
    django.setup()
    from django.conf import settings
    assert settings.DATABASES["default"]["HOST"]=="127.0.0.1"
    from django.test.runner import DiscoverRunner
    class EvidenceRunner(DiscoverRunner):
        def run_suite(self,suite,**kwargs):
            result=super().run_suite(suite,**kwargs)
            data={"tree":name,"labels":LABELS,"django":django.get_version(),"psycopg":psycopg.__version__,"testsRun":result.testsRun,"failures":[{"id":t.id(),"traceback":tb} for t,tb in result.failures],"errors":[{"id":t.id(),"traceback":tb} for t,tb in result.errors],"skipped":[[t.id(),why] for t,why in result.skipped]}
            (ROOT/(name+"-results.json")).write_text(json.dumps(data,indent=2),encoding="utf-8")
            return result
    sys.exit(bool(EvidenceRunner(verbosity=2,interactive=False).run_tests(LABELS)))
def parent():
    import psycopg
    for name in TREES:
        with socket.socket() as sock:
            assert sock.connect_ex(("127.0.0.1",55439))!=0,"Port occupied: stop without touching listener"
        data=ROOT/(name+"-cluster")
        with (ROOT/(name+"-infrastructure.log")).open("w",encoding="utf-8") as log:
            def pg(args):
                print("COMMAND",args,flush=True)
                subprocess.run([str(BIN/args[0])]+args[1:],env=ENV,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
            pg(["initdb.exe","-D",str(data),"-U","subculture_qa","--auth-local=trust","--auth-host=trust","--encoding=UTF8","--locale=C"])
            started=False
            try:
                pg(["pg_ctl.exe","-D",str(data),"-l",str(ROOT/(name+"-postgres.log")),"-o","-h 127.0.0.1 -p 55439","-w","start"])
                started=True
                with psycopg.connect(host="127.0.0.1",port=55439,dbname="postgres",user="subculture_qa",autocommit=True) as conn:
                    info=conn.execute("SELECT version(), current_setting(%s), current_setting(%s)",("data_directory","listen_addresses")).fetchone()
                    assert Path(info[1]).resolve()==data.resolve()
                    assert info[2]=="127.0.0.1"
                    print(name,"SERVER",info,flush=True)
                    conn.execute("CREATE DATABASE subculture_qa")
                    (ROOT/(name+"-server.json")).write_text(json.dumps(info,indent=2),encoding="utf-8")
                start=time.monotonic()
                command=[str(PY),"-B",str(Path(__file__)),"--child",name]
                print("TEST COMMAND",command,flush=True)
                with (ROOT/(name+"-tests.log")).open("w",encoding="utf-8") as output:
                    result=subprocess.run(command,env=ENV,stdout=output,stderr=subprocess.STDOUT,timeout=240)
                print(name,"EXIT",result.returncode,"SECONDS",round(time.monotonic()-start,2),flush=True)
                if (ROOT/(name+"-results.json")).exists():
                    r=json.loads((ROOT/(name+"-results.json")).read_text())
                    print(name,"RESULT",r["testsRun"],"failures",len(r["failures"]),"errors",len(r["errors"]),"skips",len(r["skipped"]),flush=True)
            finally:
                if started: pg(["pg_ctl.exe","-D",str(data),"-m","fast","-w","stop"])
    print("ARTIFACTS",ROOT,flush=True)
if __name__=="__main__":
    if len(sys.argv)>1: child(sys.argv[2])
    else: parent()
```

## Artifact SHA-256

| Artifact | SHA-256 |
|---|---|
| `compare.py` | `149cab9994d88ee700a1be50da65eb34a12430192810aa384744acb5e7c1aa2d` |
| `main-infrastructure.log` | `61ec2d4a234eddcdf2a4d6a0f3610b919ad20de44f203b5ecfbb7b433fe3d140` |
| `main-postgres.log` | `c32c5495d5e829ae111bcac141ae4d3ad13594bac32e4c3dc56e6410d7d11bb6` |
| `main-results.json` | `af34193c89b127158cb0a68dca8c1f81e6704701f60e7d5a68ccb18aa1bbca77` |
| `main-server.json` | `cdd98908f6c9740cef652ec4434281dc0033b12f96eeb1b711a23869c5146d15` |
| `main-tests.log` | `1b7bd388c95d46c9c8e5a6a5c062eca65c7ea946c1aba27bca178f567289e39f` |
| `worktree-infrastructure.log` | `e1de6d6e9d6f537643650185679a456cf7e68b921478c2381f2e9082262eb570` |
| `worktree-postgres.log` | `5a6bc3f6e6bdc6c52b27863a11bbbdddadf43692973a936e9b34da6720b934b2` |
| `worktree-results.json` | `d960fcf9e64dbfc54a1365e65a7b6e6126d27710089a57e2e5fde303cfd7d9f8` |
| `worktree-server.json` | `b86b8230203f2a2c72eadeab68262f2b0c03e5543dd426e7eee64f2054d3d77d` |
| `worktree-tests.log` | `d59bf58922fb048ca8ac63a0c9f0a5b655fe838212bb5734df1bf4ebf2cd0541` |

## Implementation-ready optional quantitative PostgreSQL run

Requested follow-up after backend implementation-ready notification, including migration `0012_optional_subculture_allocations`. This run is worktree-only; it does not change the earlier Transfer/portable-lineage baseline attribution.

**Result: 77 tests, 76 passed, 0 assertion failures, 1 error, 0 skips; exit 1.** Django test-runner time 76.162 s; total child time 91.46 s. Django system check reported no issues. The requested core quantitative/migration/concurrency scope passed **62/62**, now with the PostgreSQL-only tests actually executed rather than skipped. Biological-measurement races passed **3/3**. Code-preview tests passed **11/12**, with one fixture error detailed below. Do not describe this entire 77-test invocation as green.

| Module | Passed | Errors | Skips |
|---|---:|---:|---:|
| `apps.cultures.test_quantitative_subculture` | 48 | 0 | 0 |
| `apps.cultures.test_quantitative_subculture_migrations` | 2 | 0 | 0 |
| `apps.cultures.test_quantitative_subculture_concurrency` | 12 | 0 | 0 |
| `apps.cultures.test_subculture_code_preview` | 11 | 1 | 0 |
| `apps.cultures.test_biological_measurement_concurrency` | 3 | 0 | 0 |

### Exact command and version

Executed from the worktree root:

```sh
./.venv/Scripts/python.exe -B C:/Users/antoc/AppData/Local/Temp/polypbase-pg-optional-uyim29u1/quantitative.py
```

The runner executes the following equivalent labeled test command from the worktree `backend/`, with dotenv loading disabled before importing settings and the OS-only child environment allowlist described above. Run the safe wrapper, not an unguarded default-environment command.

```sh
POSTGRES_DB="" DJANGO_DEBUG=1 POLYPBASE_TEST_POSTGRES=1 python manage.py test   apps.cultures.test_quantitative_subculture   apps.cultures.test_quantitative_subculture_migrations   apps.cultures.test_quantitative_subculture_concurrency   apps.cultures.test_subculture_code_preview   apps.cultures.test_biological_measurement_concurrency   --settings=config.test_settings --noinput --verbosity=2
```

Exact engine: **PostgreSQL 17.4 on x86_64-windows, compiled by msvc-19.43.34808, 64-bit**. Same existing Python environment: Django **5.2.17**, psycopg **3.3.2**. A new empty local cluster was initialized with UTF8/locale C, independent of both earlier clusters. Bound only to `127.0.0.1:55439`; server version, data directory and binding verified before creating empty `subculture_qa`. Same fixed test DB `test_subculture_qa`, role, empty password and timeout options as the comparison. No inherited application credentials, no `.env` reads, no shared/default database, Neon, production or copied data.

No backend edits or packages installed. Hashes of all five selected test modules, migration 0012 and test settings matched before/after execution. Full-suite, frontend and baseline-main quantitative tests were not run.

### Migration and immutability verification

`cultures.0012_optional_subculture_allocations` applied successfully on the new database. Before and after the complete suite, explicit catalog assertions verified:

- Migration 0012 present in `django_migrations`.
- `protect_subculture_event` and `protect_subculture_allocation` each present and enabled (`tgenabled = O`).
- Both are row-level **BEFORE DELETE OR UPDATE** triggers on the expected tables.
- `SubcultureAllocation.allocated_polyps` nullable (`YES`).
- The revised `subculture_absolute_polyp_balance` constraint includes complete, partial and legacy-null cases.

Behavioral checks passed (not just catalog inspection):

- `test_optional_upgrade_preserves_complete_evidence_and_guards`: populated 0011-to-0012 upgrade, complete evidence preserved, nullable partial allocation accepted, inconsistent partial rows rejected, and raw SQL updates to partial event/allocation rejected.
- `test_populated_upgrade_preserves_legacy_values_without_inventing_occurrence`: populated legacy upgrade and reverse preservation.
- `test_raw_sql_cannot_rewrite_quantitative_snapshot_or_allocations`: raw SQL UPDATE guards.
- `test_postgresql_raw_delete_protects_quantitative_history`: raw SQL DELETE guards for event and allocation, executed on PostgreSQL without skip.
- `test_snapshot_allocations_are_immutable_and_protected`: ORM protections.
- All 12 quantitative independent-connection races and all 3 biological-measurement races passed.

The optional/partial measurement-state tests also passed. This report does not assert that partial events leave the revision counter unchanged; it records the implementation behavior exercised by those tests.

### Single error and attribution limit

`apps.cultures.test_subculture_code_preview.SubcultureCodePreviewTests.test_code_length_validation_does_not_create_counter` errors at `test_subculture_code_preview.py:116`, when saving the fixture, before reaching the preview HTTP request. The test assigns `self.strain.code = "X" * 97`; the actual `Strain.code` model is `CharField(max_length=80)`, and PostgreSQL rejects the write with `StringDataRightTruncation` / Django `DataError`. SQLite does not enforce the varchar length in the same way.

This is an invalid persisted test fixture on PostgreSQL, not evidence that migration 0012 failed or that the preview endpoint handled the request incorrectly: that endpoint assertion was never reached. This is a **worktree-added preview test fixture defect**, not a pre-existing canonical-main or production failure: `test_subculture_code_preview.py` is absent from clean main (explicitly checked during the correction). No main-equivalent execution was attempted. No fix, bypass, skipped test or retry with altered application/test behavior was made during this initial failing run. The authorized test-only correction and successful fresh rerun are recorded below; the original traceback is retained.

Full error traceback:

```text
Traceback (most recent call last):
  File "C:\Users\antoc\worktrees\POLYPBASE\subculture-popup-overhaul\POLYPBASE\.venv\Lib\site-packages\django\db\backends\utils.py", line 105, in _execute
    return self.cursor.execute(sql, params)
           ~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^
  File "C:\Users\antoc\worktrees\POLYPBASE\subculture-popup-overhaul\POLYPBASE\.venv\Lib\site-packages\psycopg\cursor.py", line 117, in execute
    raise ex.with_traceback(None)
psycopg.errors.StringDataRightTruncation: value too long for type character varying(80)

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "C:\Users\antoc\worktrees\POLYPBASE\subculture-popup-overhaul\POLYPBASE\backend\apps\cultures\test_subculture_code_preview.py", line 116, in test_code_length_validation_does_not_create_counter
    self.strain.save(update_fields=["code"])
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\antoc\worktrees\POLYPBASE\subculture-popup-overhaul\POLYPBASE\.venv\Lib\site-packages\django\db\models\base.py", line 902, in save
    self.save_base(
    ~~~~~~~~~~~~~~^
        using=using,
        ^^^^^^^^^^^^
    ...<2 lines>...
        update_fields=update_fields,
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "C:\Users\antoc\worktrees\POLYPBASE\subculture-popup-overhaul\POLYPBASE\.venv\Lib\site-packages\django\db\models\base.py", line 1008, in save_base
    updated = self._save_table(
        raw,
    ...<4 lines>...
        update_fields,
    )
  File "C:\Users\antoc\worktrees\POLYPBASE\subculture-popup-overhaul\POLYPBASE\.venv\Lib\site-packages\django\db\models\base.py", line 1138, in _save_table
    updated = self._do_update(
        base_qs, using, pk_val, values, update_fields, forced_update
    )
  File "C:\Users\antoc\worktrees\POLYPBASE\subculture-popup-overhaul\POLYPBASE\.venv\Lib\site-packages\django\db\models\base.py", line 1203, in _do_update
    return filtered._update(values) > 0
           ~~~~~~~~~~~~~~~~^^^^^^^^
  File "C:\Users\antoc\worktrees\POLYPBASE\subculture-popup-overhaul\POLYPBASE\.venv\Lib\site-packages\django\db\models\query.py", line 1288, in _update
    return query.get_compiler(self.db).execute_sql(ROW_COUNT)
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^
  File "C:\Users\antoc\worktrees\POLYPBASE\subculture-popup-overhaul\POLYPBASE\.venv\Lib\site-packages\django\db\models\sql\compiler.py", line 2060, in execute_sql
    row_count = super().execute_sql(result_type)
  File "C:\Users\antoc\worktrees\POLYPBASE\subculture-popup-overhaul\POLYPBASE\.venv\Lib\site-packages\django\db\models\sql\compiler.py", line 1623, in execute_sql
    cursor.execute(sql, params)
    ~~~~~~~~~~~~~~^^^^^^^^^^^^^
  File "C:\Users\antoc\worktrees\POLYPBASE\subculture-popup-overhaul\POLYPBASE\.venv\Lib\site-packages\django\db\backends\utils.py", line 79, in execute
    return self._execute_with_wrappers(
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~^
        sql, params, many=False, executor=self._execute
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "C:\Users\antoc\worktrees\POLYPBASE\subculture-popup-overhaul\POLYPBASE\.venv\Lib\site-packages\django\db\backends\utils.py", line 92, in _execute_with_wrappers
    return executor(sql, params, many, context)
  File "C:\Users\antoc\worktrees\POLYPBASE\subculture-popup-overhaul\POLYPBASE\.venv\Lib\site-packages\django\db\backends\utils.py", line 100, in _execute
    with self.db.wrap_database_errors:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\antoc\worktrees\POLYPBASE\subculture-popup-overhaul\POLYPBASE\.venv\Lib\site-packages\django\db\utils.py", line 91, in __exit__
    raise dj_exc_value.with_traceback(traceback) from exc_value
  File "C:\Users\antoc\worktrees\POLYPBASE\subculture-popup-overhaul\POLYPBASE\.venv\Lib\site-packages\django\db\backends\utils.py", line 105, in _execute
    return self.cursor.execute(sql, params)
           ~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^
  File "C:\Users\antoc\worktrees\POLYPBASE\subculture-popup-overhaul\POLYPBASE\.venv\Lib\site-packages\psycopg\cursor.py", line 117, in execute
    raise ex.with_traceback(None)
django.db.utils.DataError: value too long for type character varying(80)
```

### Follow-up artifacts and cleanup

Artifact root: `C:/Users/antoc/AppData/Local/Temp/polypbase-pg-optional-uyim29u1`. Retained runner, test log/results JSON, schema-evidence JSON, source hashes, server identity, infrastructure log and PostgreSQL log. Django destroyed the test database. The server stopped successfully with `pg_ctl -m fast -w stop`; the port was verified closed and the disposable cluster directory removed. No commit.

Exact schema evidence:

```json
{
  "before_suite": {
    "applied_cultures_migrations": [
      "0001_initial",
      "0002_alter_box_status_alter_boxlineage_relationship_type_and_more",
      "0003_thermalzone_capacity",
      "0004_thermalzone_salinity_psu",
      "0005_boxtransfer_polyp_count",
      "0006_boxtransferimport",
      "0007_box_inventory_lifecycle",
      "0008_transfer_v2_source_package",
      "0009_portable_lineage",
      "0010_quantitative_subculture",
      "0011_protect_subculture_history",
      "0012_optional_subculture_allocations"
    ],
    "enabled_triggers": [
      [
        "cultures_subcultureallocation",
        "protect_subculture_allocation",
        "O",
        "CREATE TRIGGER protect_subculture_allocation BEFORE DELETE OR UPDATE ON public.cultures_subcultureallocation FOR EACH ROW EXECUTE FUNCTION protect_subculture_allocation()"
      ],
      [
        "cultures_subcultureevent",
        "protect_subculture_event",
        "O",
        "CREATE TRIGGER protect_subculture_event BEFORE DELETE OR UPDATE ON public.cultures_subcultureevent FOR EACH ROW EXECUTE FUNCTION protect_subculture_event()"
      ]
    ],
    "allocated_polyps_nullable": "YES",
    "balance_constraint": "CHECK ((((allocated_polyp_count IS NULL) AND (occurred_at IS NULL) AND (parent_polyp_count_after IS NULL) AND (parent_polyp_count_before IS NULL) AND (parent_state_sequence IS NULL) AND (parent_state_snapshot IS NULL)) OR ((allocated_polyp_count IS NOT NULL) AND (occurred_at IS NOT NULL) AND (parent_polyp_count_after IS NOT NULL) AND (parent_polyp_count_before = (allocated_polyp_count + parent_polyp_count_after)) AND (parent_polyp_count_before IS NOT NULL) AND (parent_state_sequence IS NOT NULL) AND (parent_state_snapshot IS NOT NULL)) OR ((allocated_polyp_count IS NULL) AND (occurred_at IS NOT NULL) AND (parent_polyp_count_after IS NULL) AND (parent_polyp_count_before IS NOT NULL) AND (parent_state_sequence IS NOT NULL) AND (parent_state_snapshot IS NOT NULL))))"
  },
  "after_suite": {
    "applied_cultures_migrations": [
      "0001_initial",
      "0002_alter_box_status_alter_boxlineage_relationship_type_and_more",
      "0003_thermalzone_capacity",
      "0004_thermalzone_salinity_psu",
      "0005_boxtransfer_polyp_count",
      "0006_boxtransferimport",
      "0007_box_inventory_lifecycle",
      "0008_transfer_v2_source_package",
      "0009_portable_lineage",
      "0010_quantitative_subculture",
      "0011_protect_subculture_history",
      "0012_optional_subculture_allocations"
    ],
    "enabled_triggers": [
      [
        "cultures_subcultureallocation",
        "protect_subculture_allocation",
        "O",
        "CREATE TRIGGER protect_subculture_allocation BEFORE DELETE OR UPDATE ON public.cultures_subcultureallocation FOR EACH ROW EXECUTE FUNCTION protect_subculture_allocation()"
      ],
      [
        "cultures_subcultureevent",
        "protect_subculture_event",
        "O",
        "CREATE TRIGGER protect_subculture_event BEFORE DELETE OR UPDATE ON public.cultures_subcultureevent FOR EACH ROW EXECUTE FUNCTION protect_subculture_event()"
      ]
    ],
    "allocated_polyps_nullable": "YES",
    "balance_constraint": "CHECK ((((allocated_polyp_count IS NULL) AND (occurred_at IS NULL) AND (parent_polyp_count_after IS NULL) AND (parent_polyp_count_before IS NULL) AND (parent_state_sequence IS NULL) AND (parent_state_snapshot IS NULL)) OR ((allocated_polyp_count IS NOT NULL) AND (occurred_at IS NOT NULL) AND (parent_polyp_count_after IS NOT NULL) AND (parent_polyp_count_before = (allocated_polyp_count + parent_polyp_count_after)) AND (parent_polyp_count_before IS NOT NULL) AND (parent_state_sequence IS NOT NULL) AND (parent_state_snapshot IS NOT NULL)) OR ((allocated_polyp_count IS NULL) AND (occurred_at IS NOT NULL) AND (parent_polyp_count_after IS NULL) AND (parent_polyp_count_before IS NOT NULL) AND (parent_state_sequence IS NOT NULL) AND (parent_state_snapshot IS NOT NULL))))"
  }
}
```

Exact follow-up runner source:

```python
import os, sys, json, subprocess, socket, time, hashlib
from pathlib import Path
ROOT=Path(__file__).parent
PY=Path("C:/Users/antoc/worktrees/POLYPBASE/subculture-popup-overhaul/POLYPBASE/.venv/Scripts/python.exe")
BIN=Path("C:/Program Files/PostgreSQL/17/bin")
TREES={"worktree":Path("C:/Users/antoc/worktrees/POLYPBASE/subculture-popup-overhaul/POLYPBASE")}
LABELS=['apps.cultures.test_quantitative_subculture', 'apps.cultures.test_quantitative_subculture_migrations', 'apps.cultures.test_quantitative_subculture_concurrency', 'apps.cultures.test_subculture_code_preview', 'apps.cultures.test_biological_measurement_concurrency']
DB={"ENGINE":"django.db.backends.postgresql","NAME":"subculture_qa","USER":"subculture_qa","PASSWORD":"","HOST":"127.0.0.1","PORT":"55439","TEST":{"NAME":"test_subculture_qa"},"OPTIONS":{"options":"-c lock_timeout=10000 -c statement_timeout=30000"}}
ENV={k:v for k,v in os.environ.items() if k.upper() in {"SYSTEMROOT","WINDIR","TEMP","TMP","PATH","COMSPEC","PATHEXT","APPDATA","LOCALAPPDATA","USERPROFILE"}}
ENV.update(POSTGRES_DB="",DJANGO_DEBUG="1",POLYPBASE_TEST_POSTGRES="1",DJANGO_SETTINGS_MODULE="config.test_settings",PYTHONDONTWRITEBYTECODE="1",PYTHONUTF8="1")
def child(name):
    os.chdir(TREES[name]/"backend")
    sys.path.insert(0,str(Path.cwd()))
    sys.argv=["manage.py","test","--settings=config.test_settings","--noinput"]
    import dotenv
    dotenv.load_dotenv=lambda *a,**kw: False
    import config.test_settings as cfg
    if name=="worktree":
        assert cfg.DATABASES["default"]==DB, "Worktree DB opt-in changed; stop"
    cfg.DATABASES={"default":dict(DB)}
    import django, psycopg
    django.setup()
    from django.conf import settings
    assert settings.DATABASES["default"]["HOST"]=="127.0.0.1"
    from django.test.runner import DiscoverRunner
    class EvidenceRunner(DiscoverRunner):
        def run_suite(self,suite,**kwargs):
            from django.db import connection
            def schema_evidence():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT name FROM django_migrations WHERE app = %s ORDER BY name", ["cultures"])
                    migrations=[row[0] for row in cursor.fetchall()]
                    assert "0012_optional_subculture_allocations" in migrations
                    cursor.execute("SELECT c.relname, t.tgname, t.tgenabled, pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE t.tgname IN (%s, %s) AND NOT t.tgisinternal ORDER BY t.tgname", ["protect_subculture_event", "protect_subculture_allocation"])
                    triggers=cursor.fetchall()
                    assert len(triggers)==2 and all(row[2]=="O" for row in triggers)
                    assert all("BEFORE DELETE OR UPDATE" in row[3] or "BEFORE UPDATE OR DELETE" in row[3] for row in triggers)
                    cursor.execute("SELECT is_nullable FROM information_schema.columns WHERE table_schema=%s AND table_name=%s AND column_name=%s", ["public", "cultures_subcultureallocation", "allocated_polyps"])
                    nullable=cursor.fetchone()[0]
                    assert nullable=="YES"
                    cursor.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname=%s", ["subculture_absolute_polyp_balance"])
                    constraint=cursor.fetchone()[0]
                return {"applied_cultures_migrations":migrations,"enabled_triggers":triggers,"allocated_polyps_nullable":nullable,"balance_constraint":constraint}
            before=schema_evidence()
            result=super().run_suite(suite,**kwargs)
            after=schema_evidence()
            (ROOT/"schema-evidence.json").write_text(json.dumps({"before_suite":before,"after_suite":after},indent=2),encoding="utf-8")
            data={"tree":name,"labels":LABELS,"django":django.get_version(),"psycopg":psycopg.__version__,"testsRun":result.testsRun,"failures":[{"id":t.id(),"traceback":tb} for t,tb in result.failures],"errors":[{"id":t.id(),"traceback":tb} for t,tb in result.errors],"skipped":[[t.id(),why] for t,why in result.skipped]}
            (ROOT/(name+"-results.json")).write_text(json.dumps(data,indent=2),encoding="utf-8")
            return result
    sys.exit(bool(EvidenceRunner(verbosity=2,interactive=False).run_tests(LABELS)))
def parent():
    import psycopg
    for name in TREES:
        with socket.socket() as sock:
            assert sock.connect_ex(("127.0.0.1",55439))!=0,"Port occupied: stop without touching listener"
        data=ROOT/(name+"-cluster")
        with (ROOT/(name+"-infrastructure.log")).open("w",encoding="utf-8") as log:
            def pg(args):
                print("COMMAND",args,flush=True)
                subprocess.run([str(BIN/args[0])]+args[1:],env=ENV,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
            pg(["initdb.exe","-D",str(data),"-U","subculture_qa","--auth-local=trust","--auth-host=trust","--encoding=UTF8","--locale=C"])
            started=False
            try:
                pg(["pg_ctl.exe","-D",str(data),"-l",str(ROOT/(name+"-postgres.log")),"-o","-h 127.0.0.1 -p 55439","-w","start"])
                started=True
                with psycopg.connect(host="127.0.0.1",port=55439,dbname="postgres",user="subculture_qa",autocommit=True) as conn:
                    info=conn.execute("SELECT version(), current_setting(%s), current_setting(%s)",("data_directory","listen_addresses")).fetchone()
                    assert Path(info[1]).resolve()==data.resolve()
                    assert info[2]=="127.0.0.1"
                    print(name,"SERVER",info,flush=True)
                    conn.execute("CREATE DATABASE subculture_qa")
                    (ROOT/(name+"-server.json")).write_text(json.dumps(info,indent=2),encoding="utf-8")
                start=time.monotonic()
                command=[str(PY),"-B",str(Path(__file__)),"--child",name]
                print("TEST COMMAND",command,flush=True)
                with (ROOT/(name+"-tests.log")).open("w",encoding="utf-8") as output:
                    result=subprocess.run(command,env=ENV,stdout=output,stderr=subprocess.STDOUT,timeout=240)
                print(name,"EXIT",result.returncode,"SECONDS",round(time.monotonic()-start,2),flush=True)
                if (ROOT/(name+"-results.json")).exists():
                    r=json.loads((ROOT/(name+"-results.json")).read_text())
                    print(name,"RESULT",r["testsRun"],"failures",len(r["failures"]),"errors",len(r["errors"]),"skips",len(r["skipped"]),flush=True)
            finally:
                if started: pg(["pg_ctl.exe","-D",str(data),"-m","fast","-w","stop"])
    print("ARTIFACTS",ROOT,flush=True)
if __name__=="__main__":
    if len(sys.argv)>1: child(sys.argv[2])
    else: parent()

```

Follow-up artifact SHA-256:

| Artifact | SHA-256 |
|---|---|
| `quantitative.py` | `7405c0c3d22d2f5c96af317af533d3f0d80573b60e8ee8af88b17e32b65eec11` |
| `schema-evidence.json` | `1f6de897b00dcb5b488356c213164aba0cf8bb450ab023e92fb58e54fdf0ff5f` |
| `source-hashes.json` | `64912e6d6fd1ce461e42eca103f84d8fa8a46434e386436b845c6e25375aa70a` |
| `worktree-infrastructure.log` | `08d344f49ecacbdfd69fd3226f0455e7d1ba7360d7b2f8057c3b40a150feb26a` |
| `worktree-postgres.log` | `9385e2dc2ca629789480d6b7c60b7d89cf53557e1fa461f8b05c63a21742d4fc` |
| `worktree-results.json` | `6dbb4da279ceb70cfd279d300521314edf0460dca2f4a8a95e99506833377ee1` |
| `worktree-server.json` | `f69492b368b9c3cee572957ceb53dd0ca2bdbc74090a723d2f52e52298125c00` |
| `worktree-tests.log` | `493fdabb7f064e2f18db130e584ef0667ce1f0804eac6c7b5e11d1899a7c1414` |

## Post-fixture-correction PostgreSQL rerun — successful

Only backend change: `backend/apps/cultures/test_subculture_code_preview.py`. Added `unittest.mock.patch` and corrected `test_code_length_validation_does_not_create_counter`:

- Kept the real helper call `preview_box_codes("X" * 97, 1)` and its Django `ValidationError` assertion; snapshots now explicitly verify that this call does not create or mutate counters or other tracked entities.
- Removed the invalid 97-character persisted Strain code. The fixture keeps its valid original code.
- Patched the helper at its API lookup site, `apps.cultures.api_views.preview_box_codes`, to raise the actual Django validation exception captured from the direct helper call.
- Asserted the API called the helper once with the valid strain code and count 2, returned HTTP 400 with the original error messages, retained `Cache-Control: no-store`, and made no state changes.

No production source behavior, field lengths, constraints or migrations were modified. This fixes a worktree-added preview test fixture, **not a pre-existing clean-main or production failure**. The earlier failing run and full original traceback remain in this report.

**Fresh PostgreSQL result: 77 tests, 77 passed, 0 failures, 0 errors, 0 skips; exit 0.** Test-runner time **68.819 s**; total child time **83.09 s**. Django system check reported no issues.

| Module | Passed | Errors | Skips |
|---|---:|---:|---:|
| `apps.cultures.test_quantitative_subculture` | 48 | 0 | 0 |
| `apps.cultures.test_quantitative_subculture_migrations` | 2 | 0 | 0 |
| `apps.cultures.test_quantitative_subculture_concurrency` | 12 | 0 | 0 |
| `apps.cultures.test_subculture_code_preview` | 12 | 0 | 0 |
| `apps.cultures.test_biological_measurement_concurrency` | 3 | 0 | 0 |

### Exact test labels and runner command

Exact labels in runner input order:

```text
apps.cultures.test_quantitative_subculture
apps.cultures.test_quantitative_subculture_migrations
apps.cultures.test_quantitative_subculture_concurrency
apps.cultures.test_subculture_code_preview
apps.cultures.test_biological_measurement_concurrency
```

Executed from `C:/Users/antoc/worktrees/POLYPBASE/subculture-popup-overhaul/POLYPBASE`:

```sh
./.venv/Scripts/python.exe -B C:/Users/antoc/AppData/Local/Temp/polypbase-pg-preview-fixed-3x8ku85h/quantitative.py
```

The script is byte-identical to the exact follow-up runner source recorded above (SHA-256 `7405c0c3d22d2f5c96af317af533d3f0d80573b60e8ee8af88b17e32b65eec11`), copied into a new temporary directory to initialize a fresh cluster. It launches this child from the worktree `backend/`:

```text
C:/Users/antoc/worktrees/POLYPBASE/subculture-popup-overhaul/POLYPBASE/.venv/Scripts/python.exe -B C:/Users/antoc/AppData/Local/Temp/polypbase-pg-preview-fixed-3x8ku85h/quantitative.py --child worktree
```

The child disables dotenv loading before settings import, uses the OS-only environment allowlist with `POSTGRES_DB=""`, `DJANGO_DEBUG=1`, `POLYPBASE_TEST_POSTGRES=1`, `DJANGO_SETTINGS_MODULE=config.test_settings`, and invokes `DiscoverRunner(verbosity=2, interactive=False).run_tests(LABELS)`. This preserves the safe fixed-loopback `config.test_settings` opt-in and does not replace production/application behavior.

### Version, guards and cleanup

Server: **PostgreSQL 17.4 on x86_64-windows, compiled by msvc-19.43.34808, 64-bit**. Django **5.2.17**, psycopg **3.3.2**. Newly initialized empty UTF8/locale C cluster bound only to `127.0.0.1:55439`, isolated database/role `subculture_qa` and test DB `test_subculture_qa`. Server identity/data directory/binding verified before tests. No `.env` reads, shared/default database, copied data, Neon or production access.

Migration `0012_optional_subculture_allocations`, nullable allocations, the revised balance constraint and both enabled BEFORE DELETE OR UPDATE immutability triggers were verified before and after the suite. Schema evidence matched exactly before/after. All populated-upgrade, SQL UPDATE/DELETE protection and independent-connection race tests passed without skips.

Hashes of the selected source files matched before/after execution. Django destroyed the test database. The server stopped successfully, the loopback port was verified closed, and the disposable cluster directory was removed. No commit. Whole-worktree `git diff --check` passed. Full PostgreSQL suite not rerun; the separate Transfer/portable-lineage baseline finding remains unchanged.

Artifact root: `C:/Users/antoc/AppData/Local/Temp/polypbase-pg-preview-fixed-3x8ku85h`. Runner, test log, JSON results, schema evidence, source hashes, server identity and infrastructure/PostgreSQL logs retained.

Final test summary:

```text
Ran 77 tests in 68.819s

OK
Destroying test database for alias default (test_subculture_qa)...
System check identified no issues (0 silenced).
```

Artifact SHA-256:

| Artifact | SHA-256 |
|---|---|
| `quantitative.py` | `7405c0c3d22d2f5c96af317af533d3f0d80573b60e8ee8af88b17e32b65eec11` |
| `schema-evidence.json` | `1f6de897b00dcb5b488356c213164aba0cf8bb450ab023e92fb58e54fdf0ff5f` |
| `source-hashes.json` | `c7c8222244e98d3303c2ab1bf1585ecbc47d173f0532603b667e3ee9d98a5ed4` |
| `worktree-infrastructure.log` | `2ee594ee34d99fe0a593f3a547bbd88c74ac6ee7a9aedd785afb79de0feb9d30` |
| `worktree-postgres.log` | `7c5d4ce337ea8c73c265545a061f9b61307a62cd3be6c06008c234ccbd2ac61e` |
| `worktree-results.json` | `5baca8a0ed28151ad14d78d8f5c68a9e7ca792c7de88ff19614fa593f1a8218f` |
| `worktree-server.json` | `a2f579b342a64c2acb19fabddda0741c43289da57b695f3b2511f00f79d1b51a` |
| `worktree-tests.log` | `1857766aa07857ca889dd99f4e498e9bcf79c740af619bc6c798d844557f9859` |
