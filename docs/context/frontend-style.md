# POLYPBASE frontend style

## Purpose

This document captures POLYPBASE's established frontend design language and
how to reuse it. It is not a pixel-perfect specification: current code,
components, CSS tokens, tests, and product decisions remain authoritative for
implementation details. Product need comes before visual consistency when the
two genuinely conflict; a new pattern needs a reason, not novelty.

**Reuse before inventing.** Before adding a page structure, card family,
interaction, list, preview, modal, status treatment, data presentation, or
visual vocabulary, find the closest existing POLYPBASE pattern. Reuse it when
it solves the same problem; adapt it only for a real context difference.

Pattern labels in this guide:

- **CANONICAL** — preferred starting point for a similar need.
- **CONTEXTUAL** — established and useful in its own product context.
- **LEGACY / AVOID** — do not copy without explicit product review.
- **UNDECIDED** — current evidence does not establish intent.

The labels are not a judgement of code quality. Existing UI is evidence, not
automatically a product rule.

## Core principles

### Reuse before inventing

Inspect the component, styles, tokens, and tests of the closest reference.
Prefer shared components and existing CSS families over one-off equivalents.

### Consistency beats local polish

A coherent screen using familiar controls is preferable to a more decorative
screen that introduces a local design language.

### Product hierarchy beats visual symmetry

Give primary scientific/operational work the space it needs. Do not force
equal columns or same-sized cards when business importance differs.

### Facts before interpretation

Show observations as observations. Do not derive warnings, success states, or
threshold meanings from values or colors unless an explicit product rule does
so.

### Progressive interaction

Keep routine read states compact. Reveal correction forms, advanced details,
and secondary actions when requested, rather than keeping every control open.

### Calm, professional, information-dense

POLYPBASE is a laboratory tool: favor factual language, readable hierarchy,
useful density, restrained decoration, and data as the visual focus. Avoid a
generic SaaS dashboard vocabulary. Density must not compromise scanning,
keyboard/touch use, or clear metadata.

## Canonical pattern catalog

### Page and entity identity — CANONICAL, with contextual composition

**Problem:** orient users immediately and distinguish identity from current
facts, actions, and supporting metadata.

- Strong references: box detail (`frontend/src/App.tsx`,
  `frontend/src/styles/components/entity-header.css`,
  `frontend/src/styles/pages/box-detail.css`); zone/emplacement detail and
  directory (`frontend/src/components/ZonesView.tsx`,
  `frontend/src/styles/pages/zones.css`); profile identity
  (`frontend/src/components/ProfileView.tsx`,
  `frontend/src/styles/pages/profile.css`).
- `entity-header` is a real shared component/style family, used with
  context-specific composition. Put the entity's name/code and identifying
  secondary metadata in identity; put current summary facts and relevant
  actions in their own areas. Profile identity is deliberately simpler.
- A value is not a KPI merely because it is prominent. Keep contextual facts
  secondary, and do not duplicate the same fact in the header and body.
- Reuse `entity-header` styles and established page heading/identity treatment
  when the entity shape fits. Do not copy a box header's four-column layout or
  dimensions as a global requirement.
- Misuse: turning every metadata field into a badge/stat tile, or imposing
  identical header columns on unlike entities.

### Measurement summary and correction — CANONICAL family; business rules contextual

**Problem:** make the latest reading quickly scannable while keeping creation
and correction deliberate.

- Strong references: box biological reading in `BoxPage`
  (`frontend/src/App.tsx`, `frontend/src/styles/pages/box-detail.css`) and the
  Emplacement salinity summary/editor in `frontend/src/components/ZonesView.tsx`.
- Reuse the `measurement-summary`, `measurement-module`,
  `measurement-editor-fields`, and shared save/action styles where appropriate.
  Read mode shows the value, date, and useful note; edit mode is exposed by an
  explicit action. Existing icons distinguish correction (pencil) from adding
  a new reading (+) in their established contexts; follow actual semantics,
  not icon folklore.
