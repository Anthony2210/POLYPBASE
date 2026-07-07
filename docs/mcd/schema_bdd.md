# Schéma de la base de données POLYPBASE (tables réelles)

Diagrammes **entité-relation** générés à partir des modèles Django actuels
(`backend/apps/*/models.py`). Ils reflètent la base **telle qu'implémentée**, et
suivent le découpage des 3 MCD validés (`métier`, `environnement`, `gestion`).

> **Comment visualiser** : ce fichier se rend automatiquement sur **GitHub**.
> Dans **VS Code**, installe l'extension « Markdown Preview Mermaid Support »
> puis ouvre l'aperçu (Ctrl+Shift+V). Pour une image PNG/SVG à coller dans une
> présentation, voir la note en bas.
>
> Les attributs listés sont les principaux (identifiants, champs métier, clés
> étrangères `FK`) ; les colonnes techniques (`created_at`, `notes`…) sont
> omises pour la lisibilité. `PK` = clé primaire, `FK` = clé étrangère.

---

## 1. Domaine MÉTIER — souches, boîtes, suivi biologique

Apps `taxonomy`, `cultures`, `measurements` (relevés biologiques).

```mermaid
erDiagram
    Taxon ||--o{ Species : "classe"
    Taxon ||--o{ Taxon : "parent"
    Species ||--o{ Strain : "possède"
    Origin ||--o{ Strain : "provient de"
    Origin ||--o{ Box : "provenance"
    Strain ||--o{ Box : "peuplée par"
    Box ||--o{ BiologicalMeasurement : "mesurée par"
    Box ||--o{ Observation : "observée par"
    Box ||--o{ SubcultureEvent : "repiquée (parent)"
    Box ||--o{ BoxLineage : "parent"
    Box ||--o{ BoxLineage : "enfant"
    SubcultureEvent ||--o{ BoxLineage : "trace"
    Box ||--o{ IdentificationTag : "étiquetée par"
    Box ||--o{ BoxTransfer : "transférée par"

    Taxon {
        int id PK
        string name
        string rank
        int parent_id FK
    }
    Species {
        int id PK
        string scientific_name
        string genus_species_code
        int taxon_id FK
    }
    Origin {
        int id PK
        string source_type
        date event_date
        int partner_institution_id FK
    }
    Strain {
        int id PK
        int species_id FK
        string code
        int number
        string origin_code
        int origin_id FK
    }
    Box {
        int id PK
        string global_code
        string local_code
        string box_number
        string status
        int organization_id FK
        int strain_id FK
        int origin_id FK
        int thermal_zone_id FK
    }
    BiologicalMeasurement {
        int id PK
        int box_id FK
        date measured_on
        int polyp_count
        int ephyrae_count
        int strobila_count
        string culture_status
        int user_id FK
    }
    Observation {
        int id PK
        int box_id FK
        date observed_on
        string observation_type
        int user_id FK
    }
    SubcultureEvent {
        int id PK
        int parent_box_id FK
        date event_date
        int user_id FK
    }
    BoxLineage {
        int id PK
        int parent_box_id FK
        int child_box_id FK
        int subculture_event_id FK
        string relationship_type
    }
    IdentificationTag {
        int id PK
        string tag_type
        string code
        int box_id FK
        int thermal_zone_id FK
    }
    BoxTransfer {
        int id PK
        int box_id FK
        int from_organization_id FK
        int to_organization_id FK
        date transfer_date
        string status
        int user_id FK
    }
```

---

## 2. Domaine ENVIRONNEMENT — zones thermiques, sondes, températures

Apps `cultures` (zones, emplacements, mouvements), `measurements` (sondes et mesures physiques).

