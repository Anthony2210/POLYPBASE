# Terminology and FR / EN / JA translations

Read this reference when working on user-visible text, i18n, or domain terms. See the [frontend reference](frontend.md#internationalization) for runtime behavior.

Use the confirmed product vocabulary below and the current catalogs. The [legacy translation review archive](../archive/translation-review.md) preserves historical review evidence, including decisions based on the “Traduction Polypbase” workbook. The archive and workbook do not drive current edits; earlier proposals are not approvals.

## A. Confirmed product vocabulary

| Concept | FR | EN | Exact context |
|---|---|---|---|
| Institution | Structure | Institution | The institutional entity (`Organization`) only, not every use of the word “structure.” Existing FR strings that say “institution” are not automatically rewritten. |
| Transplant | Repiquage, repiquer | Transplant (to transplant, transplanted, a transplant) | Biological operation that distributes polyps from a parent box to child boxes (`SubcultureEvent`). Use the form that fits the context. |
| Record | Relevé | Record | Biological record for a box: counts of polyps, ephyrae, and strobilae. At most one per box per ISO week. |
| Measurement | Mesure | Measurement | Temperature and salinity: a manual measurement or a timestamped probe reading. Never a biological record. |
| Access | Accès | Access | The right to access an institution. |
| Membership | Appartenance | Membership | An account's membership in an institution, with a role (`OrganizationMembership`). |
| Read-only role | Lecteurs | Read-only mode | An actual user role (`viewer`, read-only access). The EN label is accepted as is. |
| Manager privilege | Responsable | Manager (approved wording in G33) | An additional Admin privilege (`role=admin`, `is_responsable=True`), not a fourth technical role. Relinquishing it preserves the Admin role. |
| Box status `pending_review` | À vérifier | Pending | Label for this status only (`Box.Status.PENDING_REVIEW`), where the context refers only to that status. |

Rules:

- **Permission denials (permanent product rule).** Every visible permission-denial message asks the user to contact an administrator, in each interface language: “Merci de contacter un administrateur.” / “Please contact your administrator.” Do not add the request if it is already present, do not detail the missing permissions, and do not add it to an error that is not an authorization denial (validation, conflict, business rule, or technical error). This wording rule does not change backend authorization semantics.
- **Record ≠ Measurement.** Biological counts: Record. Temperature and salinity: Measurement. Never replace a word globally without checking what the key actually displays. The same FR word can apply in both contexts. For example, the “Mesures” audit family covers both and still needs a name.
- **Transplant and technical identifiers.** `subculture`, `SubcultureEvent`, `subcultureAction`, and similar names remain code identifiers and are not renamed. The English word “Subculture” never appears in user-visible text for this operation.
- **Access ≠ Membership.** Deactivating a membership removes access to that institution only.
- **Product roles**: Admin (Administrateurs), Technicien (Techniciens), Lecteur (Lecteurs), plus the Responsable privilege. The Django superuser is not a product role.
- **Missing data and zero**: `0` is a measured value and retains its digit (“0 polyps”, ポリプ0). “Inconnu / Unknown”, “Non renseigné”, and “Aucun relevé / No records” indicate missing data. This is never `0`.

Examples:

| Correct | Incorrect | Why |
|---|---|---|
| Latest record (biological record) | Latest measurement | Counts: Record |
| New salinity measurement | New salinity record | Salinity: Measurement |
| Save this transplant / Only an active box can be transplanted. | Save this transplanting / Subculture saved | Use the form that fits the context, not “Subculture” |
| Transfer between institutions | Transfer between organizations | Entity: Institution |
| Manual temperature for {zone} recorded | Transplant created from {code} | Meaning and variable of the FR key |
| Demote to Read-only mode | Promote to Read-only mode | Demotion; the role label is accepted |
| Ce compte ne peut pas créer de repiquage. Merci de contacter un administrateur. | Ce compte ne peut pas créer de repiquage. | Permission denial without a request to contact an administrator |

## B. Approved translations to preserve

- **Japanese**: The Japanese reviewer approved the 1,384 historical JA translations marked “Fait.” Never overwrite, normalize, retranslate, or automatically “align” approved Japanese, even to apply a rule from section A. Adaptations of that wording require an explicit decision and are never applied automatically. The 59 React keys independently reviewed by AI in the 2026-11-04 batch were not human-approved and did not resolve deferred questions; see the [historical Japanese provenance note](../archive/2026-11-traductions/provenance-japonaise.md). When a React key has no JA entry, the interface shows EN.
- **G36 glossary**: Lecteurs / Read-only mode / 閲覧専用 stays as is.
- **Historical workbook**: a Correction cell is a reviewer comment, not an approved value. Do not change or infer approval from historical evidence.
- **Reference Japanese values**, already approved: 容器 (box), 測定記録 (biological record), 測定値 (measurement), 移植 (transplant), 機関 (institution as an entity), 系統 (strain), 系譜 (lineage), 設置場所 (location), 現在の設置場所 (current location), 設置期間 (location period). Citing them does not reopen them for review.

## C. Key-specific exceptions

| Key | Language | Value to keep | Do not |
|---|---|---|---|
| `overviewEmpty` | EN | No living box to display. | Replace Living with Active |
| `movementKindUnclassified` | EN | Provenance not established (protected current application wording) | Automatically replace with Origin unknown (historical worksheet) |
| `adminZoneTypeCabinet` | FR / EN | Emplacement / Rack | Align with Armoire, Location, or Cabinet |
| `historicalTemperaturePeriod` | FR / EN | Mesuré pour la période : / Measured for the period: | Use “Période disponible” / “Available date range” (retired): the key precedes the week for a historical temperature |
| `auditValuePendingReview` | EN | Needs review (protected current application wording) | Replace with To be checked (historical worksheet) or Pending: the label is shared by box status and historical temperature status |
| G36 Lecteurs | EN / JA | Read-only mode / 閲覧専用 | Propose Viewer or 閲覧者 |
| Other uses of “À vérifier” | all | depends on context | Align with Pending: `auditMetaNeedsAttention` (a record requiring attention) and review actions are different concepts |

The current values `Provenance not established` and `Needs review` were recorded in the legacy register (T019/T022, then T764/T763) and are protected in `frontend/scripts/test-i18n-catalogs.mjs`. `Origin unknown` and `To be checked` reflect the historical worksheet, not a decision to replace the wording in the application. Any alignment requires a new explicit decision.

Do not import wording automatically from historical evidence or resolve discrepancies without a decision. `adminZoneTypeCabinet` was aligned with Emplacement / Rack by the product owner's explicit decision (2026-10-28).

## D. Unresolved language choices

Do not treat these as normative. Reuse the current approved value and raise the question with the product owner.

- English wording still proposed in `translation_review_queue.csv` (status other than `confirmed_product_decision`): none is approved.
- Japanese adaptations requiring new wording: movement provenance, 継代 / 移植 on an audit key, 機関 / 所属機関, the term for transfers between institutions, 撤去 for the end of a period, last known location, JA terms for Appartenance and Accès, and the request to contact an administrator in Django's transplant denial message.
- Institution or Working institution for the active institution workspace (`organizationMenuTitle`); “Non renseigné” or “Non fourni”; the name of the mixed “Mesures” audit family; `auditMetaMembershipId` (“Identifiant accès” refers to a membership); `django:gettext:Provenance non établie` (same exception as `movementKindUnclassified`?).

## Separate implementation tracking

The request to contact an administrator has already been applied in FR/EN to `boxActivateForbidden` and `manageErrorResponsableRequired` since B02 (register T058/T065 and T062/T069). B09 then adjusted the EN Manager wording for the latter (T707) without removing the request. The current catalogues and `frontend/scripts/test-i18n-catalogs.mjs` cover these denials: do not describe them as still needing correction.

The overall audit of visible permission-denial messages (frontend and Django messages returned by the API) is incomplete; B02 does not establish full compliance. `manualTemperatureForbidden` (EXCLU section of the worksheet) remains a candidate for review in a dedicated task. Distinguish each case from a validation or business-rule error before changing the text.

## New or ambiguous terms

1. Look up the key, the code that displays it, and the domain context ([domain reference](../INDEX.md)). The FR word alone is not enough.
2. Reuse a term from section A or B if it covers exactly that meaning.
3. Otherwise, do not invent product or scientific terminology: ask the product owner. New Japanese wording remains unresolved.
4. Add new copy in FR, EN, and JA through i18n, preserve placeholder names and counts (`{zone}`, `{count}`, etc.), and do not use the middle dot. Identify entries by exact key, not by line number or matching text.

## Frontend review workflow

From `frontend/`, use `npm run i18n:find -- <key or text>`. It searches keys and FR/EN/JA values for case-insensitive substrings. It reports the section file and line, values, review statuses, and any stale source. For example:

```sh
npm run i18n:find -- overviewEmpty
npm run i18n:find -- "No living box to display."
```

Edit the key in its owning `frontend/src/i18n/sections/<section>.ts` file and its review record in the matching `frontend/src/i18n/status/<section>.json`. Add keys in sorted order and provide FR, EN, and JA for new copy. For a wording correction, check all three values in that same entry, update those that need to change, and preserve approved text unless the correction is explicit. The legacy catalog may still omit JA. Each EN value and each present JA value must have a status record. FR is reviewed in the PR and has no status record. If you add or rename a section, run `npm run i18n:generate-index`.

| Status | Required fields | Meaning |
|---|---|---|
| `ai` | `status`, `source_sha256` | Use for new or changed translations that have not been human-validated. Do not add a `reviewer` field. |
| `validated` | `status`, `source_sha256`, `reviewer` | The exact translation and source were approved by the relevant reviewer: `english-reviewer` for EN or `japanese-reviewer` for JA. |

`source_sha256` is the lowercase, 64-character SHA-256 hex digest of the exact UTF-8 **string value**, not the commit, file, or string literal syntax. Hash FR for the EN record and EN for the JA record. Include whitespace, punctuation, and placeholders exactly; do not trim, normalize, or add quotes, a newline, or a BOM to the value being hashed.

A source change makes the old digest stale. `npm run i18n:find --` marks it `(stale source)` and the section checker warns without failing on that mismatch alone. The warning does not change the rendered value or status. Recheck the translation with the appropriate reviewer rather than refreshing an approval hash without review.

If an authorized edit changes a previously `validated` translation value, set it back to `ai`, remove `reviewer`, and hash its exact current source. Do not remove a previously validated value. The delta check rejects removal and rejects changed validated values that do not go back to `ai`, using the merge base with `origin/main`.

## Usage checks

From `frontend/`, `npm run check:i18n-usage` inventories statically detectable usages. A flagged key is not permission to remove it: check dynamic access, conditional workflows, and explicit exceptions. Run the [i18n QA checks](../operations/development-qa.md#i18n-checks) after editing.
