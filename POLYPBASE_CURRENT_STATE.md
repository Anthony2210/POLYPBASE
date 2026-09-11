# POLYPBASE CURRENT STATE

Last updated: 2026-09-11

## Repository

- Canonical development repository: `C:\Users\antoc\POLYPBASE`
- Canonical branch: `main`
- Canonical sanitized HEAD: `0474f6e5e8fb5a4a9e8cd527e4b9a99182cd7c06`
- `origin` fetch/push: `https://github.com/Anthony2210/POLYPBASE.git`
- The public history rewrite is complete and independently verified. Normal development may resume from sanitized `main`.

## History migration

- The public history rewrite (privacy cleanup) is DONE and independently verified.
- The GitHub public repository now contains only `main`.
- Deleted public branches: `ajout-notebook-nettoyage`, `docs/mcd`, `import-excel-historique`.
- `C:\Users\antoc\POLYPBASE` is again the canonical development repository; it no longer holds the old public history.
- GitHub Support ticket `#4748399` remains open for server-side cleanup of old PR references, cached views and unreachable pre-rewrite objects.

## Old history archive

- Preserved locally at: `C:\Users\antoc\POLYPBASE_OLD_HISTORY_20260911`
- Old HEAD: `c506a1e85e30b8ce5e6ea950da1d4e2bed75023b`
- Push URL intentionally disabled: `disabled://old-history-do-not-push`
- Contains old rewritten history, 18 local branches (including `main`), 4 stashes, local `docs/PROJECT_CONTEXT.md`, local `docs/audits/maintainability-2026-09.md`, and ignored `docs/audits/security-2026-09.md`.
- Historical Codex checkpoint refs currently report as broken after archival relocation; leave them untouched pending separate classification.
- No old-history ref may be merged, rebased or transplanted directly into sanitized history. Useful old work must be reviewed at file/diff level and recreated as new commits on sanitized ancestry.
- Old branches, stashes and docs remain pending classification and must never be pushed or merged directly into sanitized history.

## Private analysis repository

- `Anthony2210/POLYPBASE-ANALYSES` Phase A is complete and published.
- `POLYPBASE-ANALYSES` is the private companion repository for notebooks, analysis scripts, ML/statistical experiments, scientific figures/results, and analysis-specific documentation/dependencies.
- Real raw institutional datasets remain outside Git by default, including outside `POLYPBASE-ANALYSES`.
- Internal engineering docs and audits are not scientific analysis material and should not automatically move to `POLYPBASE-ANALYSES`.

## BoxInsights

- The BoxInsights experiment was intentionally discarded and is fully retired locally.
- The linked worktree was force-removed and local branch `experiment/boxinsights-scientific-soft` was deleted.
- Its uncommitted frontend work and Playwright output were intentionally discarded.
- BoxInsights is no longer a blocker for repository path normalization and is no longer an active chantier.

## Temporary residual

- `C:\Users\antoc\POLYPBASE_SPLIT_RESIDUAL_20260911` remains temporary recovery material from the interrupted Windows directory move during local realignment.
- It is not a Git development repository and is not canonical.
- Do not delete or modify it; clean it separately only after final recovery verification.

## Production

- No production deployment occurred during the history rewrite.
- Last known deployed old-history source: `68771909ebf163401bb439d6cb412bfdec72eecb`.
- Mapped sanitized equivalent: `3c691217ce406c279b2b36288f22a3a7b419b7d8`.
- Production realignment is a separate future operation and must not implicitly deploy current `main`.

## PostgreSQL QA

- Docker Desktop with WSL2 is working.
- Disposable local PostgreSQL 17 Docker QA is a valid method for transaction, locking, and concurrency validation.
- S-01 validation: 5/5 real PostgreSQL concurrency tests passed; accounts suite 78/78 passed on PostgreSQL.
- Never use Neon or production for QA.

## Completed milestones

- Milestone hashes below are pre-rewrite (old-history) references; verified sanitized equivalents are shown.
- `9910b15` -> `e317dd568b63a99208ee336f43a564f76b623574` - improve team access management - DONE, PUSHED, NOT DEPLOYED.
- `23fa202` -> `1ad2788373fcb4a54d529f47964931de3b651d4e` - align sidebar brand and navigation - DONE, PUSHED, NOT DEPLOYED.
- `c6972a2` -> `85ca53f0f30565f136cce5105748ec4fe308b186` - make invitation audit atomic (S-03) - DONE, independently reviewed, integrated to `main`, PUSHED, no migration, NOT DEPLOYED.
- `df31743` -> `c13583f0678a659bf11b2210593e4a5f36af2778` - prevent last admin concurrency race (S-01) - DONE, independently reviewed, PostgreSQL validated, integrated to `main`, PUSHED, no migration, NOT DEPLOYED.

## Active / frozen work

- Active implementation work was closed for the privacy-cleanup sequence; that sequence is now complete.
- S-01 and S-03 are complete; no deployment of these commits is confirmed.
- BoxInsights is retired (see above).

## Contractual roadmap

1. Fix minor and major bugs.
2. Refine scientific exports with Delphine Bonnet.
3. Finalize English and Japanese translations.
4. Connect control temperature probes.
5. Open/connect POLYPBASE to partner institutions in France and abroad.
6. Build large-dataset visualization.
7. Build ephyrae prediction and culture-follow-up assistance.
8. Build climatic-accident analysis.

## Next operational work

1. Review and port `docs/PROJECT_CONTEXT.md` and audits if appropriate, at file/diff level and recreated as new commits on sanitized ancestry.
2. Classify old local branches and stashes from the old-history archive.
3. Wait for the GitHub Support response on ticket `#4748399`.
4. Clean `POLYPBASE_SPLIT_RESIDUAL_20260911` only after explicit verification.
5. Perform production realignment separately, if and when approved.
6. Resume contractual roadmap work.

## Important local state

- The old-history archive holds local `docs/PROJECT_CONTEXT.md` and `docs/audits/` material; preserve it pending classification.
- `test_membership_concurrency.py` is part of the S-01 tracked work if present; it is not local-only state.
