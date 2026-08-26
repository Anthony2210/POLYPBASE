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

- Use `config.test_settings` for backend tests.
- Set `POSTGRES_DB` to an empty string for local SQLite commands and visual testing.
- Never run `migrate`, `loaddata`, `flush`, or `seed_demo_data` against the default
  environment unless the user has explicitly identified an isolated database.
- Do not use production accounts or production data for QA.

## Run deterministic checks

Run `scripts/run_checks.ps1` from this skill with the repository path. It checks Git
whitespace, Django configuration, migration drift, backend tests, CSS architecture,
TypeScript, and the production frontend build. Stop and report the exact failing
command when a check fails.

## Perform visual QA

For user-facing behavior:

1. Start Django against local SQLite and start Vite.
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
