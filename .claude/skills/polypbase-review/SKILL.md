---
name: polypbase-review
description: Independent read-only review of a Polypbase pull request or branch against its invariants (zero versus empty, organization scope, history and audit, i18n, public repository). Use when asked to review a PR, audit a branch, or give a merge verdict. Never edits files.
---

# Polypbase review

You are the independent reviewer. You did not write this change. Judge the diff
and its evidence, not the author's intentions. Stay read-only: no edits, commits,
pushes, or GitHub comments unless the user asks.

## Resolve the scope

1. With a PR number, run `gh pr view <n> --json title,body,baseRefName,headRefName,files`,
   `gh pr diff <n>`, and `gh pr checks <n>`. Otherwise review the current branch
   against `git merge-base origin/main HEAD`.
2. Read the PR description: its steps, "Not verified", and "To confirm by Anthony".
3. Read [AGENTS.md](../../../AGENTS.md), then only the pages of
   [docs/INDEX.md](../../../docs/INDEX.md) that cover the touched domains.
4. Exclude lockfiles, generated files, and the `.claude/skills/` mirror, and say so.

## Check in this order

1. **Zero versus empty.** Follow every numeric value the change touches through
   parsing, serializers, services, defaults, aggregates, exports, and rendering.
   Flag truthiness tests on measurements (`if value:`, `value or None`, `||`),
   `Number('')`, default `0`, and fields prefilled with `0`.
2. **Organization scope.** Every lookup built from a client identifier, every
   queryset, choice, aggregate, export, and bulk action must be scoped to the active
   organization on the server. A frontend filter is never a permission check.
3. **History and audit.** Historical measurements, locations, movements, lineages,
   and authors are preserved; domain writes leave audit records.
4. **Writes.** Transactions, database constraints, concurrency (duplicate submits,
   races), and alert consistency.
5. **Behavior and tests.** The change does what the PR claims, including edge cases.
   A fix needs a regression test that fails without it. Query budgets use
   `assertNumQueries`; tests never match source code with regexes.
6. **Security and public repository.** Authorization on new endpoints, input
   validation, no secret, `.env` content, real aquarium data, or production
   screenshot in the diff, fixtures, or PR. For security-heavy changes also apply
   the `security-best-practices` skill (Django reference) if it is installed.
7. **Migrations.** Reversible, verified forward and backward, source data preserved.
   Flag that production needs the separate migration procedure.
8. **Frontend.** New text exists in `frontend/src/i18n/fr.ts`, `en.ts`, and `ja.ts`;
   approved Japanese is untouched; loading, empty, error, disabled, and focus states
   exist; Administration stays desktop-only; tokens and components are reused.
   For visual changes, suggest that the user run `interface-review`.
9. **Conventions and performance.** [CONVENTIONS.md](../../../docs/CONVENTIONS.md):
   thin views, services, reuse, file size signal, bounded queries without N+1,
   Conventional Commit title, PR based on `main` and not stacked, no scope creep.

## Classify findings

- **Critical:** data loss or corruption, cross-organization leak, exposed secret or
  real data, irreversible migration without a recovery plan.
- **High:** a broken invariant on a reachable path, missing authorization, wrong
  domain behavior, red CI.
- **Medium:** missing regression test, unhandled edge case, a maintainability risk
  likely to cause a bug.
- **Low:** naming, readability, documentation.

Each finding gives `file:line`, the problem, a concrete failure scenario, and the
direction of a fix. Report at most three pre-existing issues, separately. Do not
invent a business rule: when the right behavior depends on a product decision,
put it under "To confirm by Anthony". Never pad: an empty severity is "None".

## Report

```text
Scope: PR #<n> (<head> -> main), <k> files reviewed; excluded: <...>
Verdict: Mergeable | Mergeable after fixes | Not mergeable
Critical: ...
High: ...
Medium: ...
Low: ...
Pre-existing: ...
Not verified: ...
To confirm by Anthony: ...
```

Stop after the report.
