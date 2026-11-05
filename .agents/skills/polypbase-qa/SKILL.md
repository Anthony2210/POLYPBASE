---
name: polypbase-qa
description: Validate Polypbase changes locally with isolated Django tests, frontend checks, and real-browser QA for desktop and laboratory tablet. Use after implementation, before commits or releases, and for regression checks. Never touch Neon or production.
---

# Polypbase QA

Verify the requested behavior without risking shared or production data.

## Establish scope

1. Locate the repository and read its `AGENTS.md`.
2. Inspect `git status --short`, the relevant diff, and the files involved in the
   changed workflow. Read additional documentation only when that workflow needs it.
3. Preserve every pre-existing change. Never reset, clean, restore, or overwrite
   unrelated work.
4. Never read or print secrets from `backend/.env`.

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
  settings, which load dotenv; neither promises that `.env` is not read.
- An empty `POSTGRES_DB` alone is not an isolation boundary. In Windows PowerShell
  5.1 it can remove the environment variable, allowing dotenv to restore a
  configured PostgreSQL value. Do not use default settings for unverified QA.
- Follow the explicit SQLite commands and child-process boundary in
  [the development guide](../../../docs/context/development-deployment.md#backend-isolé).
  In-memory settings are for checks/tests, not persistent visual-QA data.
- Never run `migrate`, `loaddata`, `flush`, or `seed_demo_data` against the default
  environment unless the user has explicitly identified an isolated database.
- Do not use production accounts or production data for QA.

## Run deterministic checks

**Do not run the current `scripts/run_checks.ps1` in an unverified environment.**
It only empties `POSTGRES_DB`, runs Django configuration and migration-drift
checks without explicit settings, and leaves the PostgreSQL test opt-in inherited.
It therefore does not establish the isolation required above.

A separate code correction must select unconditional SQLite settings for those
checks and explicitly neutralize the PostgreSQL opt-in for ordinary SQLite tests.
Until reviewed, use the explicit commands in the development guide instead.
Frontend checks can be selected separately from `frontend/package.json`.
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
6. Stop temporary servers and remove temporary screenshots, traces, and scripts.

## Finish

Run `git diff --check` and `git status --short`. Report the behavior verified, checks
run, failures or skipped checks, viewports exercised, and remaining risks. Do not
commit, push, or deploy unless the user explicitly asks.
