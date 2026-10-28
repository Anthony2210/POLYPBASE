# CLA / SME — procédure opérateur PostgreSQL préparée, NON EXÉCUTÉE

## Statut, responsabilité et sources

**Ce document n'autorise aucune exécution sur des données réelles.** Aucun accès
à la VM, à Neon ou à une base partagée, aucune application opérationnelle,
sauvegarde de production, restauration opérationnelle ou release n'a été effectué.
Les seuls tests PostgreSQL ont utilisé des clusters locaux neufs, synthétiques et
jetables, arrêtés puis supprimés. Les commandes opérateur ci-dessous n'ont pas été
exécutées ; chaque phase réelle exige ses accords explicites.

**Statut : prêt pour revue, pas prêt pour exploitation.** Le préflight réel,
l'attestation root, la sauvegarde restaurée et l'administrateur réel restent à valider.

Sources de contrat inspectées : `AGENTS.md`, `docs/PROJECT_CONTEXT.md`,
`docs/context/development-deployment.md`, `docs/context/imports-exports.md`,
`docs/deploiement_vm.md`, `deploy/deploy_vm.ps1`, les modèles Django et
`backend/apps/cultures/historical_2026/identity_execution.py`,
`identity_references.py`, `identity_reconciliation.py`, ainsi que les commandes
`prepare_historical_identity_references` et `correct_historical_identities`.
Les preuves scientifiques et les limites de correspondance restent dans
[`cla-sme-identity-reconciliation.md`](cla-sme-identity-reconciliation.md).
Ses anciennes restrictions SQLite / cible détenue / coexistence SSP ne décrivent
pas la nouvelle frontière PostgreSQL : vérifier le code de la release effectivement
revue avant usage. Ne pas appliquer une procédure ancienne à une commande nouvelle.

**Prérequis de release :** revue indépendante, tests synthétiques isolés et résultats
consignés par le responsable d'intégration, commit puis release explicitement
approuvés. Utiliser exclusivement le workflow `deploy/deploy_vm.ps1`, avec ses
gardes et ses approbations de migration éventuelles. Ne pas reproduire ses étapes,
modifier le code sur la VM ou déployer le worktree en cours. Le déploiement ne vaut
ni préparation des références, ni autorisation de correction. Rien ici ne lance
ce workflow. Contrôler ensuite le commit réellement servi et la cohérence complète
du schéma ; une release incomplète ou une commande non importable est un arrêt.

## Diagnostic communiqué : faits à revérifier, pas résultats de l'agent

Les observations suivantes ont été **fournies par l'utilisateur**. Elles ne sont
pas issues de requêtes exécutées par l'agent et doivent être reprises après la
release, dans une phase de lecture explicitement autorisée.

| Organisation 1 | Observation communiquée | Conséquence à confirmer |
|---|---|---|
| CLA | 10 Box, source Strain **213**, `CLA-JKA-1`, `Cyanea lamarckii` ; absence de Species `Chrysaora lactea` et de Strain `CLC-JKA-1` | Préparation additive nécessaire si l'absence exacte est confirmée et aucun candidat ambigu n'existe |
| SME | 8 Box approuvées, source Strain **245**, `SME-JKA-1`, `Stomolophus meleagris` | Correction d'attribution vers l'espèce opérationnelle `Stomolophus sp.2` |
| Cible SME | Strain globale **247**, `SSP-JKA-1`, Species **145**, `Stomolophus sp.2` ; 5 Box propres à l'organisation témoignent déjà de son usage | Éligibilité par témoins existants, sans appropriation de la référence globale |
| SSP coexistantes | Parmi ces témoins, `SSP-JKA-1.008`, `.009`, `.010` ; historiques à partir de 2024, sans chevauchement signalé avec les anciennes SME | Observation temporelle ; aucune preuve de continuité physique |
| Attributions AAA | Absence signalée de `CLA`, `CLC`, `SME`, `SSP` pour l'organisation 1 | Créer seulement les attributions exactes absentes via préparation revue |

Un nombre de témoins peut évoluer après correction : les huit SME rattachées à 247
deviennent elles aussi des usages propres. Ne pas exiger aveuglément « 5 » dans les
postcontrôles ; comparer les PK des cinq témoins initiaux, qui restent intacts.

### Manque de référence, collision et coexistence

- Une référence absente n'est pas une collision. `MISSING_REFERENCE` ou
  `MISSING_OR_INCOMPATIBLE_REFERENCE` dans un plan de correction impose d'abord
  une préparation séparée, pas une écriture SQL manuelle ni une levée de garde.
- Une attribution AAA existante mais incompatible, une orthographe lactea
  approchante, plusieurs candidats, un code réservé ou une vraie collision de
  renommage impose l'arrêt et une revue séparée. Aucun rapprochement approximatif.
- La coexistence de SME et de SSP au même suffixe est **observationnelle** :
  `corresponding_ssp_present` n'est pas une autorisation de fusion. L'attribution
  biologique des huit SME est sûre dans le périmètre décidé sans prétendre que
  SME et SSP soient la même boîte. `physical_continuity = NOT_ASSERTED` doit rester
  explicite. Aucun renommage SME → SSP, transfert de relevés ou partage d'histoire.
