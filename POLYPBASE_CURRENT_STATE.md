# POLYPBASE CURRENT STATE

Last updated: 2026-09-10

## Repository

- Canonical repository: `C:\Users\antoc\POLYPBASE`
- Canonical branch: `main`
- Latest completed application milestone: `df31743` (`fix: prevent last admin concurrency race`).
- S-01 is complete, independently reviewed, PostgreSQL validated, and pushed.
- Documentation-only commits after `df31743` do not change the application milestone.

## Worktrees

- `C:\Users\antoc\POLYPBASE` - `main`; local work present in `docs/PROJECT_CONTEXT.md` and untracked `docs/audits/`.
- `C:\Users\antoc\worktrees\POLYPBASE\boxinsights-a1\POLYPBASE` - `experiment/boxinsights-scientific-soft`, `6877190`; FROZEN experiment with local modifications and untracked `output/`. Do not modify or integrate unless Anthony explicitly reopens it.

## Completed milestones

- `9910b15` - improve team access management - DONE, PUSHED, NOT DEPLOYED.
- `23fa202` - align sidebar brand and navigation - DONE, PUSHED, NOT DEPLOYED.
- `c6972a2` - make invitation audit atomic (S-03) - DONE, independently reviewed, integrated to `main`, PUSHED, no migration, NOT DEPLOYED.
- `df31743` - prevent last admin concurrency race (S-01) - DONE, independently reviewed, PostgreSQL validated, integrated to `main`, PUSHED, no migration, NOT DEPLOYED.

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

1. Freeze unrelated Git writers.
2. Preserve intentional local/private state outside the rewrite workspace.
3. Prepare/create private `POLYPBASE-ANALYSES`.
4. Preserve private scientific analysis material.
5. Create and restore-test a complete private backup before history rewriting.
6. Test the privacy rewrite in a fresh isolated GitHub-sourced workspace.
7. Verify the sanitized history and public refs.
8. Perform the separately approved controlled public history replacement.
9. Adopt a fresh sanitized local POLYPBASE environment.
10. Handle production Git realignment separately.
11. Resume contractual roadmap work.

## Important local state

- The `main` worktree contains intentionally unrelated local changes in `docs/PROJECT_CONTEXT.md` and untracked `docs/audits/`; preserve them.
- The BoxInsights worktree contains local experiment changes and untracked `output/`; preserve it while the experiment remains frozen.
- `test_membership_concurrency.py` is part of the S-01 tracked work if present; it is not local-only state.
