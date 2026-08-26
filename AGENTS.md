# Polypbase Working Agreements

## Product context

Polypbase is a Django and React application for researchers and laboratory staff
who track jellyfish polyp cultures. Optimize operational screens for repeated,
accurate work rather than for marketing presentation.

Desktop and landscape tablet are both primary interfaces. Desktop is especially
important for administration, charts, exports, and dense analysis; landscape tablet
is especially important for operational laboratory work. Phone support must remain
functional, but deep mobile redesign is not currently the priority.

## Non-negotiable invariants

- Scope organization-owned data and permissions to the active organization.
- An administrator can act only within organizations they administer.
- Keep `0` as a real biological measurement; never treat it as missing data.
- Preserve historical measurements, locations, lineage, authorship, and audit data.
- Django remains authoritative for permissions, validation, and business rules.
- Do not change API contracts without checking every backend and frontend consumer.
- Never expose, print, copy, or commit secrets, `.env` contents, production data,
  passwords, database credentials, or SSH keys.
- Never run local tests or migrations against Neon or production.

## Engineering practice

- Inspect the relevant implementation, styles, types, permissions, and tests before
  editing. Follow existing patterns and keep changes within the requested scope.
- Preserve pre-existing work in a dirty tree. Never reset, clean, or restore unrelated
  changes.
- Do not add a production dependency or UI framework without explicit approval.
- Keep identifiers and code comments in clear English. Keep user-facing text in the
  translation system and preserve French and English behavior.
- Prefer structured parsers and existing helpers over ad hoc transformations.
- Treat temporary business rules as temporary: identify them clearly and avoid
  extending hard-coded dates or thresholds without validation.

## Interface practice

- Reuse the existing CSS tokens, typography, controls, spacing, and page patterns.
- Keep researcher-facing interfaces quiet, dense enough for work, and easy to scan.
- Avoid decorative icons; use familiar functional symbols only when they improve an
  action, such as arrows, close, add, or download.
- Do not use the middle dot character in interface copy.
- Preserve scroll position where practical and provide useful loading, empty, error,
  disabled, focus, keyboard, and touch states.
- For substantial visual changes, inspect the result in a real browser. Prioritize
  desktop and landscape tablet; check phone for regressions when the affected view is
  available there.
- Remove temporary screenshots and browser artifacts after verification.

## Verification

- Use `$polypbase-qa` for a complete local validation or release preparation.
- Backend tests must use `config.test_settings` or another explicitly isolated local
  database configuration.
- Run the frontend production build for TypeScript, CSS, and Vite validation after
  frontend changes.
- Use `$polypbase-deploy` for VM changes. Production mutations require explicit user
  approval and the documented backup and verification workflow.
- Do not commit, push, or deploy unless the user explicitly requests it.
