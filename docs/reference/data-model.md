# Data model and traceability

## Concrete project models

This is an inventory of the Django code, not the deployed schema. It excludes abstract models, automatically generated join tables, and `auth.User` (provided by Django). The migrations remain authoritative for constraints and their order.

| Application | Models |
|---|---|
| `accounts` | `AccountInvitation`, `AuthenticationThrottle`, `OrganizationMembership`, `UserPreference` |
| `audit` | `Alert`, `AuditLog` |
| `cultures` | `AutomaticBoxCreationRequest`, `Box`, `BoxCodeNamespace`, `BoxDnaValidation`, `BoxDonationProposal`, `BoxGlobalIdentity`, `BoxHistoryEventIdentity`, `BoxHistoryRevision`, `BoxHistoryStream`, `BoxInventoryInitialization`, `BoxLineage`, `BoxLocalIdAlias`, `BoxLocation`, `BoxMovement`, `BoxOwnershipEpisode`, `BoxTransfer`, `BoxTransferImport`, `GlobalBoxIdentity`, `HistoricalBoxMovement`, `IdentificationTag`, `PortableLineageEdge`, `PortableLineageNode`, `SubcultureAllocation`, `SubcultureEvent`, `ThermalZone`, `TransferEnvelope`, `TransferItem` |
| `exports` | `DataExport`, `ExcelImport`, `ExcelImportRow` |
| `measurements` | `BiologicalMeasurement`, `DailyTemperature`, `HistoricalTemperatureEvidence`, `HistoricalTemperatureImportReceipt`, `HistoricalTemperatureReading`, `MeasurementImportReceipt`, `Observation`, `Probe`, `ProbeTemperatureBucket`, `SalinityMeasurement`, `TemperatureMeasurement`, `ThermalAnomaly` |
| `organizations` | `Organization`, `PartnerInstitution`, `SharingAgreement` |
| `sharing` | `BoxReaderShare`, `BoxReaderShareCapture` |
| `taxonomy` | `BiologicalProvenance`, `GlobalStrainIdentity`, `InstitutionSpecies`, `InstitutionStrainCreationRequest`, `LocalStrainIdentity`, `LocalStrainNumberCounter`, `OrganizationProvenanceCode`, `OrganizationSpeciesCode`, `Origin`, `Species`, `SpeciesCodeChangeRequest`, `SpeciesTranslation`, `Strain`, `StrainTranslation`, `Taxon` |

## Reading relationships and constraints

| Conceptual data model area | Django mapping and limits |
|---|---|
| Core domain | `Strain` → `Box` → `BiologicalMeasurement`. The canonical weekly slot does not erase duplicate evidence. `BoxLineage` and `SubcultureAllocation` are neither transfers nor records. See [boxes](boxes-lifecycle.md) and [measurements](measurements-temperatures-probes.md). |
| Environment | `ThermalZone`, `Probe`, `TemperatureMeasurement`, `ProbeTemperatureBucket`, `DailyTemperature`, and `SalinityMeasurement` distinguish setpoints, readings, and aggregates. `BoxLocation` stores a period; `BoxMovement` stores an event. |
| Administration | `OrganizationMembership` stores roles; `AuditLog` records tracked changes. External sharing, donation proposals, and portable ownership have separate models and permissions. See [permissions](organizations-permissions-sharing.md) and [transfers](imports-exports-transfers.md). |
| Extensions | `InstitutionSpecies`, strain identities, DNA evidence, Local ID aliases, history, and receipts document separate contracts, not an implicit migration of old data. See [taxonomy](taxonomy-species.md), [history](history-revisions.md), and [imports](imports-exports-transfers.md). |

The [original conceptual data models](../sources/mcd/) remain design sources. The [PR3 Django diagrams](../archive/sources-fusion/mcd/schema_bdd.md) and the [conceptual data model → Django mapping](../archive/sources-fusion/tracabilite_mcd.md) are dated references. Diagram cardinalities do not establish authorization, composite or conditional constraints, or the state of an environment.
