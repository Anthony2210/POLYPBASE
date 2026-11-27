---
name: polypbase-qa
description: Validate Polypbase changes locally with isolated Django tests, frontend checks, and real-browser QA for desktop and laboratory tablet. Use after implementation, before commits or releases, and for regression checks. Never touch Neon or production.
---

# Polypbase QA

Verify the requested behavior without risking shared or production data.

## Establish scope

1. Locate the repository and read its [AGENTS.md](../../../AGENTS.md) and the
   relevant [contributor conventions](../../../docs/CONVENTIONS.md).
2. Inspect `git status --short`, the relevant diff, and the files involved in the
   changed workflow. Read additional documentation only when that workflow needs it.
3. Preserve every pre-existing change. Never reset, clean, restore, or overwrite
   unrelated work.
4. Never read or print a real `.env` file.

Scale verification to the change. A local text or spacing edit does not need the same
browser matrix as a shared data-flow or permissions change.

## Protect databases

- Use explicit settings for every Django command. `config.test_settings` defaults
  to in-memory SQLite, but `POLYPBASE_TEST_POSTGRES=1` selects a fixed local
  PostgreSQL QA endpoint. Set that opt-in to the nonempty value `0` in a disposable
  child process before SQLite tests; dotenv must not be able to supply it.
- Use `config.preflight_settings` for configuration and migration-drift checks:
  its final database override is unconditionally in-memory SQLite, even if the
  PostgreSQL test opt-in is inherited. Both settings modules import application
  settings, which load dotenv unless disabled. Set `PYTHON_DOTENV_DISABLED=1`
  in the disposable process and start with a verified local environment without
  inherited demo variables, service credentials, or test harness opt-ins.
- An empty `POSTGRES_DB` alone is not an isolation boundary. In Windows PowerShell
  5.1 it can remove the environment variable, allowing dotenv to restore a
  configured PostgreSQL value. Do not use default settings for unverified QA.
- Follow the explicit SQLite commands and child-process boundary in
  [the development and QA guide](../../../docs/operations/development-qa.md).
  In-memory settings are for checks/tests, not persistent visual-QA data.
- Never run `migrate`, `loaddata`, `flush`, or `seed_demo_data` against the default
  environment unless the user has explicitly identified an isolated database.
- Do not use production accounts or production data for QA.

## Run deterministic checks

Prefer targeted behavior and regression checks first. Use `assertNumQueries` to
verify query budgets; do not add tests that match source code with regexes.
For broader local QA, run
[`scripts/run_checks.ps1`](scripts/run_checks.ps1) from the repository root as
`./.agents/skills/polypbase-qa/scripts/run_checks.ps1` in the verified child
session above. It selects `config.preflight_settings` for `check` and
`makemigrations --check --dry-run`, and `config.test_settings` for selected Django
labels, including `apps.sharing` and `config.test_api_defaults`. It sets
`POLYPBASE_TEST_POSTGRES=0` and restores the previous value on exit. Set
`PYTHON_DOTENV_DISABLED=1` before invoking it: the script does not set it itself.

The runner also checks Git whitespace and builds the frontend. It is not the
full CI workflow: GitHub Actions discovers the Django SQLite suite without labels,
runs targeted PostgreSQL regressions in one disposable PostgreSQL 18 service,
and runs frontend `test:all` when selected. Keep full suites on CI; run focused
checks locally. The final branch-protection check is `ci-ok`. See the
[development and QA guide](../../../docs/operations/development-qa.md) for discovery
and isolation. Select frontend checks from [`frontend/package.json`](../../../frontend/package.json).
Stop and report the exact failing command when a check fails.

## Perform visual QA

For user-facing behavior:

1. Identify and verify a disposable local database and Django settings before
   starting Django and Vite. The in-memory check/test configuration above is not
   a persistent visual-QA database; do not fall back to an unverified default
   environment or rely on an empty `POSTGRES_DB`.
2. Exercise the changed workflow in a real browser, including the affected controls.
3. Prioritize desktop and landscape tablet. Use representative viewports such as
   `1440x1000`, `1280x800`, and `960x600` when the layout breakpoint is relevant.
4. Check phone only for regressions when the changed workflow is available there or
   when the request explicitly concerns phone behavior.
5. Inspect console errors, failed requests, overflow, clipped text, focus, keyboard,
   touch, loading, empty, error, success, and disabled states as applicable.
6. For UI PRs, attach 1–3 before/after screenshots using demo-only data; never
   commit the images. Stop temporary servers and remove local QA artifacts.

## Finish

Run `git diff --check` and `git status --short`. Report the behavior verified, checks
run, failures or skipped checks, viewports exercised, and remaining risks. In PRs,
use **Not verified** for skipped checks and **To confirm** for specific
manual checks or decisions. Do not commit, push, or deploy unless the user explicitly
asks.
