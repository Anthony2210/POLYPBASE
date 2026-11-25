# Frontend and user experience

## Architecture

The frontend is a React/TypeScript application built with Vite. `frontend/src/App.tsx` orchestrates global state, internal routing, and shared data loading for the institutional workspace. Specialized views and components live in `frontend/src/components/`, reusable calls in `frontend/src/api/`, and shared types in `frontend/src/types.ts`. Styles are brought together in `frontend/src/styles/index.css`.

Startup loads only the profile, zones, and dashboard. It does not load the full box list: `data.boxes` contains only known boxes (opened details, recent access) and is complete only when `boxCollection.status` is `ready`. Routes that need the full list (zone views other than history, labels, and typed search) request it via `requestBoxCollection` in `App.tsx` and display their own loading or error state; `needsFullBoxCollection` in `utils/boxCollection.ts` lists those routes. Never display results, counts, or suggestions calculated from a partial list. Ordinary creation no longer loads this collection to predict a number: the server allocates the code in the creation transaction. A detail page reached by URL or QR is resolved through `/api/boxes/?q=` and then `/api/boxes/<id>/`, without the list.

The M8/M9 extractions are integrated: `PilotageView`, `CreateBoxPanel`, their leaf components, `QrSearchModal` (eager import), and `BoxPage` are in `components/`. `BoxPage` owns local detail state and interactions, and `LatestMovementBanner` owns its editor; `App` retains mutations, caches, navigation, institution context, and recovery. The detail page's mount key combines institution and box. Source/AST tests must follow the actual owner after extraction without weakening assertions. The current architectural recommendation is not to split modules further solely because of line count; it is not a permanent ban on extraction.

There is no React Router. The browser path is mapped to route state and managed through the History API. Before changing navigation, locate the relevant path parsers, navigation helpers, `popstate` handling, and internal links.

`frontend/src/api/client.ts` handles shared requests, the organization header, and CSRF tokens. Do not create a second implementation of these mechanisms in a component.

