# Schéma de la base de données POLYPBASE — modèles Django

Inventaire PR3 : **68 modèles concrets propres au projet**, dont **38 nouvellement dessinés**.
Les trois domaines des MCD validés (métier, environnement, gestion) servent de
point de départ. Les diagrammes couvrent aussi les extensions du code, sans
modifier les MCD historiques ni attester le schéma déployé.

| Application Django | Modèles |
|---|---:|
| `accounts` | 4 |
| `audit` | 2 |
| `cultures` | 27 |
| `exports` | 3 |
| `measurements` | 12 |
| `organizations` | 3 |
| `sharing` | 2 |
| `taxonomy` | 15 |
| **Total** | **68** |

Source : registre des applications `apps.*`, chargé avec `config.preflight_settings`
(SQLite `:memory:`, dotenv neutralisé, aucune connexion ouverte). Sont exclus les
modèles abstraits, proxys et modèles générés automatiquement. `AuthUser` représente
**`auth.User`**, le modèle utilisateur fourni par Django (`AUTH_USER_MODEL`),
affiché comme cible externe et exclu du total.

**Lecture :** chaque modèle a un bloc d’attributs dans un seul diagramme. Un nœud
sans attributs renvoie à son bloc dans un autre diagramme. Les champs non
relationnels sont sélectifs. Les liens FK et OneToOne vont du modèle référencé
vers le modèle porteur, avec le nom du champ Django sur le lien et le nom de la
colonne SQL dans le bloc. Les cardinalités décrivent la nullabilité et l’unicité
du champ seul, pas les contraintes conditionnelles, composites ou métier.
`PK` = clé primaire ; `FK` = clé étrangère.

## 1. Métier : souches, boîtes et suivi biologique

Les rôles `box` et `environment_source_box` d’un relevé sont distincts.
`IdentificationTag` a deux FK nullables ; `identification_tag_targets_one_object`
impose exactement une cible, boîte ou zone.

```mermaid
erDiagram
    Taxon |o..o{ Taxon : "parent"
    Taxon |o..o{ Species : "taxon"
    PartnerInstitution |o..o{ Origin : "partner_institution"
    Species ||..o{ Strain : "species"
    Organization |o..o{ Strain : "organization"
    GlobalStrainIdentity |o..o{ Strain : "global_identity"
    Origin |o..o{ Strain : "origin"
    Organization ||..o{ Box : "organization"
    Strain ||..o{ Box : "strain"
    Origin |o..o{ Box : "origin"
    ThermalZone |o..o{ Box : "thermal_zone"
    Box |o..o{ Box : "merged_into"
    BoxGlobalIdentity |o..o{ Box : "global_identity"
    Box ||..o{ BiologicalMeasurement : "box"
    BiologicalMeasurement |o..o{ BiologicalMeasurement : "duplicate_of"
    Box |o..o{ BiologicalMeasurement : "environment_source_box"
    AuthUser |o..o{ BiologicalMeasurement : "user"
    AuthUser |o..o{ BiologicalMeasurement : "original_observer"
    AuthUser |o..o{ BiologicalMeasurement : "imported_by"
    Box ||..o{ Observation : "box"
    AuthUser |o..o{ Observation : "user"
    Box ||..o{ SubcultureEvent : "parent_box"
    AuthUser |o..o{ SubcultureEvent : "user"
    SubcultureEvent ||..o{ SubcultureAllocation : "event"
    Box ||..o| SubcultureAllocation : "child_box"
    Box ||..o{ BoxLineage : "parent_box"
    Box ||..o{ BoxLineage : "child_box"
    SubcultureEvent |o..o{ BoxLineage : "subculture_event"
    Box |o..o{ IdentificationTag : "box"
    ThermalZone |o..o{ IdentificationTag : "thermal_zone"

    Taxon {
        bigint id PK
        bigint parent_id FK
        string name
        string rank
    }
    Species {
        bigint id PK
        bigint taxon_id FK
        string scientific_name
        string genus_species_code
    }
    Origin {
        bigint id PK
        bigint partner_institution_id FK
        string source_type
        date event_date
    }
    Strain {
        bigint id PK
        bigint species_id FK
        bigint organization_id FK
        bigint global_identity_id FK
        bigint origin_id FK
        string code
        int number
        string origin_code
    }
    Box {
        bigint id PK
        bigint organization_id FK
        bigint strain_id FK
        bigint origin_id FK
        bigint thermal_zone_id FK
        bigint merged_into_id FK
        uuid global_id FK
        string global_code
        date created_on
        string previous_global_code
    }
    BiologicalMeasurement {
        bigint id PK
        bigint box_id FK
        bigint duplicate_of_id FK
        bigint environment_source_box_id FK
        int user_id FK
        int original_observer_id FK
        int imported_by_id FK
        date measured_on
        date week_start
        string temporal_precision
        bigint polyp_state_sequence
        int polyp_count
        int ephyrae_count
        int strobila_count
        string culture_status
        string observer_status
        string origin
        string import_format
    }
    Observation {
        bigint id PK
        bigint box_id FK
        int user_id FK
        date observed_on
        string observation_type
    }
    SubcultureEvent {
        bigint id PK
        bigint parent_box_id FK
        int user_id FK
        date event_date
        datetime occurred_at
        bigint parent_state_sequence
        int parent_polyp_count_before
        int allocated_polyp_count
        int parent_polyp_count_after
        json parent_state_snapshot
    }
    SubcultureAllocation {
        bigint id PK
        bigint event_id FK
        bigint child_box_id FK
        int position
        int allocated_polyps
        bigint child_state_sequence
        string child_global_code
    }
    BoxLineage {
        bigint id PK
        bigint parent_box_id FK
        bigint child_box_id FK
        bigint subculture_event_id FK
        string relationship_type
    }
    IdentificationTag {
        bigint id PK
        bigint box_id FK
        bigint thermal_zone_id FK
        string tag_type
        string code
    }
```