- Deux Box `CLA-JKA-1.009` et `CLC-JKA-1.009` (idem `.010`) constituent en revanche
  une collision de renommage : aucune fusion ni adoption automatique.

## Mutations précisément admises

### Préparation `cla_sme_references_20261027_v1`

Simulation par défaut ; application séparée, additive, auditée, transactionnelle :

- **CLA** : créer Species **exactement** `Chrysaora lactea` seulement si absente,
  sans candidat approximatif ou ambigu ; créer les attributions institutionnelles
  `CLA` → `Cyanea lamarckii` et `CLC` → `Chrysaora lactea` si absentes ; créer, si
  réellement absente et non conflictuelle, une Strain **détenue par organisation 1**
  `CLC-JKA-1`, `number=1`, `origin_code="JKA"`, et sa `LocalStrainIdentity` liée à
  l'attribution CLC. Provenance biologique inconnue :
  `provenance_code_assignment_id = NULL`, `origin_id = NULL` ; aucune provenance
  JKA déduite du libellé, aucun `GlobalStrainIdentity` ni lignée globale inventée.
  Ne pas préallouer un PK : relever `prepared_target_strain_id` après application.
- **SME** : créer uniquement les attributions institutionnelles `SME` et `SSP`
  manquantes vers les deux espèces exactes ; utiliser **247 déjà éligible**.
  Aucune création de Strain ou Species SME, aucune `LocalStrainIdentity` pour 247,
  aucun changement de propriétaire, d'identité globale ou de provenance de 247.
- Aucune Box, mesure ou référence préexistante n'est modifiée. La source partagée
  **213 reste intacte pour tous ses consommateurs**, y compris les autres
  organisations : aucun changement de Species, code, ownership, identité locale,
  identité globale ou provenance de cette référence partagée.

### Correction `cla_sme_identity_20261027_v2`

Chaque famille a son propre plan, hash, accord et transaction :

- **CLA : 10 PK conservés**. Réattribuer `.001` à `.010` à la cible lactea revue.
  Conserver les codes CLA `.001`–`.008`. Renommer uniquement
  `CLA-JKA-1.009` → `CLC-JKA-1.009` et `.010` → `.010`, sur les mêmes PK.
- **SME : 8 PK conservés**, suffixes fermés `.001`, `.004`, `.005`, `.006`, `.007`,
  `.008`, `.009`, `.010`. Rattacher à 247 ; conserver tous les codes SME.
  Les SSP préexistantes restent entièrement intactes.
- Ne modifier ni `local_code`, `merged_into_id`, `previous_global_code`, statut,
  dates, QR/tags, valeurs scientifiques, auteurs, emplacements, mouvements,
  environnement, lignées, repiquages ou transferts. Ne pas réécrire les anciens
  audits ; ajouter seulement les audits et reçus de l'opération.
- Le helper de réservation protège **symétriquement les deux paires**
  CLA/CLC `.009` et `.010` ; ce n'est pas un alias général ou une fusion.
  Les namespaces CLA/CLC sont verrouillés dans un ordre déterministe, compteurs
  jamais abaissés. Le manifeste ancien et sa preuve D11 restent scellés ; son
  rejeu ne doit pas recréer l'ancienne identité. La protection HS reste intacte,
  y compris pour `CLC-JKA-1.009`. Ne pas lancer d'import ni de désactivation ici.

## Frontière de production et documents root épinglés

Deux chemins **propres à CLA/SME**, non paramétrables :

- `/etc/polypbase/cla-sme-production-target.json` : `cla_sme_target_v1` ;
- `/etc/polypbase/cla-sme-production-authorization.json` : `cla_sme_authorization_v1`.

Ne pas utiliser, écraser ou réinterpréter l'attestation PCA. Les clés de la cible
sont celles de `pca_production.TARGET_KEYS`, mais le schéma et le chemin diffèrent.
Les clés d'autorisation sont exactement `identity_execution.AUTHORIZATION_KEYS`.
Aucune clé `review_ref`, `authorization`, `source_strain_id`, `verification` ou autre
ne doit être ajoutée à ces JSON ; conserver les preuves humaines séparément.

Le runtime doit être le compte local non privilégié correspondant au rôle peer,
avec les settings **existants `config.settings`**, `DEBUG=False`, mot de passe vide,
une seule base `default`, sans routeurs et en autocommit avant l'appel. Aucune
nouvelle configuration à credentials, aucun TCP, hôte distant, tunnel, Neon,
settings QA ou variable `PG*` héritée (casse indifférente). Les seuls sockets admis
sont `/run/postgresql` et `/var/run/postgresql`. La connexion effective doit
correspondre à la cible, à PostgreSQL 18, `session_user=current_user`, schéma
`public`, rôle de réplication `origin`, hors recovery et sans privilèges SQL élevés.

