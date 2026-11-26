---
name: polypbase-ui
description: Build or change Polypbase screens and components (React, TypeScript, CSS) within its device, i18n, zero-versus-empty, and component rules. Use for any Polypbase frontend or interface task, before writing JSX or CSS.
---

# Polypbase UI

Polypbase is a laboratory tool used repeatedly at the bench. Speed, readability,
accuracy, and traceability come before decoration.

## Read first

- The "Interface" section of [AGENTS.md](../../../AGENTS.md).
- [Frontend reference](../../../docs/reference/frontend.md): Devices, Interface
  principles, Visual patterns to reuse, CSS cascade.
- "Frontend" and "UI and CSS" in [CONVENTIONS.md](../../../docs/CONVENTIONS.md).
- The [terminology and i18n reference](../../../docs/reference/terminology-i18n.md)
  when visible text changes.

## Before writing code

1. Name the devices affected. Desktop: administration, charts, exports, dense
   analysis. Landscape lab tablet: search, scanning, box details, data entry.
   Phone: consultation and scanning. Use `useIsDesktopApp`, `useIsTabletLayout`,
   and `useIsPhoneLayout`; never re-implement breakpoints.
2. Search `frontend/src/components/`, `frontend/src/hooks/`, and
   `frontend/src/styles/tokens.css` for what already exists. Reuse or extend it.
   If a reusable piece is missing, build it once as a shared component with its
   CSS Module, then use it. Never copy a component into a view.
3. List the states to handle: loading, empty list, error, missing data, value equal
   to zero, disabled, focus, long text, narrow width.

## Rules

- **Numbers.** An empty field stays empty and is never prefilled with `0`. A real
  `0` is accepted, saved, and displayed as `0`. Use `??`, not `||`, on numeric
  values; an empty input becomes `null`, never `0`.
- **Text.** Every visible string is an i18n key in `frontend/src/i18n/fr.ts`,
  `en.ts`, and `ja.ts`. Labels are short; one help sentence at most; details go to
  the user guide. No middle dot. Never overwrite approved Japanese.
- **Styles.** Semantic tokens only: no hard-coded color, font size, radius, shadow,
  or breakpoint. Component styles go in a `*.module.css`; shared styles stay in the
  existing CSS layers; no unlayered global override; no new UI library.
- **Interaction.** Actions work without hover, with visible focus, keyboard access,
  and touch-sized targets on tablet. A placeholder is not a label. Preserve scroll
  position after targeted mutations and avoid layout shifts.
- **Authority.** The interface may hide an action, but Django decides permissions,
  transitions, and validation. Administration stays desktop-only: on other devices
  its menu entry is absent and `/administration...` routes redirect to `/` with no
  intermediate screen.
- **Direction.** Do not introduce a new visual style on your own. When a change
  needs a new look, present two or three options and let Anthony choose.

## Generic design skills

The global `better-*` skills help with layout, typography, color, accessibility,
and copy. When they disagree with Polypbase rules or existing tokens, Polypbase
wins. `variant` (explore options) and `break` (stress a component in every state)
are user-invoked: suggest them instead of imitating them.

## Verify and report

- Run the TypeScript check, the build, and targeted tests; follow the
  `polypbase-qa` skill for the commands.
- Check every affected device width in a real browser with demo data only. For
  Administration, check desktop, then only the tablet redirect.
- Screenshots in a PR use demo data only, since the repository is public.
- Report: files changed, components reused or added, states covered, devices
  checked, and what was not verified.