## 2. Environnement : zones, sondes et mesures

`BoxMovement` distingue zones de départ et d’arrivée et périodes d’emplacement
associées. `Probe.thermal_zone` décrit seulement l’affectation courante.

```mermaid
erDiagram
    Organization ||..o{ ThermalZone : "organization"
    Box ||..o{ BoxLocation : "box"
    ThermalZone ||..o{ BoxLocation : "thermal_zone"
    Box ||..o{ BoxMovement : "box"
    ThermalZone |o..o{ BoxMovement : "from_thermal_zone"
    ThermalZone ||..o{ BoxMovement : "to_thermal_zone"
    AuthUser |o..o{ BoxMovement : "user"
    BoxLocation |o..o| BoxMovement : "origin_location"
    BoxLocation |o..o| BoxMovement : "destination_location"
    Organization ||..o{ Probe : "organization"
    ThermalZone ||..o{ Probe : "thermal_zone"
    Probe ||..o{ TemperatureMeasurement : "probe"
    AuthUser |o..o{ TemperatureMeasurement : "user"
    Probe ||..o{ ProbeTemperatureBucket : "probe"
    ThermalZone ||..o{ DailyTemperature : "thermal_zone"
    ThermalZone ||..o{ SalinityMeasurement : "thermal_zone"
    AuthUser |o..o{ SalinityMeasurement : "user"
    ThermalZone ||..o{ ThermalAnomaly : "thermal_zone"

    ThermalZone {
        bigint id PK
        bigint organization_id FK
        string name
        string zone_type
        decimal target_temperature_c
        bool is_active
    }
    BoxLocation {
        bigint id PK
        bigint box_id FK
        bigint thermal_zone_id FK
        datetime starts_at
        datetime ends_at
    }
    BoxMovement {
        bigint id PK
        bigint box_id FK
        bigint from_thermal_zone_id FK
        bigint to_thermal_zone_id FK
        int user_id FK
        bigint origin_location_id FK
        bigint destination_location_id FK
        datetime moved_at
        string kind
        date iso_week_start
        int revision
    }
    Probe {
        bigint id PK
        bigint organization_id FK
        bigint thermal_zone_id FK
        string code
        string probe_type
        string external_serial
        int external_channel
        string location
    }
    TemperatureMeasurement {
        bigint id PK
        bigint probe_id FK
        int user_id FK
        datetime measured_at
        decimal temperature_c
    }
    ProbeTemperatureBucket {
        bigint id PK
        bigint probe_id FK
        datetime period_start
        int period_minutes
        decimal average_temperature_c
        int measurement_count
    }
    DailyTemperature {
        bigint id PK
        bigint thermal_zone_id FK
        date date
        decimal average_temperature_c
        decimal min_temperature_c
        decimal max_temperature_c
    }
    SalinityMeasurement {
        bigint id PK
        bigint thermal_zone_id FK
        int user_id FK
        date measured_on
        decimal salinity_psu
    }
    ThermalAnomaly {
        bigint id PK
        bigint thermal_zone_id FK
        datetime starts_at
        datetime ends_at
        string level
    }
```