Lancer Python après abandon des privilèges, jamais root : UID réel/effectif/
sauvé/filesystem non nul et égal, GID égaux, capacités inheritable/permitted/
effective/ambient nulles, `NoNewPrivs=1`, namespace UID initial. `setpriv
--no-new-privs` ne confère aucun droit. Auditer groupes, ACL et délégations :
le runtime ne doit ni posséder ni pouvoir écrire le répertoire socket, ni appartenir
au groupe PostgreSQL. Seul l'alias root `/var/run` → `/run` est admis.

### 1. Attester indépendamment la cible — accord infrastructure distinct

Un opérateur indépendant confirme l'inventaire machine, le compte peer et le
socket ; il ne prend pas les valeurs d'une fixture ni d'un résumé de l'agent.
Lecture OS proposée, non exécutée :

```sh
cat /etc/machine-id
namei -l /var/run/postgresql/.s.PGSQL.5433
stat -c '%F %u:%g %a %n' / /etc /etc/polypbase /var /run /var/run
readlink /var/run
stat -c '%F %u:%g %a %n' /run/postgresql /run/postgresql/.s.PGSQL.5433
id postgres
id polypbase
```

Compléter avec ACL et délégations, sans lire de `.env`. La requête Q1 du SQL fournit
les OID, version et instant postmaster exacts, via une session de lecture locale
indépendamment établie. **Les OID SQL ne sont pas des UID/GID OS.** Les coordonnées
locales documentées sont `polypbase`, rôle `polypbase`, socket
`/var/run/postgresql`, port `5433` ; les confirmer avant de les utiliser.

Gabarit JSON **non installable tel quel** : tous les `A_REMPLACER_*` sont des
placeholders, pas des valeurs attestées. Remplacer les placeholders numériques
par des nombres JSON positifs, sans guillemets ; ne jamais inventer OID ou instant.

```json
{
  "schema": "cla_sme_target_v1",
  "machine_id": "A_REMPLACER_MACHINE_ID_32_HEX",
  "configured_host": "/var/run/postgresql",
  "configured_port": "5433",
  "database": "polypbase",
  "role": "polypbase",
  "server_address": null,
  "server_port": null,
  "database_oid": "A_REMPLACER_ENTIER_Q1",
  "role_oid": "A_REMPLACER_ENTIER_Q1",
  "postmaster_started_at": "A_REMPLACER_Q1_YYYY-MM-DDTHH:MM:SS.ffffffZ",
  "server_version_num": "A_REMPLACER_ENTIER_Q1",
  "connection_mode": "unix",
  "socket_uid": "A_REMPLACER_ENTIER_STAT",
  "socket_gid": "A_REMPLACER_ENTIER_STAT"
}
```

Les documents root sont réguliers, à un seul lien, sans symlink, lisibles par le
runtime mais non modifiables/remplaçables par lui. Tous leurs ancêtres, jusqu'à
`/`, sont root et non inscriptibles groupe/autres. L'opérateur root prépare un
fichier relu dans `/root`, mode 0600, puis installe la cible mode 0644 sous
`/etc/polypbase` root 0755, **après vérification des chemins existants et refus d'une
destination existante ou symlink**. Ne pas changer les permissions d'un arbre
existant pour faire passer le garde. L'installation/rotation est une mutation
infrastructure demandant son propre accord. Après redémarrage PostgreSQL ou
changement d'identité, nouvelle attestation indépendante et nouveaux plans ; ne
jamais ajuster l'attestation seulement pour faire disparaître un refus.

Les sorties et archives vont dans des répertoires dédiés privés, par exemple
`/srv/polypbase/deployments/cla-sme` et `/srv/polypbase/backups/cla-sme`, à préparer
par l'opérateur sous accord infrastructure : ancêtres root/runtime non inscriptibles
groupe/autres, répertoire final **0700**, fichiers **0600**, réguliers à un seul lien,
chemins absolus canoniques, aucun symlink ni `/tmp`. Vérifier les ACL. Ne pas toucher
aux droits ou à la rétention des sauvegardes ordinaires. Ne pas committer les
sorties, dumps, attestations remplies ou données de production.

### 2. Autorisation root — nouveau document pour chaque application

Gabarit de **préparation CLA sans cible demandée**. Les placeholders restent
volontairement invalides ; renseigner séparément le vrai acteur et chaque hash.

```json
{
  "schema": "cla_sme_authorization_v1",
  "operation": "cla_sme_references_20261027_v1",
  "organization_id": 1,
  "family": "cla",
  "target_strain_id": null,
  "actor_id": "A_REMPLACER_ENTIER_ADMIN_ACTIF_ORG1",
  "plan_hash": "A_REMPLACER_SHA256_PLAN_PREPARATION_CLA_REVU",
  "target_hash": "A_REMPLACER_SHA256_JSON_CANONIQUE_CIBLE",
  "authorized_at": "A_REMPLACER_INSTANT_UTC_AVEC_OFFSET",
  "expires_at": "A_REMPLACER_INSTANT_UTC_AVEC_OFFSET",
  "backup": {
    "archive": {
      "path": "/srv/polypbase/backups/cla-sme/A_REMPLACER_ARCHIVE_PRIVEE.dump",
      "sha256": "A_REMPLACER_SHA256_ARCHIVE",
      "size_bytes": "A_REMPLACER_ENTIER_TAILLE_OCTETS"
    },
    "created_at": "A_REMPLACER_INSTANT_CREATION_SAUVEGARDE_UTC",
    "restoration_tested": true
  }
}
```

