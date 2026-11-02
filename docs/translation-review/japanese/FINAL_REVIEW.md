# Japanese interface — security correction handoff

## Verdict

**BLOCKED: the full isolated Django suite did not finish within its 900-second timeout.**

The three Gemini findings have implementation and targeted regression coverage, but there is no final full-backend success result. Do not treat the historical baseline as current proof. No commit, push, merge, cherry-pick, deployment, production migration, Neon access or shared-database test was performed.

## Recovered baseline and ownership

- Branch: `feat/i18n-ja-interface`.
- HEAD: `d3b3e2b1b06843a93fad1f2983021ceec01a9f52`.
- Recovered 34 modified tracked files and 12 untracked files. No existing changes were discarded.
- Windows process inspection found editor/agent processes, but no active Python/Node test suite before validation. The initial `ps` output alone was insufficient; Windows process inspection was also used. No Python/uv process remained after the timed-out full suite and completed short checks.
- Already present on recovery: Japanese frontend integration, preference migration, validated JA PO, effective-catalog compilation, checker adaptation, standard Django activation, thread/cache tests. Contrary to the last provisional progress message, the middleware already selected its response cookie from the final active language, and login/preference cookie regressions already existed. The generator already classified `OPEN_QUESTION` as deferred, but its generated outputs were stale. The compiler regression contained an interrupted Python string literal.
- Completed in this continuation: repaired and strengthened the compiler regression using real temporary source discovery and compilation; added checker effective-MO corruption/missing-output regression; regenerated stale JA artifacts from the original checksum-verified workbook; added explicit eight-key deferral regressions; added asynchronous gettext isolation and middleware exception-cleanup tests; completed logout-cookie handling and form/logout regressions; updated reconciliation/context documentation and ran the validations below.

## Final Django fallback design

`scripts/compile_django_messages.py` compiles the effective JA MO at build time. For each active application msgid, precedence is:

1. Validated JA from the JA PO.
2. Approved EN from the EN PO.
3. Source msgid, without inventing an English translation.

The JA PO retains only 81 genuine reused Japanese translations. English and untranslated source fallbacks exist only in the effective MO, not in Takeru provenance records. The compiler includes all active application msgids so Django's normal French default fallback cannot override these decisions. FR/EN use their original compilation path and retain byte-identical MO files.

Django activation is standard. `backend/apps/accounts/language.py` is absent. There are no runtime assignments to `_fallback`, calls to `add_fallback`, or uses of the removed activation helper in backend Python. Generic framework translations retain Django's upstream Japanese catalog; they are not represented as Takeru-approved text.

The compiler test creates an isolated `backend/locale` tree and real Python gettext call sites, runs the actual compiler CLI function, reads the resulting MO with `gettext.GNUTranslations`, checks JA/EN/source precedence, unchanged PO files and FR/EN output, repeated deterministic generation, rejection of a JA-only stale MO and regeneration. It does not mock the source extractor or the compilation implementation.

## Concurrency and cookies

`backend/apps/accounts/test_interface_language.py` verifies every active application msgid in FR/EN/JA, framework messages, repeated first JA activation after cache rebuild, and:

- 24 simultaneous threads, barrier-started on cold caches, each switching/reading its language 50 times.
- 90 middleware requests across 12 threads with language-specific text and cookies.
- 24 asynchronous tasks interleaving gettext reads with suspension points, 20 iterations each, starting from cold caches.
- Cleanup of the active language if the wrapped view raises.

No catalog is modified at request time. Isolation uses Django's normal context-local language activation. These are runtime concurrency tests, not a proof about all possible schedules. A native async HTTP-server/browser session was not exercised.

The cookie uses the final activated language for authenticated requests, not the value captured before the view. Existing login and profile/form views already activate and persist the final preference. For an anonymous request, the final session language or French default is authoritative; logout therefore does not emit the departed user's JA cookie after flushing the session. Translation deactivation is in `finally`.

Tests cover FR -> JA -> EN on the immediate PATCH response and next GET, login into a JA account, form POST/redirect, logout/new anonymous session, rejected preferences, separate users and concurrent requests. Authentication, session flush semantics and authorization implementations were not changed.

## Japanese reconciliation

The workbook was read only, with its required SHA-256 checked before generation and verified afterward:

`e80b409fbea485d97fbccf66d06da83eb46374883d1e3343f50e51e656b8c307`

