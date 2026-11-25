# Organizations, accounts, and permissions

## Current model

`Organization` represents the institution that owns a workspace. A user can have an active `OrganizationMembership` in several institutions, with a separate role in each:

- `admin`: administer the active institution;
- `lab_technician`: write laboratory data;
- `viewer`: read-only access.

Responsable is not a fourth role. It is an institution-level privilege held by an Admin membership: `role="admin"` and `is_responsable=True`. A database constraint prevents this privilege from being retained with another role. No existing Admin is designated automatically.

An active Responsable retains all Admin capabilities and can also invite, promote, demote, activate, or deactivate ordinary Admins in their institution, subject to protections for the last active Admin. An ordinary Admin still manages Techniciens and Lecteurs but cannot appoint or modify another Admin. No product user can grant the Responsable privilege or modify a membership that holds it through the generic endpoint.

An active Responsable can relinquish only their own privilege, and only when another active membership with `role="admin"` and `is_responsable=True` already exists in the same institution. The operation preserves their Admin role, locks the institution, and writes the audit entry in the same transaction. Product behavior for inactive Responsables is deliberately undecided; they do not count for this operation, and generic mutations affecting them remain blocked.

The Django superuser is a technical mechanism, not a product role to reproduce in the interface. It retains explicit break-glass access for ordinary memberships. Forcibly granting or removing the Responsable privilege uses the controlled `set_institution_responsable` command, never the product interface or the read-only `ModelAdmin`. Sponsorship and institution creation capabilities remain out of scope.

Accounts use Django sessions. Sign-in, invitation, and password recovery workflows live in `backend/apps/accounts/`; email transport configuration comes from the environment. An invitation or password reset link is an access secret for as long as it is valid and must never appear in logs or documentation.

## Invitations and team display

Administration > Équipe separates **Membres** and **Invitations**, now shown as separate lists on the same screen, with Invitations below Membres (fix `9199871`). An unaccepted invitation is a membership whose user still has an unusable password **and** for which there is proof of invitation (`unaccepted_invitation_q`, `invitations.py`): either the membership has an `AccountInvitation` row, or (for legacy cases) it has no such row, the user has never signed in, and the invitation API's “Member access created” audit entry identifies that exact membership and user in that institution. An unusable password alone does not prove an invitation: without that evidence, the account stays in Membres. An accepted invitation (password set through the link or through “mot de passe oublié”) moves to Membres. An invitation with an inactive membership or user currently appears in neither list (an open product question).

`AccountInvitation` (one row per membership, never containing a secret) stores `generation`, `issued_at`, and `expires_at`. The `<id invitation>-<generation>-<digest>` token covers the invitation, membership, institution, user (password hash and last sign-in), `generation`, and `issued_at`; resending increments `generation` and invalidates all previous links. The 24-hour validity period follows a single rule, `invitation_is_expired` (`tokens.py`): valid up to and including `expires_at`. API status, `can_resend`, and token validation all use it.

A legacy invitation has no recorded send time: it is exposed as `expired`, with a null `expires_at`, and can be resent immediately. No date is invented; its old links are no longer accepted. The exposed invitation ID is the membership ID.

Common lock order: `Organization` → `OrganizationMembership` → `AccountInvitation` → `User`. Creation, resending, membership edits, and invitation confirmation lock the institution first. Confirmation revalidates the token against the locked state before writing the password, so a link replaced while waiting fails. Confirmation through “mot de passe oublié” locks only `User` and does not require an invitation row.

`GET /api/accounts/invitations/` and `POST /api/accounts/invitations/<id>/resend/` require an Admin in the active institution; resending an invitation for the Admin role requires a Responsable, with the check based on the current locked role. The resend operation rejects a still-valid or accepted invitation (409), sends the email within the transaction, writes a “Member invitation resent” `UPDATE` audit entry with no link or token, and responds using the mutation state. No response or audit entry exposes a UID, token, or link.

`OrganizationMembership.is_hidden_from_team` (false by default) hides a membership from the Membres and Invitations lists and therefore from their counts; resending by ID remains possible. This affects display only: roles, permissions, and protections for the last Admin or Responsable do not change. Only `set_team_membership_visibility` (dry run by default, superuser actor, exact membership IDs, audit) changes it; no name, domain, `is_staff`, or `is_superuser` is used as a heuristic.

