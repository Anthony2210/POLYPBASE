---
name: polypbase-deploy
description: Audit, deploy, and verify Polypbase on its Debian production VM using the repository deployment workflow. Use for releases, VM operations, backups, production troubleshooting, or rollback planning. Require explicit approval immediately before any production mutation.
---

# Polypbase Deploy

Operate production deliberately and use the deployment automation already maintained
by the repository.

## Read the source of truth

1. Locate the repository and read its `AGENTS.md`.
2. Read `docs/deploiement_vm.md` and the deployment scripts relevant to the requested
   operation. Do not load unrelated project history.
3. Inspect the local branch, `git status --short`, relevant diffs, and recent commits.
4. Never display or copy `.env` contents, credentials, passwords, private keys, or
   database connection strings. Inspect only non-sensitive presence and permissions.

## Safety boundaries

- Never deploy uncommitted work or edit application code directly on the VM.
- Never force-pull, reset, clean, or discard remote changes.
- Never mutate Neon as part of a production operation.
- Keep application, database, and organization scopes explicit.
- Use a fresh verified backup before migrations, data repair, account intervention, or
  another production data mutation where rollback matters.
- Stop on the first failed backup, build, migration, restart, or health check.
- Do not restore a database or roll back migrations without a separate explicit
  approval after presenting the observed failure and recovery options.

## Prepare the operation

1. Identify the exact local and `origin/main` commits.
2. Run `$polypbase-qa` or the deployment preflight when a release is involved.
3. Audit the VM in read-only mode: branch, commit, tree status, service state, recent
   focused errors, disk space, database identity, and public health.
4. Present the intended mutation, backup checkpoint, verification sequence, and
   rollback boundary.
5. Request explicit approval for that production-changing phase.

## Execute

For a normal release, use `deploy/deploy_vm.ps1` and its versioned Linux executor
instead of assembling an alternative `git pull` and restart sequence. The workflow
must preserve its fast-forward, backup, dependency, build, migration, static-file,
service, Nginx, and health-check safeguards.

For a targeted production operation that is not a release, keep the command narrowly
scoped, transactional where possible, auditable, and preceded by the appropriate
backup. Never broaden approval from one operation to another.

## Verify and report

Check the service, API health, HTTPS, affected workflow, and any applicable backup or
audit entry. Avoid writing scientific control data unless the user explicitly approves
that test and its clean removal.

Report the production commit, backup identifier, migrations or data changes, service
status, tested URLs or workflows, skipped checks, and remaining operational work.
Never include secrets in the report.
