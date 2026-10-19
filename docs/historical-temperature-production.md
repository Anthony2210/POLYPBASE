# Import sécurisé des températures historiques 2020–2026

## Statut et limites

Procédure préparée localement, **pas une autorisation de production**. Aucun accès
VM, production, Neon ou base partagée pendant cette reprise. Aucun import,
commit, push ou déploiement réalisé. Une revue indépendante du diff final reste
nécessaire avant intégration, puis chaque phase opérateur exige son accord propre.
Ne jamais exécuter ce code non intégré sur la VM.

L'état PCA communiqué pour préparer ce contrat est : organisation 1, boîte
canonique 2135, boîte retirée 2141, 92 mesures déplacées, 18 doublons conservés,
reçu d'audit 2834. Le service le vérifie contre la base et le reçu, sans réexécuter
la fusion. Cet état n'a pas été relu en production pendant cette session.

**Ni les 40 291 associations de l'ancien plan QA, ni son snapshot, ni son empreinte
approuvée ne sont une approbation de ce workflow.** Aucun total de production
post-PCA n'est annoncé ici : il sera calculé par un nouveau plan sur l'état réel.

## Contrat implémenté

- Commande distincte `historical_temperature_production`, action obligatoire parmi
  `plan`, `backup`, `apply`, `postcheck`; `--production` explicite. Ni migration,
  démarrage applicatif ni déploiement ne déclenche l'import.
- Réutilisation de `build_report` / `extract_sources` / `plan` et `select_partial`.
  Les ambiguïtés et désaccords sont exclus, pas résolus par une nouvelle règle
  scientifique. Un plan partiel peut être prêt alors que `global_ready` est faux :
  les exclusions doivent être revues explicitement.
- Sept classeurs contrôlés par l'allowlist SHA-256 existante, inchangée dans le code
  applicatif. Les tests remplacent cette allowlist en mémoire seulement. Les
  octets originaux sont conservés dans le reçu en base, jamais dans Git.
- Écritures limitées aux températures et preuves des mesures canoniques de
  l'organisation 1, plus reçu et audit. Aucun changement de comptes, date, auteur,
  doublon, boîte, emplacement, mouvement ou lignée. Zéro reste une valeur réelle;
  les NULL biologiques ne sont pas remplacés.
- Acteur actif avec membership administrateur actif de l'organisation 1; droits
  revérifiés sous verrou avant écriture. Le rôle SQL ne doit être ni privilégié,
  ni propriétaire des tables/fonctions protégées, ni capable de créer dans la
  base ou le schéma `public`. Un refus ne justifie aucune élévation de privilèges.
- Attestation indépendante `pca_target_v3` épinglée à
  `/etc/polypbase/pca-production-target.json`. Réutiliser les règles de confiance,
  d'exécution Linux non-root, sans capacités, avec `NoNewPrivs=1`, et de chemins
  protégés de [la procédure PCA](pca-production-execution.md). Un redémarrage
  PostgreSQL change l'identité attestée : refaire l'attestation et le plan après
  vérification indépendante, jamais modifier le plan approuvé.
- Plan read-only en snapshot repeatable-read; recomputation avant sauvegarde et
  application; comparaison de baseline sous verrou. Toute différence nécessite
  un nouveau plan et une nouvelle revue.
- Les tables boîtes, biologie et températures sont verrouillées en
  `SHARE ROW EXCLUSIVE`. Cela bloque temporairement **les écritures des autres
  organisations aussi**, sans les lire/importer/modifier. Prévoir une fenêtre
  d'indisponibilité des écritures. `lock_timeout=10s`, `statement_timeout=120s`;
  les autres délais session doivent satisfaire les contrôles existants.
- Toutes les insertions, preuves, reçu et audit sont atomiques; les contraintes
  différées sont exécutées avant commit. Les gardes append-only restent actifs.

## Séquence opérateur proposée — ne pas exécuter dans cette session

