# External read-only Box sharing (backend foundation)

Status: backend only, not reviewed, not integrated. No React reader, no XLSX,
no biological transfer. Creation stays unavailable until two lifetime bounds
are configured (see "Activation").

Code: `apps/sharing/` (`models.py`, `services.py`, `api_views.py`,
`tokens.py`). Scientific content comes only from
`apps/measurements/scientific_snapshot.py` (schema
`polypbase.scientific-snapshot` 1.0) through
`apps/cultures/scientific_projection.py`.

## Model

`BoxReaderShare`: one recipient-specific invitation and, once accepted, the
individual read grant of one account for one Box. Source institution, Box,
invited mailbox and mode are never updated. `BoxReaderShareCapture`: immutable
disclosed content of a FROZEN share for one policy revision.

States (derived, never stored): `pending`, `invitation_expired`, `active`,
`expired`, `revoked`; the recipient also sees `unavailable` when the source
institution is inactive.

## Authorization boundaries

| Step | Who | Check |
|---|---|---|
| A. Manage | active Admin membership of the active source institution (explicit `X-Organization-Id`); no superuser shortcut | re-checked under the institution row lock in every mutation |
| B. Accept | signed-in active account whose email is the invited mailbox, presenting the secret emailed to that mailbox | under institution + share locks; single use |
| C. Read | the account bound by acceptance, still carrying the invited mailbox | every read: not revoked, not expired, institution active; re-checked after the content is prepared |
| D. Project | internal | identifiers come from the stored share only |

Nothing creates a membership or an organization context for the recipient.
The reader endpoints ignore `X-Organization-Id`, query parameters and bodies.

Lock order: `Organization -> BoxReaderShare`.

## Modes

- LIVE: projected from current source data at each read. Several reads, not
  one atomic snapshot (`content.consistency = "non_atomic"`).
- FROZEN: captured when the share is created (and again, explicitly, when its
  selectors change). On PostgreSQL the capture owns a REPEATABLE READ
  transaction; a concurrent change to a row it depends on (for example a
  measurement entry, which updates the Box row) refuses the capture with
  `concurrent_source_change` instead of mixing two states. An earlier capture
  is never rewritten.

On SQLite no snapshot behaviour is verified, so a frozen capture (creation or
recapture) answers `503 frozen_capture_unavailable` unless the verified
environment sets `BOX_SHARING_SQLITE_FROZEN_CAPTURE = True`. Only the isolated
test settings do; the default is `False`. LIVE sharing and the reading of an
existing capture do not depend on it. `options/` reports
`frozen_capture_available`.

Both modes need the Box to still belong to the source institution and not be
merged. Box status (active, inactive, pending review) is not considered.

## Disclosure

Selectors are the catalogue names of the snapshot contract. Mandatory:
`source.institution_name` (must be selected explicitly; never added silently).
Reducing the selection needs no confirmation; widening needs
`confirm_expansion`; any selector change of a FROZEN share needs
`confirm_recapture`. Every selector change increments `policy_revision`.
Not available: lineage, notes, temperatures, movements, a time range.

## Invitation delivery

SMTP never runs inside a transaction or under a lock. A creation or reissue
first commits the share, its new secret digest, the policy, the capture and
the audit row; only then is the email sent, then its outcome is recorded in a
short separate transaction (institution lock first, as everywhere).

`invitation.delivery.status` (with `updated_at`) describes the current
invitation generation:

| Status | Meaning |
|---|---|
| `pending` | not recorded yet; also what remains after a crash or an unrecordable outcome |
| `sent` | the relay accepted the message (not that it was read) |
| `failed` | definitely refused: connection, authentication, sender or recipient refused, or nothing accepted |
| `uncertain` | unknown outcome (timeout, disconnect, data error): the message may have left |

A delivery problem never undoes the share: the API answers `201` / `200` with
the status. For anything but `sent`, the Admin reissues the invitation
(`reissue-invitation/`): a new secret replaces the old one, so a duplicate of
an earlier mail is harmless. No recoverable secret is stored; a retry never
changes the access end, revokes nothing and never reactivates a revoked or
accepted share. A status is written only for the generation that was sent and
only while it is still pending, so a concurrent revocation, acceptance or
reissue is never overwritten. SMTP is not exactly-once and nothing here claims
it. A crash between commit and send leaves `pending` with a valid secret
nobody received; the Admin sees it and reissues.

## Activation

`BOX_SHARING_INVITATION_MAX_LIFETIME_SECONDS` and
`BOX_SHARING_ACCESS_MAX_LIFETIME_SECONDS` have no default. While either is
unset: create, extend and reissue answer `503 sharing_policy_not_configured`.
Each request must also state its own `invitation_expires_at` and
`access_expires_at`. Email delivery must be enabled.

## API

All responses: `Cache-Control: no-store`, `Vary: Cookie`. Session
authentication and CSRF as elsewhere. Errors: `{"detail", "code", ...}`.

### Source Admin (`X-Organization-Id` required)

