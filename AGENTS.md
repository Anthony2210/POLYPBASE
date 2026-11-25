# Polypbase working rules

## Get the context

1. Read this file.
2. Find the relevant domain in the [documentation index](docs/INDEX.md).
3. Read only the relevant pages. Consult the [project status](docs/STATUS.md) when the task depends on a dated state; do not treat it as a permanent rule.
4. Then inspect the related implementation, migrations, and tests.

## Inspection and scope

- Before editing, review `git status`, the existing diff, and the relevant code and tests.
- Preserve existing work. Do not use `reset`, `restore`, `clean`, `rebase`, or a destructive stash unless the need is understood and explicitly approved.
- Do not impose a solution before inspecting the code or add a production dependency without approval.
- Never invent a biological, scientific, operational, or authorization rule. If the requirements, code, tests, and documentation do not settle a question, ask for a product decision.
- Keep changes within scope. Avoid opportunistic refactoring and check every consumer before changing an API contract.
- Write code, comments, docstrings, documentation, agent instructions, skills, commit messages, and PR descriptions in English. Leave `README.md` unchanged. All user-facing text goes through FR/EN/JA i18n.
- Follow the shared [contributor conventions](docs/CONVENTIONS.md) for code, comments, tests, and review. Look for existing code to reuse or extend before writing a duplicate.

## Non-negotiable invariants

- **`0` is a real scientific measurement.** It is never equivalent to `null`, `None`, an empty string, missing data, or the absence of a reading.
- Authorize and filter all organization-owned data on the server in the active organization's context. Check querysets, client-provided identifiers, relationships, choices, aggregates, exports, and bulk actions. A frontend filter is never a permission check.
- Django is the authority for permissions, validation, and domain transitions.
- Preserve historical measurements, locations, movements, lineages, authors, and audit records.
- For every domain write, consider transactions, constraints, concurrency, alerts, and audit consistency.
- Never expose, copy, or commit a secret, a `.env` file, production data, a password, database credentials, or an SSH key.
- Never use Neon or production for testing, demos, or trial imports. Production migrations use a dedicated procedure and require explicit approval separate from application deployment.

## Interface

- Polypbase is a laboratory tool: prioritize speed, readability, accuracy, and traceability. Reuse existing components, tokens, and translations.
- **Administration is desktop-only.** On tablets, hide its navigation entry and redirect every `/administration...` URL to `/` without intermediate UI or messaging. This UX guard does not replace backend permissions. Do not make Administration responsive without an explicit product decision.
- Preserve loading, empty, error, disabled, and focus states, keyboard and touch access, and the distinction between an empty value and zero.
- Do not use the middle dot in the interface. Never overwrite approved Japanese translations.
- See the [frontend reference](docs/reference/frontend.md) for frontend and CSS architecture and the [terminology and i18n reference](docs/reference/terminology-i18n.md) for user-facing text, translations, and domain terms.

## Git and multiple agents

- Avoid having two agents edit the same files. Do not silently rewrite another agent's work.
- Reviews are read-only by default: inspect invariants, diffs, and tests, then classify findings before making requested corrections.
- Do not commit, push, or deploy unless explicitly asked. Review the final diff before committing.
- Use [Conventional Commit headlines](docs/CONVENTIONS.md#commits-and-pull-requests) for commits and PR titles. Keep PRs small and non-stacked against `main`; follow the [PR template](.github/pull_request_template.md) and use a merge commit when authorized to merge.

## Validation and production

- Validate in proportion to risk: run targeted tests first, then broader checks if a cross-cutting invariant is affected.
- As relevant, run isolated Django tests, migration checks, TypeScript, CSS, a Vite build, browser QA, and `git diff --check`. Follow the [development and QA guide](docs/operations/development-qa.md).
- Any production mutation requires explicit approval. Follow the [deployment and operations guide](docs/operations/deployment-operations.md) and use existing automation rather than rebuilding its steps.

## Agent skills

Edit only the sources in [`.agents/skills/`](.agents/skills/). From the repository root, run `python scripts/sync_agent_skills.py` to regenerate [`.claude/skills/`](.claude/skills/). The mirror contains real copies and removes entries absent from the source. Run `python scripts/sync_agent_skills.py --check` to verify inventory and bytes without writing. Do not edit the mirror by hand.