The React screens for external sharing are no longer mounted. The [sharing API contract](organizations-permissions-sharing.md#external-read-only-sharing) is separate from donation proposals.

## Request freshness: built-in guards and limits

The institution generation guard prevents a response from an old context from replacing the new one. Within a single institution, `App.tsx` now uses a per-box revision, targeted invalidation of in-flight detail GET requests in `api/client.ts`, a watermark captured at the start of a mutation, and guarded publication that is checked again when React applies the updater. The watermark also covers IDs discovered in creation and transplant responses. A concurrent mutation response whose freshness cannot be established is not published directly: a fresh read reconciles the detail. Revisions are cleared when the generation changes; the client also stops sharing old detail GET requests when the institution changes. Invalidation does not remove promises that callers are already awaiting: publication guards remain essential.

Zone collections go through `readZoneCollection` / `refreshZones`: fetch all pages, deduplicate IDs, and publish the whole collection only if its generation and revision are still current, including when React applies the update later. A superseded read becomes a no-op; a page failure preserves the previous complete collection. Zone mutations and targeted reads have their own guards. `refreshZoneSalinityCapability` only reissues the zone's salinity history GET and updates `latest_salinity` without changing which zones are in the collection (`null` if empty, never an invented zero). The `/api/thermal-zones/...` GET requests on this path use `cache: 'no-store'` and do not join a previous shared GET. `refreshRecovery` remains a single slot for retrying reads: never automatically replay a write.

These boundaries are client-side tokens, **not a server commit order** or a global guarantee against all concurrent writes. The tests `frontend/scripts/test-box-read-freshness.mjs`, `test-zone-read-freshness.mjs`, and `test-zone-refresh-pagination.mjs` cover out-of-order responses, zero, deferred publication, and GET-only recovery, among other cases. Their presence does not establish that anything has been deployed.

## Devices

- **Desktop**: administration, charts, exports, and dense analysis views.
- **Landscape lab tablet**: search, scanning, box details, and operational data entry.
- **Phone**: must remain usable without requiring a major redesign for every change.

The tablet layout uses a permanent 88px side rail, not top navigation: it applies from 760 to 1023px, extending to 1180px with `pointer: coarse`, except where the phone layout applies (under 760px, or portrait up to 900px). The `useIsTabletLayout` / `useIsPhoneLayout` hooks and `styles/responsive/tablet.css` implement this contract.

Administration is desktop-only. `frontend/src/hooks/useIsDesktopApp.ts` centralizes detection: a minimum width of 1024px **and** `hover: hover` **and** `pointer: fine`; width alone is not enough. On non-desktop devices, the Administration navigation entry is absent and any `/administration...` route is replaced with `/` before the view renders. There must be no intermediate admin screen, no message asking users to switch to a computer, and no attempt to make these pages responsive without a new product decision. This display rule never replaces backend permissions. The active sections are Équipe, Inventaire, Espèces, Emplacements, Envois et réceptions, and Historique. The former sections Référentiels partagés and Institutions are no longer routed; their old URLs on desktop return to Pilotage.

## Interface principles

- Design for repeated use: dense but readable, with predictable controls and concise text.
- Reuse existing CSS tokens, components, icons, and interaction patterns.
- Preserve scroll position during targeted mutations; avoid unnecessary global reloads.
- Avoid layout shifts that disrupt repetitive use.
- Actions must remain understandable without hover. Provide visible focus, keyboard access, adequate touch targets, and disabled states.
- Clearly distinguish loading, an empty list, a network error, missing data, and data equal to zero.
- Do not display the same information twice when it is already clear within the same block.
- Do not duplicate in React a permission, transition, or validation for which Django is the authority.

For significant changes, inspect the result in a real browser on the devices actually affected. Check Administration on desktop; on tablets, check only that the menu is absent and the redirect works. Remove temporary screenshots and QA artifacts afterward.

## Ordinary box creation

The home button opens `CreateBoxPanel` as a modal on desktop and tablet; phones use the same component inline. Admins and technicians retain their permissions. The form asks for an existing strain (`StrainPicker`: accent- and case-insensitive search, usable strains only, grouped by species, with the Souche N label, stored code, and active box count), the location, entry date, initial polyp count (required, `0` is valid), an optional DNA evidence link, and an optional note. It does not offer an editable code or number or preselect a value. Descriptions and reasons for ineligibility are not rendered; no historical data is changed. The final code is displayed only after the server confirms creation. This form does not offer strain creation; any future species or strain creation flow must fit into the picker.

The form uses `POST /api/boxes/automatic/intents/` and then `/intents/execute/`: first it durably registers an immutable intent tied to the actor, institution, UUID, and an independent recovery secret, then the server allocates the code atomically. No additional confirmation is requested. Before any request, the session saves the raw UUID and a complete v2 envelope containing the scope, secret, parameters, and integrity fingerprint. After an interruption or reload, an intact envelope resumes registration with exactly the same identity and parameters, even if the first registration never reached Django. The panel resumes automatically with bounded delays, including on reconnection and reopening. A committed creation is recovered; a durable rejection releases the draft, unlike a plain `4xx`. Bare UUIDs, old records without a secret, corrupted envelopes, and mismatched UUIDs remain inspection-only: no automatic execution or cancellation, and no replacement after `unknown`/`pending`. Committed boxes stay visible even if another candidate remains uncertain. No identifier, secret, or technical control is shown to the researcher. See the [creation contract](boxes-lifecycle.md#automatic-creation-of-an-ordinary-box).

## Administration: references by domain

- [Espèces: catalogue, detail page, and dialogs](taxonomy-species.md).
- [Envois et réceptions: donation proposals](imports-exports-transfers.md#whole-box-donation-proposals).
- [Box creation, events, and detail history](boxes-lifecycle.md).
- [Tests and QA by device](../operations/development-qa.md); [catalogues and vocabulary](terminology-i18n.md).

## Administration: Espèces

The catalogue and detail page use institution-scoped routes with `X-Organization-Id`. Navigation uses the profile ID or, if needed, the shared reference ID. After association, the old URL returns the full profile and its revision. Box counts and dates remain hidden until the box collection is complete. Retrying after an error does not switch institutions.

A species already used by the institution offers the same actions with or without a profile. Opening or closing the detail page or a dialog writes nothing. “Nom scientifique et WoRMS” is prefilled and cannot submit an unchanged state. The normalized name is limited to 150 characters and follows the format validated by Django; an invalid non-empty name shows a field-level error and blocks saving, even without a WoRMS reference. The server remains the authority. If the revision is stale, the interface offers to reload. “Nouvelle souche” follows `can_create_strain` and `strain_creation_issue`, never the displayed code; after creation, the detail page is fetched again. See the [taxonomy contracts](taxonomy-species.md#institutional-catalogue).

## Visual patterns to reuse

- Entity page: back navigation, a type label, a clear title, and actions at the same level. On Espèces, Boîte, or Emplacement detail pages, do not repeat the section header in the detail view.
- Short forms: reuse box dialogs and their validation states; keep cancellation, initial focus, and focus restoration explicit. Correct a movement in its banner, not in a new modal.
- Operational table: align values and dates, keep rows compact, and keep the action menu stable. A `BoxTrackingPreview` fetches data only when opened and remains accessible by keyboard focus.
- Charts: make zero visible, distinguish missing data from breaks, and never present interpolation as an observation. Hover, focus, and touch must provide the same information.
- Start from the [components and CSS organization](../../frontend/src/styles/README.md), tokens, and existing screens. Do not add a variant simply because a screen is isolated. Check large and small layouts, translated labels, keyboard navigation, and visible focus.

## CSS cascade

`frontend/src/styles/index.css` defines the `tokens`, `base`, `layout`, `components`, `pages`, `responsive`, and `print` layers in that order. Put shared variables in `tokens.css`, reusable patterns in `components/`, and view-specific compositions in `pages/`. Device-specific adjustments belong in the `responsive` or `print` layers; do not create an unlayered stylesheet to override a component. Use the existing color, spacing, and text tokens before introducing a variant.