## Portable technical identity

`Organization.portable_id` is an opaque, unique, non-null UUID that is not editable in ordinary workflows. New institutions automatically receive a `uuid.uuid4()`; migration `0002_organization_portable_id` assigns a separate random UUID to each existing institution without changing its primary key or relationships. Names, slugs, and codes are never used to derive or match this identity.

This field lays the groundwork for portable institution references in Transfer v2. It is not a permission, a biological identity (`GlobalStrainIdentity`), or a public routing identifier. Current serializers do not expose it or accept writes to it; forms and admin exclude it via `editable=False`. This does not guard against explicit technical writes through the ORM or SQL, which must preserve the established identity.

Restoring the same database preserves these UUIDs. An independently initialized institution receives its own UUID, even if its name matches another institution's. There is no special clone or restore workflow and no automatic matching.

The internal `create_source_package` service requires an authenticated, active actor with Admin authority in the explicitly selected source `Organization`, via `user_can_administer_organization` (existing superuser break-glass access is retained). It reloads the institution and filters boxes server-side. It rejects any source strain without a `GlobalStrainIdentity`; it does not create, infer, or attach that identity. Institution, transfer, and item UUIDs grant no rights; the structural parser verifies neither authorization nor authenticity. The package service remains internal: no endpoint exposes `2.0`/`2.1` packages. Whole-box donation proposals have a separate API. The destination resolver remains read-only. For a strain with a non-null institution and global identity, the database enforces at most one representation per `(organization, global_identity)`, without Species in the key; legacy cases with a NULL institution or global identity remain allowed. The readiness diagnostic is still available. The internal X allocator uses a persistent counter per Organization + Species + BiologicalProvenance (an explicit NULL is allowed), without materializing anything at the destination or accepting automatically.

## Active organization

The frontend keeps the selected organization ID and sends it in the `X-Organization-Id` header. The backend resolves this context in `backend/apps/accounts/permissions.py` and accepts it only if the user can actually access the requested institution.

This mechanism is only the starting point. Each endpoint must still scope the following on the server:

- lists and detail views;
- creation, updates, and deletion;
- client-supplied identifiers;
- relationships between objects;
- choices offered by serializers;
- aggregates, counts, and suggestions;
- exports and bulk actions.

Hiding a button or filtering a list in React does not protect the API. Django remains the source of truth for permissions.

Resolving a QR link and accessing protected QR SVG resources require the explicit context of the active `Organization`. Membership in another institution does not widen the box search when that institution is not the active context. `/bac/<id>/` is a login-protected relay, with no box lookup or SCAN audit entry; the API performs authorized resolution and preserves its SCAN audit entry, with no success audit entry for a denied cross-institution lookup. The frontend passes context to QR resources and discards stale scan and resource responses when the institution changes; camera scan behavior is unchanged.

The request generation guard prevents an old Organization response from overwriting a more recently selected context. It does not guarantee detail freshness within the same institution; M10.1 remains a local fix pending final review. See [`frontend-ux.md`](../archive/sources-fusion/context/frontend-ux.md). Failure to load the replacement context does not leave old institutional data displayed as current.

## Relationships and reference data

When a mutation links two objects, verify the scope on both sides. For example, a box must not be assigned a zone from another institution, even if its ID was supplied manually. The same care applies to transfers, members, probes, imports, and objects selected in bulk.

`Species` is a shared reference; `InstitutionSpecies` stores institution-specific names and WoRMS references. Espèces requires explicit institution context and Admin authority; an operational strain is normally institution-scoped, with bounded legacy exceptions. Shared reference data does not grant access to measurements or cultures. The historical resolution POST checks permissions and then returns `410`, without creating a profile, code, or audit entry.

