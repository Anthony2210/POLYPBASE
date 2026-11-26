---
name: polypbase-debug
description: Diagnose a Polypbase bug, failing test, CI failure, or suspicious value and find its root cause with evidence before proposing a fix. Use for bug reports, regressions, red CI, or "this number looks wrong". Never touches production, the VM, or Neon.
---

# Polypbase debug

Find the root cause before changing code. If the `systematic-debugging` skill is
installed, follow its discipline; this skill adds what is specific to Polypbase.

## Safety

- Never connect to production, the VM, Neon, or any shared database. Reproduce
  with tests, the isolated settings described in the `polypbase-qa` skill, or demo
  data on a local database the user has explicitly verified.
- Never read or print a real `.env` file. Never paste real aquarium data into an
  issue, PR, test fixture, or screenshot.

## Steps

1. **State the symptom.** Expected versus actual value, where it appears (screen,
   API, export, command), for which role and organization, since which commit, and
   whether it reproduces.
2. **Classify it.** Code defect, data anomaly, or unclear business rule. The
   current behavior is not necessarily the intended rule. If the intended rule is
   unclear, stop and list the question under "To confirm by Anthony".
3. **Reproduce.** Write a failing test with synthetic data first: API, service, or
   component level. For red CI, read `gh run view <run-id> --log-failed`.
4. **Trace the value** through each layer: input parsing, API client, serializer,
   service, model and constraints, query or aggregate, serializer output, rendering
   or export. Check the usual suspects:
   - `0` treated as empty (`if value:`, `or`, `||`, `Number('')`, default `0`);
   - missing or wrong organization scope;
   - date and week boundaries (ISO weeks, local time versus UTC);
   - effective or historical value logic and stale requests;
   - migration state that differs between environments.
5. **Use history.** `git log -S "<text>"`, `git blame` on suspect lines, and
   `git bisect` with the failing test when it is a regression.
6. **Confirm.** Test one hypothesis at a time. The root cause is confirmed when the
   test fails before the minimal fix and passes after it.

## Report

```text
Symptom: ...
Root cause: <file:line> ... (why it happens)
Evidence: failing test, command output, or trace
Proposed fix: smallest change; other paths with the same pattern
Regression test: ...
Not verified: ...
To confirm by Anthony: ...
```

If the user asked only for a diagnosis, stop before fixing.