Les exemples suivants sont des gabarits, pas des chemins ou identifiants approuvés.
Utiliser le Python de l'installation intégrée, depuis `backend/`, sur le serveur
local attesté, avec `config.settings`, DEBUG désactivé, sans variables `PG*`, tunnel
ou changement de configuration. Préparer hors Git les sources et répertoires
privés protégés, les outils `pg_dump`/`pg_restore` compatibles et l'espace disque.
Ne pas imprimer les secrets ni le contenu privé des plans, reçus ou sauvegardes.
Les fichiers de sortie sont créés exclusivement, sans écrasement.

### 1. Plan post-PCA — accord de lecture seule distinct

```text
python manage.py historical_temperature_production plan --production --organization-id 1 --actor-id <ADMIN_ID> --target /etc/polypbase/pca-production-target.json --raw-directory <PRIVATE_SOURCE_DIR> --plan-file <NEW_PRIVATE_PLAN_JSON>
```

Contrôler `ready`, les blocages, le reçu PCA, les compteurs, les associations PCA,
les zéros, les labels normalisés, toutes les exclusions et preuves. Faire relire
le fichier complet et son `plan_hash` SHA-256 par un reviewer indépendant. Ne pas
utiliser un hash de rapport, de source ou de plan QA à la place de ce hash. Une
erreur PCA structurelle peut interrompre la génération avant création du fichier.
Un plan généré mais bloqué est écrit pour inspection et la commande échoue.

### 2. Sauvegarde — accord séparé d'écriture d'artefacts

```text
python manage.py historical_temperature_production backup --production --organization-id 1 --actor-id <ADMIN_ID> --target /etc/polypbase/pca-production-target.json --raw-directory <PRIVATE_SOURCE_DIR> --plan-file <REVIEWED_PLAN_JSON> --expected-plan-hash <APPROVED_SHA256> --backup-dir <PRIVATE_BACKUP_DIR> --backup-evidence <NEW_PRIVATE_BACKUP_JSON>
```

Le dump complet utilise un snapshot exporté correspondant à la baseline, gardé
ouvert pendant `pg_dump`. La preuve lie cible, plan, archive, taille et SHA-256.
L'application refuse une archive modifiée/manquante, une mauvaise cible ou un
mauvais plan, ainsi qu'une preuve future ou datant de plus d'une heure.

**Limite explicite :** `pg_restore --list` prouve la lisibilité de l'archive, pas
une restauration complète (`restoration_tested=False`). Avant l'autorisation
finale, décider de la répétition de restauration dans un environnement distinct,
autorisé et isolé. Ne jamais restaurer dans une base existante pour tester.
Le contrôle local synthétique ne prouve pas la restaurabilité d'un futur dump
privé de production. Ne pas falsifier les marqueurs du manifeste pour y inscrire
une validation extérieure.

### 3. Application — nouvelle autorisation explicite sur le hash exact

Confirmer fenêtre d'écritures, sauvegarde valide et revue indépendante, puis
obtenir immédiatement avant l'opération une autorisation spécifique. L'accord de
planification ou de sauvegarde n'autorise pas cette étape.

```text
python manage.py historical_temperature_production apply --production --organization-id 1 --actor-id <ADMIN_ID> --target /etc/polypbase/pca-production-target.json --raw-directory <PRIVATE_SOURCE_DIR> --plan-file <REVIEWED_PLAN_JSON> --expected-plan-hash <APPROVED_SHA256> --backup-evidence <VERIFIED_BACKUP_JSON> --review-ref <INDEPENDENT_REVIEW_REF> --authorization APPLY_HISTORICAL_TEMPERATURES_ORGANIZATION_1:<APPROVED_SHA256>
```

Ne pas réessayer aveuglément après une erreur ou une perte de connexion. Noter le
reçu éventuel et passer au contrôle read-only. Le texte d'autorisation est un
garde d'intention lié au plan, pas un remplacement des permissions backend.

### 4. Contrôle après commit — sans relire les classeurs

```text
python manage.py historical_temperature_production postcheck --production --organization-id 1 --actor-id <ADMIN_ID> --target /etc/polypbase/pca-production-target.json --plan-file <REVIEWED_PLAN_JSON> --expected-plan-hash <APPROVED_SHA256>
```