```mermaid
erDiagram
    ThermalZone ||--o{ Box : "héberge"
    ThermalZone ||--o{ BoxLocation : "emplacement"
    ThermalZone ||--o{ BoxMovement : "destination"
    ThermalZone ||--o{ Probe : "équipée de"
    ThermalZone ||--o{ DailyTemperature : "température jour"
    ThermalZone ||--o{ SalinityMeasurement : "salinité"
    ThermalZone ||--o{ ThermalAnomaly : "anomalie"
    Box ||--o{ BoxLocation : "séjour"
    Box ||--o{ BoxMovement : "déplacement"
    Probe ||--o{ TemperatureMeasurement : "mesure"

    ThermalZone {
        int id PK
        int organization_id FK
        string name
        string zone_type
        decimal target_temperature_c
        bool is_active
    }
    Box {
        int id PK
        string global_code
        int thermal_zone_id FK
    }
    BoxLocation {
        int id PK
        int box_id FK
        int thermal_zone_id FK
        datetime starts_at
        datetime ends_at
    }
    BoxMovement {
        int id PK
        int box_id FK
        int from_thermal_zone_id FK
        int to_thermal_zone_id FK
        datetime moved_at
        int user_id FK
    }
    Probe {
        int id PK
        int organization_id FK
        int thermal_zone_id FK
        string code
        string probe_type
        string location
    }
    TemperatureMeasurement {
        int id PK
        int probe_id FK
        datetime measured_at
        decimal temperature_c
    }
    DailyTemperature {
        int id PK
        int thermal_zone_id FK
        date date
        decimal average_temperature_c
        decimal min_temperature_c
        decimal max_temperature_c
    }
    SalinityMeasurement {
        int id PK
        int thermal_zone_id FK
        date measured_on
        decimal salinity_psu
    }
    ThermalAnomaly {
        int id PK
        int thermal_zone_id FK
        datetime starts_at
        datetime ends_at
        string level
    }
```

---

## 3. Domaine GESTION — structures, comptes, alertes, imports/exports

Apps `organizations`, `accounts`, `audit`, `exports`.

```mermaid
erDiagram
    Organization ||--o{ OrganizationMembership : "rattache"
    User ||--o{ OrganizationMembership : "membre"
    User ||--|| UserPreference : "préférences"
    Organization ||--o{ SharingAgreement : "propriétaire"
    Organization ||--o{ SharingAgreement : "partenaire"
    PartnerInstitution ||--o{ Origin : "institution d'origine"
    Organization ||--o{ Alert : "concerne"
    Organization ||--o{ AuditLog : "journalise"
    Organization ||--o{ ExcelImport : "importe"
    Organization ||--o{ DataExport : "exporte"
    ExcelImport ||--o{ ExcelImportRow : "lignes"
    User ||--o{ AuditLog : "auteur"
    User ||--o{ Alert : "crée/résout"

    Organization {
        int id PK
        string name
        string slug
        string city
        string country
        bool is_active
    }
    PartnerInstitution {
        int id PK
        string name
        string city
        string country
        string contact_name
    }
    SharingAgreement {
        int id PK
        int owner_organization_id FK
        int partner_organization_id FK
        string status
    }
    User {
        int id PK
        string username
        string email
        bool is_superuser
    }
    OrganizationMembership {
        int id PK
        int user_id FK
        int organization_id FK
        string role
        bool is_active
    }
    UserPreference {
        int id PK
        int user_id FK
        string interface_language
    }
    Alert {
        int id PK
        int organization_id FK
        int box_id FK
        int thermal_zone_id FK
        string alert_type
        string level
        datetime resolved_at
    }
    AuditLog {
        int id PK
        int organization_id FK
        int user_id FK
        string action
        string object_type
        string object_id
    }
    ExcelImport {
        int id PK
        int organization_id FK
        string file_name
        string status
        int user_id FK
    }
    ExcelImportRow {
        int id PK
        int excel_import_id FK
        int row_number
        bool is_valid
    }
    DataExport {
        int id PK
        int organization_id FK
        string export_type
        string file_format
        int user_id FK
    }
```

---

## Liens entre domaines (rappel)

- **`Box`** (métier) appartient à une **`Organization`** (gestion) et vit dans une
  **`ThermalZone`** (environnement).
- **`Alert`** (gestion) peut cibler une **`Box`** ou une **`ThermalZone`**.
- **`Origin`** (métier) référence une **`PartnerInstitution`** (gestion).
- La plupart des tables portent un lien vers **`User`** (gestion) pour la traçabilité.

## Générer une image (PNG/SVG) pour une présentation

Les blocs ```mermaid``` peuvent être convertis en image avec le CLI Mermaid :

```bash
npx -y @mermaid-js/mermaid-cli -i docs/mcd/schema_bdd.md -o docs/mcd/schema_bdd.png
```

(génère un PNG par diagramme). Alternative : copier un bloc dans
<https://mermaid.live> pour l'exporter en PNG/SVG.