## 3. Gestion : institutions, comptes, audit et fichiers

`Alert` représente le stockage historique dormant, pas une fonctionnalité
active. `AuditLog` et Actions restent actifs ; `AuditLog.object_id` n’est pas une FK.

```mermaid
erDiagram
    Organization ||..o{ SharingAgreement : "owner_organization"
    Organization ||..o{ SharingAgreement : "partner_organization"
    AuthUser ||..o{ OrganizationMembership : "user"
    Organization ||..o{ OrganizationMembership : "organization"
    AuthUser ||..o| UserPreference : "user"
    OrganizationMembership ||..o| AccountInvitation : "membership"
    Organization |o..o{ AuditLog : "organization"
    AuthUser |o..o{ AuditLog : "user"
    AuthUser |o..o{ AuditLog : "edited_by"
    Organization ||..o{ Alert : "organization"
    Box |o..o{ Alert : "box"
    ThermalZone |o..o{ Alert : "thermal_zone"
    AuthUser |o..o{ Alert : "created_by"
    AuthUser |o..o{ Alert : "resolved_by"
    Organization ||..o{ ExcelImport : "organization"
    AuthUser |o..o{ ExcelImport : "user"
    ExcelImport ||..o{ ExcelImportRow : "excel_import"
    Organization ||..o{ DataExport : "organization"
    AuthUser |o..o{ DataExport : "user"

    Organization {
        bigint id PK
        uuid portable_id
        string name
        string slug
        string city
        string country
        bool is_active
    }
    PartnerInstitution {
        bigint id PK
        string name
        string city
        string country
        string contact_name
    }
    SharingAgreement {
        bigint id PK
        bigint owner_organization_id FK
        bigint partner_organization_id FK
        string status
    }
    AuthUser {
        int id PK
        string username
        string email
        bool is_superuser
    }
    OrganizationMembership {
        bigint id PK
        int user_id FK
        bigint organization_id FK
        string role
        bool is_active
        bool is_responsable
        bool is_hidden_from_team
    }
    UserPreference {
        bigint id PK
        int user_id FK
        string interface_language
    }
    AccountInvitation {
        bigint id PK
        bigint membership_id FK
        int generation
        datetime issued_at
        datetime expires_at
    }
    AuthenticationThrottle {
        bigint id PK
        string scope
        string key_hash
        int event_count
        datetime blocked_until
    }
    AuditLog {
        bigint id PK
        bigint organization_id FK
        int user_id FK
        int edited_by_id FK
        string action
        string object_type
        string object_id
    }
    Alert {
        bigint id PK
        bigint organization_id FK
        bigint box_id FK
        bigint thermal_zone_id FK
        int created_by_id FK
        int resolved_by_id FK
        string alert_type
        string level
        datetime resolved_at
    }
    ExcelImport {
        bigint id PK
        bigint organization_id FK
        int user_id FK
        string file_name
        string status
    }
    ExcelImportRow {
        bigint id PK
        bigint excel_import_id FK
        int row_number
        bool is_valid
    }
    DataExport {
        bigint id PK
        bigint organization_id FK
        int user_id FK
        string export_type
        string file_format
    }
```

## 4. Référentiels institutionnels et identités de souche

`InstitutionSpecies` référence l’espèce partagée, mais n’est pas la cible de
`Strain.species`. `LocalStrainIdentity` référence les affectations AAA/BBB par
leurs FK, pas par des copies de codes.

