# Traçabilité MCD validé ↔ implémentation Django

Ce document boucle la traçabilité entre les **3 MCD validés par les professeurs**
(`docs/mcd/` : métier, environnement, gestion) et l'**implémentation Django**
(les `models.py` des applications). Il sert de preuve de cohérence conception →
code pour le mémoire, et liste les écarts à arbitrer.

- **Méthode** : pour chaque entité et chaque association du MCD, on indique le
  modèle / champ Django correspondant, puis on note les écarts.
- **Statut** : ✅ couvert · 🟠 partiel / divergence structurelle · ❌ manquant
  (champ ou relation absente du code).

> Rappel de correspondance des domaines :
> - **MCD métier** → apps `cultures`, `taxonomy`
> - **MCD environnement** → apps `cultures` (zones), `measurements`
> - **MCD gestion** → apps `organizations`, `accounts`, `audit`, `exports`

---

## 1. Entités

| Entité MCD | Modèle Django | Correspondance des champs | Statut |
|---|---|---|---|
| **STRUCTURE** (id_structure, nom_structure, type_structure) | `organizations.Organization` | id→id, nom_structure→`name` ; **type_structure absent** | 🟠 |
| **UTILISATEUR** (id_utilisateur, nom, prenom, email) | `auth.User` (+ `accounts.UserPreference`) | id→id, nom_utilisateur→`last_name`, prenom→`first_name`, email→`email` (+ `username`/droits Django) | ✅ |
| **ESPECE** (id_espece, code_espece, nom_scientifique) | `taxonomy.Species` | id→id, code_espece→`genus_species_code`, nom_scientifique→`scientific_name` (+ common_name, worms_aphia_id) | ✅ |
| **TAXON** (id_taxon, nom_scientifique, rang_taxonomique) | `taxonomy.Taxon` | id→id, nom_scientifique→`name`, rang_taxonomique→`rank` (+ `parent` hiérarchie) | ✅ |
| **SOUCHE** (id_souche, code_souche, nom_souche) | `taxonomy.Strain` | id→id, code_souche→`code` ; **nom_souche absent** (only `code`/`number`) | 🟠 |
| **BOITE** (id_boite, global_id, code_local, date_creation, statut_boite) | `cultures.Box` | id→id, global_id→`global_code`, code_local→`local_code`, date_creation→`created_on`, statut_boite→`status` | ✅ |
| **ZONE_THERMIQUE** (id_zone, nom_zone, type_zone, temperature_cible) | `cultures.ThermalZone` | id→id, nom_zone→`name`, type_zone→`zone_type`, temperature_cible→`target_temperature_c` | ✅ |
| **SONDE_TEMPERATURE** (id, nom_sonde, numero_serie, url_api) | `measurements.Probe` | id→id, nom_sonde→`code` ; **numero_serie absent**, **url_api absent** | 🟠 |

## 2. Associations

| Association MCD (attributs) | Implémentation Django | Statut |
|---|---|---|
| **CLASSER** (ESPECE 0,1 — TAXON 0,N) | `Species.taxon` (FK SET_NULL) | ✅ |
| **POSSEDER_SOUCHE** (ESPECE 0,N — SOUCHE 1,1) | `Strain.species` (FK PROTECT) | ✅ |
| **CONTENIR** (SOUCHE 0,N — BOITE 1,1) | `Box.strain` (FK PROTECT) | ✅ |
| **AVOIR_PROVENANCE** (SOUCHE 0,1 — STRUCTURE 0,N : type_provenance, lieu, commentaire) | `taxonomy.Origin` (source_type, description, latitude/longitude, **partner_institution** → `organizations.PartnerInstitution`) relié par `Strain.origin` | 🟠 provenance reliée à `PartnerInstitution`, pas à `STRUCTURE` ; `lieu` éclaté en lat/long + nom institution |
| **SAISIR_RELEVE** (UTILISATEUR — BOITE : date_releve, nb_polypes, nb_ephyrules, type_observation, commentaire) | `measurements.BiologicalMeasurement` (measured_on, polyp_count, ephyrae_count, notes, user) **+** `measurements.Observation` (observation_type) | 🟠 type_observation porté par un modèle `Observation` séparé ; relevé enrichi (strobila_count, culture_status, needs_attention) |
| **EFFECTUER_REPIQUAGE** (UTILISATEUR + BOITE parent + BOITE enfant : date_repiquage, motif, nb_polypes_preleves) | `cultures.SubcultureEvent` (event_date, reason, user) **+** `cultures.BoxLineage` (parent_box, child_box) | 🟠 **nombre_polypes_preleves absent** |
| **RANGE** (UTILISATEUR + BOITE + ZONE : date_entree, date_sortie, motif) | `cultures.BoxLocation` (starts_at, ends_at, notes) **+** `cultures.BoxMovement` (from/to zone, moved_at, user) | 🟠 historique de présence = `BoxLocation` (sans `user`) ; déplacement = `BoxMovement` (avec `user`) |
| **AFFECTER_SONDE** (SONDE 0,N — ZONE 0,N : date_debut, date_fin) | `Probe.thermal_zone` (FK simple) | ❌ **non historisé** : pas de date_debut/date_fin, une sonde = une zone courante |
| **MESURER_TEMPERATURE** (SONDE — ZONE : temperature_mesuree, date_mesure, heure_mesure) | `measurements.TemperatureMeasurement` (probe, measured_at, temperature_c) **+** `DailyTemperature` (agrégat) | ✅ date+heure → `measured_at` (DateTime) |
| **MESURER_SALINITE** (ZONE — UTILISATEUR : date_mesure, heure_mesure, valeur_salinite) | `measurements.SalinityMeasurement` (thermal_zone, measured_on, salinity_psu, user) | 🟠 **heure_mesure absente** (`measured_on` est un DateField) |
| **APPARTENIR** (UTILISATEUR — STRUCTURE : role) | `accounts.OrganizationMembership` (user, organization, role) | ✅ |
| **IMPORTER** (STRUCTURE + UTILISATEUR : date_import, chemin_fichier, type_fichier, base_cible) | `exports.ExcelImport` (organization, file_name, status, user, imported_at) **+** `ExcelImportRow` | 🟠 **type_fichier** et **base_cible** absents (Excel implicite) |
| **EXPORTER** (STRUCTURE + UTILISATEUR : date_export, chemin_fichier, type_fichier, base_cible) | `exports.DataExport` (organization, export_type→base_cible, file_format→type_fichier, file_name, user, exported_at) | ✅ |
| **TRACER_ACTION** (STRUCTURE + UTILISATEUR : date, type_action, type_objet, identifiant_objet, origine_action) | `audit.AuditLog` (organization, user, action, object_type, object_id, description, metadata, created_at) | 🟠 **origine_action absent** (manuel/auto) — pourrait vivre dans `metadata` |