All eight identities now have `FALLBACK_EN_REVIEW` / `OPEN_QUESTION`, are absent from `ja.ts`, and display current EN. Their original Japanese remains in both manifest and review data:

- `auditActionTransfer`
- `auditObjectOrganization`
- `auditMetaMembershipId`
- `boxInventoryLastKnownLocation`
- `boxLifecycleLastKnownLocation`
- `adminTransferText`
- `movementErrorProvenance`
- `zoneMovementExits`

Counts were recalculated from generated CSV, then asserted by regression tests:

| Surface | Reused JA | Absent | Deferred | Inline EN | Total |
|---|---:|---:|---:|---:|---:|
| React | 927 | 202 | 39 | 0 | 1168 |
| Django | 81 | 11 | 6 | 0 | 98 |
| Local tables | 67 | 0 | 1 | 0 | 68 |
| Inline | 0 | 0 | 0 | 14 | 14 |

React reuse comprises 903 exact matches and 24 explicitly compatible differences. The review contains 46 deferred identities, including the eight open questions. The 249 obsolete workbook keys (217 React, 32 Django) remain excluded. Manifest/frontend tests verify deleted keys do not return, including the register's removed identities.

## Validation outcomes

Every successful command below returned exit code **0**. For chained commands, `&&` ensured subsequent steps only ran after success.

| Validation | Final outcome |
|---|---|
| `build_ja_catalogs.py <original workbook> --check` | Passed after regeneration; original checksum enforced |
| `compile_django_messages.py` and `--check` | Passed; final check repeated |
| `scripts/check_i18n.py` | 98 msgids; JA 81/98; 0 blocking errors |
| Compiler unittest discovery | 26 tests OK |
| Checker unittest discovery | 22 tests OK |
| JA manifest unittest discovery | 7 tests OK |
| Application `check_register.py` | Passed; 844 register entries |
| Application-register unittest discovery | 62 tests OK |
| Django targeted `test_interface_language test_i18n` | 23 tests OK before subsequent added coverage |
| Django `apps.accounts` | 258 tests OK, including 12 skips |
| `npm run test:i18n` | 255 passed, 0 failed/skipped |
| `npm run check:i18n-usage` | Passed; 1168 FR/EN keys, 1165 source-referenced, 3 documented exceptions |
| `npm run test:all` | 2372 passed, 0 failed/skipped; 417.156 s |
| `npm run typecheck` | Passed |
| `npm run build` | CSS check, TypeScript and Vite passed; 2256 modules |
| Django `check --settings=config.test_settings` | Passed |
| Django `makemigrations --check --dry-run --settings=config.test_settings` | No changes detected |
| Full isolated Django suite | **Timed out at 900 s; 3064 tests discovered; no final outcome/skip count or numeric test exit code captured** |
| `git diff --check` | Passed |

Backend tests explicitly used `POSTGRES_DB=''`, `POLYPBASE_TEST_POSTGRES=0`, `--settings=config.test_settings`, and `--noinput`. This selects in-memory SQLite, not the shared database. Configuration/migration checks additionally used process-local `DJANGO_DEBUG=1` to permit the existing local-development key fallback without loading or copying a real secret. No migration was applied outside the temporary test database.

Intermediate failures, not hidden as successes:

- First `npm run test:i18n`: timed out at 60 s without a final result. A bounded rerun at 240 s completed successfully (255 tests).
- New checker-tree test initially failed (exit 1) because default JSON ASCII escaping produces PO syntax rejected by the strict parser. Its fixture now emits literal UTF-8; all 22 checker tests then passed.
- Initial standalone Django check failed (exit 1) because DEBUG was off and no secret was configured. Rerunning with process-local development mode and isolated settings passed, including migration drift.
- Full Django suite hit the 900-second timeout. It was not relaunched or duplicated. There is no successful complete backend result.
- A read-only final diff command also timed out at 10 seconds. Relevant changes were inspected through earlier diffs and targeted file reads; generated artifacts were checked against the original workbook and manifest invariants.

The packaged QA wrapper was inspected but not executed: it would duplicate suites/builds and uses default settings for configuration/migration checks. Equivalent explicit isolated checks above were used instead. No visual/browser QA was performed with the available tools; desktop/tablet rendering and native async HTTP execution remain unverified.

## Integrity and scope

