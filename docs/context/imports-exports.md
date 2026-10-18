# Imports et exports

## Import scientifique générique : fondations intégrées au 2026-10-14

La préparation canonique/CSV (`measurements/canonical.py`, `csv_import.py`, contrat 1.0) et le planner Python en lecture seule (`import_planner.py`, plan 1.1) sont intégrés. Ce ne sont ni une interface d'import, ni un upload persistant, ni un applicateur. L'acteur Admin/technicien actif et l'institution sélectionnée sont validés; les boîtes sont résolues par code exact dans cette institution.

- Statuts : `IGNORED`, `INVALID`, `UNKNOWN_BOX`, `CREATE`, `ALREADY_MATCHES`, `CONFLICT`, `BLOCKED`. Une ligne sans valeurs scientifiques est ignorée et comptée; dès qu'un payload existe, les deux comptes requis doivent être valides. Les champs facultatifs conservent absent/vide/NULL/zéro selon leur contrat.
- Tout doublon source boîte/semaine bloque, même identique. `ALREADY_MATCHES` ne réécrit ni valeur, ni date, ni précision, ni provenance. Un plan/hash n'est ni une réservation, ni une permission d'appliquer, ni un snapshot transactionnel.
- Un repiquage pertinent dans la semaine d'un relevé semaine seule produit `week_subculture_chronology_conflict`, avec evidence dédupliquée boîte/semaine. Un parent partiel avec occurrence connue reste pertinent; une allocation enfant zéro est connue. Les occurrences/allocations legacy inconnues ne sont pas certifiées sans risque.
- Décision produit pour le futur applicateur : tout ou rien, aucune erreur non résolue; renseigner la vraie date de relevé dans la semaine en cas de conflit, ou annuler. Pas de force-import ni de maintien forcé de la précision hebdomadaire. Toute résolution régénère préparation et plan.

La cible approuvée sépare applicateurs de saisie, correction de masse (ancienne valeur attendue + audit) et onboarding, après préparation/revue commune. La saisie/import laboratoire reste hors Administration; correction de masse/onboarding restent administratifs. L'applicateur local exact-date ajoute source originale privée en base, plan durable, idempotence/reçus et revalidation sous verrous. Les sources acceptées, y compris les lots sans création, sont conservées sans expiration ni suppression automatique. Inspection et aperçu ne conservent aucune source.

Le panneau existant propose désormais confirmation explicite et application atomique des plans valides CREATE exact-date / ALREADY_MATCHES / IGNORED. Le serveur reparse la source et le mapping, vérifie les empreintes et les permissions, verrouille les boîtes puis revalide le plan 1.2 complet. `application_available` reflète la readiness du plan, jamais une réservation. Des endpoints autorisés permettent de relire le reçu et télécharger la source privée. Les semaines seules CREATE et observateurs nommés restent bloqués. Périmètre, stockage, limites et preuves de validation : [`../measurement-csv-diagnostics.md`](../measurement-csv-diagnostics.md).

Contrats détaillés : [`../canonical-measurement-csv.md`](../canonical-measurement-csv.md), [`../measurement-import-planner.md`](../measurement-import-planner.md). Ne pas généraliser ces garanties aux importeurs historiques ci-dessous.

## Import historique normalisé

La commande `import_bdd_csv` importe les CSV normalisés de `data/tables/`. Elle s'exécute dans une transaction et propose un `--dry-run` qui rollbacke les écritures. Ses opérations `get_or_create` et `update_or_create` rendent la reprise idempotente selon les clés actuelles. Ce chemin legacy peut mettre à jour les comptes d'un relevé au même lundi conventionnel; ce n'est pas le contrat du POST interactif ni une autorisation d'overwrite pour le futur applicateur générique. La conversion de `numero_souche_local` utilise encore `or None` après parsing et peut perdre X=0 : dette vérifiée, non corrigée ici.

L'éventuelle remise à zéro est strictement limitée aux boîtes de l'organisation explicitement ciblée et à leurs relations concernées. Un import institutionnel ne doit jamais modifier ou supprimer les données d'une autre institution, y compris par cascade.

Le fichier consommé est un produit normalisé, pas la donnée historique brute. Pour les colonnes biologiques de `saisir_releve.csv`, le contrat confirmé est :

- `0` et `"0"` sont des mesures valides;
- vide, valeur absente, non numérique, négative, `NaN` ou infinie sont des violations du contrat;
- l'erreur indique au minimum la ligne et le champ;
- l'import complet est interrompu et rollbacké;
- le traitement des données brutes et la traçabilité des anomalies ont lieu dans le pipeline de normalisation en amont.