Vérifie reçu complet, archive source et hashes, températures/preuves exactes,
compteurs, biologie rattachée toutes colonnes, baseline planifiée hors insertions,
audit et auteur, état PCA et triggers. Conserver la sortie et le reçu en privé.
Une modification métier légitime après import peut rendre ce contrôle obsolète :
arrêter et analyser, ne pas effacer ou réécrire les données pour le faire passer.

### Réexécution et rollback

Une réexécution du même `apply` n'insère rien si le reçu, les sources, la baseline,
la biologie et l'audit correspondent encore. Elle exige toujours une sauvegarde
valide de moins d'une heure : après expiration, utiliser `postcheck`, pas une
preuve antidatée. Un nouveau plan après application ne réimporte pas les semaines
existantes; s'il ne reste rien d'accepté, il est bloqué par `NOTHING_TO_IMPORT`.

Avant commit, toute erreur rollbacke l'ensemble de l'import. Après commit, il
n'existe **aucune suppression inverse automatique**, incompatible avec les preuves
append-only. Une restauration complète est une opération exceptionnelle distincte,
avec nouvelle autorisation, sauvegarde de l'état courant, analyse des écritures
postérieures et procédure d'exploitation approuvée. Ne pas désactiver les triggers
ou retirer le reçu pour rejouer l'import.

## Validation locale de cette reprise

```text
uv run --no-sync python scripts/test_historical_temperature_production.py --run --pg-bin "C:/Program Files/PostgreSQL/18/bin"
```

Le lanceur nettoie les variables DB héritées et neutralise dotenv, crée un nouveau
cluster temporaire sur un port loopback alloué, puis arrête/supprime uniquement
ce cluster. Il ne reçoit aucun chemin de cluster existant. Rôle propriétaire
synthétique pour migrations/fixtures; garde de rôle et attestation de production
neutralisés dans les fixtures de workflow, testés séparément. Durabilité disque
désactivée uniquement sur ces données synthétiques. Ne pas l'utiliser pour des
classeurs ou données réels. Les sorties locales restent dans `.local-docs/` ignoré.

Résultats obtenus sous Windows avec PostgreSQL **18.6** :

- 50 tests de workflow en 475,691 s : **49 réussis, 1 ignoré** (charge volumétrique
  optionnelle). Inclut PCA post-fusion, associations/exclusions, cible, péremption,
  sources, permissions, sauvegarde corrompue, concurrence, rollback, reçu/audit et
  réexécution. Le vrai test de trigger contourne seulement la validation modèle
  pour soumettre une evidence invalide à PostgreSQL après une insertion valide.
- 201 tests complémentaires source/plan/partiel/PCA production/sauvegarde/artefacts
  sur SQLite en 27,482 s : **192 réussis, 9 ignorés** (anciens tests de workflow
  PCA exigeant leur propre harness PostgreSQL). Les contrôles d'artefacts sont
  testés avec mocks; les preuves Linux natives ne sont pas remplacées par cette
  exécution Windows.
- Après correction de portabilité des binaires de sauvegarde (suffixe `.exe`
  uniquement sous Windows), relance ciblée PostgreSQL : **9/9 réussis** en
  13,012 s, dont la séquence commande plan/sauvegarde/application/postcheck.
  Une erreur d'indentation transitoire introduite dans cette correction a été
  détectée, corrigée puis couverte par cette relance et le contrôle syntaxique.
- `manage.py check --settings=config.test_settings` : aucun problème.
- `makemigrations --check --dry-run --settings=config.test_settings` : aucune dérive.
- Des tentatives antérieures ont dépassé les limites de lancement/temps global;
  elles ne sont pas comptées comme validations réussies.

Non exécutés : charge représentative 40k+, QA Linux native de bout en bout,
restauration complète de sauvegarde, lecture ou plan réel de production. La durée
et l'empreinte mémoire de la transaction/du reçu à pleine échelle restent à mesurer
avant autorisation opérateur. Aucun changement frontend : pas de QA navigateur.