```mermaid
erDiagram
    Species ||..o{ SpeciesTranslation : "species"
    Strain ||..o{ StrainTranslation : "strain"
    Organization ||..o{ InstitutionSpecies : "organization"
    Species ||..o{ InstitutionSpecies : "species"
    Organization ||..o{ InstitutionStrainCreationRequest : "organization"
    AuthUser ||..o{ InstitutionStrainCreationRequest : "actor"
    Strain ||..o| InstitutionStrainCreationRequest : "strain"
    Organization ||..o{ SpeciesCodeChangeRequest : "organization"
    AuthUser ||..o{ SpeciesCodeChangeRequest : "actor"
    InstitutionSpecies ||..o{ SpeciesCodeChangeRequest : "profile"
    Organization ||..o{ OrganizationSpeciesCode : "organization"
    Species ||..o{ OrganizationSpeciesCode : "species"
    Organization ||..o{ OrganizationProvenanceCode : "organization"
    BiologicalProvenance ||..o{ OrganizationProvenanceCode : "biological_provenance"
    Strain ||..o| LocalStrainIdentity : "strain"
    OrganizationSpeciesCode ||..o{ LocalStrainIdentity : "species_code_assignment"
    OrganizationProvenanceCode |o..o{ LocalStrainIdentity : "provenance_code_assignment"
    Organization ||..o{ LocalStrainNumberCounter : "organization"
    Species ||..o{ LocalStrainNumberCounter : "species"
    BiologicalProvenance |o..o{ LocalStrainNumberCounter : "biological_provenance"

    SpeciesTranslation {
        bigint id PK
        bigint species_id FK
        string language_code
        string name
    }
    StrainTranslation {
        bigint id PK
        bigint strain_id FK
        string language_code
        string name
    }
    InstitutionSpecies {
        bigint id PK
        bigint organization_id FK
        bigint species_id FK
        string scientific_name
        int worms_aphia_id
        bigint revision
    }
    InstitutionStrainCreationRequest {
        bigint id PK
        bigint organization_id FK
        int actor_id FK
        bigint strain_id FK
        uuid request_id
        json intent
    }
    SpeciesCodeChangeRequest {
        bigint id PK
        bigint organization_id FK
        int actor_id FK
        bigint profile_id FK
        uuid request_id
        json intent
        json result
    }
    BiologicalProvenance {
        bigint id PK
        string name
    }
    GlobalStrainIdentity {
        bigint id PK
        uuid global_id
    }
    OrganizationSpeciesCode {
        bigint id PK
        bigint organization_id FK
        bigint species_id FK
        string code
    }
    OrganizationProvenanceCode {
        bigint id PK
        bigint organization_id FK
        bigint biological_provenance_id FK
        string code
        string country_code
    }
    LocalStrainIdentity {
        bigint id PK
        bigint strain_id FK
        bigint species_code_assignment_id FK
        bigint provenance_code_assignment_id FK
    }
    LocalStrainNumberCounter {
        bigint id PK
        bigint organization_id FK
        bigint species_id FK
        bigint biological_provenance_id FK
        int last_number
    }
```

## 5. Création, identification et inventaire des boîtes

`Box.global_identity` cible `BoxGlobalIdentity.global_id` (UUID, colonne
`global_id` sur `Box`), pas la PK ni `GlobalBoxIdentity`. `Box.merged_into`
cible une autre ligne `Box`. `BoxCodeNamespace` n’a pas de FK vers les boîtes
qu’il aide à numéroter.

```mermaid
erDiagram
    Organization ||..o{ AutomaticBoxCreationRequest : "organization"
    AuthUser ||..o{ AutomaticBoxCreationRequest : "actor"
    Box |o..o| AutomaticBoxCreationRequest : "box"
    Box ||..o{ BoxDnaValidation : "box"
    AuthUser ||..o{ BoxDnaValidation : "created_by"
    AuthUser ||..o{ BoxDnaValidation : "updated_by"
    AuthUser |o..o{ BoxDnaValidation : "revoked_by"
    Organization ||..o| BoxInventoryInitialization : "organization"
    Box ||..o{ BoxLocalIdAlias : "box"

    AutomaticBoxCreationRequest {
        bigint id PK
        bigint organization_id FK
        int actor_id FK
        bigint box_id FK
        uuid request_id
        json intent
        bool cancelled
    }
    BoxCodeNamespace {
        bigint id PK
        string namespace
        bigint high_water
    }
    BoxDnaValidation {
        bigint id PK
        bigint box_id FK
        int created_by_id FK
        int updated_by_id FK
        int revoked_by_id FK
        string evidence_url
        bigint revision
        datetime revoked_at
    }
    BoxGlobalIdentity {
        bigint id PK
        uuid global_id
        string allocation_reason
    }
    BoxInventoryInitialization {
        bigint id PK
        bigint organization_id FK
        datetime initialized_at
        int box_count
        string selection_hash
    }
    BoxLocalIdAlias {
        bigint id PK
        bigint box_id FK
        string code
        datetime created_at
    }
```

## 6. Transferts, lignée portable et propriété