Ne pas généraliser ce contrat à un autre import sans l'inspecter. Une valeur invalide ne doit jamais être remplacée silencieusement par zéro.

`initialize_box_inventory` est une commande distincte pour qualifier un lot historique : institution obligatoire, simulation par défaut, contrôle du nombre et de l'empreinte attendus, puis marqueur empêchant une réinitialisation implicite. Elle ne constitue pas une règle générale pour les futurs imports.

## Import historique 2026 (manifeste revu)

L'import du classeur `Suivi_2026_actualisé.xlsx` ne relit jamais Excel en production. Le code de `backend/apps/cultures/historical_2026/` sépare : lecteur XLSX en bibliothèque standard (`workbook.py`), décisions explicites (`decisions.py`), génération du manifeste (`source.py`, `manifest.py`) et service d'import (`importer.py`). Le manifeste `manifest_2026.json` est versionné et se régénère de façon déterministe :

```text
uv run python manage.py build_historical_2026_manifest --workbook <chemin> [--write]
```

Sans `--write`, la commande vérifie seulement que le manifeste versionné est identique. Le SHA-256 du classeur est contrôlé avant tout traitement et tout autre classeur est refusé. Le manifeste porte une empreinte (`fingerprint`) recalculée à chaque chargement.

Règles confirmées (aucune n'est généralisée) : la colonne E (récapitulatif 2025) est exclue; la colonne de `Semaestomeae` dont l'en-tête est « - » vaut S20; seuls `ASP-EVA1.01` et `CLA-JKA1.10` sont corrigés; pour les lignes `TTH-AVI-1.09` repérées, la ligne « Nb éphyrules » contient les polypes; seul le bloc continu à 15 °C de `CCO-JKA-1.04` est importé, le bloc isolé à 10 °C reste visible comme source exclue; `strobila = NULL`, `user = NULL`, `measured_on` = lundi ISO. Les valeurs cachées sous une cellule fusionnée (hors ancre) sont ignorées comme dans Excel.

`import_historical_2026` classe chaque ligne contre l'état courant de la base : `EXACT_ALREADY_PRESENT`, `CREATE`, `EXPECTED_EXPLICIT_CORRECTION`, `CONFLICT`, `MISSING_BOX`, `IDENTITY_MISMATCH`, `EXCLUDED_SOURCE`. Le défaut est la simulation. `--apply` exige `--expected-fingerprint`, `--expected-plan-hash` (affiché par la simulation revue) et `--actor` (administrateur actif de l'institution). Tout `CONFLICT` ou `IDENTITY_MISMATCH` interrompt l'application sans écriture; une semaine déjà occupée avec les mêmes comptes est satisfaite, avec d'autres comptes elle est un conflit. Aucune boîte n'est réactivée. Les boîtes manquantes sont créées en `pending_review`, sans emplacement ni date inventés, avec Species/Strain opérationnelles issues de l'étiquette source. La seule correction de valeur est `LDR-JAP-1.001` S18 : 80/8 vers 80/0, sous garde de l'ancien état exact et avec audit. `COR-JIS-1.001` devient `ATO-JIS-1.001` par renommage de la même boîte (historique d'audit et étiquette QR suivent, aucun doublon); si les deux codes existent, l'import est refusé.

Catégories supplémentaires : `EXPECTED_TTH_CORRECTION` (la valeur stockée est exactement la lecture littérale inversée des mêmes cellules, à la même date; correction gardée et auditée, idempotente) et `EXPECTED_TEST_DATA_COLLISION` (créneau occupé par une mesure de test revue). Cette dernière bloque `--apply` tant que `cleanup_reviewed_test_data` n'a pas été exécuté; l'import ne supprime jamais de donnée de test. Séquence : simulation du nettoyage, application du nettoyage, nouvelle simulation de l'import (les créneaux deviennent `CREATE`), puis application de l'import.

`cleanup_reviewed_test_data` (simulation par défaut) ne supprime que les neuf mesures, l'îlot de la boîte 2312 (emplacement, lignée, événement) et les entrées d'audit revues, après vérification de leurs champs et de la fermeture des dépendances sous verrou; tout écart bloque. Un reçu d'audit conserve l'instantané des objets retirés.

`deactivate_hs_boxes` est une commande séparée (simulation par défaut, mêmes garde-fous) qui réutilise `deactivate_box` pour la liste validée par Étienne. `CTU-CFC-2.007` reste `PENDING_CONFIRMATION`.

## Correcteur local CCA-EOB / V2

