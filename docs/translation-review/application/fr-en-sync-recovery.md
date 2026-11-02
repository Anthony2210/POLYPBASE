# FR/EN synchronization recovery — 2026-11-02

## Baseline and recovered state

- Branch: `feat/i18n-fr-en-sync`.
- HEAD: `4866520eae2b30f676ab96365fd806486948e832` (`fix: update vulnerable Vite and source-map dependencies`).
- No staged changes at recovery or completion. No commit, branch change, push, merge, deployment, migration, dependency or lockfile change.
- Ten files were already dirty: the two frontend catalogs, four Django PO/MO files, accounts i18n tests, the application register, and the gettext checker and its tests.
- Actual recovered frontend changes were **126 EN and 4 FR**, not the reported 5 FR. `adminTransferTarget` already contained its HEAD value, `Institution destinataire`, although B09 T757 claimed `Structure destinataire` had been applied.
- The recovered register contained **840 events**, including 212 appended B09 events (T629–T840).
- Existing logs and scripts were recovered from `C:/Users/antoc/AppData/Local/Temp/i18nsync/`. The older `i18naudit` directory describes a different historical worktree and was not used as validation of this change.
- The existing isolated Django suite was still active and was not duplicated. No unrelated process was stopped. Other worktree processes were left untouched.

## Approval evidence and final catalog changes

The requested `C:/Users/antoc/POLYPBASE_INPUTS/Traduction Polypbase(4).xlsx` was absent. The verified copies are:

- `C:/Users/antoc/Downloads/Traduction Polypbase(4).xlsx`
- `C:/Users/antoc/POLYPBASE_PRIVATE_RETENTION/cleanup/Traduction Polypbase(4).xlsx`

Both have SHA-256 `e80b409fbea485d97fbccf66d06da83eb46374883d1e3343f50e51e656b8c307`. Neither workbook was modified.

The approval is the explicit product decision **Terminology + stylistic Fait**, including wording beyond AC01–AC28, subject to current functionality and terminology constraints. The Correction column was not treated as approval.

| Catalog | Existing values changed against HEAD | Entries added | Final changes |
|---|---:|---:|---:|
| Frontend EN | 126 | 0 | 126 |
| Frontend FR | 4 | 0 | 4 |
| Django EN | 15 | 5 | 20 |
| Django FR | 2 | 0 | 2 |
| Total | 147 | 5 | 152 |

Every changed identity has an exact workbook key marked Fait and an individual register event with initial value, final value, sheet value, context, decision reference, HEAD and workbook hash. The precise values and proof for each change are in `register.csv`, B09 T629–T759 and T810–T832, interpreted with the B10 reversals T841–T842. T757 and T821 are **not** final applied changes.

The 152 final changes comprise 132 matches to trimmed sheet wording and 20 contextual adaptations:

- Terminology/context: T643, T695–T696, T737, T744–T750, T820.
- Preserve administrator-contact invitations: T704, T707, T822.
- Preserve final punctuation: T691, T708, T734.
- Preserve grammar/meaning: T722, T735.

Both frontend catalogs still have **1,168 keys**. No key was added, removed or restored. All 202 frontend keys absent from the workbook, in each language, remain unchanged. Placeholder names and multiplicities are preserved. No Japanese text or language support was modified. No inline strings or functional application code were changed.

## Complete initial frontend failure list

The recovered `test_all.log` completed with **2,360 tests, 2,344 passing and 16 failing**. Numbers below refer to that run, not a stable test identifier.