`GlobalBoxIdentity` est distinct de `BoxGlobalIdentity` : il est référencé par
les épisodes de propriété et les propositions de don. La boîte locale d’un
épisode est reliée par `local_box` (OneToOne). Les UUID de provenance `arrival_*`,
`PortableLineageEdge.transfer_id` / `item_id` et les identifiants institutionnels
de l’enveloppe ne sont pas des FK. Les liaisons `local_box` et `local_lineage` du
graphe portable sont facultatives.

```mermaid
erDiagram
    Box ||..o{ BoxTransfer : "box"
    Organization ||..o{ BoxTransfer : "from_organization"
    Organization ||..o{ BoxTransfer : "to_organization"
    AuthUser |o..o{ BoxTransfer : "user"
    Organization ||..o{ BoxTransferImport : "destination_organization"
    Box ||..o| BoxTransferImport : "created_box"
    AuthUser |o..o{ BoxTransferImport : "imported_by"
    Organization ||..o{ TransferEnvelope : "source_organization"
    AuthUser |o..o{ TransferEnvelope : "created_by"
    TransferEnvelope ||..o{ TransferItem : "envelope"
    Box ||..o{ TransferItem : "source_box"
    Organization ||..o{ PortableLineageNode : "organization"
    Box |o..o| PortableLineageNode : "local_box"
    Organization ||..o{ PortableLineageEdge : "organization"
    PortableLineageNode ||..o{ PortableLineageEdge : "source_node"
    PortableLineageNode ||..o{ PortableLineageEdge : "target_node"
    BoxLineage |o..o| PortableLineageEdge : "local_lineage"
    GlobalBoxIdentity ||..o{ BoxOwnershipEpisode : "identity"
    Organization ||..o{ BoxOwnershipEpisode : "organization"
    Box ||..o| BoxOwnershipEpisode : "local_box"
    Organization ||..o{ BoxDonationProposal : "source_organization"
    Organization ||..o{ BoxDonationProposal : "destination_organization"
    Box ||..o{ BoxDonationProposal : "source_box"
    GlobalBoxIdentity ||..o{ BoxDonationProposal : "identity"
    BoxOwnershipEpisode ||..o{ BoxDonationProposal : "source_episode"
    AuthUser ||..o{ BoxDonationProposal : "history_verified_by"
    AuthUser ||..o{ BoxDonationProposal : "created_by"
    AuthUser |o..o{ BoxDonationProposal : "cancelled_by"

    BoxTransfer {
        bigint id PK
        bigint box_id FK
        bigint from_organization_id FK
        bigint to_organization_id FK
        int user_id FK
        date transfer_date
        string status
    }
    BoxTransferImport {
        bigint id PK
        bigint destination_organization_id FK
        bigint created_box_id FK
        int imported_by_id FK
        string format_version
        string source_transfer_id
        string source_global_code
        datetime imported_at
    }
    TransferEnvelope {
        bigint id PK
        bigint source_organization_id FK
        int created_by_id FK
        uuid transfer_id
        int protocol_major
        int protocol_minor
        uuid source_institution_id
        uuid destination_institution_id
    }
    TransferItem {
        bigint id PK
        bigint envelope_id FK
        bigint source_box_id FK
        uuid item_id
        uuid global_strain_id
        int declared_polyp_quantity
        json lineage_snapshot
    }
    PortableLineageNode {
        bigint id PK
        bigint organization_id FK
        bigint local_box_id FK
        uuid node_id
    }
    PortableLineageEdge {
        bigint id PK
        bigint organization_id FK
        bigint source_node_id FK
        bigint target_node_id FK
        bigint local_lineage_id FK
        uuid edge_id
        string relationship_type
        uuid transfer_id
        uuid item_id
    }
    GlobalBoxIdentity {
        bigint id PK
        uuid global_box_id
    }
    BoxOwnershipEpisode {
        bigint id PK
        bigint identity_id FK
        bigint organization_id FK
        bigint local_box_id FK
        int sequence
        datetime started_at
        datetime ended_at
        uuid arrival_transfer_id
        uuid arrival_item_id
    }
    BoxDonationProposal {
        bigint id PK
        bigint source_organization_id FK
        bigint destination_organization_id FK
        bigint source_box_id FK
        bigint identity_id FK
        bigint source_episode_id FK
        int history_verified_by_id FK
        int created_by_id FK
        int cancelled_by_id FK
        uuid proposal_id
        int source_ownership_sequence
        string history_sha256
        string state
        int revision
    }
```