| Application autorisée séparément | `operation` | `family` | `target_strain_id` |
|---|---|---|---|
| Préparation CLA, sans option cible au dry-run | `cla_sme_references_20261027_v1` | `cla` | `null` |
| Préparation SME | `cla_sme_references_20261027_v1` | `sme` | `247` |
| Correction CLA après préparation | `cla_sme_identity_20261027_v2` | `cla` | nouveau PK réellement préparé et revu |
| Correction SME après préparation | `cla_sme_identity_20261027_v2` | `sme` | `247` |

`target_strain_id` de préparation lie **l'argument demandé** : ne pas remplacer le
`null` CLA par la cible créée après coup. `actor_id` est un entier PK d'administrateur
actif de l'organisation 1 ; il doit correspondre exactement au `--actor-id ADMIN_PK`
fourni pour la préparation comme pour la correction. Ne pas publier son nom ni ses
informations personnelles. Le mode `--actor` par nom est réservé aux tests SQLite
isolés et n'est pas admis pour une application PostgreSQL.

`target_hash` est le SHA-256 du JSON cible **canonique**, et non celui des octets du
fichier indenté : `json.dumps(value, sort_keys=True, separators=(",", ":"),
ensure_ascii=False, allow_nan=False).encode("utf-8")`, puis SHA-256 hex minuscule.
Le hash peut être comparé au binding `execution.target_hash` du résumé de correction.
Ne pas le déduire d'une attestation PCA ni d'un hash de plan synthétique.

Tous les instants doivent être UTC avec offset (`+00:00` ou `Z`). À chaque contrôle :
`authorized_at <= maintenant < expires_at <= authorized_at + 1 heure` et âge
`authorized_at` entre 0 et 1 heure. La sauvegarde doit elle aussi avoir un âge entre
0 et 1 heure, y compris après sa vérification et avant commit. Prévoir une marge,
ne pas choisir une expiration au voisinage de l'application. Aucune date de
création de sauvegarde ou d'autorisation future, même légèrement, n'est admise ;
aucune antidatation ni prolongation au-delà d'une heure.

`restoration_tested: true` est une **attestation root de restauration indépendante
réussie**, jamais un résultat déduit de `pg_restore --list`. Après accord sauvegarde,
produire une sauvegarde fraîche de la cible attestée ; conserver provenance, date
UTC, SHA-256 et taille. Attention : `deploy/scripts/backup_database.py` appelle
actuellement `create_archive`, qui utilise `/usr/bin/pg_dump` et le wrapper de
`pg_restore`, sans épinglage de version 18. Ne pas présenter ce script seul comme
preuve de création/vérification avec les clients 18. Pour ce checkpoint ponctuel
hors workflow de release, après accord sauvegarde distinct et validation du peer
local, utiliser explicitement `/usr/lib/postgresql/18/bin/pg_dump` et `pg_restore`.
Ce checkpoint ne remplace aucune sauvegarde orchestrée par `deploy/deploy_vm.ps1`.

Gabarit futur, non exécuté, avec chemin absolu privé unique à remplacer pour chaque
famille et phase. Confirmer auparavant l'identité Q1, le socket, le répertoire 0700
et tous ses ancêtres. Le shell runtime crée le fichier sous umask 077 et refuse
l'écrasement (`set -C`) ; conserver un éventuel fichier incomplet à part, ne pas le
prendre pour une archive valide. Ne pas réutiliser ce nom pour un second checkpoint.

```sh
sudo -u polypbase /bin/sh -c 'set -euC; umask 077; exec /usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 /usr/bin/setpriv --no-new-privs -- /usr/lib/postgresql/18/bin/pg_dump --format=custom --no-owner --no-acl --no-password --host=/var/run/postgresql --port=5433 --username=polypbase --dbname=polypbase > /srv/polypbase/backups/cla-sme/UNIQUE_PHASE_CHECKPOINT.dump'
```

Vérifier la version des deux clients 18, puis, dans le runtime non privilégié,
contrôler `pg_restore --list` avec le binaire versionné (sortie vers un fichier privé,
pas un dump partagé), SHA-256, taille et lisibilité. Conserver la date UTC réelle de
création et la liaison à la cible attestée dans les preuves privées ; aucun credential,
URL de connexion ou fichier de mot de passe n'est nécessaire, le dump utilise peer.

Faire restaurer cette archive uniquement dans un **nouveau cluster jetable isolé,
hors production, indépendamment et explicitement autorisé**, puis vérifier la
restauration. Ne jamais restaurer dans une base partagée, un cluster existant,
la production ou Neon. Aucune commande de restauration vers une telle cible n'est
prescrite ici ; l'opérateur de restauration doit établir et prouver sa cible jetable
avant toute écriture. Aucun script
CLA/SME ne fait cette restauration pour l'opérateur. Si le délai d'une heure ne peut
être tenu, arrêter et refaire un checkpoint complet ; ne pas antidater les champs.