- Keep correction distinct from creation in labels and behavior. Include
  loading, locked/disabled, validation, save, cancel, and error states as the
  flow requires.
- Salinity's correction window/capability and its particular create/edit
  details are business rules, not generic UI rules. Do not transfer them to
  biological measurements.
- Misuse: leaving a full editor permanently expanded when users usually read;
  treating a missing reading as zero; making every measurement a standalone
  mini-card.

### Compact modal entry — CANONICAL interaction, CONTEXTUAL use

**Problem:** focus a bounded task without permanently expanding a page.

- References: manual temperature entry and established lifecycle, movement,
  confirmation, and batch dialogs (`frontend/src/components/ZonesView.tsx`,
  `frontend/src/components/BoxLifecycleModal.tsx`,
  `frontend/src/components/MoveBoxModal.tsx`,
  `frontend/src/components/ConfirmActionModal.tsx`).
- Prefer a modal for short, interruptible, self-contained entry/confirmation.
  Prefer inline presentation when the task is the page's primary repeated
  workflow or needs persistent side-by-side context (box measurement editor is
  one such context; new-box creation has modal/inline variants by layout).
- Reuse `ModalPortal` and the existing dialog conventions/styles. Provide a
  labelled dialog, clear primary/cancel actions, saving/error states, keyboard
  focus handling, Escape where appropriate, and focus return. Do not assume
  every current dialog implements every behavior equally; inspect its code.
- Misuse: using a modal for a long multi-stage workflow or using a permanent
  form when the action is occasional and bounded.

### Dense operational rows — CANONICAL

**Problem:** support scanning and acting on many entities without hiding the
business facts in nested cards.

- Strong references: Inventory and Emplacement `ZoneBoxes` directory
  (`frontend/src/components/BoxInventoryAdminSection.tsx`,
  `frontend/src/components/ZonesView.tsx`,
  `frontend/src/styles/pages/administration.css`,
  `frontend/src/styles/pages/zones.css`).
- Put identity and decision-relevant facts directly in the row; use restrained
  separators/surfaces, clear secondary metadata, and contextual row actions.
  Use semantic buttons/links for actionable rows, visible keyboard focus, and
  do not make hover the only route to an action. Reuse `BoxTrackingPreview`
  for box identity links where its rich preview is relevant.
- Avoid decorative lift/translation on every operational row. The zone
  overview's clickable cards are a distinct navigation context, not a rule for
  dense lists.
- Misuse: turning every row into a floating card, hiding key facts in a menu,
  or making an entire row an ambiguous click target when it contains separate
  actions.

### Rich entity preview — CANONICAL for box references

**Problem:** let users inspect box context from a list/history without losing
place or opening a second page.

- Primary reference and reusable implementation:
  `frontend/src/components/BoxTrackingPreview.tsx`, with
  `frontend/src/components/BoxTrackingChart.tsx` and
  `frontend/src/styles/components/popovers.css`.
- Used by Profile action history, inventory/zone rows, and movement history.
  It exposes the code as a normal navigable link and offers the richer
  tracking preview on pointer or keyboard focus. Reuse this component; do not
  build reduced one-off box previews just to satisfy a local list.
- Keep preview data/loading/error/empty states and keyboard dismissal/focus
  behavior. It is specifically a box preview, not a universal entity popover.
- Misuse: replacing it with a tooltip that cannot convey the timeline, or
  placing an equivalent duplicate preview beside it.

### Histories and event presentation — two CONTEXTUAL families

**Problem:** show what happened at the right level of detail without mixing
business meaning with audit storage details.

- Personal/institution action history: `ProfileActionsSection` and
  `AdminAuditSection`, using shared presentation pieces in
  `frontend/src/components/AuditTimeline.tsx` and
  `frontend/src/styles/components/audit-timeline.css`. This is a business
  activity narrative: readable event summary, relevant values/changes inline,
  useful context, and disclosure for details that are not already shown.
  Actor information belongs when it helps explain operational responsibility;
  whether it is useful depends on audience and event.