## 7. Historique des boîtes, températures historiques et reçus

`BoxHistoryStream.head` pointe vers une révision ; `BoxHistoryRevision.stream`
et `.predecessor` sont deux autres relations. Les localisateurs d’événements,
`original_box_id`, `author_identifier` et les `actor_id` des reçus sont des
valeurs, pas des FK. Aucun lien FK ne rattache les reçus aux lignes de relevé
ou de preuve. `HistoricalTemperatureReading` se rattache au relevé biologique,
pas à une sonde.

```mermaid
erDiagram
    Organization ||..o{ BoxHistoryStream : "organization"
    Box |o..o| BoxHistoryStream : "box"
    BoxHistoryRevision |o..o{ BoxHistoryStream : "head"
    BoxHistoryStream ||..o{ BoxHistoryEventIdentity : "stream"
    BoxHistoryStream ||..o{ BoxHistoryRevision : "stream"
    BoxHistoryRevision |o..o{ BoxHistoryRevision : "predecessor"
    AuthUser |o..o{ BoxHistoryRevision : "author"
    Box ||..o{ HistoricalBoxMovement : "box"
    ThermalZone ||..o{ HistoricalBoxMovement : "from_thermal_zone"
    ThermalZone ||..o{ HistoricalBoxMovement : "to_thermal_zone"
    BoxLocation ||..o{ HistoricalBoxMovement : "origin_location"
    BoxLocation ||..o{ HistoricalBoxMovement : "destination_location"
    AuthUser |o..o{ HistoricalBoxMovement : "accepted_by"
    BiologicalMeasurement ||..o| HistoricalTemperatureReading : "biological_measurement"
    HistoricalTemperatureReading ||..o{ HistoricalTemperatureEvidence : "reading"
    Organization ||..o{ HistoricalTemperatureImportReceipt : "organization"
    Organization ||..o{ MeasurementImportReceipt : "organization"

    BoxHistoryStream {
        uuid id PK
        bigint organization_id FK
        bigint box_id FK
        uuid head_id FK
        bigint original_box_id
    }
    BoxHistoryEventIdentity {
        uuid id PK
        uuid stream_id FK
        string kind
        string source_key
        json locator
    }
    BoxHistoryRevision {
        uuid id PK
        uuid stream_id FK
        uuid predecessor_id FK
        int author_id FK
        bigint sequence
        json operations
        int schema_version
    }
    HistoricalBoxMovement {
        bigint id PK
        bigint box_id FK
        bigint from_thermal_zone_id FK
        bigint to_thermal_zone_id FK
        bigint origin_location_id FK
        bigint destination_location_id FK
        int accepted_by_id FK
        int iso_year
        int iso_week
        string candidate_key
        json evidence
    }
    HistoricalTemperatureReading {
        bigint id PK
        bigint biological_measurement_id FK
        string association_status
        int iso_year
        int iso_week
        text temperature_c
    }
    HistoricalTemperatureEvidence {
        bigint id PK
        bigint reading_id FK
        string temperature_cell
        text source_value
        int source_iso_year
        int source_iso_week
    }
    HistoricalTemperatureImportReceipt {
        bigint id PK
        bigint organization_id FK
        bigint actor_id
        string plan_fingerprint
        string snapshot_sha256
        json accepted_plan
    }
    MeasurementImportReceipt {
        uuid id PK
        bigint organization_id FK
        bigint actor_id
        string source_sha256
        string plan_fingerprint
        json accepted_plan
    }
```

## 8. Partage externe en lecture seule

`BoxReaderShareCapture` dépend de `BoxReaderShare`. Le partage avec un destinataire
ne crée pas de `OrganizationMembership` et ne transfère pas la boîte. Voir
[le contrat backend](../../backend/BOX_READER_SHARING.md).

```mermaid
erDiagram
    Organization ||..o{ BoxReaderShare : "organization"
    Box ||..o{ BoxReaderShare : "box"
    AuthUser |o..o{ BoxReaderShare : "recipient_user"
    AuthUser |o..o{ BoxReaderShare : "revoked_by"
    AuthUser |o..o{ BoxReaderShare : "created_by"
    BoxReaderShare ||..o{ BoxReaderShareCapture : "share"
    AuthUser |o..o{ BoxReaderShareCapture : "captured_by"

    BoxReaderShare {
        bigint id PK
        bigint organization_id FK
        bigint box_id FK
        int recipient_user_id FK
        int revoked_by_id FK
        int created_by_id FK
        uuid public_id
        string mode
        int policy_revision
        datetime accepted_at
        datetime access_expires_at
        datetime revoked_at
    }
    BoxReaderShareCapture {
        bigint id PK
        bigint share_id FK
        int captured_by_id FK
        int policy_revision
        string content_sha256
        datetime captured_at
    }
```