L'application revérifie le fichier protégé, sa taille et son SHA-256, puis sa
lisibilité avec le client root contrôlé **`/usr/lib/postgresql/18/bin/pg_restore`**
(`client_major=18`). Le wrapper Debian par défaut peut sélectionner 17 : ne pas
l'utiliser comme preuve 18. Une archive lisible seule n'est pas une sauvegarde
restaurée. L'archive demeure sous `protected_file`, ancêtres conservés et vérifiés.

Après revue du plan et accord humain immédiat sur cette seule application,
l'opérateur indépendant installe le JSON d'autorisation root, mode 0644, mêmes
règles d'ancêtres que la cible. Le chemin unique n'accueille qu'une autorisation à
la fois : archiver/révoquer la précédente sous contrôle root avant remplacement
séparément approuvé ; jamais pendant une transaction. Ne pas fabriquer le document
avec le compte runtime. Le code le relit avant/après vérification et avant commit ;
une modification, expiration ou divergence doit faire échouer l'application.

## Séquence opérateur future — une famille à la fois

### A. Lecture initiale autorisée et fenêtre de maintenance

1. Confirmer release revue, commit et schéma, attestation indépendante et chemins
   privés. Autoriser séparément la lecture opérationnelle ; aucune connexion par
   l'agent. Exécuter le SQL ci-joint et conserver les sorties privées avant changement.
2. Planifier une fenêtre sans écrivain applicatif, tâches de fond, import ni
   intervention concurrente. Les applications prennent **toutes les tables gérées
   et `django_migrations` en `EXCLUSIVE MODE NOWAIT`**, puis leurs verrous de lignes.
   Les SELECT ordinaires, y compris déjà actifs, restent possibles ; les écrivains
   et SELECT FOR UPDATE ne sont pas compatibles avec ce verrou. Un écrivain actif
   détenant un verrou incompatible provoque un **échec immédiat de la famille
   entière**, sans correction partielle et **sans retry automatique**. Un conflit
   de verrou signifie arrêt et diagnostic, pas boucle d'essais ni suppression de garde.
3. Maintenir l'absence d'écrivains entre photographie, revue et application. Les
   plans incluent dépendances, témoins, références, audits et état de schéma ;
   toute dérive impose une nouvelle simulation et une nouvelle autorisation.

Exemple de lecture SQL, seulement après confirmation humaine des coordonnées,
depuis le serveur, avec le compte peer non privilégié. `-X` ignore `.psqlrc`, `-w`
interdit une demande de mot de passe. L'environnement vide évite les redirections
libpq héritées. Ne pas exporter de credentials ni afficher de configuration.

```sh
sudo -u polypbase /usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 /usr/bin/setpriv --no-new-privs -- /usr/lib/postgresql/18/bin/psql -X -w -v ON_ERROR_STOP=1 -h /var/run/postgresql -p 5433 -U polypbase -d polypbase -f /srv/polypbase/app/deploy/sql/cla_sme_readonly_diagnostics.sql
```

Le fichier contient seulement `BEGIN READ ONLY;` puis des SELECT ; pas de table
temporaire, fonction d'écriture, DDL ou DML. La déconnexion clôt la transaction par
rollback ; ne pas le sourcer dans une session portant une transaction existante.
Il ne remplace pas les gardes applicatifs ni l'attestation indépendante. Sous
READ COMMITTED, ses SELECT ne forment pas à eux seuls une photographie unique :
la fenêtre sans écrivains et les plans REPEATABLE READ sont indispensables.

### B. Simuler la préparation — aucune écriture métier

Commandes futures depuis `/srv/polypbase/app/backend`, coordonnées déjà confirmées.
Elles utilisent les settings déployés existants, sans créer de settings dédiés ni
lire/afficher leur `.env`. L'environnement nettoyé n'empêche pas `config.settings`
de charger sa configuration existante. Un refus signifie arrêt, jamais remplacement
par un réglage QA ou injection de credentials.

```sh
sudo -u polypbase /usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 /usr/bin/setpriv --no-new-privs -- ../.venv/bin/python manage.py prepare_historical_identity_references --settings=config.settings --organization-id 1 --family cla --source-strain-id 213
```

```sh
sudo -u polypbase /usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 /usr/bin/setpriv --no-new-privs -- ../.venv/bin/python manage.py prepare_historical_identity_references --settings=config.settings --organization-id 1 --family sme --source-strain-id 245 --target-strain-id 247
```

Ne pas enchaîner automatiquement les deux familles. Conserver chaque résumé,
`plan_hash`, classification, `references[].create`, conflits et nombre de témoins.
`READY` autorise seulement à demander une revue ; `ALREADY_PRESENT` n'autorise pas
une correction implicite. `CONFLICT` ou manque requis non résolu bloque. La simulation
n'écrit ni référence, audit ou reçu. Aucun acteur n'est déduit du compte SSH.

### C. Appliquer une préparation — accord spécifique, sauvegarde et JSON root