- Emplacement movement history: `frontend/src/components/ZoneMovementHistory.tsx`
  and `frontend/src/styles/pages/zones.css`. This is a directional operational
  flow with separate arrivals/departures, time, related place when relevant,
  and a factual weekly chart. The chart has textual accessible representation.
- Reuse the suitable family; do not force both into one universal timeline.
  Audit implementation metadata is not automatically user-facing history.
- Misuse: exposing raw audit descriptions, duplicating details already visible,
  or implying movement totals are biological activity.

### Operational layout — CONTEXTUAL composition, reusable principle

**Problem:** prioritize the main operational/scientific section while keeping
supporting controls nearby.

- Reference: Emplacement detail in `frontend/src/components/ZonesView.tsx`
  and `frontend/src/styles/pages/zones.css`. Its primary salinity/operational
  content precedes recent movements and secondary panels; the supporting
  sidebar is intentionally subordinate. Box detail also adapts composition
  for read-only and editable cases.
- Use asymmetric layout when the work has a clear primary/secondary hierarchy;
  stack complex columns when available space becomes uncomfortable. Reuse the
  CSS grid/layout tokens, not a fixed column ratio.
- Misuse: equalizing sections for visual symmetry, or keeping a dense sidebar
  beside content when it becomes cramped.

### Search and lookup — CANONICAL interaction principles

**Problem:** locate a box quickly using identifiers familiar to lab users.

- References: Pilotage lookup in `frontend/src/App.tsx` and
  `frontend/src/components/SearchField.tsx`; shared matching in
  `frontend/src/utils/boxLookup.ts`; phone lookup is a separate responsive
  interaction in `App.tsx`.
- Reuse `SearchField` and the matching helper where applicable. Keep search
  forgiving of case/outer whitespace, rank precise matches ahead of broad
  matches, and make result identity/context scannable. The exact searchable
  fields and visible result count are not global design rules.
- Misuse: inventing a new ranking behavior per page or making a hover-only
  result interaction.

### Forms and creation flows — CANONICAL principles; workflow-specific

**Problem:** collect accurate data efficiently without overwhelming routine
users.

- References: new-box flow (`CreateBoxPanel` in `frontend/src/App.tsx`),
  measurement entry, and established modal forms. Reuse form controls,
  shared field styles, and existing field components; put related fields
  together, expose advanced choices progressively, and keep one clear primary
  submit action with a visible cancel/secondary action where appropriate.
- Present validation/error feedback near the affected input or form and keep
  values/context available after recoverable errors. Use server/business
  validation as authority; frontend validation assists but does not replace it.
- Creation fields and grouping depend on the entity and task. Do not reproduce
  the new-box form's particular fields in unrelated forms.

### Profile/preferences — CONTEXTUAL

`ProfileView` (`frontend/src/components/ProfileView.tsx` and
`frontend/src/styles/pages/profile.css`) groups identity, organization context,
preferences, action history, and sign-out. Reuse its clear sectioning and
compact settings controls for account/preferences tasks, not as the default
layout for scientific/operational pages.

### Labels workspace — CONTEXTUAL

`LabelsView` and the shared `QrLabel`/`QrLabelModal` implement selection,
preview, and print-specific geometry. Reuse them for label workflows; the
print preview's precise physical layout is not a general page card/layout rule.

## Scientific data presentation

These are semantic invariants, not stylistic preferences:

- Scientific `0` is a real measurement. Zero, null/missing, and unknown are
  distinct; never use truthiness to collapse them.
- A target/consigne is distinct from an observed value. Label each honestly.
  Min/Max are not automatically alert thresholds.
- Color must not invent business meaning. Red/green do not automatically mean
  bad/good, and a status badge must come from an explicit product rule.
- Labels and units describe what the value actually represents. Keep date and
  provenance/context where they are needed to interpret a reading.
- Current chart conventions include Polypes and Éphyrules series tokens
  (`--color-primary`, `--color-ephyrae`) in
  `frontend/src/styles/components/biological-trend-chart.css`. Emplacement
  movement charts distinguish Entry and Exit with existing success/danger
  tokens. These are established visual distinctions in their specific chart
  contexts; they do not define generic health/quality semantics.