| Test | Original failing test name | Class | Resolution |
|---:|---|---|---|
| 139 | EN measurement creation is readable and distinct from a correction | A | Expect Biological record saved / Biological record corrected; preserve the creation-versus-correction distinction. |
| 142 | the legacy measurement creation description is translated in EN | A | Expect Biological record saved; leave the legacy backend description fixture unchanged. |
| 144 | Responsable events render readable FR and EN wording | A | Expect Manager in EN; preserve raw historical event fixtures and all three transitions. |
| 147 | member role and activation changes are readable | A | Expect Read-only mode for the viewer label; role identifiers and activation assertions unchanged. |
| 152 | subculture summaries name all children and the parent in FR and EN | A | Expect transplanting; retain every child and parent identifier and rich-summary assertions. |
| 153 | a legacy subculture without usable children keeps the parent box visible | A | Expect Transplant from; retain the parent visibility fallback. |
| 163 | action and object type labels are translated | A | Expect Change saved; preserve unknown-action fallback and object labels. |
| 171 | salinity summaries and immutable values are readable in both timelines | A | Expect saved instead of recorded in the approved sentence; retain zero, immutable values, units and timeline assertions. |
| 195 | all eight audit families use the central FR/EN presentation registry | A | Expect Transplants; retain the full family list, ordering and registry contract. |
| 345 | tablet compact text and callbacks use existing en actions | A | Expect Transplanting; callbacks, accessible labels and phone actions unchanged. |
| 364 | specific inter-institution fr transfer keys are unchanged from HEAD | C, conservatively retained | Source already matched HEAD at recovery; preserve the entire test and its text, reconcile T757 with append-only T841. See analysis below. |
| 1318 | Move en: real central translator preserves supported labels and rendered state | A | Expect New location / No location; preserve date-free flow, history, pending state and translated rendering checks. |
| 1337 | SubcultureModal: en shared layout keeps labelled fields/errors in one body and actions outside it | A | Expect Transplanting in title and submit label; layout and accessibility assertions unchanged. |
| 1338 | SubcultureModal: en label, initial focus, forward/reverse Tab, idle Escape and opener restore | A | Same approved title expectation; all focus and keyboard assertions unchanged. |
| 1349 | MoveBoxModal: location flow preserves exact payload with null current zone and historical display | A | Expect No location; retain exact payload, missing-zone semantics and historical display. |
| 1857 | en: compact parent identity and honest advisory child identity | A | Expect Transplanting and Transplant note (optional); retain scientific state, identity and optional-allocation checks. |

There were no demonstrated category B frontend regressions or category D unrelated failures in this complete initial run. Only literal test expectations changed; no historical backend fixtures, permission assertions or business checks were weakened.

## Independent analysis of transfer test 364

`frontend/scripts/test-box-action-wording.mjs` was introduced by commit `23a9f3e` (`fix: improve mobile tablet laboratory UX`). Its local movement tests distinguish a movement within an institution from an inter-institution transfer. The transfer test compares 17 selected catalog keys in each language with the current HEAD; it is a **scope guard**, not a fixed protocol-version vocabulary snapshot.

The failing comparison was only FR `adminTransferTarget`: `Structure destinataire` versus HEAD `Institution destinataire`. The sheet marks Structure destinataire Fait; the consumer labels the destination Organization. No evidence established an immutable CSV token, authorization rule or historically frozen wording contract for this display label. Nevertheless, the terminology document also explicitly says existing French institution wording is not automatically rewritten, and the continuation request requires preserving the validated guarded contract when reconciliation remains uncertain.

Therefore the source remains **Institution destinataire**, and the test file is entirely unchanged. B10 T841 records the reversal of B09's claimed application state, explicitly noting that no source edit was needed during recovery. Approval to replace this guarded label and adapt the guard remains a follow-up, not an implicit decision made to get green tests.

## Django and append-only history

- Current msgids and source behavior are unchanged. Five EN translations for still-active msgids replace fallbacks, so their five exact EN coverage exemptions were removed.
- Preserve Record for biological data and Measurement for salinity/temperature. Transplant terminology and administrator-contact invitations remain intact.
- B10 T842 withdraws T821's sheet wording `The allocation exceeds the current parent box count.` The service compares against `state["polyp_count"]`, not a count of boxes. The original `The allocation exceeds the current parent count.` is preserved pending precise approved replacement wording.
- MO files were regenerated and checked after this reversal. Runtime accounts i18n tests were repeated afterward.
- Final register: **842 events in 10 batches**. Event-status totals: 96 ALREADY_MATCHING, 193 APPLIED, 70 DEFERRED, 45 PRESERVED, 438 REMOVED. Event totals are historical, not the number of final catalog changes.
- The original local register snapshot (`register.before.csv`, 357,406 bytes, 629 CRLF lines including header) remains an exact byte prefix. B09 rows are also retained without rewriting; B10 only appends two events. Git stores LF while the Windows worktree uses CRLF; comparison with the HEAD blob confirms identical historical content after that expected EOL conversion.

## Completed validation