Pour **une seule famille**, faire relire le plan B, vérifier la sauvegarde fraîche
restaurée, obtenir l'accord explicite d'application, puis installer l'autorisation
root dédiée. Les mots en MAJUSCULES ci-dessous sont des placeholders à remplacer,
jamais des arguments connus. Ne pas copier un hash d'une autre famille ou phase.

```sh
sudo -u polypbase /usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 /usr/bin/setpriv --no-new-privs -- ../.venv/bin/python manage.py prepare_historical_identity_references --settings=config.settings --organization-id 1 --family cla --source-strain-id 213 --actor-id ADMIN_PK --expected-plan-hash PREPARATION_CLA_SHA256 --apply
```

Ou, sous une **autre** autorisation root et après revue de son propre plan :

```sh
sudo -u polypbase /usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 /usr/bin/setpriv --no-new-privs -- ../.venv/bin/python manage.py prepare_historical_identity_references --settings=config.settings --organization-id 1 --family sme --source-strain-id 245 --target-strain-id 247 --actor-id ADMIN_PK --expected-plan-hash PREPARATION_SME_SHA256 --apply
```

Contrôler le reçu `identity_reference_preparation`, les audits de création et
`prepared_target_strain_id`. Refaire les SELECT de références : les seules
créations attendues sont celles décrites au plan. Vérifier l'absence de modification
de 213, 245, 247 et des Box. Si une préparation est `ALREADY_PRESENT`, le service
peut ne rien écrire ni produire de nouveau reçu ; ne pas inventer un reçu.

### D. Nouveau plan de correction APRÈS préparation

Ne jamais faire « préparation + correction avec le même hash ». La préparation a
changé l'état ; tout ancien plan de correction est périmé. Utiliser le vrai PK CLA
préparé, pas un PK supposé ni `213`. Chaque plan de correction est nouveau :

```sh
sudo -u polypbase /usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 /usr/bin/setpriv --no-new-privs -- ../.venv/bin/python manage.py correct_historical_identities --settings=config.settings --organization-id 1 --family cla --target-strain-id CLC_TARGET_PK
```

```sh
sudo -u polypbase /usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 /usr/bin/setpriv --no-new-privs -- ../.venv/bin/python manage.py correct_historical_identities --settings=config.settings --organization-id 1 --family sme --target-strain-id 247
```

Relire les 10 / 8 entrées, PK, codes avant/après, espèces, binding de cible,
`schema_hash`, `preserved_state_hash`, `corresponding_ssp_present` et
`physical_continuity`. Toute entrée `BLOCKED` bloque la famille entière.
Un état déjà corrigé doit être prouvé par le reçu exact de cette opération,
pas seulement par un code ou une souche correspondante. Aucun PK manquant ne doit
être créé ou adopté. Les correspondances physiques SME → SSP restent non affirmées.

### E. Autoriser séparément et appliquer la correction

Présenter le **nouveau** plan D et son hash à la revue humaine. Obtenir une seconde
autorisation explicite immédiatement avant correction ; la préparation n'en tient
pas lieu. Prévoir une sauvegarde fraîche restaurée **post-préparation**, puis un
nouveau JSON root avec `operation=cla_sme_identity_20261027_v2`, famille, cible,
acteur et hash exacts. Révoquer l'autorisation de préparation. Ne pas réutiliser
celle-ci ou l'empreinte d'une application précédente.

```sh
sudo -u polypbase /usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 /usr/bin/setpriv --no-new-privs -- ../.venv/bin/python manage.py correct_historical_identities --settings=config.settings --organization-id 1 --family cla --target-strain-id CLC_TARGET_PK --actor-id ADMIN_PK --expected-plan-hash CORRECTION_CLA_SHA256 --apply
```

Ou, dans une phase SME distincte, avec son propre plan et son propre accord :

```sh
sudo -u polypbase /usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 /usr/bin/setpriv --no-new-privs -- ../.venv/bin/python manage.py correct_historical_identities --settings=config.settings --organization-id 1 --family sme --target-strain-id 247 --actor-id ADMIN_PK --expected-plan-hash CORRECTION_SME_SHA256 --apply
```

`ADMIN_PK` est le PK positif réel renseigné localement par l'opérateur et identique
à `actor_id` du JSON root. Préparation et correction utilisent toutes deux
**`--actor-id`** : la commande transmet le PK sans recherche métier préalable à
l'entrée dans la frontière d'exécution. Django recharge ensuite l'acteur et son
membership actif admin sous verrou. La compatibilité `--actor` par nom reste
strictement réservée aux tests SQLite isolés ; ne jamais l'utiliser ici.

L'application réévalue le plan sous verrous avant toute écriture. Les modifications,
audits individuels et reçu de lot sont atomiques ; une erreur doit provoquer le
rollback de la famille entière. Les gardes de connexion, de cible et d'autorisation
sont répétés avant commit. Les délais applicatifs incluent lock 10 s, statement
30 s, idle transaction 60 s et transaction 900 s ; ils ne prolongent pas la durée
de validité de l'autorisation ou de la sauvegarde.

### F. Postcontrôles en lecture seule, puis clôture