## 3. Écarts à arbitrer (champs/relations manquants)

Par ordre d'impact pour le besoin métier :

1. **SONDE_TEMPERATURE : `numero_serie` + `url_api`** (`measurements.Probe`) — important
   pour la récupération **automatique** des mesures. → ajouter 2 champs.
2. **AFFECTER_SONDE non historisé** — si l'historique sonde↔zone est requis,
   créer un modèle `ProbeAssignment` (probe, zone, date_debut, date_fin). Sinon
   documenter le choix « zone courante uniquement ».
3. **EFFECTUER_REPIQUAGE : `nombre_polypes_preleves`** — ajouter le champ sur
   `SubcultureEvent` (ou `BoxLineage`).
4. **STRUCTURE : `type_structure`** (aquarium/laboratoire) — ajouter sur
   `organizations.Organization`.
5. **MESURER_SALINITE : `heure_mesure`** — passer `measured_on` en DateTime ou
   ajouter un champ heure.
6. **RANGE : utilisateur** — si l'on veut savoir *qui* a rangé une boîte, ajouter
   `user` sur `BoxLocation` (aujourd'hui porté seulement par `BoxMovement`).
7. **TRACER_ACTION : `origine_action`** — ajouter un champ explicite (ou
   normaliser via `metadata`).
8. **IMPORTER : `type_fichier` + `base_cible`** — ajouter si plusieurs formats/
   cibles d'import sont prévus.
9. **SOUCHE : `nom_souche`** — ajouter un champ `name` sur `Strain` si un libellé
   distinct du `code` est attendu.

## 4. Éléments implémentés hors MCD (enrichissements)

Présents dans le code mais absents des 3 MCD validés. Décision à prendre :
**les remonter dans le MCD** (cohérence documentaire) ou **les documenter comme
extensions techniques**.

| Modèle Django | Rôle | Recommandation |
|---|---|---|
| `cultures.IdentificationTag` | **QR code / NFC / RFID** des boîtes (feature QR) | À ajouter au MCD métier (entité TAG liée à BOITE/ZONE) |
| `cultures.BoxTransfer` | Transfert d'une boîte entre structures | À ajouter (MCD gestion) si le besoin est confirmé |
| `cultures.BoxMovement` | Déplacement ponctuel de boîte (complète RANGE) | Documenter avec RANGE |
| `measurements.Observation` | Observations qualitatives (porte type_observation) | Documenter avec SAISIR_RELEVE |
| `measurements.ThermalAnomaly` | Anomalies de température | Extension technique (alerting) |
| `audit.Alert` | Alertes biologiques/température/salinité | Extension technique (alerting) |
| `organizations.PartnerInstitution` | Institutions de provenance | Lié à AVOIR_PROVENANCE |
| `organizations.SharingAgreement` | Accords de partage inter-structures | À ajouter (MCD gestion) si confirmé |
| `accounts.UserPreference` | Langue d'interface | Extension technique (UI) |

## 5. Conclusion

Le cœur du MCD validé est **fidèlement implémenté** : toutes les entités
principales (ESPECE, TAXON, SOUCHE, BOITE, ZONE_THERMIQUE, UTILISATEUR,
STRUCTURE) et la majorité des associations existent en base. Les écarts sont
surtout :

- des **champs de détail manquants** (section 3), faciles à ajouter par migration ;
- des **divergences structurelles assumées** (relevé/observation, range/movement,
  provenance via PartnerInstitution) qui *couvrent* le besoin différemment ;
- des **enrichissements hors MCD** (section 4) — notamment le **QR code** — à
  réintégrer dans le MCD pour que la documentation reste le miroir du code.

**Actions recommandées :**
1. Trancher les 9 écarts de la section 3 (ajout de champs / modèle d'historisation).
2. Mettre à jour les 3 MCD pour intégrer les enrichissements retenus (section 4),
   au minimum le QR code (`IdentificationTag`).
3. Générer migrations + tests pour les champs ajoutés, puis re-vérifier ce document.