| Method and path | Body | Result |
|---|---|---|
| `GET /api/admin/box-shares/options/` | | catalogue, `mandatory_selectors`, modes, `lifetime_policy` |
| `POST /api/admin/box-shares/preview/` | `box_id`, `selectors` | `{snapshot}` exactly as a recipient would receive it now |
| `GET /api/admin/box-shares/?box_id=&limit=&offset=` | | `{shares, has_more, server_time}` |
| `POST /api/admin/box-shares/` | `box_id`, `recipient_email`, `mode`, `selectors`, `invitation_expires_at`, `access_expires_at` | `201 {share, server_time}` |
| `GET /api/admin/box-shares/<id>/` | | `{share, server_time}` |
| `PATCH /api/admin/box-shares/<id>/` | `expected_policy_revision`, optional `selectors`, `access_expires_at`, `confirm_expansion`, `confirm_recapture` | `{share, server_time}` |
| `POST /api/admin/box-shares/<id>/revoke/` | | idempotent |
| `POST /api/admin/box-shares/<id>/reissue-invitation/` | `invitation_expires_at` | new secret; earlier ones stop working; also the delivery retry |

`share`: `id`, `box {id, global_code}`, `recipient_email`, `mode`, `state`,
`schema_version`, `selectors`, `policy_revision`,
`invitation {generation, issued_at, expires_at, accepted_at, delivery {status, updated_at}}`,
`access_expires_at`, `revoked_at`, `created_at`, `created_by`,
`capture {captured_at, content_sha256} | null`.

Codes: `sharing_admin_required` 403, `box_not_found` 404 (missing, foreign and
merged alike), `share_not_found` 404, `invalid_request` 400,
`invalid_disclosure` 400 (`diagnostics`), `invalid_recipient_email` 400,
`invitation_expiry_out_of_bounds` 400, `access_expiry_out_of_bounds` 400,
`share_already_exists` 409, `policy_revision_conflict` 409
(`current_policy_revision`), `disclosure_expansion_requires_confirmation` 409
(`added_selectors`), `frozen_policy_change_requires_recapture` 409,
`share_revoked` 409, `invitation_not_pending` 409, `share_expired` 409,
`box_not_projectable` 409, `concurrent_source_change` 409 (retry),
`sharing_policy_not_configured` 503, `email_delivery_unavailable` 503 (delivery disabled, before anything is created),
`frozen_capture_unavailable` 503.

### Recipient (no organization context)

| Method and path | Body | Result |
|---|---|---|
| `GET /api/shared-boxes/` | | `{shares: [summary], server_time}` |
| `POST /api/shared-boxes/invitations/inspect/` | `token` | `{invitation: {mode, read_only, invitation_expires_at, access_expires_at, source_institution {name}}}`; consumes nothing |
| `POST /api/shared-boxes/invitations/accept/` | `token` | `{share: summary}` |
| `GET /api/shared-boxes/<uuid>/` | | `{share: summary, content, snapshot}` |

`summary`: `id` (uuid), `mode`, `read_only: true`, `state`, `policy_revision`,
`schema_version`, `access_expires_at`, `source_institution {name}`.
`content`: `{kind: "frozen_capture", captured_at, content_sha256}` or
`{kind: "live_projection", projected_at, consistency: "non_atomic"}`.
`snapshot`: the canonical 1.0 payload; an undisclosed field has no key.

Codes: `invitation_unavailable` 404 (malformed, unknown, superseded, consumed,
revoked: one answer), `invitation_recipient_mismatch` 403,
`invitation_expired` 410, `share_expired` 410, `share_revoked` 410,
`share_not_found` 404 (unknown or not this account's),
`share_source_unavailable` 409, `share_policy_changed` 409 (retry).

The emailed link is `{PUBLIC_BASE_URL}/shared-boxes/accept#token=<secret>`.
The fragment is not sent to the server; the frontend route must read it, ask
the user to sign in, call `inspect`, then `accept` on an explicit action.

## Audit

`AuditLog`, institution = source, `object_type = "box_reader_share"`,
`object_id` = public uuid: created (`creation`), policy updated / invitation
reissued / invitation accepted (`update`), invitation delivery recorded (`update`, no user, status only), revoked (`archive`), data served
(`view`, with a SHA-256 of the delivered content, never the content). Each
row is written in the transaction of its mutation; a delivery that cannot be
audited is not delivered. A `view` row proves server delivery, not reading.

## Tests

```powershell
$env:POLYPBASE_TEST_POSTGRES = "0"; $env:POLYPBASE_PG18_BIN = ""; $env:DJANGO_DEBUG = "1"
uv run python manage.py test apps.sharing --settings=config.test_settings
```

PostgreSQL evidence (`test_postgres_concurrency.py`, skipped on SQLite) needs
a disposable empty local cluster on `127.0.0.1:55441`, role and database
`sharing_pgqa`, trust authentication:

```powershell
$env:POLYPBASE_SHARING_PGQA = "1"
uv run python manage.py test apps.sharing --settings=config.sharing_pgqa_settings --noinput
```

That schema is synced from the models, not built by the migration chain.
