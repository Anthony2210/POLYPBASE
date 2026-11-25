# Imports et exports

## Import scientifique : fondations et acceptation exact-date

Au jalon historique du 2026-10-14, la préparation canonique/CSV (`measurements/canonical.py`, `csv_import.py`, contrat 1.0) et le planner Python en lecture seule (`import_planner.py`, plan 1.1) étaient intégrés sans applicateur. Le planner courant reste read-only, en version **1.3**; un applicateur séparé ajoute l'acceptation restreinte exact-date décrite ci-dessous, pas les fonctions génériques de correction de masse ou d'onboarding. L'acteur Admin/technicien actif et l'institution sélectionnée sont validés; les boîtes sont résolues par code exact dans cette institution.

- Statuts : `IGNORED`, `INVALID`, `UNKNOWN_BOX`, `CREATE`, `ALREADY_MATCHES`, `CONFLICT`, `BLOCKED`. Une ligne sans valeurs scientifiques est ignorée et comptée; dès qu'un payload existe, les deux comptes requis doivent être valides. Les champs facultatifs conservent absent/vide/NULL/zéro selon leur contrat.
- Tout doublon source boîte/semaine bloque, même identique. `ALREADY_MATCHES` ne réécrit ni valeur, ni date, ni précision, ni provenance. Un plan/hash n'est ni une réservation, ni une permission d'appliquer, ni un snapshot transactionnel.
- Un repiquage pertinent dans la semaine d'un relevé semaine seule produit `week_subculture_chronology_conflict`, avec evidence dédupliquée boîte/semaine. Un parent partiel avec occurrence connue reste pertinent; une allocation enfant zéro est connue. Les occurrences/allocations legacy inconnues ne sont pas certifiées sans risque.
- Décision produit pour le futur applicateur : tout ou rien, aucune erreur non résolue; renseigner la vraie date de relevé dans la semaine en cas de conflit, ou annuler. Pas de force-import ni de maintien forcé de la précision hebdomadaire. Toute résolution régénère préparation et plan.

La cible approuvée sépare applicateurs de saisie, correction de masse (ancienne valeur attendue + audit) et onboarding, après préparation/revue commune. La saisie/import laboratoire reste hors Administration; correction de masse/onboarding restent administratifs. L'applicateur exact-date ajoute source originale privée en base, plan durable, idempotence/reçus et revalidation sous verrous. Les sources acceptées, y compris les lots sans création, sont conservées sans expiration ni suppression automatique. Inspection et aperçu ne conservent aucune source.

L'API d'application et les composants `MeasurementImportEntry` / `MeasurementImportPanel` implémentent confirmation explicite et application atomique des plans valides CREATE exact-date / ALREADY_MATCHES / IGNORED. Ces composants ne sont pas montés dans l'interface React active; leur présence ne prouve pas un parcours utilisateur accessible. Le serveur reparse la source et le mapping, vérifie les empreintes et les permissions, verrouille les boîtes puis revalide le plan **1.3** complet. Ses baselines incluent `duplicate_of_id` et `environment_source_box_id`, en plus des champs de précision et de provenance introduits par la version 1.2; l'occupation scientifique est filtrée sur les mesures canoniques, sans supprimer les preuves doublons. `application_available` reflète la readiness du plan, jamais une réservation. Des endpoints autorisés permettent de relire le reçu et télécharger la source privée. Les semaines seules CREATE et observateurs nommés restent bloqués. Périmètre, stockage, limites et preuves de validation : [`../measurement-csv-diagnostics.md`](../archive/sources-fusion/measurement-csv-diagnostics.md).

Contrats détaillés : [`../canonical-measurement-csv.md`](../archive/sources-fusion/canonical-measurement-csv.md), [`../measurement-import-planner.md`](../archive/sources-fusion/measurement-import-planner.md). Ne pas généraliser ces garanties aux importeurs historiques ci-dessous.

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

## Campagnes ponctuelles archivées

