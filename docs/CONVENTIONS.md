# Contributor conventions

These rules apply to humans and agents. Start at the [documentation index](INDEX.md)
for domain contracts and follow the invariants and safety rules in [AGENTS.md](../AGENTS.md).

## Language and i18n

Write identifiers, comments, docstrings, developer docs, agent instructions, commit
messages, and PR titles and descriptions in English. Put all user-facing copy,
including placeholders, errors, and accessible labels, through FR/EN/JA i18n.
Reuse keys and follow the [terminology and i18n guide](reference/terminology-i18n.md);
never overwrite approved Japanese translations.

- **Good:** Name a handler `saveMeasurement` and render the translated save label.
- **Bad:** Name it `enregistrerMesure` and hard-code `Enregistrer` in JSX.

## Comments and docstrings

Explain why, not what. Use these labels only when they help a reader spot a
non-obvious constraint:

| Label | Good | Bad |
|---|---|---|
| `INVARIANT` | `INVARIANT: Zero is a reading; only null means missing.` | `INVARIANT: Check the value.` |
| `SECURITY` | `SECURITY: Scope the lookup before resolving IDs to avoid disclosing another organization's records.` | `SECURITY: Get the record.` |
| `PERF` | `PERF: Fetch chart data on open so startup does not load every series.` | `PERF: Fetch data.` |
| `COMPAT` | `COMPAT: Keep this URL while the root README links to it.` | `COMPAT: Old code.` |

Document non-obvious public functions, API entry points, and services. State the
purpose, authorization requirements (including what the caller must enforce), and
side effects such as writes, audit events, or notifications where applicable.
Delete commented-out code. A TODO must name a tracking issue.

- **Good:** `"""Correct a reading in the caller's authorized scope and record the change in the audit log."""`
  or `TODO(#123): Remove the fallback when old clients stop sending this field.`
- **Bad:** `"""Correct a reading."""`, a commented-out handler, or `TODO: clean this up`.

## Reuse and file size

Search for an existing implementation first. Reuse it or extend it without
breaking its callers. When the same behavior is needed a third time, extract a
shared implementation instead of adding a third copy. Do not abstract unrelated
code just because it looks similar. Start with these shared locations:

- Backend: [organization and permission helpers](../backend/apps/accounts/permissions.py),
  [measurement writes](../backend/apps/measurements/services.py), and
  [audit services](../backend/apps/audit/services.py).
- Frontend: [API client](../frontend/src/api/client.ts),
  [hooks](../frontend/src/hooks/), [components](../frontend/src/components/),
  [shared types](../frontend/src/types.ts), and [style tokens](../frontend/src/styles/tokens.css).

Aim for roughly 500 lines or fewer per hand-written source file. Treat that as a
review signal, not a hard limit: extract a cohesive responsibility when it makes
the change easier to understand, not just to meet a line count.

- **Good:** Use the shared client for a third organization-scoped call; extract a
  self-contained editor from an oversized view when changing that editor.
- **Bad:** Copy CSRF and organization-header handling into another component or
  split a view into arbitrary chunks solely to get under 500 lines.

## Backend

Keep views thin: authenticate, resolve the authorized active organization, scope
querysets and object/relationship lookups, validate through serializers, and
call services. Serializers own API input validation and output representation;
services own reusable domain rules, transactions, and write side effects. Models
and migrations enforce persistent invariants with PostgreSQL constraints where
possible. Treat client IDs as untrusted, including in exports and bulk actions.

- **Good:** Validate the payload in a serializer, pass authorized scope to the
  measurement service, and enforce one reading per box and week with `UniqueConstraint`.
- **Bad:** Look up a submitted ID globally, duplicate write rules in a view, or use
  an existence check as the only protection against concurrent duplicates.

## Frontend

Use typed props, API responses, and hook inputs and results. Extract reusable
stateful logic into typed hooks, not hidden effects in render helpers. Keep
feature-specific views, types, hooks, and helpers close to their feature; promote
only genuinely shared pieces to the existing shared locations. Keep permissions
and domain transitions on the backend.

- **Good:** Keep a measurement editor's local state with the editor and reuse
  [`useMutationDialog`](../frontend/src/hooks/useMutationDialog.ts) for common mutation UI.
- **Bad:** Move every field into `App.tsx`, cast API results to `any`, or recreate
  server write permissions in a hook.

## UI and CSS

