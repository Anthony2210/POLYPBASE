# Local pull request workflow

## Set up either PC

Keep the main checkout at `POLYPBASE` under your Windows user profile. With Git
and an authenticated GitHub CLI (`gh`) on `PATH`, update that checkout and add
this line to `$PROFILE` in the PowerShell edition you use:

```powershell
. (Join-Path $env:USERPROFILE 'POLYPBASE\scripts\pr_tools.ps1')
```

On the old PC, use the same line in Windows PowerShell 5.1. It uses the current
Windows account automatically. If you have older copies of these functions in
your profile, put the line after them or replace those copies yourself.

## Write and review

- Use one of the linked worktrees per writer:
  `New-PrWorktree -Name pr-tools -Branch chore/pr-tools`. The helper fetches
  `origin/main`, creates the branch without upstream tracking, and refuses an
  existing path or branch. Add `-Setup` to run `npm --prefix frontend ci` and
  `uv sync` in the new checkout.
- Commit logical steps using [Conventional Commits](../CONVENTIONS.md#commits-and-pull-requests).
  For the first push, run `git push -u origin <branch>` to set tracking to the
  writer branch, never `main`. Then open a draft PR targeting `main`.
  Keep the PR description current with the steps, test results, **Not verified**,
  and **To confirm**. Leave the draft for review when CI is green or blocked.
- Use `Wait-PrChecks 123` to check the current PR head without changing the PR.
  It waits up to 30 minutes by default (`-TimeoutSeconds` and `-PollSeconds`
  override the defaults). It refuses if the head changes; rerun for the new head.

## Merge and clean up

Only the product owner merges. From the main checkout, run `Complete-Pr 123`
when the PR is approved. It waits for the required checks, including `ci-ok`, on
that exact head, marks a draft ready, and requests a merge commit with a head
SHA match. It does not bypass branch protection. After GitHub confirms the merge,
it switches the main checkout to `main`, pulls with `--ff-only`, and runs cleanup.
If the error comes after the merge, check the PR state before retrying locally.

Preview cleanup with `Remove-MergedWorktrees -WhatIf`, or prompt before each
removal with `Remove-MergedWorktrees -Confirm`. Cleanup only considers worktrees
under `worktrees\POLYPBASE\<name>\POLYPBASE`. It keeps the main checkout, dirty
worktrees, and branches without a merged PR for their exact tip or with commits
missing from `origin/main`. It does not force deletion. If a preview reports a
stale `origin/main`, fetch separately and retry. GitHub deletes merged remote
branches automatically.

## Dependabot

The product owner can preview with `Complete-DependabotPrs -WhatIf`. The real
command processes open Dependabot PRs oldest first. For each PR it posts
`@dependabot rebase`, waits for a different head (10 minutes by default), and
uses the same check and merge rules as `Complete-Pr`. It skips a PR if no new
head arrives or the checks do not pass.

Immediately after a rebase request, `gh pr checks --watch` can report green for
the **old** SHA. On the new SHA, it may say **no required checks reported** before
CI starts. Neither is permission to merge: wait for the new head and its own
required checks. The helpers parse GitHub JSON using `ConvertFrom-Json` instead
of using `--jq` expressions, whose quotes can break in Windows PowerShell.