Collection locations are an exception: `BiologicalProvenance` remains a shared table with no owner, but its visibility is limited by the institutional association `OrganizationProvenanceCode`. The GET endpoints `/api/taxonomy/biological-provenances/` and `/api/taxonomy/provenance-codes/` return only locations and BBB codes associated with the active institution. The two legacy POST endpoints are retired (405): creation without an assignment and claiming a historical row by ID are both prohibited. Institution-scoped strain creation atomically creates its new location and BBB code or reuses an existing association in the institution. Two institutions can register the same name independently. Unassigned rows are preserved without inventing owners. Details: [`../../backend/INSTITUTION_SPECIES_FOUNDATION.md`](annexes/institution-species-foundation.md).

`PartnerInstitution` and `SharingAgreement` grant no implicit cross-institution access. External sharing uses an individual `BoxReaderShare` managed by an active Admin of the source institution in its explicit context, with no superuser bypass. The invitation links a box to the invited email address and then to the active account that accepts it; reads recheck the recipient, status, and expiry of the grant. No membership or access to another institution's operational detail pages is created. The APIs remain in place; PR2 removed the React management and read screens. See [`BOX_READER_SHARING.md`](../archive/sources-fusion/backend/BOX_READER_SHARING.md).

Donation is separate: an active Admin of the source institution can propose a whole box to another eligible institution. Admins on both sides can view the authorized projection in their own context; the recipient gets neither access to the source history nor ownership. React supports sending and viewing; source-side cancellation is available in the API but not on screen. Acceptance is not exposed.

## Permissions and audit

A sensitive action must check the following, in this logical order:

1. the user is authenticated;
2. the user has an active membership and sufficient role in the active organization;
3. the target objects belong to that organization;
4. the action is valid under the business rules;
5. the audit entry is written in the correct institution when an audit trail is part of the operation.

Creating an Organization and writing its required audit entry share a transaction; the same applies to an update and its required audit entry. An audit failure rolls back the corresponding mutation. A no-op update preserves the existing behavior: no UPDATE audit entry. This guarantee does not change deletion behavior and does not implicitly extend to every audit write in the application.

For other compound writes, determine whether the data and its `AuditLog` must share a transaction. Do not generalize a convention from another view without inspecting the service in question.

## Expected tests

Any change that depends on organization context must include at least two institutions and verify:

- authorized access in the active institution;
- denial or absence of the object for the other institution;
- rejection of an out-of-scope ID supplied directly;
- inability to create a cross-institution relationship;
- consistent permissions between the interface and the backend;
- correct audit attribution.

Backend tests must use an isolated configuration. Commands are in [`development-deployment.md`](../operations/development-qa.md).

## Entry points

- Account models: `backend/apps/accounts/models.py`.
- Organization resolution: `backend/apps/accounts/permissions.py`.
- API for members, invitations, and relinquishing the Responsable privilege: `backend/apps/accounts/api_views.py`.
- Platform-level privilege assignment: `backend/apps/accounts/management/commands/set_institution_responsable.py`.
- Institution model: `backend/apps/organizations/`.
- Frontend API client: `frontend/src/api/client.ts`.
- Data model traceability: [`../tracabilite_mcd.md`](data-model.md).

## External read-only sharing

The `admin/box-shares/` and `shared-boxes/` APIs no longer have mounted React screens. A `BoxReaderShare` targets a single box and a single invited email address; the accepted grant is tied to the active account with that address. It grants no membership, ownership, or institution context. Recipient reads ignore `X-Organization-Id` and verify the account, email address, expiry, revocation, and availability. On the source side, only an active Admin of the explicitly selected institution can manage sharing, with no superuser shortcut.

- **LIVE** projects current data without an atomic snapshot. **FROZEN** retains an immutable `BoxReaderShareCapture` per revision; on PostgreSQL the capture detects concurrent changes rather than mixing states. SQLite does not validate this isolation.
- Explicitly selecting `source.institution_name` is mandatory; broadening the selection requires confirmation, and any FROZEN change requires a new confirmed capture. Notes, lineage, temperatures, and movements are not disclosed.
- The invitation secret confers no rights until the account with the invited address accepts it once. Maximum durations are configured; their absence does not revoke existing grants. Writes recheck authority under lock and preserve the audit trail without exposing the secret.

See the [API map](api.md) and [institutional exchanges](imports-exports-transfers.md). A donation proposal does not grant access to the source box's history.