- A factual status such as a lifecycle state may use its established status
  presentation (`frontend/src/boxStatus.ts` and shared primitives). Do not
  extend it to arbitrary measurements.

## Cards, surfaces, and hierarchy

Use a separate surface when it defines a meaningful unit: a dialog, a
self-contained entity summary, a distinct operational panel, a selected
workspace step, or a navigable overview item. Do **not** wrap every metric or
small text group in an independent card merely to create structure.

Current page families establish hierarchy with shared spacing and typography,
section grouping, selective subtle surfaces, and dividers. Dense lists often
use rows and separators; headers and summaries may use a bounded panel where
it clarifies a real unit. The box reading summary's surface is a specific
module, not a mandate for a card around every value.

## Color

Use semantic tokens in `frontend/src/styles/tokens.css` and existing component
styles: ink/muted text, page/surface/line neutrals, primary actions/focus,
and contextual series/status tokens. The token set includes primary,
success, warning, danger, temperature, and ephyrae roles. Do not hardcode a
new color family for one screen or add a semantic token for a distinction an
existing token/pattern already expresses.

Color is presentation, not a business state. Pair color distinctions with
labels, shape, position, or text; never rely on color alone. Existing special
colors in charts or lifecycle treatments should remain limited to the
semantics their product context establishes.

## Typography, density, and microcopy

Reuse `--font-body`, `--font-display`, existing type styles, and page
components rather than introducing fixed font sizes as global rules. Establish
an obvious order: page/entity identity first, section title next, value or
business event next, then muted metadata. Operational rows can be dense when
identity, decision-relevant facts, and secondary context remain distinguishable.

Avoid repeating a label when the context already makes it clear, but preserve
units, dates, and qualifications needed for professional interpretation. Use
direct business language. Do not add explanatory microcopy to every control;
add it when it resolves a real ambiguity or prevents an error.

## Actions

Use one visually clear primary action for the current task and subordinate
secondary actions. Reuse `primary-button`, `secondary-button`, `icon-button`,
`RowActionMenu`, and existing popover conventions where they fit. Compact
icon buttons need an accessible name and adequate hit area; destructive actions
need clear wording and existing danger treatment, often with confirmation when
the operation warrants it.

Pencil and plus are used for correction and creation in established
measurement contexts; confirm the local meaning before reuse. `•••` is a
contextual menu affordance, not a substitute for the main action. Links to a
complete list/detail should be explicit when they leave a summary. Avoid
repeating the same CTA in multiple places without a task-flow reason.

## Responsive design

- Desktop can use available width and richer layouts where the task benefits.
- The laboratory tablet must remain genuinely usable for lookup, scanning,
  box detail, and operational entry. Stack complex multi-column layouts when
  space becomes uncomfortable; do not preserve desktop columns at any cost.
- Phone prioritizes the primary task and uses established phone-specific
  navigation/lookup where present.
- Administration is desktop-only: its menu entry is hidden on tablet and
  `/administration...` redirects directly to `/` on tablet, with no
  intermediate UI or message. Do not invent an Administration tablet screen.
  This UX guard does not replace backend permissions.
- Reuse `frontend/src/styles/responsive/tablet.css`,
  `frontend/src/styles/responsive/phone.css`, and the existing layout hooks.
  Check actual supported widths and interactions rather than copying a single
  breakpoint or dimension into a new page.

## Accessibility

- Use semantic buttons for actions and links for navigation. Dense rows and
  row actions must be keyboard accessible; visible focus is shared through
  `frontend/src/styles/base.css` and component styles.
- Never make hover the sole way to discover or use an action. Preserve touch
  operation and adequate hit areas.
- Dialogs should have a name, `role="dialog"`, `aria-modal`, sensible initial
  and return focus, keyboard containment, Escape behavior where appropriate,
  and safe close behavior while saving. Existing implementations vary; inspect
  the nearest dialog rather than assuming all details are automatic.
