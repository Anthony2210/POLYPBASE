# Imports et exports

## Import historique normalisé

La commande `import_bdd_csv` importe les CSV normalisés de `data/tables/`. Elle s'exécute dans une transaction et propose un `--dry-run` qui rollbacke les écritures. Ses opérations `get_or_create` et `update_or_create` rendent la reprise idempotente selon les clés actuelles.

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

## Diagnostic avant contrainte

Avant d'appliquer une contrainte d'unicité sur une base contenant de l'historique, exécuter la commande de diagnostic en lecture seule prévue pour le domaine. Pour les relevés biologiques, `check_biological_measurement_duplicates` rapporte organisation, boîte, date, nombre et identifiants.

Si un doublon existe, ne pas appliquer la migration concernée et ne pas choisir automatiquement une mesure à conserver. La résolution de données scientifiques demande une décision explicite. Les principes généraux de diagnostic avant migration sont documentés dans [`development-deployment.md`](development-deployment.md).

## Exports actuels

Les endpoints sous `/api/exports/` sont limités à l'organisation active et vérifient les permissions côté serveur. L'export téléchargeable actuel produit un CSV hebdomadaire : une ligne par semaine ISO avec, pour chaque boîte, les colonnes de polypes, éphyrules et température.

Contrats importants :

- les filtres espèce, souche, zone, boîte et période sont cumulables;
- une boîte sans relevé dans la période et le périmètre retenus est exclue du CSV et des graphiques;
- une cellule sans relevé reste vide, tandis qu'un zéro enregistré reste `0`;
- le filtre de zone s'appuie sur l'emplacement historique à la date du relevé;
- `include_other_zones` permet, pour les boîtes ayant contribué dans la zone sélectionnée, d'inclure aussi leurs relevés réalisés dans d'autres zones;
- la sélection, la génération et l'audit restent rattachés à l'organisation active.

Le modèle `DataExport` prévoit plusieurs formats. Une valeur de choix dans un modèle ne prouve pas qu'un format dispose d'un endpoint et d'un parcours UI; ne l'annoncer qu'après vérification des consommateurs actuels.

## Transferts inter-institutions

Transfer v1 dispose maintenant d'une frontière de service backend dédiée. Préparation + audit obligatoire et import atomique, ainsi que les cas de rejeu, sont couverts par des tests ciblés. Les conclusions de concurrence réelle exigent PostgreSQL isolé; SQLite seul ne valide pas les verrous. Consulter [`../transferts_csv.md`](../transferts_csv.md) pour les détails, sans recopier le contrat ici. Un transfert n'accorde aucun accès durable aux données de l'institution source.

### Transfer v2 — Phase 2B DONE and integrated

Phase 2B est intégrée à `main` au commit `20adf0a` (`feat: add transfer v2 protocol foundation`). `TransferEnvelope` / `TransferItem` sont persistés avec des UUID stables, le discriminateur `polypbase.transfer` et la version exacte `2.0`. Le service interne construit des snapshots allowlistés côté backend; le parser strict reste indépendant du transport et n'effectue aucune recherche ni écriture en base. `GlobalStrainIdentity` est obligatoire sur chaque souche source. Package, items et audit obligatoire sont créés dans une transaction. Le périmètre reste un service Python interne.

Validation fournie sur `main` après cherry-pick : suite backend complète **579 au total / 559 réussis / 20 ignorés / aucune erreur ni échec**; contrôle Django, contrôle de dérive des migrations et `git diff --check` réussis.

Le contrat exact est décrit dans [`../transferts_csv.md`](../transferts_csv.md). Il n'existe toujours aucun endpoint/UI v2, receipt/acceptation destinataire, résolution de Strain destination, représentation locale canonique, diagnostic de namespace, identité portable de Box, lignée, allocateur X, inbox de transfert direct, adaptateur fichier ou mutation du cycle de vie/stock source. V1 reste séparé et inchangé. La phase technique suivante est la Phase 3 : résolution destination de `GlobalStrainIdentity`, représentation locale canonique et diagnostic de namespace.

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