`correct_cca_eob_v2` prépare une matrice exacte en lecture seule par défaut et peut réattribuer les relevés ISO 2026 documentés par V2, après revue indépendante. Il ne généralise pas l'import historique : correspondance au manifeste ancien, cellules V2 vérifiées sur les mêmes octets hashés, codes exacts, plan empreinté, administrateur actif, revalidation sous verrous, ordre de transferts compatible avec l'unicité et audits atomiques. Aucun changement de valeur biologique, date ou statut; les semaines hors source restent intactes.

**Décision produit explicite d'Anthony, contrat V3 :** V2 fait autorité pour les identités biologiques 2026 concernées. Les relevés réattribués résolvent leur contexte environnemental via la boîte cible dans les exports/graphiques existants, même si la zone diffère ou reste non résolue. Tous les `BoxLocation`, `BoxMovement`, `ThermalZone`, les pointeurs `Box.thermal_zone` et les températures scientifiques existants restent inchangés. Aucun emplacement, mouvement, lien historique ou température n'est inféré depuis Excel. Le reçu atteste la correction d'attribution biologique et cette décision, pas une correction environnementale ni une équivalence de zones.

La présence d'historiques, de fins inconnues, de liens legacy absents ou de zones différentes/manquantes ne bloque plus à elle seule une réattribution. Les références de zones étrangères, périodes mal formées/chevauchantes et liens mouvement-période persistés contradictoires restent bloquants, sans reconstruction. Les blocages indépendants de lignée/repiquage/transfert et les ambiguïtés d'appariement demeurent. Les snapshots complets de périodes/mouvements, le fingerprint, les verrous et tous les contrôles de péremption sont conservés, y compris pour les changements sans modification de `Box`.

L'opération utilise `OPERATION = cca_eob_v2_20261016_v3`, la politique de baseline `target_box_context_preserve_location_history_v2` et la métadonnée explicite `product_decision` dans le plan et chaque audit individuel/de lot. **Toutes les anciennes empreintes de plan sont invalides.** L'empreinte V3 `2b08dddd35a5a85cac04b02c9dc7b241238444e348f9e37f8d96347175041571` a été appliquée sur la seule copie locale de test et ne vaut pas autorisation ailleurs. Les identifiants de source ne sont pas des empreintes de plan. La commande, ses options et la configuration dédiée restent inchangées, sauf son résumé précisant l'effet de contexte : local read-only par défaut; production, Neon et référence immuable refusés. Procédure, limites et historique de validation : [`../cca-eob-v2-correction.md`](../cca-eob-v2-correction.md). Tests synthétiques V3 validés sur SQLite et sur PostgreSQL 17.4 natif isolé (verrous, y compris l'ordre boîtes, organisation, membership face au PATCH des droits). Les 72 réattributions ont été appliquées uniquement sur la copie locale de test, après sauvegarde restaurée et comparaison exhaustive; aucune application sur la référence immuable, la production ou Neon. L'implémentation seule ne vaut pas autorisation d'application.

## Précision temporelle : limite actuelle de persistance

La fondation `0009_temporal_precision` conserve toutes les dates et clés ISO existantes. Les lundis conventionnels des imports CSV normalisés et `historical_2026` restent `legacy_unverified`, y compris pour de nouvelles créations via ces chemins; aucune date n'est automatiquement certifiée ou convertie. Observateur/origine Phase 4A et comportements d'import restent inchangés.

La préparation canonique sait représenter `iso_week` sans `measured_on`, mais le planner Phase 3 reste en lecture seule : une semaine libre peut être classée `CREATE` avec l'issue bloquante `week_precision_persistence_required` (`ready=False`). Le nouveau champ n'active pas la persistance semaine seule : `measured_on` est toujours NOT NULL et la contrainte de précision refuse `iso_week`. Le contrat de baseline et les classifications du planner sont inchangés.

Une première tranche backend de lecture expose désormais `week_start` (date ISO du lundi, clé seulement) séparément de `measured_on`, prépare les tris latest par boîte et distingue l'existence d'un relevé de la disponibilité de son jour exact dans le filtre d'inventaire. Elle n'active aucune écriture semaine seule et ne modifie ni baseline/fingerprint 1.1, ni classification, ni readiness du planner. La timeline mixte, les tris inter-boîtes, la fraîcheur, le frontend et les exports restent à adapter avant activation.

Les prérequis de schéma, résolution scientifique, contrats d'import/correction et consommateurs sont détaillés dans [`measurements-integrity.md`](measurements-integrity.md#prérequis-exacts-pour-une-future-activation-semaine-seule). Une future clé ISO du lundi ne devra jamais être présentée comme un jour d'observation réel.

## Diagnostic avant contrainte

Avant d'appliquer une contrainte d'unicité sur une base contenant de l'historique, exécuter la commande de diagnostic en lecture seule prévue pour le domaine. Pour les relevés biologiques, `check_biological_measurement_duplicates` groupe par boîte/semaine ISO et rapporte organisation, boîte, clé/semaine ISO, nombre, identifiants et dates des relevés.