Reprendre le SQL dans une nouvelle connexion après commit et comparer aux sorties
avant application **par PK**, pas seulement par libellé. Relancer une simulation
D : `ALREADY_CORRECT` exige le reçu exact. Cette simulation ne doit ajouter aucun
audit ; son **nouveau hash** n'est pas celui utilisé avant application.

| Requête | Lecture / critère de comparaison |
|---|---|
| Q1 | Identité indépendante : OID, version 18, instant postmaster inchangé, adresses SQL NULL pour Unix, rôle attendu, `public`, `origin`, pas recovery, lecture seule |
| Q2 | Métadonnées de schéma et migrations ; les maxima lexicographiques ne prouvent pas la complétude. Le garde applicatif exige exactement migrations/tables/colonnes attendues |
| Q3 | Espèces exactes, éligibilité et témoins propres ; AAA cohérentes. Les digests de 213, 245, 247 restent identiques. La cible CLA créée est détenue par 1, numéro 1, libellé JKA, provenance inconnue et identité globale NULL ; 247 reste globale sans identité locale ajoutée |
| Q4 | Collision/référence cachée : booléens seulement, jamais contenu étranger. Un `true` exige revue, pas adoption. L'absence de ligne dans Q3 n'est pas à elle seule preuve d'absence globale |
| Q5 | Même ensemble de PK ; 10 CLA puis 8 SME ; seuls souche et deux codes CLA changent. `preserved_box_digest`, `local_code`, fusion/réservation restent identiques. Conserver aussi les cinq PK témoins initiaux de 247 |
| Q6 | Par PK : nombre, dates min/max, canonique/duplicata, zéros, NULL et hash de **toutes** les mesures identiques. `count=0` signifie aucun relevé ; `0` scientifique reste distinct de NULL |
| Q7 | Chevauchement des semaines stockées pour les mêmes suffixes SME/SSP. Un `false`, même avec SSP à partir de 2024, ne démontre ni continuité ni succession physique |
| Q8 | Nombres et empreintes des emplacements, mouvements, tags/QR, lignées, événements parents et allocations enfants propres inchangés. Un lien étranger signale arrêt. Ces SELECT ciblés ne remplacent pas la fermeture complète des dépendances réalisée par le plan applicatif |
| Q9 | Reçus par opération/famille avec hash exact et nombre d'entrées (10 CLA / 8 SME pour la correction). Seules les clés de métadonnées listées sont exposées, pas `reviewed_plan`, snapshots, utilisateurs ou notes |

Les `md5(string_agg(to_jsonb(row)::text, ',' ORDER BY row.id))` sont des empreintes
de comparaison **locales**, non des signatures d'autorisation. Elles couvrent
également les champs d'auteurs et notes sans les afficher ; aucun détail personnel
n'est retourné. `NULL` d'agrégat vide n'est pas une donnée scientifique nulle. Le
hash SHA-256 du plan et le contrôle intégral des dépendances restent les gardes.
Pour les SSP témoins, comparer aussi les codes et souche eux-mêmes : le digest
Box exclut volontairement ces deux champs pour les corrections autorisées.

