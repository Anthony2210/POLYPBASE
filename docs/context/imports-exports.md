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

Le CSV de transfert de boîtes possède son propre contrat, ses validations de sécurité et sa traçabilité. Consulter [`../transferts_csv.md`](../transferts_csv.md) au lieu de recopier ce format ici. Un transfert n'accorde aucun accès durable aux données de l'institution source.

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