- `frontend/src/i18n/fr.ts`, `en.ts`, and FR/EN PO sources have no Git diff against HEAD (the Windows working tree uses CRLF; raw working-tree source bytes therefore differ from Git blob line endings).
- FR/EN MO bytes were compared directly with HEAD and are identical after recompilation.
- `uv.lock` and `frontend/package-lock.json` are unchanged. `package.json` only adds JA to the targeted i18n test command; dependencies are unchanged.
- The workbook checksum is unchanged. No new Japanese wording was invented.
- The language migration only adds a choice to `UserPreference.interface_language`, preserving its French default and max length. Migration drift check passed.
- Scientific `CONTENT_LANGUAGES` behavior is asserted unchanged (FR required, EN/JA optional). No scientific data, zero/missing handling, organization permission/queryset, audit/history or box lifecycle implementation was edited. Existing presentation changes only select language/locale/copy. Broader regression proof is limited by the full Django timeout.
- Administration guards, routes, responsive logic and permission code are unchanged. This is source-scope evidence, not visual QA.
- No tracked unrelated dependency, lockfile or production/configuration secret was changed. Existing test harness changes are JA import/locale adaptations, not discarded test coverage.

## Exact final changed-file inventory

47 files: 34 tracked modifications plus 13 untracked additions, including this report. All paths are repository-relative.

### Tracked modifications (34)

```text
backend/apps/accounts/middleware.py
backend/apps/accounts/models.py
backend/config/settings.py
docs/context/frontend-ux.md
docs/context/terminology-i18n.md
frontend/package.json
frontend/scripts/request-context-test-harness.mjs
frontend/scripts/test-admin-transfer-qr.mjs
frontend/scripts/test-box-action-wording.mjs
frontend/scripts/test-box-dialogs.mjs
frontend/scripts/test-box-insights.mjs
frontend/scripts/test-box-movement-inline.mjs
frontend/scripts/test-measurement-history.mjs
frontend/scripts/test-move-box-modal.mjs
frontend/scripts/test-mutation-dialogs.mjs
frontend/scripts/test-overview-history.mjs
frontend/scripts/test-quantitative-subculture.mjs
frontend/scripts/test-user-identity.mjs
frontend/src/boxStatus.ts
frontend/src/components/BoxInsights.tsx
frontend/src/components/ChartWindowControls.tsx
frontend/src/components/ErrorBoundary.tsx
frontend/src/components/ExportsView.tsx
frontend/src/components/InteractiveLineageGraph.tsx
frontend/src/components/LineageModal.tsx
frontend/src/components/MoveBoxModal.tsx
frontend/src/components/OverviewView.tsx
frontend/src/components/ZonesView.tsx
frontend/src/i18n/index.ts
frontend/src/utils/boxMovementCorrection.ts
scripts/check_i18n.py
scripts/compile_django_messages.py
scripts/test_check_i18n.py
scripts/test_compile_django_messages.py
```

### Untracked additions to include in a future reviewed commit (13)

```text
backend/apps/accounts/migrations/0008_interface_language_japanese.py
backend/apps/accounts/test_interface_language.py
backend/locale/ja/LC_MESSAGES/django.mo
backend/locale/ja/LC_MESSAGES/django.po
docs/translation-review/japanese/FINAL_REVIEW.md
docs/translation-review/japanese/README.md
docs/translation-review/japanese/build_ja_catalogs.py
docs/translation-review/japanese/ja-manifest.csv
docs/translation-review/japanese/ja-obsolete-workbook-keys.csv
docs/translation-review/japanese/ja-takeru-review.csv
docs/translation-review/japanese/test_ja_manifest.py
frontend/scripts/test-i18n-japanese.mjs
frontend/src/i18n/ja.ts
```

## Remaining decisions and targeted independent review

1. Obtain explicit agreement to rerun the isolated full Django suite with a longer bound, or investigate its runtime before declaring validation complete. Do not use a shared database to accelerate this.
2. Review effective catalog source coverage/precedence, standard activation and context isolation; verify the compiler test really compiles the temporary source tree.
3. Review immediate cookies across preference changes, login and logout, particularly the DRF request wrapper boundary.
4. Review the exact eight deferrals and original-text preservation. Takeru's decisions remain pending; current EN fallback makes those decisions non-blocking for implementation safety.
5. Preserve the distinction between source fallback that happens to be French and an approved English translation. The former is intentional when EN has no approved entry.
6. Run desktop/tablet browser QA before release. Upstream framework Japanese remains outside Takeru's validation, by design.

**No release readiness claim: BLOCKED on the incomplete full Django validation.**
