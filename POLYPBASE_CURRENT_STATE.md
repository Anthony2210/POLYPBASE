# POLYPBASE CURRENT STATE

Last updated: 2026-09-10

## Repository

- Current worktree: `C:\Users\antoc\worktrees\POLYPBASE\docs-current-state\POLYPBASE`
- Current branch: `docs/current-state`
- Current commit: `df317438c418e1c3d313fe4a91174e4b8b18334b` (`fix: prevent last admin concurrency race`)
- `main` and `origin/main` are aligned at `df317438c418e1c3d313fe4a91174e4b8b18334b`.

## Worktrees

- `C:\Users\antoc\POLYPBASE` — `main`, `df31743`; local work present in `docs/PROJECT_CONTEXT.md` and untracked `docs/audits/`.
- `C:\Users\antoc\worktrees\POLYPBASE\docs-current-state\POLYPBASE` — `docs/current-state`, `df31743`; clean before this state-file update.
- `C:\Users\antoc\worktrees\POLYPBASE\boxinsights-a1\POLYPBASE` — `experiment/boxinsights-scientific-soft`, `6877190`; FROZEN experiment with local modifications and untracked `output/`. Do not modify or integrate unless Anthony explicitly reopens it.

## Completed milestones

- `9910b15` — improve team access management — DONE, PUSHED, NOT DEPLOYED.
- `23fa202` — align sidebar brand and navigation — DONE, PUSHED, NOT DEPLOYED.
- `c6972a2` — make invitation audit atomic (S-03) — DONE, independently reviewed, integrated to `main`, PUSHED, no migration, NOT DEPLOYED.
- `df31743` — prevent last admin concurrency race (S-01) — DONE, independently reviewed, integrated to `main`, PUSHED, no migration, NOT DEPLOYED.

## Active / frozen work

- Active implementation work is currently closed for the privacy-cleanup sequence.
- S-01 and S-03 are complete; no deployment of these commits is confirmed.
- BoxInsights remains FROZEN as described above.

## PostgreSQL QA

- Docker Desktop with WSL2 is working.
- Disposable local PostgreSQL 17 Docker QA is a valid method for transaction, locking, and concurrency validation.
- S-01 validation: 5/5 real PostgreSQL concurrency tests passed; accounts suite 78/78 passed on PostgreSQL.
- Never use Neon or production for QA.

## Privacy / public repository

- REAL INSTITUTIONAL SCIENTIFIC DATA MUST REMAIN PRIVATE.
- Confirmed public-history purge candidates: `notebooks/`, `docs/capture_ecran/`, `memoire/assets/screen_webapp/`, `exports_polypbase/`, `anomalies_par_type/`.
- `data/` was not found in reachable Git history and remains private/local.
- History rewrite has NOT been performed. Do not execute cleanup in this task.
- A normal deletion commit is insufficient because sensitive content exists in Git history.
- Cleanup prerequisites: close active implementation work, freeze Git writers, preserve a safe backup, test filtering in a disposable clone, inventory branches/stash/non-standard refs, and have Anthony perform history rewriting and force-push.
- Production Git realignment is separate and must not implicitly deploy current `main`.

## Repository split

- `POLYPBASE`: public application repository containing backend/frontend, migrations, tests, and publishable technical documentation with synthetic/demo fixtures only.
- `POLYPBASE-ANALYSES`: private companion repository for notebooks, analysis scripts, ML/statistical experiments, scientific figures/results, and analysis-specific documentation/dependencies.
- Real raw institutional datasets remain outside Git by default, including outside `POLYPBASE-ANALYSES`.
- Internal engineering docs and audits are not scientific analysis material and should not automatically move to `POLYPBASE-ANALYSES`.

## Production

- Last known deployed application revision: `6877190`.
- No later deployment is confirmed. Team Access, sidebar alignment, S-03, and S-01 are PUSHED to `main` but NOT DEPLOYED as far as current evidence shows.

## Contractual roadmap

1. Fix minor and major bugs.
2. Refine scientific exports with Delphine Bonnet.
3. Finalize English and Japanese translations.
4. Connect control temperature probes.
5. Open/connect POLYPBASE to partner institutions in France and abroad.
6. Build large-dataset visualization.
7. Build ephyrae prediction and culture-follow-up assistance.
8. Build climatic-accident analysis.

## Current priorities

1. Clean the finished S-01 worktree if still needed.
2. Freeze unrelated Git writers.
3. Prepare/create private `POLYPBASE-ANALYSES`.
4. Preserve private scientific analyses.
5. Perform controlled public Git-history cleanup in a separately approved workflow.
6. Verify the sanitized repository.
7. Recreate a clean local POLYPBASE clone/worktree environment.
8. Handle production Git realignment separately.
9. Resume the contractual product roadmap.

## Important local state

- This `docs/current-state` worktree was clean before this file was created.
- The `main` worktree contains intentionally unrelated local changes in `docs/PROJECT_CONTEXT.md` and untracked `docs/audits/`; preserve them.
- The BoxInsights worktree contains local experiment changes and untracked `output/`; preserve it while the experiment remains frozen.
- `test_membership_concurrency.py` is part of the S-01 tracked work if present; it is not local-only state.