- Color is not the only information channel. Charts should have an accessible
  label and a textual/screen-reader equivalent when their data matters; see
  `ZoneMovementHistory` and `BoxTrackingChart` for chart-specific examples.
- Keep loading, empty, error, disabled, and focus states, and distinguish empty
  data from zero.

## Anti-patterns

Avoid these unless product evidence gives a specific reason:

- Generic SaaS-dashboard rows of KPI cards, equal-sized cards without business
  hierarchy, or a card for every value.
- Arbitrary gradients, shadows, motion, or decorative effects that compete
  with the data. The existing entity header/overview use selective surface
  treatments; they are not a mandate to decorate every section.
- Microcopy everywhere, duplicated labels, or duplicated CTAs.
- Invented status badges, red/green data semantics, or alerts inferred from
  values without an explicit product rule.
- Hover translation/lift on every operational row or hover-only actions.
- Replacing `BoxTrackingPreview` with a lightweight one-off preview.
- Building a one-off component when an established POLYPBASE component or
  pattern fits; adding a new color family for one page; rebuilding the
  measurement summary/editor family without a real need.
- Copying implementation-specific widths, heights, grid ratios, breakpoints,
  or chart dimensions as global requirements.
- Forcing symmetry over the business hierarchy, keeping complex columns when
  they no longer fit, or making every form permanently expanded.
- Local polishing that reduces global consistency or makes repeated work less
  predictable.

## Reference matrix

| Need | Primary POLYPBASE reference | Reusable pattern |
|---|---|---|
| Entity detail | Box detail; Emplacement detail | Shared `entity-header` family, contextual identity/summary/actions |
| Measurement summary | Box biological reading; Emplacement salinity | Compact read state, explicit progressive correction/create state |
| Dense box directory | Inventory; Emplacement `ZoneBoxes` | Scannable operational rows, direct facts, restrained separators |
| Rich box preview | `BoxTrackingPreview` | Shared anchored tracking preview from box references |
| Business history | Profile actions; admin audit | Business-first summaries, details only when useful |
| Physical location history | `ZoneMovementHistory` | Separate directional operational event lists and factual flow summary |
| Search / lookup | Pilotage; phone lookup | Shared `SearchField`/lookup matching with context-aware presentation |
| Profile/preferences | `ProfileView` | Identity plus grouped organization/preferences/actions |
| Modal data entry | Manual temperature; lifecycle/move dialogs | Focused bounded task using portal/dialog conventions |
| Labels | `LabelsView`, `QrLabel` | Selection and print-specific preview workspace |

## Current pattern classification

- **CANONICAL:** `entity-header` as a reusable family (not a fixed composition);
  measurement summary/editor interaction; dense operational rows; rich
  `BoxTrackingPreview`; Pilotage search/lookup principles; semantic tokens and
  shared focus/dialog primitives.
- **CONTEXTUAL:** Profile's account layout; Emplacement's asymmetric operational
  hierarchy and movement flow; zone overview cards and their selective hover
  treatment; labels' print workspace; box-specific header accents and charts.
- **LEGACY / AVOID:** no entire audited page was conclusively identified as
  legacy from this scope. Avoid the anti-patterns above; do not label an
  uninspected legacy screen canonical merely because it exists.
- **UNDECIDED:** where a one-off style reflects product intent versus historical
  implementation, where actor visibility is useful outside the current audit
  contexts, and whether other legacy screen families should be migrated. Decide
  from product need and reviewed evidence, not visual inference.

## Before implementing a frontend screen

1. Identify the user task and the facts/actions that matter most.
2. Read this guide and the relevant business context.
3. Find the closest established POLYPBASE reference.
4. Inspect its current component, styles, tokens, and tests.
5. Reuse before creating patterns; adapt only for a real contextual need.
6. Preserve scientific and business semantics, especially zero/missing/status.
7. Add a new visual pattern only when established ones genuinely do not fit.
8. Test responsive layouts and keyboard interaction on relevant devices.
9. Get manual visual validation for significant UI changes.
