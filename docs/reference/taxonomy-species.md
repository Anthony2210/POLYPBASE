# Taxonomy, species, and strains

`Species` remains the shared reference for strains and boxes. `InstitutionSpecies` stores the scientific name and AphiaID specific to an institution, without moving historical relationships or switching all consumers to that name. `OrganizationSpeciesCode` stores the institution's AAA code; it is neither the shared code nor a URL key. The BBB code and biological provenance cannot be inferred from a supplier, a donation, or a historical string.

## Institutional catalogue

Administration > Espèces requires an active Admin in the selected institution. The catalogue opens the detail page by profile ID or, if no profile exists, by shared reference ID, never by AAA. A species already used by the institution has the same actions with or without a profile. The `…/institution-species/resolve/` POST checks authorization and then returns `410 institution_species_resolution_retired`, without creating a profile, code, or audit entry. React does not call it; opening a detail page or dialog assigns nothing.

Creation is explicit via POST, and profile edits use PATCH with `expected_revision`. With no profile, `PATCH …/historical/<species_id>/` explicitly saves the name and AphiaID without changing the shared reference or assigning an AAA code. An unchanged state writes nothing; if a profile has been created in the meantime, the response is `409 stale_revision` and the client offers to reload. Editing the name and WoRMS reference does not depend on AAA. WoRMS search returns species-rank results; the user explicitly selects a reference or enters a name without one. A reference that has become incompatible must be explicitly removed or replaced. There is no automatic taxonomic correction. Box-dependent counts and dates use only the full collection; the date bounds come from canonical records, not a technical creation date.

## Strains

Without a profile, `POST …/historical/<species_id>/strains/` can establish the profile and its AAA assignment in the same transaction as the strain. With or without a profile, a missing AAA assignment can be saved only if the server finds exactly one current code in the institution's data. This workflow does not generate any new AAA code. Missing or contradictory evidence, or a collision, blocks creation without partial writes; existing identifiers remain unchanged.

Institution-scoped creation selects an allowed country and reuses its BBB code or asks for a three-letter code. Acquisition, coordinates, and a descriptive name are saved only if provided. Django allocates the X number and code; an identical intent reuses its `request_id`, and a changed intent uses a different one. Historical codes and labels are not normalized on read. The detail page shows Souches et boîtes with the dates of the first and last records; do not substitute a creation date when no record exists. This workflow does not support correcting a box's species or editing a strain.

## AAA code change

`GET …/code-change-preview/?proposed_code=…` is an advisory preview: `200` does not imply `feasible`, and it reserves or changes nothing. No React screen is mounted for this change. No route invokes the internal service `apply_species_code_change`: it requires the expected plan, an idempotent intent, and revalidation under locks. Affected identities are updated consistently and audited in a single transaction; there is no implicit rename, no manifest authorized by a preview, and no proof of PostgreSQL validation from SQLite.

See the [institutional appendix](annexes/institution-species-foundation.md), the [original service contract](../archive/sources-fusion/species-code-change-apply.md), and the [routes](api.md).
