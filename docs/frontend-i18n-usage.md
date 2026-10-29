# Conservative frontend i18n usage check

## Commands and CI

From `frontend/`, with the existing development dependencies installed:

```sh
npm run check:i18n-usage
npm run test:i18n
```

The first command prints catalog counts, scanned files, referenced-key count and
all source-unreferenced keys; failures exit nonzero. The second runs catalog
validation, usage-check regressions (including the real repository inventory), and
TypeScript contract tests for the hardened translation paths.
`npm run test:all` automatically discovers `scripts/test-i18n-usage.mjs` as one
isolated entry point. It imports only the non-test helper `i18n-usage.mjs`, not
`test-i18n-catalogs.mjs`, so the two suites are not duplicated. Existing frontend
CI runs `test:all`; no CI workflow change is needed. No new dependency is added:
the checker uses the existing TypeScript parser and Node built-ins.

## Typed translation boundaries

All six consumers now reuse the existing `Translator` from `src/i18n/index.ts`;
there is no second translation-key contract or unchecked key assertion.

| Consumer | Original widening and chosen contract |
|---|---|
| `AccountInvitationsPanel.tsx` | Local `TFunction` accepted any string, although the table and panel use fixed keys and literal conditional branches. Both props now use `Translator`; backend role labels remain display text. |
| `AdminView.tsx` | Local `TFunction` propagated through the view and its account, environment, institution and transfer helpers. Fixed calls, the literal `ADMIN_FLOW_ITEMS` labels and `MEMBER_MUTATION_FEEDBACK` literal keys are checked through `Translator`. Backend text and permission decisions are untouched. |
| `OverviewView.tsx` | Local `TFunction` widened both the view and mini-chart props. Fixed labels and day/history conditional literal keys now pass through `Translator`; measurement presentation is untouched. |
| `ZonesView.tsx` | Local `TFunction` widened the exported pages and internal management, salinity and temperature helpers. All now use `Translator`; fixed labels and literal variants need no key construction or cast. |
| `auditPresentation.ts` | Local `Translate`, string-valued lookup tables, prefix tuples and a mutable summary key lost catalog identity. Translation-bearing values now use `TranslationKey`; translators use `Translator`. Arbitrary backend strings only select own entries in partial maps. |
| `biologicalTimeline.ts` | The label builder accepted any string despite using only fixed keys. It now accepts `Translator`, preserving its allocated-polyps-to-polyps fallback. |

Audit metadata field names remain arbitrary source data, not translation keys.
`getAuditMetadataKeyLabel` translates only a known own mapping; an unknown field
still uses the exact `key.replace(/_/g, ' ')` fallback. Catalog-key lookalikes do
not become translation identities. Action, object, value and description maps
also reject inherited properties before translation. Known labels, historical
prefixes, raw/date fallbacks, and scientific zero versus missing values retain
their existing presentation. Runtime regressions exercise both catalogs and
prototype-property inputs.

### Existing unmounted transfer-preview blocker

TypeScript exposed two pre-existing invalid calls in `TransferImportForm`:
`species` and `cultureStatus` are absent from both baseline catalogs. The old
translator therefore returned those exact raw strings in either language. The
form is retained but not mounted. No replacement key or wording is approved by
this task: `LEGACY_TRANSFER_PREVIEW_LABELS` explicitly preserves those two
fallback strings without passing invalid keys to a translator. A runtime test
compares them with the actual baseline translator fallback. Selecting translated
preview headings remains a separate product decision before mounting this form.
This does not reopen any B03 removal or add an orphan-check exception.

### Compile-time regressions

`test-i18n-types.mjs` uses the existing TypeScript compiler and an in-memory
fixture importing the real consumer signatures. It checks valid literals,
conditional variants and lookup maps, then asserts compiler diagnostics for
invalid literals and arbitrary strings across all six paths (including exported
zone variants). It also rejects an invalid literal map and mutates the real audit
action map in memory to prove its annotation catches an invalid mapped key. No
catalog, source fixture, suppression directive or dependency is written by these
tests. They run in both `test:i18n` and automatic `test:all` discovery.

## Reliable scope and deliberate limits

This is a **conservative exact-reference inventory**, not a dead-code proof or a
translator-binding/data-flow analyzer. It parses every non-test JS/JSX/TS/TSX
source under `src/` (including `.mjs`, `.cjs`, `.mts`, `.cts` variants), independent
of imports, routes or mounting. Retained/unmounted components therefore count.
It inventories the union of FR/EN catalog keys and reports parity failures.
Malformed source and unsupported catalog shapes fail rather than silently
producing an incomplete inventory. Catalogs are parsed as data, never executed.

