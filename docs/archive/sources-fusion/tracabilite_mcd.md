# Traçabilité MCD validé ↔ implémentation Django

Ce document boucle la traçabilité entre les **3 MCD validés par les professeurs**
(`docs/mcd/` : métier, environnement, gestion) et l'**implémentation Django**
(les modèles enregistrés par les applications). Il documente la correspondance
conception → code et liste les écarts à arbitrer.

**Relecture PR3 :** le [schéma Django](mcd/schema_bdd.md) couvre les **68 modèles
concrets propres au projet**, dont **38 nouvellement dessinés** (section 4).
Le décompte provient du registre `apps.*` sous `config.preflight_settings`
(SQLite en mémoire, dotenv neutralisé, aucune connexion ouverte) et exclut
les modèles abstraits, proxys et modèles générés automatiquement. `auth.User`
est fourni par Django et sert de cible externe ; il n'entre pas dans ce total.
Les trois MCD validés restent des pièces de conception historiques. Un écart
n'est ni une autorisation de migration ni un engagement produit. Voir
[l'état courant](../etat-historique-2026-10-14_2026-11-16.md).

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
| **UTILISATEUR** (id_utilisateur, nom, prenom, email) | `auth.User` (externe) + `accounts.UserPreference` (projet) | id→id, nom_utilisateur→`last_name`, prenom→`first_name`, email→`email` (+ `username`/droits Django) | ✅ |
| **ESPECE** (id_espece, code_espece, nom_scientifique) | `taxonomy.Species` | id→id, code_espece→`genus_species_code`, nom_scientifique→`scientific_name` (+ common_name, worms_aphia_id) | ✅ |
| **TAXON** (id_taxon, nom_scientifique, rang_taxonomique) | `taxonomy.Taxon` | id→id, nom_scientifique→`name`, rang_taxonomique→`rank` (+ `parent` hiérarchie) | ✅ |
| **SOUCHE** (id_souche, code_souche, nom_souche) | `taxonomy.Strain` | id→id, code_souche→`code` ; pas de nom sur `Strain` ; `StrainTranslation.name` porte un libellé par langue | 🟠 |
| **BOITE** (id_boite, global_id, code_local, date_creation, statut_boite) | `cultures.Box` | id→id, code_local→`local_code` dans la lecture historique, date_creation→`created_on`, statut_boite→`status` ; `global_code` est aujourd'hui le Local ID opérationnel, distinct de la FK nullable `global_identity` vers `BoxGlobalIdentity.global_id` | 🟠 |
| **ZONE_THERMIQUE** (id_zone, nom_zone, type_zone, temperature_cible) | `cultures.ThermalZone` | id→id, nom_zone→`name`, type_zone→`zone_type`, temperature_cible→`target_temperature_c` | ✅ |
| **SONDE_TEMPERATURE** (id, nom_sonde, numero_serie, url_api) | `measurements.Probe` | id→id, nom_sonde→`code`, numero_serie→`external_serial`, voie→`external_channel` (migration `0015_iminilide_probe_collection`) ; pas de champ `url_api` par sonde : le client iMinilide construit l'URL à partir de la configuration commune, de la série et de la voie | 🟠 URL du MCD représentée autrement; collecte iMinilide implémentée, activation réelle non vérifiée ici |

## 2. Associations

| Association MCD (attributs) | Implémentation Django | Statut |
|---|---|---|
| **CLASSER** (ESPECE 0,1 — TAXON 0,N) | `Species.taxon` (FK SET_NULL) | ✅ |
| **POSSEDER_SOUCHE** (ESPECE 0,N — SOUCHE 1,1) | `Strain.species` (FK PROTECT) | ✅ |
| **CONTENIR** (SOUCHE 0,N — BOITE 1,1) | `Box.strain` (FK PROTECT) | ✅ |
| **AVOIR_PROVENANCE** (SOUCHE 0,1 — STRUCTURE 0,N : type_provenance, lieu, commentaire) | `taxonomy.Origin` (source_type, description, latitude/longitude, **partner_institution** → `organizations.PartnerInstitution`) relié par `Strain.origin` | 🟠 provenance reliée à `PartnerInstitution`, pas à `STRUCTURE` ; `lieu` éclaté en lat/long + nom institution |
| **SAISIR_RELEVE** (UTILISATEUR — BOITE : date_releve, nb_polypes, nb_ephyrules, type_observation, commentaire) | `measurements.BiologicalMeasurement` (measured_on, week_start, temporal_precision, counts, notes, user, original_observer, observer_status, origin, imported_by) **+** `Observation` (observation_type) | 🟠 observation séparée; unicité canonique boîte/semaine (`duplicate_of IS NULL`), preuves doublons conservées en lecture seule, acteur/observateur/origine distincts; `measured_on` reste obligatoire, pas de semaine seule persistée |
| **EFFECTUER_REPIQUAGE** (UTILISATEUR + BOITE parent + BOITE enfant : date_repiquage, motif, nb_polypes_preleves) | `SubcultureEvent` (event_date, occurred_at, reason, user, parent_polyp_count_before, allocated_polyp_count, parent_polyp_count_after, parent_state_snapshot) **+** `SubcultureAllocation` (child_box, allocated_polyps) **+** `BoxLineage` | ✅ quantités/snapshot implémentés; total/après NULL en saisie partielle, allocation enfant zéro distincte d'inconnue; legacy conservé |
| **RANGE** (UTILISATEUR + BOITE + ZONE : date_entree, date_sortie, motif) | `BoxLocation` (starts_at, ends_at, notes) **+** `BoxMovement` (from/to zone, moved_at, user, kind, iso_week_start, revision, origin/destination_location) | 🟠 périodes et événements distincts; auteur original et corrections auditées; un ordinary/semaine Paris; legacy non reclassé |
| **AFFECTER_SONDE** (SONDE 0,N — ZONE 0,N : date_debut, date_fin) | `Probe.thermal_zone` (FK simple) | ❌ **non historisé** : pas de date_debut/date_fin, une sonde = une zone courante |
| **MESURER_TEMPERATURE** (SONDE — ZONE : temperature_mesuree, date_mesure, heure_mesure) | `measurements.TemperatureMeasurement` (probe, measured_at, temperature_c) **+** `DailyTemperature` (agrégat) | ✅ date+heure → `measured_at` (DateTime) |
| **MESURER_SALINITE** (ZONE — UTILISATEUR : date_mesure, heure_mesure, valeur_salinite) | `measurements.SalinityMeasurement` (thermal_zone, measured_on, salinity_psu, user) | 🟠 **heure_mesure absente** (`measured_on` est un DateField) |
| **APPARTENIR** (UTILISATEUR — STRUCTURE : role) | `accounts.OrganizationMembership` (user, organization, role) | ✅ |
| **IMPORTER** (STRUCTURE + UTILISATEUR : date_import, chemin_fichier, type_fichier, base_cible) | `exports.ExcelImport` (organization, file_name, status, user, imported_at) **+** `ExcelImportRow` | 🟠 **type_fichier** et **base_cible** absents (Excel implicite) |
| **EXPORTER** (STRUCTURE + UTILISATEUR : date_export, chemin_fichier, type_fichier, base_cible) | `exports.DataExport` (organization, export_type→base_cible, file_format→type_fichier, file_name, user, exported_at) | ✅ |
| **TRACER_ACTION** (STRUCTURE + UTILISATEUR : date, type_action, type_objet, identifiant_objet, origine_action) | `audit.AuditLog` (organization, user, action, object_type, object_id, description, metadata, created_at) | 🟠 **origine_action absent** (manuel/auto) — pourrait vivre dans `metadata` |

## 3. Écarts à arbitrer (champs/relations manquants)

Par ordre d'impact pour le besoin métier :

1. **SONDE_TEMPERATURE : série/voie implémentées** — `Probe.external_serial` et
   `external_channel` existent (migration `0015_iminilide_probe_collection`). La
   collecte iMinilide utilise ces identifiants et le client/configuration communs;
   l'absence d'un champ `url_api` par sonde ne constitue pas un besoin établi
   d'ajout. Ne pas confondre capacité implémentée et activation réelle non vérifiée.
2. **AFFECTER_SONDE non historisé** — si l'historique sonde↔zone est requis,
   créer un modèle `ProbeAssignment` (probe, zone, date_debut, date_fin). Sinon
   documenter le choix « zone courante uniquement ».
3. **EFFECTUER_REPIQUAGE — écart quantitatif résolu** : champs avant/réparti/après
   sur `SubcultureEvent` et allocations enfant sur `SubcultureAllocation`.
   Ne plus programmer l'ajout du champ comme travail manquant; voir
   [`../backend/QUANTITATIVE_SUBCULTURE.md`](../../reference/annexes/quantitative-subculture.md).
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
9. **SOUCHE : `nom_souche`** — absent de `Strain` ; vérifier si les libellés de
   `StrainTranslation` couvrent le besoin avant d'envisager un champ distinct.

## 4. Éléments implémentés hors MCD (enrichissements)

Les éléments ci-dessous complètent les trois MCD validés dans la documentation
du code. Leur présence dans le [schéma Django](mcd/schema_bdd.md) ne les rend pas
actifs et ne modifie pas les MCD historiques.

| Modèle Django | Rôle | Recommandation |
|---|---|---|
| `cultures.IdentificationTag` | **QR code / NFC / RFID** des boîtes (feature QR) | À ajouter au MCD métier (entité TAG liée à BOITE/ZONE) |
| `cultures.BoxTransfer` | Transfert d'une boîte entre structures | À ajouter (MCD gestion) si le besoin est confirmé |
| `cultures.BoxMovement` | Déplacement ponctuel de boîte (complète RANGE) | Documenter avec RANGE |
| `measurements.Observation` | Observations qualitatives (porte type_observation) | Documenter avec SAISIR_RELEVE |
| `measurements.ThermalAnomaly` | Modèle d'anomalies de température | Présence technique, pas une fonctionnalité d'alertes active ni une règle d'avertissement inféré |
| `audit.Alert` | Stockage historique des anciennes alertes | Dormant : modèle, table, données et migrations conservés; décision distincte explicitement autorisée et non destructive requise pour leur devenir |
| `organizations.PartnerInstitution` | Institutions de provenance | Lié à AVOIR_PROVENANCE |
| `organizations.SharingAgreement` | Accords de partage inter-structures | À ajouter (MCD gestion) si confirmé |
| `accounts.UserPreference` | Langue d'interface | Extension technique (UI) |

L'abandon produit des Alertes ne concerne pas `AuditLog` ni Actions : la
traçabilité et les entrées historiques sont préservées. Les éléments liés aux
anciennes alertes ne sont pas des fonctionnalités à réintroduire ou à promouvoir
dans le MCD actif. Les mesures restent factuelles, sans avertissement
opérationnel ou biologique inféré.

### Modèles nouvellement dessinés (38)

| Domaine | Modèles Django | Correspondance avec les MCD historiques |
|---|---|---|
| Comptes | `accounts.AccountInvitation`, `accounts.AuthenticationThrottle` | Invitation liée en OneToOne à l'appartenance ; stockage de limitation d'authentification sans FK utilisateur. |
| Référentiels et traductions | `taxonomy.InstitutionSpecies`, `taxonomy.InstitutionStrainCreationRequest`, `taxonomy.SpeciesCodeChangeRequest`, `taxonomy.SpeciesTranslation`, `taxonomy.StrainTranslation` | Profil institutionnel au-dessus de `Species`, demandes de création/changement et libellés par langue ; `Strain.species` reste liée à `Species`, pas au profil. |
| Identités et codes de souche | `taxonomy.BiologicalProvenance`, `taxonomy.GlobalStrainIdentity`, `taxonomy.LocalStrainIdentity`, `taxonomy.LocalStrainNumberCounter`, `taxonomy.OrganizationProvenanceCode`, `taxonomy.OrganizationSpeciesCode` | Identités globale et locale, provenance biologique, affectations AAA/BBB et compteur X. `BiologicalProvenance` ne remplace pas `Origin`. |
| Création, identification et inventaire | `cultures.AutomaticBoxCreationRequest`, `cultures.BoxCodeNamespace`, `cultures.BoxDnaValidation`, `cultures.BoxGlobalIdentity`, `cultures.BoxInventoryInitialization`, `cultures.BoxLocalIdAlias` | Demande de création, réservation de codes, preuve de validation ADN, identité permanente rattachée par `Box.global_identity`, initialisation par institution et alias locaux. |
| Transferts et lignée portable | `cultures.BoxTransferImport`, `cultures.TransferEnvelope`, `cultures.TransferItem`, `cultures.PortableLineageNode`, `cultures.PortableLineageEdge` | Reçu v1 distinct de l'enveloppe et des items source v2. Lignée portable avec rattachements locaux facultatifs ; ses UUID de provenance ne sont pas des FK. |
| Propriété et don | `cultures.GlobalBoxIdentity`, `cultures.BoxOwnershipEpisode`, `cultures.BoxDonationProposal` | `GlobalBoxIdentity` est distinct de `BoxGlobalIdentity`. L'épisode rattache une institution à sa boîte locale ; la proposition référence la boîte et l'épisode source. Ni acceptation v2 ni matérialisation destination. |
| Historique des boîtes | `cultures.BoxHistoryStream`, `cultures.BoxHistoryEventIdentity`, `cultures.BoxHistoryRevision`, `cultures.HistoricalBoxMovement` | Flux, identités d'événements et révisions ; mouvement historique avec zones et périodes propres, distinct de `BoxMovement`. |
| Mesures et imports historiques | `measurements.HistoricalTemperatureReading`, `measurements.HistoricalTemperatureEvidence`, `measurements.HistoricalTemperatureImportReceipt`, `measurements.MeasurementImportReceipt`, `measurements.ProbeTemperatureBucket` | Lecture historique reliée au relevé biologique (OneToOne) et preuves liées à la lecture ; reçus rattachés à l'institution, acteurs stockés sans FK ; agrégat périodique rattaché à la sonde. |
| Partage externe | `sharing.BoxReaderShare`, `sharing.BoxReaderShareCapture` | Droit de lecture individuel sur une boîte et capture figée ; ni appartenance institutionnelle ni transfert. |

### Contraintes et limites d'implémentation

- Relevés : UNIQUE `(box, week_start)` seulement lorsque `duplicate_of IS NULL`
  (migration `0014_measurement_canonical_evidence`). Les doublons référencent un
  relevé canonique et restent des preuves conservées, en lecture seule dans les
  parcours ordinaires, pas des mesures supplémentaires à compter. Les managers
  ordinaires les exposent; les lectures scientifiques utilisent `.canonical()`, y
  compris `box.biological_measurements.canonical()` via le manager inverse. Une
  semaine canonique occupée reste un conflit `409 measurement_week_conflict` en
  création ordinaire, même pour `0/0` : zéro est une mesure réelle. Preuves :
  `backend/apps/measurements/models.py`, `services.py`, `test_canonical_evidence.py`.
- `Organization.portable_id`, `GlobalStrainIdentity`, AAA/BBB institutionnels,
  `LocalStrainIdentity`, cardinalité canonique et compteur X interne : identité
  technique/biologique distincte des permissions, sans backfill inféré.
- Les snapshots source Transfer v2 jusqu'à la Phase 5 et les fondations de
  propriété et de proposition de don en Phase 6 ne constituent pas un parcours
  d'acceptation ou de matérialisation destination.
- `BoxCodeNamespace`, révisions/séquences scientifiques et allocations immuables :
  cohérence du repiquage et réservation de codes, distinctes de l'allocateur Strain.
- Invitations persistées, Responsable et visibilité technique de membership :
  contrat comptes dans [`context/organizations-permissions.md`](context/organizations-permissions.md).

## 5. Conclusion

Le cœur du MCD validé est **fidèlement implémenté** : toutes les entités
principales (ESPECE, TAXON, SOUCHE, BOITE, ZONE_THERMIQUE, UTILISATEUR,
STRUCTURE) et la majorité des associations existent en base. Les écarts sont
surtout :

- des **champs de détail manquants** (section 3), dont le besoin et les effets
  doivent être décidés avant toute migration ;
- des **divergences structurelles assumées** (relevé/observation, range/movement,
  provenance via PartnerInstitution) qui *couvrent* le besoin différemment ;
- des **enrichissements hors MCD** (section 4), notamment `IdentificationTag`
  pour les QR codes, désormais repérables dans le schéma Django sans réécrire
  les MCD historiques.

Les écarts encore ouverts de la section 3 relèvent d'une décision produit
séparée. Le repiquage quantitatif est déjà implémenté.
