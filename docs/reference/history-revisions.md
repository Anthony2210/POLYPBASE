# Box history and revisions

## Reading the history

Neither the box detail API nor `GET /api/boxes/<box_id>/history/` exposes stored revisions. Ordinary movement edits have their [own contract](boxes-lifecycle.md#correcting-a-movement). A location period is distinct from a recorded movement.

Historical reconstruction distinguishes the observation, its time precision, the source evidence, and the placement decision. An ISO week does not specify an instant. Inconsistent source evidence remains visible as a conflict rather than being silently moved. See the [historical movements appendix](annexes/historical-movements.md) for the evidence and attribution rules for the Aquarium de Paris work.

## Previewing a correction

`GET /api/boxes/<box_id>/history/preview/?start=…&end=…` returns the initial state for two ISO8601 bounds with time zones. `POST` to the same route simulates a complete draft in a JSON body, with no query parameters. A session, CSRF, and an active Admin role in the explicitly selected institution are required. Missing, merged, or out-of-institution boxes are indistinguishable (`404`). The response is private and `no-store`. Neither superuser status alone nor a client-supplied identity confers authority.

The preview compares before and after, affected identities and events, periods, temperature attribution, gaps, and conflicts. Decimal values are strings; zero, negative values, and null remain distinct. Evidence references are bounded and contain no raw audit data or confidential cells. `not_reported_after` means the engine no longer reports the conflict, not that a resolution has been certified. The window is limited to 366 elapsed days, with at most 100 operations and 100 justifications, plus limits on comparisons and reads. A rejected batch produces no partial preview. The API does not approve, save, publish, or apply anything. [Examples from the original contract](../archive/sources-fusion/history-preview-api.md).

## Revision storage

- `BoxHistoryStream` reserves the institution + original box identity. The box pointer can become null without losing the record. A head identifies an explicit prefix; inserting a successor does not publish it automatically. The head never skips a revision or moves backward.
- `BoxHistoryRevision` stores ordered, typed operations, minimal evidence, the reason, author, fingerprints, and idempotency key. The sequence and key are each unique per stream. Deleting an account can detach its reference without rewriting the captured author or content.
- `BoxHistoryEventIdentity` reserves identities for persisted evidence or events inserted into a stream. Revisions compose an effective history without rewriting the original evidence, the box, its records or measurements, or its current scientific state.

This foundation is not a public publishing service. Storage integrity does not replace request authorization. Enabling a future publisher requires verified authority, a head comparison, and atomic append and advance. The [dated PostgreSQL evidence](../archive/rapports/history-revision-postgres-qa.md) does not constitute production validation.
