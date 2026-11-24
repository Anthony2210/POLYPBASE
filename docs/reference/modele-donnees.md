# Modèle de données et traçabilité

## Modèles concrets du projet

Inventaire du code Django, pas du schéma déployé. Les modèles abstraits, les tables intermédiaires automatiques et `auth.User` (fourni par Django) sont exclus. Les migrations restent l’autorité pour les contraintes et leur ordre.

| Application | Modèles |
|---|---|
| `accounts` | `AccountInvitation`, `AuthenticationThrottle`, `OrganizationMembership`, `UserPreference` |
| `audit` | `Alert`, `AuditLog` |
| `cultures` | `AutomaticBoxCreationRequest`, `Box`, `BoxCodeNamespace`, `BoxDnaValidation`, `BoxDonationProposal`, `BoxGlobalIdentity`, `BoxHistoryEventIdentity`, `BoxHistoryRevision`, `BoxHistoryStream`, `BoxInventoryInitialization`, `BoxLineage`, `BoxLocalIdAlias`, `BoxLocation`, `BoxMovement`, `BoxOwnershipEpisode`, `BoxTransfer`, `BoxTransferImport`, `GlobalBoxIdentity`, `HistoricalBoxMovement`, `IdentificationTag`, `PortableLineageEdge`, `PortableLineageNode`, `SubcultureAllocation`, `SubcultureEvent`, `ThermalZone`, `TransferEnvelope`, `TransferItem` |
| `exports` | `DataExport`, `ExcelImport`, `ExcelImportRow` |
| `measurements` | `BiologicalMeasurement`, `DailyTemperature`, `HistoricalTemperatureEvidence`, `HistoricalTemperatureImportReceipt`, `HistoricalTemperatureReading`, `MeasurementImportReceipt`, `Observation`, `Probe`, `ProbeTemperatureBucket`, `SalinityMeasurement`, `TemperatureMeasurement`, `ThermalAnomaly` |
| `organizations` | `Organization`, `PartnerInstitution`, `SharingAgreement` |
| `sharing` | `BoxReaderShare`, `BoxReaderShareCapture` |
| `taxonomy` | `BiologicalProvenance`, `GlobalStrainIdentity`, `InstitutionSpecies`, `InstitutionStrainCreationRequest`, `LocalStrainIdentity`, `LocalStrainNumberCounter`, `OrganizationProvenanceCode`, `OrganizationSpeciesCode`, `Origin`, `Species`, `SpeciesCodeChangeRequest`, `SpeciesTranslation`, `Strain`, `StrainTranslation`, `Taxon` |

## Lire les relations et contraintes

| Domaine des MCD | Correspondance Django et limites |
|---|---|
| Métier | `Strain` → `Box` → `BiologicalMeasurement`. Le créneau hebdomadaire canonique n'efface pas les preuves doublons. `BoxLineage` et `SubcultureAllocation` ne sont ni des transferts ni des mesures. Voir [boîtes](boites-cycle-de-vie.md) et [mesures](mesures-temperatures-sondes.md). |
| Environnement | `ThermalZone`, `Probe`, `TemperatureMeasurement`, `ProbeTemperatureBucket`, `DailyTemperature` et `SalinityMeasurement` distinguent consigne, lectures et agrégats. `BoxLocation` conserve une période; `BoxMovement` conserve un événement. |
| Gestion | `OrganizationMembership` porte les rôles; `AuditLog` conserve les mutations tracées. Partage externe, proposition de don et propriété portable ont des modèles et permissions distincts. Voir [permissions](organisations-permissions-partage.md) et [transferts](imports-exports-transferts.md). |
| Extensions | `InstitutionSpecies`, identités de souche, preuve ADN, alias Local ID, historique et reçus documentent des contrats distincts, pas une migration implicite des données anciennes. Voir [taxonomie](taxonomie-especes.md), [historique](historique-revisions.md) et [imports](imports-exports-transferts.md). |

Les [MCD originaux](../sources/mcd/) restent des sources de conception. Les [diagrammes Django de PR3](../archive/sources-fusion/mcd/schema_bdd.md) et la [correspondance MCD → Django](../archive/sources-fusion/tracabilite_mcd.md) sont des repères datés. Les cardinalités d'un diagramme ne prouvent ni l'autorisation, ni une contrainte composite ou conditionnelle, ni l'état d'un environnement.