Si un doublon existe, ne pas appliquer la migration concernée et ne pas choisir automatiquement une mesure à conserver. La résolution de données scientifiques demande une décision explicite. Les principes généraux de diagnostic avant migration sont documentés dans [`development-deployment.md`](development-deployment.md).

## Exports actuels

Les endpoints sous `/api/exports/` sont limités à l'organisation active et vérifient les permissions côté serveur. L'export téléchargeable actuel produit un CSV hebdomadaire : une ligne par semaine ISO avec, pour chaque boîte, les colonnes de polypes, éphyrules et température.

Contrats importants :

- les filtres espèce, souche, zone, boîte et période sont cumulables;
- une boîte sans relevé dans la période et le périmètre retenus est exclue du CSV et des graphiques;
- une cellule sans relevé reste vide, tandis qu'un zéro enregistré reste `0`;
- le filtre de zone s'appuie sur l'emplacement historique à la date du relevé;
- la température utilise l'agrégat journalier disponible, sinon la consigne de la zone; ne pas présenter ce fallback comme une température observée;
- dans le preview, `measurement_count=0` distingue les zéros d'agrégat d'une semaine sans relevé d'un véritable relevé zéro; l'initialisation quantitative d'un enfant seule n'est pas un relevé exportable;
- `include_other_zones` permet, pour les boîtes ayant contribué dans la zone sélectionnée, d'inclure aussi leurs relevés réalisés dans d'autres zones;
- la sélection, la génération et l'audit restent rattachés à l'organisation active.

Le modèle `DataExport` prévoit plusieurs formats. Une valeur de choix dans un modèle ne prouve pas qu'un format dispose d'un endpoint et d'un parcours UI; ne l'annoncer qu'après vérification des consommateurs actuels.

## Transferts inter-institutions

Transfer v1 dispose maintenant d'une frontière de service backend dédiée. Préparation + audit obligatoire et import atomique, ainsi que les cas de rejeu, sont couverts par des tests ciblés. Les conclusions de concurrence réelle exigent PostgreSQL isolé; SQLite seul ne valide pas les verrous. Consulter [`../transferts_csv.md`](../transferts_csv.md) pour les détails, sans recopier le contrat ici. Un transfert n'accorde aucun accès durable aux données de l'institution source.

### Transfer v2 — fondations intégrées jusqu'à la Phase 5

Phase 2B est intégrée à `main` au commit `20adf0a` (`feat: add transfer v2 protocol foundation`). `TransferEnvelope` / `TransferItem` sont persistés avec des UUID stables, le discriminateur `polypbase.transfer` et la version exacte `2.0`. Le service interne construit des snapshots allowlistés côté backend; le parser strict reste indépendant du transport et n'effectue aucune recherche ni écriture en base. `GlobalStrainIdentity` est obligatoire sur chaque souche source. Package, items et audit obligatoire sont créés dans une transaction. Le périmètre reste un service Python interne.

Depuis cette livraison, sont aussi intégrés : resolver destination read-only (3A), diagnostic d'identité (3B), cardinalité canonique Organization + GlobalStrainIdentity (3C), allocateur X interne (4), projection de lignée portable source et snapshots protocole 2.1 opt-in (5). Le défaut reste 2.0. Les identités/lignées n'accordent aucun accès aux données opérationnelles étrangères; les observations du resolver ne réservent rien.

**Phase 6 non implémentée :** preflight/acceptation destination atomique et idempotente, matérialisation Strain/Box, fusion entrante et arête de continuation. Aucun endpoint/UI v2, receipt, inbox, adaptateur fichier ni mutation du stock/lifecycle source. Politique source active-only, effet quantité/statut et authenticité inter-instance restent à décider. V1 reste séparé. Contrat exact : [`../transferts_csv.md`](../transferts_csv.md); statuts Git et preuves QA datées : [`../../POLYPBASE_CURRENT_STATE.md`](../../POLYPBASE_CURRENT_STATE.md).

## Tests à cibler

- vraie valeur zéro contre cellule absente ou invalide;
- rollback complet d'un import en erreur;
- dry-run sans persistance;
- portée stricte de l'organisation, avec deux institutions;
- idempotence sur les clés prévues;
- export vide contre valeur `0`;
- combinaison des filtres et emplacement historique;
- `include_other_zones` activé et désactivé;
- permissions et `AuditLog`.

Points d'entrée : `backend/apps/cultures/management/commands/`, `backend/apps/exports/` et `frontend/src/components/ExportsView.tsx`.