Les anciens audits doivent être préservés intégralement. Les reçus applicatifs
contiennent le plan revu et les snapshots ; leur inspection complète reste dans
l'environnement privé autorisé. Pour une comparaison avant/après d'audits historiques,
faire relire puis exécuter séparément ce gabarit SELECT avec la **liste exacte des PK
d'audits de l'organisation 1 figée avant opération** (pas un seuil d'ID supposé) :

```sql
-- Gabarit non exécutable avant remplacement de AUDIT_PK_LIST par des entiers revus.
SELECT count(*) AS historical_audit_count,
       md5(string_agg(to_jsonb(a)::text, ',' ORDER BY a.id)) AS historical_audit_digest
FROM public.audit_auditlog a
WHERE a.organization_id = 1 AND a.id = ANY (ARRAY[AUDIT_PK_LIST]);
```

Ne pas afficher `metadata`, `original_values`, descriptions, notes, emails ou noms
d'utilisateurs. Ne pas réécrire les audits pour « corriger » leur ancien libellé.
Enregistrer dans le dossier privé de clôture : release, accords, familles,
références de sauvegarde et restauration, hashes des plans appliqués, reçus,
comparaisons avant/après, limites et contrôles non exécutés. Révoquer les
autorisations root sous accord de clôture. Ne reprendre les écrivains qu'après
validation humaine des résultats et procédure de maintenance convenue.

## Arrêts, reprise et validation encore attendue

Échec de sauvegarde/restauration, contrôle de droits, attestation, expiration,
schéma, cible, permission, code, hash ou verrou : **arrêt immédiat**. Ne pas essayer
une autre configuration, désactiver un garde ou modifier les données pour satisfaire
le plan. Refaire un diagnostic en lecture seule puis une revue ; un nouveau plan
nécessite une nouvelle autorisation. Une famille déjà validée n'autorise pas l'autre.

Aucune restauration automatique, fusion inverse ou rollback de migration. Après
incident, préserver les preuves privées et présenter les options ; toute restauration
ou correction supplémentaire exige une décision et une autorisation séparées.

### Résultats synthétiques communiqués par le responsable d'intégration

Résultats réellement obtenus lors de la validation locale finale :

| Validation | Résultat communiqué | Portée |
|---|---|---|
| SQL `deploy/sql/cla_sme_readonly_diagnostics.sql` inchangé | Exécution réussie sur PostgreSQL **18.6 jetable**, lecture seule, **zéro mutation** | Validation du fichier SQL sur cible synthétique, pas diagnostic de production |
| Suite PostgreSQL (17) + réservations (22) | **39 tests réussis, 0 ignoré**, exécution finale **139,735 s** | PostgreSQL 18.6 natif neuf, arrêté et supprimé ; concurrence, rollback, schéma et SQL de diagnostic |
| Frontière d'exécution SQLite | **28 tests réussis** | Tests isolés de frontière, pas preuve de préflight réel |
| Suite SQLite finale principale | **336 tests réussis, 0 ignoré** | Réconciliation, préparation, frontière, réservations, import 2026, D11/HS, diagnostic et CAC/OCE |
| Régressions SQLite complémentaires | **269 tests : OK, 48 ignorés** | CCA-EOB, repiquage, création automatique/résolution, lifecycle, import CSV ; les ignorés ne constituent pas des validations |
| Django check / dérive des migrations / `git diff --check` | **OK / aucune dérive / OK** | Configuration isolée `config.test_settings`, aucun secret réel |

Commande de reproduction de la sélection PostgreSQL, depuis la racine du dépôt
sur une machine QA autorisée, avec Python de l'environnement projet et binaires
natifs PostgreSQL 18. `PG18_BIN_DIR` est un placeholder de **chemin absolu du
répertoire des binaires installés et vérifiés**, pas une variable héritée ni une
adresse de serveur. Remplacer aussi le chemin Python si l'environnement QA n'est
pas POSIX. Cette commande est documentée, **non relancée ici** ; elle ne prétend
pas reproduire les durées exactes d'une autre machine.

```sh
.venv/bin/python scripts/test_historical_temperature_production.py --run --pg-bin PG18_BIN_DIR apps.cultures.test_identity_reconciliation_postgres apps.cultures.test_identity_reconciliation_reservations
```

Le lanceur versionné exige PostgreSQL 18, crée un répertoire natif neuf par
`initdb`, alloue un port loopback, démarre son propre serveur, puis arrête et retire
uniquement ce cluster. **Il ne réutilise aucun cluster existant.** Il efface les
variables héritées `PG*`, `POSTGRES_*`, `POLYPBASE_*`, `DJANGO_*` (sans distinction
de casse), désactive dotenv et injecte ses seuls settings synthétiques temporaires.
Les paramètres trust et durabilité réduite de ce lanceur sont réservés à cette QA
jetable et ne sont jamais des paramètres de production. Ne pas lancer cette QA sur
la VM de production, sur une base partagée ou sur Neon. La garde jetable vérifie
l'identité offline du cluster et l'origine des settings ; une variable d'environnement
seule ne permet pas d'adopter un serveur existant.

Reproduction ciblée des tests de frontière SQLite, depuis la racine, sans `.env`,
PostgreSQL ou connexion externe ; non exécutée ici :

```sh
/usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 PYTHON_DOTENV_DISABLED=1 POSTGRES_DB= POLYPBASE_TEST_POSTGRES=0 DJANGO_DEBUG=1 .venv/bin/python backend/manage.py test apps.cultures.test_identity_execution --settings=config.test_settings --noinput -v 2
```

### Derniers blocages avant exploitation

1. Terminer la revue indépendante du diff final. Le contrôle de schéma empreinte
   aussi les définitions PostgreSQL des contraintes, leurs états de validation et
   d'application, les index/prédicats et les triggers. Un test réel vérifie qu'un
   CHECK affaibli sous le même nom, invisible à l'introspection Django seule,
   invalide le plan et fait refuser son application.
2. Intégrer et committer la release revue, puis autoriser séparément son déploiement
   via le workflow gardé. Aucun résultat synthétique n'autorise ce déploiement.
3. Effectuer le **préflight réel après release**, sous accord humain de lecture :
   schéma final, contraintes, identités et collisions, source 213 inchangée pour
   tous ses consommateurs, cible globale 247 réutilisable grâce aux cinq témoins
   propres initiaux, sans identité locale ou appropriation ajoutée.
4. Établir l'attestation root indépendante, les droits réels du runtime/socket,
   l'administrateur actif réel et la fenêtre sans écrivains ; aucun de ces éléments
   n'est attesté par ces tests.
5. Produire et restaurer indépendamment les sauvegardes fraîches hors production,
   puis obtenir les accords et JSON root distincts pour chaque famille et phase.
   Validité maximale d'une heure, pas de dates futures, nouveaux plans et nouveaux
   hashes après chaque changement.

**Prêt pour revue uniquement, pas operations-ready ni feu vert de production.**
Les preuves synthétiques de concurrence ne remplacent pas le préflight, la restauration réelle
du checkpoint opérationnel ou les autorisations. Aucune réussite opérationnelle
sur production n'est revendiquée ici.