## Contraintes et limites à ne pas déduire des cardinalités

- `BiologicalMeasurement` : UNIQUE conditionnelle `(box, week_start)` lorsque
  `duplicate_of IS NULL` (migration `0014_measurement_canonical_evidence`) : au
  plus un relevé canonique par boîte/semaine ISO, pas une seule ligne physique.
  Les lignes `duplicate_of` référencent un relevé canonique et conservent les
  preuves doublons en lecture seule dans les parcours ordinaires. Les managers
  ordinaires les gardent; les lectures scientifiques utilisent `.canonical()`,
  y compris `box.biological_measurements.canonical()` via le manager inverse.
  Un POST ordinaire dans une semaine canonique occupée reste un
  `409 measurement_week_conflict`, même à date identique ou avec `0/0`.
  `measured_on` et la clé restent non NULL; précisions persistées
  `legacy_unverified` / `exact_date`. **Pas de persistance `iso_week`**, malgré
  les objets de préparation semaine seule. `user` est le dernier acteur legacy,
  pas l'observateur original. Strobiles et salinité peuvent être NULL; zéro reste
  une mesure réelle, jamais une absence. Preuves :
  `backend/apps/measurements/models.py`, `services.py`, `test_canonical_evidence.py`.
- `Probe` : `external_serial` et `external_channel` existent depuis la migration
  `0015_iminilide_probe_collection` et identifient la série/voie externe collectée.
  Le client iMinilide construit les URL à partir de ces identifiants et de la
  configuration commune; aucun besoin d'URL stockée par sonde n'en découle.
  La collecte est implémentée, son activation réelle n'est pas vérifiée ici.
- Repiquage : les champs quantitatifs d'un événement legacy restent NULL. Pour
  une nouvelle saisie partielle, avant connu, total/après NULL. Allocation enfant
  NULL = inconnue, zéro = connue. Snapshots/allocations acceptés ne sont pas
  recalculés après correction de relevé. `Box.polyp_state_revision` et
  `BoxCodeNamespace` participent à la concurrence/réservation, pas à une mesure.
- Mouvements : UNIQUE partielle `(box, iso_week_start)` pour `ordinary`, CHECK
  de correspondance à `moved_at` en semaine Paris. Autres kinds sans clé/quota;
  liens de périodes NULL en legacy, jamais reconstruits automatiquement.
- Taxonomie : `Strain.organization` et `global_identity` sont nullables. Au plus
  une souche par institution et identité globale non NULL ; le compteur X
  interne ne rend pas `Strain.number` universellement unique. Identité, codes
  institutionnels et permissions restent distincts.
- Transferts : le stockage source Transfer v2 jusqu’à la Phase 5 est distinct
  du reçu d’import v1 (`BoxTransferImport`). Les fondations de Phase 6
  (`GlobalBoxIdentity`, `BoxOwnershipEpisode`, `BoxDonationProposal`) ne sont
  pas une acceptation v2 ni une matérialisation destination. Voir le
  [contrat CSV](../transferts_csv.md), [l’identité de boîte](../../backend/TRANSFER_V2_BOX_IDENTITY.md)
  et la [proposition de don](../../backend/TRANSFER_V2_BOX_DONATION_PROPOSAL.md).
- `AccountInvitation` conserve génération, émission et expiration à 24 h, sans
  secret ; un membership masqué conserve ses permissions. Voir le
  [contrat comptes et permissions](../context/organizations-permissions.md).

Voir aussi la [traçabilité avec les MCD historiques](../tracabilite_mcd.md)
et [l’état courant du produit](../../POLYPBASE_CURRENT_STATE.md).

## Générer une image (PNG/SVG) pour une présentation

Les blocs ```mermaid``` peuvent être convertis en image avec le CLI Mermaid :

```bash
npx -y @mermaid-js/mermaid-cli -i docs/mcd/schema_bdd.md -o docs/mcd/schema_bdd.png
```

(génère un PNG par diagramme). Alternative : copier un bloc dans
<https://mermaid.live> pour l'exporter en PNG/SVG.