A key has source evidence when it occurs as an exact AST string literal,
no-substitution template literal, or property-access name. This covers:

- `t('key')`, aliases of translators, and conditional literal branches;
- literal tables, tuple rules, function-return literals and dynamic lookups such
  as `t(issueLabels[issue.code])` when the table contains the complete keys;
- explicit literal unions and typed maps, without needing type resolution;
- direct catalog access and aliases, such as `translations[language].key`,
  `text.key`, and `text['key']` (including the seven chart-window keys).

The six translator-hardening consumers are all scanned:

```text
src/utils/auditPresentation.ts
src/utils/biologicalTimeline.ts
src/components/AccountInvitationsPanel.tsx
src/components/AdminView.tsx
src/components/OverviewView.tsx
src/components/ZonesView.tsx
```

In particular, audit lookup/prefix/description maps and CSV field/outcome/issue
maps contain complete literal keys. Their dynamic indexing does not require
prefix exemptions. Typing these consumers is a separate change, not performed
by the detector.

**False negatives are deliberate:** an unused map, unused literal union,
unrelated exact string, quoted object property, or unrelated `object.key`
property access can retain a key. An orphan means *no evidence within this
bounded analysis*, not permission to delete it. The checker cannot infer
concatenation (`'prefix' + suffix`), interpolated templates, generated keys,
external configuration, runtime API-provided keys or reflective access. If a new
consumer constructs keys without complete source literals, review and expose its
finite literal key set or record a justified exact-key retention decision. Do not
remove a translation solely because this checker reports it. Existing TypeScript
and catalog tests remain responsible for translation contracts, missing keys and
wording; this check does not validate arbitrary `t` calls or execute the app.

Only `src/` contributes evidence. The entire `src/i18n/` directory, declarations
(`.d.ts`, `.d.mts`, `.d.cts`), comments and ordinary identifier declarations are
excluded. Tests outside `src/` are never read. Inside `src/`, directory segments
`test`, `tests`, `__tests__`, `__mocks__`, `fixture`, `fixtures`, `__fixtures__`,
filenames beginning `test-` or `test.`, and `*.test.*` / `*.spec.*` files are
excluded. New test-only naming conventions must extend these exclusions and their
synthetic regressions; test literals must not become retention evidence.

## Baseline and exact retention decisions

Baseline `67644e5418a8008399d1ff57404067410609dd81` (`67644e5`), checked from
Git objects with this AST algorithm:

| Inventory | Count |
|---|---:|
| FR keys | 1,134 |
| EN keys | 1,134 |
| CSV keys per catalog | 132 |
| Non-test source files | 128 |
| Keys with exact source evidence | 1,131 |
| Source-unreferenced keys | 3 |
| Unreviewed orphans | 0 |

The actual orphan list is exactly:

| Key | Why retained |
|---|---|
| `adminTransferText` | B01 deferred transfer introduction (register T012); no runtime source reference. B03/B06 explicitly preserve it pending a transfer-screen decision. |
| `auditSummaryAlertResolved` | B03/B06 explicitly preserve the legacy alert summary. Current audit rendering uses `auditDescriptionAlertResolved`. |
| `manualTemperatureForbidden` | B02 deferred legacy temperature refusal with no source usage; B03/B06 explicitly preserve it pending permission-copy review. |

These three exact-key exceptions live in `scripts/i18n-usage.mjs`, with individual
reasons. There is **no prefix exemption**, including for `csv*`: all 132 baseline
CSV keys have source evidence. The real-source regression checks CSV evidence,
presence of the three protected keys, and coverage of all six consumer paths.
Historical exact catalog preservation remains a separate catalog/register
contract, not an assertion that a count alone proves key/value identity.

An exception fails when its key disappears from both catalogs, becomes referenced
in non-test source, or has a blank reason. A newly referenced retained key should
lose its now-stale exception, not its translation. Missing keys in only one
catalog fail parity. A deleted protected key also fails the real-source regression.
There is no automatic catalog, register, gettext or Japanese translation rewrite.

These retention reasons follow the preservation record in
[`translation-review/application/README.md`](translation-review/application/README.md),
not a new wording or deletion approval. Any later authorized catalog change must
follow that append-only register workflow and
[`context/terminology-i18n.md`](context/terminology-i18n.md).

## Regression coverage

Synthetic cases cover direct and aliased property access, literal unions,
conditional branches, dynamic literal maps/tuples/function returns, escaped
literals, unmounted files, source locations, test/catalog exclusions, unsupported
syntax/catalog shapes, empty scans, parity, unknown orphans, unjustified and stale
exceptions, and the intentional unused-map/property-access limitation. A temporary
repository fixture verifies filesystem discovery without mutating real catalogs.