Use existing components and semantic color, spacing, and typography tokens.
Reuse translated input placeholders and shared loading placeholders such as
[`SkeletonRows`](../frontend/src/components/SkeletonRows.tsx). A placeholder does
not replace an accessible label. Use CSS Modules (`*.module.css`) for new styles
that belong only to a component when existing styles do not fit. Keep shared
styles and tokens in the existing [CSS layers](reference/frontend.md#css-cascade);
do not add unlayered global overrides. Preserve focus, keyboard and touch access,
and loading, empty, error, and disabled states.

- **Good:** Reuse `SkeletonRows` for loading, use `var(--color-muted)` for input
  placeholder text, and scope a new editor layout to its CSS Module.
- **Bad:** Copy a spinner into every view, use placeholder text as the only label,
  or hard-code a color in a global `input::placeholder` override.

## Tests and performance

Test observable behavior and add a regression that fails without the fix. Use
`assertNumQueries` for query budgets on representative data; do not add tests
that match source code with regexes. Use isolated data and run focused checks
first; see [development and QA](operations/development-qa.md).

- **Good:** Assert that a zero value survives an API round trip and that a page
  stays within its query budget with multiple related rows.
- **Bad:** Check that a source file contains `prefetch_related` instead of testing
  returned data and counting queries.

Bound lists, date ranges, batch sizes, and retries as appropriate. Preload needed
relations with `select_related` or `prefetch_related`; lazy-load heavy views and
secondary data only when needed. Measure before and after on comparable data
and report the query count, request timing, or bundle impact that supports the change.

- **Good:** Paginate results, preload the rows' related data, and report the measured
  query count as the page grows. Load the chart series only when opened.
- **Bad:** Fetch all records on startup, query once per row, or claim a speedup
  without measuring it.

## Security and public repository

Treat Git history, PRs, screenshots, logs, fixtures, and CI artifacts as public.
Never include secrets, `.env` contents, production data, or private identifiers.
Enforce authorization and organization scope on the server; do not use production
or Neon for testing, demos, or trial imports.

- **Good:** Test that another organization's record is inaccessible with synthetic
  accounts and use demo-only data in review evidence.
- **Bad:** Trust the selected organization in the browser or post a production
  screenshot with real records in a PR.

## Migrations and dependencies

Make migrations reversible and verify forward and reverse on an isolated database.
For data migrations, preserve what the reverse needs; a no-op is not an undo.
If reversal cannot be made safe, explain why and get an explicit decision on the
recovery plan before merging. Follow the separate
[production migration procedure](operations/deployment-operations.md).

- **Good:** Backfill a new field without dropping the original data, and test undo.
- **Bad:** Delete the source values and mark the reverse as a no-op.

Reuse the standard library or an existing dependency first. For any new dependency,
explain why existing options are insufficient, and note relevant license,
maintenance, security, and footprint costs. Update the lockfile when applicable;
obtain approval before adding a production dependency.

- **Good:** Explain why a maintained parser is needed and record its license and cost.
- **Bad:** Add a package for a trivial helper and omit the rationale or lockfile.

## Commits and pull requests

### Commit messages

Use Conventional Commit headlines: `type(scope): imperative English summary`
or `type: imperative English summary`. Start the subject with a lowercase letter.
Count the entire headline: **72 characters maximum**, including the prefix, with
no trailing period. Use the same format for PR titles.

- Types: `feat`, `fix`, `perf`, `refactor`, `test`, `docs`, `ci`, `build`, `chore`, `revert`.
- Optional scopes: `boxes`, `measurements`, `probes`, `zones`, `species`, `dna`,
  `sharing`, `accounts`, `exports`, `admin`, `ui`, `i18n`, `deploy`, `backup`, `ci`, `docs`.
  Do not repeat the type as the scope: use `docs:` or `ci:`, not `docs(docs):`
  or `ci(ci):`. Also omit the scope for truly cross-cutting changes. Use `i18n`
  as a scope, never a type. Use `ci:` for CI-only fixes and `build:` for dependency changes.
- A body is required when the reason is not clear from the headline. Separate it
  by a blank line, wrap at 72 characters, and explain why and any notable
  consequences without repeating the headline. Separate footers by another
  blank line when needed. Examples: `Refs #123`, `Closes #123`, or
  `BREAKING CHANGE: Remove the legacy export field`.
- **Good:** `fix(measurements): preserve zero readings in exports`
- **Bad:** `docs(docs): Fixed export docs.` (repeated type, uppercase start, past tense, trailing period)

### Pull requests

Keep PRs small and non-stacked: one reviewable purpose, independently mergeable
into `main`. Once approved and checks pass, use a merge commit, not squash or
rebase merge. Follow the [PR template](../.github/pull_request_template.md):
include a summary, why, tests actually run and their results, and risks. Put
unchecked items under **Not verified** and specific questions or manual checks
for the product owner under **To confirm**. Use `None` when a section does not
apply. For UI changes, attach 1–3 before/after screenshots using demo-only data
to the PR; never commit the images.

- **Good:** Open a standalone fix against `main`, name the regression test you ran,
  and mark tablet behavior **Not verified** if you did not check it.
- **Bad:** Base a PR on another unmerged feature, report unrun checks as passed, or
  commit screenshots to the branch.

## Documentation index and compatibility stubs

Add or update the relevant entry in [the index](INDEX.md) when adding, moving, or
retiring a current guide. Link directly to canonical pages. Keep temporary stubs
until no references remain; search for inbound references before removing one.


- **Good:** Point new links to the English guide and retain its old URL while it is referenced.
- **Bad:** Remove a stub because the new page exists, leaving the README link broken.
