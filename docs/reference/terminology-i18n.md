# Terminology and FR / EN / JA translations

Read this reference when working on any user-visible text, i18n, translation, or domain term. The i18n mechanisms (`frontend/src/i18n/` catalogues, FR/EN parity, `CONTENT_LANGUAGES`) are in [`frontend-ux.md`](frontend.md).

Sources: product decisions approved by the product owner and historical translations from the “Traduction Polypbase” workbook, archived outside the repository. The terminology rules below are the reference for new development. Changes actually applied to the application are tracked in docs/translation-review/application/register.csv. Earlier cleanup proposals are not decisions that can be applied automatically.

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

- **Japanese**: The Japanese reviewer personally approved the 1,384 JA translations marked “Fait.” Never overwrite, normalize, retranslate, or automatically “align” them, even to apply a term or rule from section A. Any Japanese adaptation requiring new wording remains unresolved until explicitly approved and is never applied automatically. Japanese is a partially supported interface language: only approved translations that are still compatible with current FR/EN are displayed; the other keys display English (manifest and the Japanese reviewer's review: [`../translation-review/japanese/`](../archive/2026-11-traductions/provenance-japonaise.md)). Japanese also remains a separate content language (`CONTENT_LANGUAGES`). Explicit exception, limited to the 2026-11-04 batch: 59 new React keys absent from the workbook use an independent AI review, tracked as `AI_REVIEWED` / `ja_ai_reviewed` in the manifest. They are neither human-approved nor validated by the Japanese reviewer, do not replace any historical Japanese translations, and do not resolve any deferred questions. See the contract and locked sources in the Japanese provenance note linked above.
- **G36 glossary**: Lecteurs / Read-only mode / 閲覧専用 stays as is.
- **Statuses**: never modify a “Fait” approval or a Correction cell. A Correction is a reviewer comment, not an approved value.
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

The current values `Provenance not established` and `Needs review` are preserved by the register (T019/T022, then T764/T763) and protected in `frontend/scripts/test-i18n-catalogs.mjs`. `Origin unknown` and `To be checked` reflect the historical worksheet, not a decision to replace the wording in the application. Any alignment requires a new explicit decision.

Application catalogues are never automatically rewritten from the historical workbook. Discrepancies identified during cleanup are kept in private archives outside the repository. Decisions and changes actually reviewed in the application are tracked in docs/translation-review/application/register.csv. Unresolved discrepancies must not be corrected automatically. adminZoneTypeCabinet was aligned with Emplacement / Rack in the application by the product owner's explicit decision (2026-10-28).

Every change to FR/EN application text, or decision not to change it, is tracked in `docs/translation-review/application/register.csv` and checked by `check_register.py` (see the README in that directory).

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
4. Add all new user-visible text in FR and EN to the catalogues, preserve variables (`{zone}`, `{count}`, etc.), and do not use the middle dot.
5. For the shared workbook: always identify a row by its exact key, never by row number; never restore a deleted key or deduplicate based on matching text.

## Usage checks

`npm run check:i18n-usage` inventories statically detectable usages. A flagged key is not permission to remove it: check dynamic access, conditional workflows, and historical script exclusions. `npm run test:i18n` checks the catalogues and the detector. The [application register](../translation-review/application/README.md) documents the integration; do not rework historical batches to make a current discrepancy disappear.