| Validation | Result |
|---|---|
| Targeted six frontend regression files, including untouched transfer test | 251/251 PASS |
| `npm run test:i18n` | 243/243 PASS |
| `npm run check:i18n-usage` | PASS; 1,168 FR/EN keys, 1,165 referenced, three existing exact exemptions |
| `npm run test:all` final completed run | 2,360/2,360 PASS; no skips, cancellations or failures; 325.523 seconds |
| `npm run typecheck` | PASS; also repeated separately after the build |
| `npm run build` | PASS, including CSS architecture and TypeScript; Vite completed |
| `scripts/compile_django_messages.py` and `--check` | PASS |
| `scripts/check_i18n.py` | PASS; 98 source msgids; FR 65/98, 33 exempted gaps; EN 87/98, 11 exempted gaps; zero blocking errors |
| Compiler regression tests | 25 PASS |
| Gettext-checker regression tests | 20 PASS |
| Register checker | PASS, 842 events |
| Register regression tests | 62 PASS |
| Django `apps.accounts.test_i18n`, isolated settings | 5/5 PASS, repeated after final MO generation |
| Recovered full Django suite, isolated SQLite settings | 3,042 tests, OK, 496 skipped, 1,806.261 seconds, EXIT 0 |
| `git diff --check` | PASS |

Backend tests used `POSTGRES_DB=` and `--settings=config.test_settings`. The recovered full suite began before this continuation and overlapped catalog finalization; it was not restarted. The final-state runtime i18n checks and catalog checks were repeated separately. PostgreSQL-specific behavior is not established by the SQLite suite and was not changed here.

The first frontend full-suite attempt timed out after ten minutes with partial results through test 1,617, without a reported failure. Process inspection confirmed it was no longer running before a longer bounded rerun. A separate initial combined i18n/typecheck/build command also timed out; final build and TypeScript were completed separately. No green result is inferred from either partial command.

Validation logs remain outside the repository in `C:/Users/antoc/AppData/Local/Temp/i18nsync/`: `test_all.log`, `django_all.log`, `resume_targeted.log`, `resume_i18n.log`, `resume_usage.log`, `resume_all_final.log`, and `resume_build_verified.log`.

No real-browser QA was performed. Rendering, accessible-label, focus, keyboard and mutation regression tests passed, but visual fit of longer copy has not been manually verified.

## Deliberate deferrals and review notes

- Guarded FR destination label (T757/T841) and scientific allocation wording (T821/T842), as above.
- Existing application decisions Needs review / Provenance not established remain preserved despite conflicting terminology-sheet exceptions (T763–T764).
- Working institution naming, qualification/classification, opened-versus-scanned text, active-location restriction, compact malformed wording, singular/plural, pagination versus page-size, and area-versus-person labels remain deferred (T765–T775).
- T762 is En attente, not Fait. Django Lecture seule remains Viewer pending reconciliation with Read-only and G36 Read-only mode (T840).
- T760's rationale calls the measurements audit family mixed. Current backend classification instead routes manual salinity and temperature to environment; the documented naming decision is still open. Keep the value unchanged and reconcile the rationale in a subsequent append-only decision rather than silently rewriting it.
- T761's Recorded temperature is adjectival, like the accepted Recorded salinity, so Record/Measurement confusion alone does not prove a conflict. Its existing conservative deferral remains pending wording review.
- Transplanting on the action button is Fait-backed; the more specific Transplant proposal remains open. Existing movement 401/403 invitation exceptions are preserved.

None of these deferrals authorize automatic replacement or Japanese implementation.

## Exact final modified files

1. `backend/apps/accounts/test_i18n.py`
2. `backend/locale/en/LC_MESSAGES/django.mo`
3. `backend/locale/en/LC_MESSAGES/django.po`
4. `backend/locale/fr/LC_MESSAGES/django.mo`
5. `backend/locale/fr/LC_MESSAGES/django.po`
6. `docs/translation-review/application/register.csv`
7. `docs/translation-review/application/fr-en-sync-recovery.md` (this report)
8. `frontend/scripts/test-audit-presentation.mjs`
9. `frontend/scripts/test-box-action-refinements.mjs`
10. `frontend/scripts/test-move-box-modal.mjs`
11. `frontend/scripts/test-mutation-dialogs.mjs`
12. `frontend/scripts/test-quantitative-subculture.mjs`
13. `frontend/src/i18n/en.ts`
14. `frontend/src/i18n/fr.ts`
15. `scripts/check_i18n.py`
16. `scripts/test_check_i18n.py`

There are no application logic changes affecting institution isolation, backend permissions, zero versus missing data, audit/history, box lifecycle, desktop-only Administration, automatic box creation or probes. Their regression tests were retained; no production, Neon or shared database was used.

## Verdict

**READY FOR INDEPENDENT REVIEW**, with the explicit deferred wording decisions and lack of manual visual QA above. This is not authorization to commit, integrate or deploy.