L’import du classeur 2026 (génération du manifeste, import, nettoyage ciblé et désactivations associées) et les correcteurs dédiés CLA/SME, Box 1902, PCA, espèces et températures historiques ne sont plus des procédures disponibles. Les commandes dédiées CCA-EOB avaient déjà été retirées. Les imports normalisés et l’applicateur exact-date décrits ci-dessus sont distincts de ces campagnes.

Pour retracer une opération, consulter les [dossiers archivés](../archive/README.md) ou la [référence avant retrait](https://github.com/Anthony2210/POLYPBASE/blob/9e00e2c6e0eb581334f9238ce041a7d3d2fafb19/docs/reference/imports-exports-transferts.md#import-historique-2026-manifeste-revu). Les procédures et validations datées ne prouvent ni l’exécution ni l’état actuel d’une base. Ne pas rejouer les anciens plans.

## Précision temporelle : limite actuelle de persistance

La fondation `0009_temporal_precision` conserve toutes les dates et clés ISO existantes. Les lundis conventionnels restent `legacy_unverified`, qu’ils proviennent de l’ancienne campagne 2026 ou d’un import CSV normalisé encore disponible, y compris pour les nouvelles créations via ce dernier. Aucune date n’est automatiquement certifiée ou convertie; les champs observateur et origine de la Phase 4A restent inchangés.

La préparation canonique sait représenter `iso_week` sans `measured_on`, mais le planner Phase 3 reste en lecture seule : une semaine libre peut être classée `CREATE` avec l'issue bloquante `week_precision_persistence_required` (`ready=False`). Le nouveau champ n'active pas la persistance semaine seule : `measured_on` est toujours NOT NULL et la contrainte de précision refuse `iso_week`. Cette fondation n'activait aucun nouveau contrat d'écriture; le plan courant 1.3 et ses baselines sont décrits en tête de ce document.

Une première tranche backend de lecture expose désormais `week_start` (date ISO du lundi, clé seulement) séparément de `measured_on`, prépare les tris latest par boîte et distingue l'existence d'un relevé de la disponibilité de son jour exact dans le filtre d'inventaire. Au jalon de cette première tranche, elle n'activait aucune écriture semaine seule et ne modifiait ni baseline/fingerprint 1.1, ni classification, ni readiness du planner; ce 1.1 est un repère historique, pas la version courante. La timeline mixte, les tris inter-boîtes, la fraîcheur, le frontend et les exports restent à adapter avant activation.

Les prérequis de schéma, résolution scientifique, contrats d'import/correction et consommateurs sont détaillés dans [`measurements-integrity.md`](mesures-temperatures-sondes.md#prérequis-exacts-pour-une-future-activation-semaine-seule). Une future clé ISO du lundi ne devra jamais être présentée comme un jour d'observation réel.

## Diagnostic avant contrainte

Avant d'appliquer une contrainte d'unicité sur une base contenant de l'historique, exécuter la commande de diagnostic en lecture seule prévue pour le domaine. Pour les relevés biologiques, `check_biological_measurement_duplicates` groupe par boîte/semaine ISO et rapporte organisation, boîte, clé/semaine ISO, nombre, identifiants et dates des relevés.

Si un doublon existe, ne pas appliquer la migration concernée et ne pas choisir automatiquement une mesure à conserver. La résolution de données scientifiques demande une décision explicite. Les principes généraux de diagnostic avant migration sont documentés dans [`development-deployment.md`](../archive/sources-fusion/context/development-deployment.md).

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

Le parcours CSV v1 est retiré; les routes de compatibilité répondent `410`. L'[ancienne spécification](../archive/sources-fusion/transferts_csv.md) décrit le format historique, pas un flux disponible.

### Transfer v2 — fondations intégrées jusqu'à la Phase 5

Phase 2B est intégrée à `main` au commit `20adf0a` (`feat: add transfer v2 protocol foundation`). `TransferEnvelope` / `TransferItem` sont persistés avec des UUID stables, le discriminateur `polypbase.transfer` et la version exacte `2.0`. Le service interne construit des snapshots allowlistés côté backend; le parser strict reste indépendant du transport et n'effectue aucune recherche ni écriture en base. `GlobalStrainIdentity` est obligatoire sur chaque souche source. Package, items et audit obligatoire sont créés dans une transaction. Le périmètre reste un service Python interne.

Depuis cette livraison, sont aussi intégrés : resolver destination read-only (3A), diagnostic d'identité (3B), cardinalité canonique Organization + GlobalStrainIdentity (3C), allocateur X interne (4), projection de lignée portable source et snapshots protocole 2.1 opt-in (5). Le défaut reste 2.0. Les identités/lignées n'accordent aucun accès aux données opérationnelles étrangères; les observations du resolver ne réservent rien.

La Phase 6 livre une identité globale et une proposition de don, sans acceptation destination ni mutation du stock source. Voir [propositions de don](#propositions-de-don-de-boîte-entière).

### Noyau scientifique d'échange (fondation interne, sans workflow)

`measurements/scientific_snapshot.py` définit le contrat pur `polypbase.scientific-snapshot` **1.0** (sans Django) : catalogue serveur de sélecteurs, sélection explicite validée, assemblage limité aux sélecteurs divulgués, validation stricte et JSON déterministe. États de champ : `value` (dont `0`), `null`, `blank`, `absent`; une information non divulguée est absente du payload, `observations: []` signifie aucune observation. Le temps porte la précision réelle (`exact_date`, `legacy_unverified`, `iso_week`); une semaine seule n'a jamais de jour ni de lundi émis. Une version ultérieure ajoute des clés sans changer le sens des existantes; toute version inconnue est refusée.

`cultures/scientific_projection.py` projette une Box de l'institution source explicitement fournie, en lecture seule : règle de lecture existante (membership actif ou superutilisateur technique), Box étrangère/fusionnée/inexistante indistinguables, relevés canoniques seulement, colonnes divulguées seulement, nombre de requêtes constant. La portée (institution, fusion, existence) est toujours décidée sur la seule ligne `cultures_box`; Strain/Species/GlobalStrainIdentity et le contrôle d'éligibilité de souche ne sont lus que si un sélecteur `identity.*` est divulgué. La validation renvoie des diagnostics, sans exception, pour des listes/objets JSON à la place de scalaires. Aucun compte, note, autre Box, lignée, température ni identifiant de ligne n'est lu ou émis; `GlobalStrainIdentity` absente reste `null`, jamais créée. Aucun snapshot transactionnel n'est garanti. L'autorité du [partage externe](organisations-permissions-partage.md#partage-externe-en-lecture-seule) est traitée séparément de ce noyau.

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

## Propositions de don de boîte entière

**Identité.** `GlobalBoxIdentity` représente la même boîte biologique à travers des représentations locales. `BoxOwnershipEpisode` associe une représentation distincte par épisode, avec au plus un propriétaire courant. Ne pas changer `Box.organization` pour simuler une transition. Un repiquage est une autre boîte, pas un nouvel épisode de propriété.

**Proposition.** Dans Administration > Envois et réceptions, un Admin source propose le don d'une boîte entière active à une autre institution éligible. Le préflight n'écrit rien; l'envoi exige confirmation de l'historique (`history_verified: true`, `history_sha256`) et un `request_id` pour le rejeu identique. Le serveur revalide identité, éligibilité et historique courant. Le détail reçu ne contient ni relevés, ni comptes source, ni empreinte d'historique, ni identifiant local de la boîte.

**Limite.** Aucune acceptation ni matérialisation de destination; ni propriété, stock, relevé ni mouvement transférés. La boîte source reste opérationnelle. Annulation côté source par API seulement, avec `expected_revision`. Voir les [routes](api.md) et le [contrat de partage distinct](organisations-permissions-partage.md#partage-externe-en-lecture-seule).
