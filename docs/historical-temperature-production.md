# Import sécurisé des températures historiques 2020–2026

## Statut et limites

Workflow intégré à `main` au commit `761cd02`, avec revue indépendante verte
communiquée pour cette intégration ; déploiement non réalisé. Les corrections
locales de fiabilisation décrites ici restent à relire et intégrer. Ce document
n'est **pas une autorisation de production**. Aucun accès VM, production, Neon ou
base partagée pendant cette reprise ; aucun import réel, commit, push ou déploiement.
Chaque phase opérateur exige son accord propre. Ne jamais exécuter les corrections
non intégrées sur la VM.

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
  octets originaux sont conservés dans le reçu en base, jamais dans Git. En
  production, extraction et archivage gardent ouverts les descripteurs des sept
  fichiers et de leurs ancêtres via `pca_protected_artifacts` : répertoire final
  `0700`, chemins absolus canoniques, propriétaires root/runtime, aucun lien
  symbolique ou fichier à plusieurs liens, aucun ancêtre inscriptible par groupe
  ou autres. Les remplacements observés pendant la lecture sont refusés, en plus
  du contrôle SHA-256. Le bypass reste celui du harness QA jetable existant.
- Écritures limitées aux températures et preuves des mesures canoniques de
  l'organisation 1, plus reçu et audit. Aucun changement de comptes, date, auteur,
  doublon, boîte, emplacement, mouvement ou lignée. Zéro reste une valeur réelle;
  les NULL biologiques ne sont pas remplacés.
- Acteur actif avec membership administrateur actif de l'organisation 1; droits
  revérifiés sous verrou avant écriture. Le rôle SQL ne doit être ni privilégié,
  ni propriétaire des tables/fonctions protégées, ni capable de créer dans la
  base ou le schéma `public`. Un refus ne justifie aucune élévation de privilèges.
- Attestation historique indépendante `historical_temperature_target_v1` épinglée
  à `/etc/polypbase/historical-temperature-production-target.json`, root-owned.
  Elle lie le rôle d'import et le rôle de sauvegarde à la même base, au même
  endpoint et au même démarrage PostgreSQL. **Le contrat PCA `pca_target_v3` et
  `/etc/polypbase/pca-production-target.json` restent inchangés.** Réutilisation
  des gardes Linux non-root, sans capacités, `NoNewPrivs=1`, procfs initial et
  chemins protégés. Toute rotation impose un nouveau plan, jamais l'édition
  d'un plan approuvé.
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

## Architecture native et diagnostic fourni par l'opérateur

Diagnostic communiqué, non relu par l'agent : Debian, PostgreSQL 18, socket
`/var/run/postgresql`, port **5433**, base `polypbase`, 61 tables et 59 séquences
`public`, aucune RLS active. Les quatre tables d'insertion ont des IDs IDENTITY.
Le rôle applicatif `polypbase` possède les tables scientifiques : il est refusé
pour l'import, mais **conservé sans changement pour application, migrations et
sauvegarde complète**. Le déploiement normal reste `deploy/deploy_vm.ps1`.

Le compte OS reste `polypbase`. Seul le processus de commande Django utilise le
nouveau login SQL `polypbase_hist_import`, sans membership propriétaire, CREATE,
attribut privilégié ou privilège global. Il exporte le snapshot approuvé dans une
transaction `REPEATABLE READ READ ONLY` maintenue ouverte. Une connexion psycopg
séparée sous le login attesté `polypbase` vérifie l'identité, importe ce snapshot
avant toute lecture dans sa propre transaction read-only, puis fournit les
paramètres de ce même rôle à `pg_dump --snapshot`. Identités/attestation/socket
sont revérifiés avant publication de la preuve. Aucun `SET ROLE`, nouvel alias
Django, changement de settings partagé ou nouvelle voie d'import.

Le plan et la preuve incluent l'attestation complète et son identité de sauvegarde.
Les anciens plans liés à la cible PCA sont refusés : refaire le plan. Les helpers
utilisent `/usr/bin/pg_dump` et `/usr/bin/pg_restore`. Le rôle import n'obtient
**aucune lecture générale de la base ou de ses séquences pour le dump**. Le rôle
backup peut posséder les tables, mais ne doit pas être superuser, createdb,
createrole, replication, bypassrls ou membre d'un rôle privilégié interdit.

Les connexions historiques utilisant un mot de passe sont refusées. La connexion
backup et le dump désactivent en plus le fichier de mots de passe (`/dev/null`).
Cela ne permet pas de distinguer peer de `trust` côté client : **l'inspection
indépendante de HBA/ident et de l'authentification effective reste obligatoire**.

## A. Préparation des rôles et droits — autorisation infrastructure séparée

Les blocs de préparation ci-dessous sont une procédure future, **jamais exécutée
sur la VM ou une infrastructure existante par l'agent**. Seuls les grants sont
exercés par les tests sur leur cluster neuf jetable. Faire approuver chaque mutation, vérifier la cible indépendamment
et travailler depuis une session administrative locale reconnue. Ne pas changer
le rôle, les propriétaires ou le `.env` existants. Arrêter si le rôle proposé
existe déjà : inspecter ses droits effectifs avant toute décision.

```sql
CREATE ROLE polypbase_hist_import NOLOGIN NOINHERIT NOSUPERUSER
    NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
```

Faire relire puis appliquer uniquement les grants nominatifs versionnés dans
`deploy/scripts/historical_temperature_import_grants.sql`. Ce fichier n'est
appelé ni par le déploiement ni par la commande d'import. Depuis la session OS
administrative autorisée, après intégration et déploiement du fichier revu :

```sh
sudo -u postgres /usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 /usr/bin/psql -X --no-password --host=/var/run/postgresql --port=5433 --dbname=polypbase --username=postgres --set=ON_ERROR_STOP=1 --file=/srv/polypbase/app/deploy/scripts/historical_temperature_import_grants.sql
```

Le contrat est : SELECT sur dix tables du workflow ; INSERT sur les colonnes
requises des trois tables historiques et de l'audit ; UPDATE sur une colonne de
six tables pour les verrous de lignes/triggers ; MAINTAIN sur les cinq tables
verrouillées ; EXECUTE de `historical_temperature_decimal(text)`. Aucun droit de
séquence n'est nécessaire pour les quatre IDs IDENTITY. Les grants incluent les
colonnes insérées par défaut par Django. Ne pas utiliser le générateur local-UI,
plus large et destiné à un autre environnement.

### Garde obligatoire contre les UPDATE directs

Les six grants UPDATE restent indispensables aux locking selects PostgreSQL 18,
y compris dans les triggers invoker. Ils sont désormais **neutralisés pour toute
commande UPDATE de l'importeur** par six triggers additionnels
`polypbase_hist_import_no_update`, BEFORE UPDATE FOR EACH STATEMENT, ENABLE ALWAYS.
Leur fonction `polypbase_hist_import_deny_update()` est SECURITY INVOKER,
appartient au rôle backup attesté `polypbase`, utilise uniquement
`search_path=pg_catalog, pg_temp` et refuse `session_user` **ou** `current_user`
égal à `polypbase_hist_import` avec SQLSTATE 42501. Pas d'exception pour no-op,
zéro ligne, CTE, UPSERT, MERGE, GUC ou profondeur de trigger. Les SELECT avec
verrou restent possibles ; aucune commande UPDATE acceptée n'est exécutée par
l'importeur, donc aucune correction métier non auditée n'est autorisée ici.

Les objets sont créés et activés **dans la même transaction de préparation que
les grants**, avant les UPDATE grants, rôle encore NOLOGIN. CREATE sans REPLACE
refuse une infrastructure déjà présente : ne pas lancer le script en aveugle
sur un rôle existant. Les propriétaires et gardes scientifiques existants sont
inchangés. Ces nouveaux objets sont hors du préfixe des gardes scientifiques et
hors migrations ; le dump complet doit les conserver. Leur installation et leur
retrait éventuel exigent une autorisation infrastructure distincte. Ne jamais
retirer le garde en conservant les droits UPDATE ou un login d'import actif.

Chaque vérification de cible historique, y compris **après les verrous de table
et avant les insertions**, contrôle en lecture seule la signature, le corps
littéral, le search_path, le propriétaire approuvé et les six attachements exacts
(sans condition, arguments ou colonnes), leur mode ALWAYS et l'absence de
partitionnement/héritage inattendu. Sont refusés : garde absent/altéré/désactivé,
membership, droit SET de `session_replication_role` ou propriété importeur.
Le rôle ne peut ni désactiver ni réécrire ces protections.

Choix examiné avant implémentation :

| Option | Décision |
|---|---|
| Retirer UPDATE ou affaiblir les verrous | Les quatre modes de row lock exigent UPDATE ; suppression des verrous dangereuse pour autorisations/concurrence. |
| Helpers SECURITY DEFINER | Nouvelle capacité privilégiée et modification des verrous internes des triggers ; non retenu. |
| RLS sur six tables partagées | Nouvelles policies globales et risques applicatifs/backup ; non retenu pour ce correctif local. |
| Triggers statement ciblés sur la session d'import | Retenu et testé réellement : aucun verrou ou privilège propriétaire ajouté, UPDATE refusés et locking selects préservés. |

**Limites qui restent explicites :** MAINTAIN permet la maintenance, pas seulement
LOCK. Les INSERT nécessaires sur températures/preuves/reçu/audit restent des
capacités SQL directes ; le rôle seul n'est pas une capacité liée à un hash de
plan. L'approbation, le filtrage institutionnel et l'audit atomique du workflow
restent obligatoires. L'isolation institutionnelle est appliquée par Django,
pas par ces ACL sans RLS. Les propriétaires/DBA restent dans la frontière de confiance.
Refuser tout membership, propriété, CREATE effectif (y compris via PUBLIC),
DELETE/TRUNCATE/TRIGGER, UPDATE de table entière ou droit supplémentaire non revu.
Ne pas révoquer les ACL PUBLIC ou applicatives globalement pour faire passer le
garde. Vérifier aussi les attributs/memberships du rôle backup `polypbase` : son
ownership confirmé n'atteste pas à lui seul qu'il est non privilégié.

## B. Peer, attestation et chemins privés — autorisation infrastructure séparée

HBA communiqué : `local all postgres peer`, puis `local all all peer`, sans
mapping ident. Les chemins sont `/etc/postgresql/18/main/pg_hba.conf` et
`/etc/postgresql/18/main/pg_ident.conf`. Après confirmation qu'ils correspondent
bien au cluster 18/main/5433, éditer sous contrôle opérateur :

```sh
sudoedit /etc/postgresql/18/main/pg_hba.conf /etc/postgresql/18/main/pg_ident.conf
```

Insérer cette seule règle HBA **avant** `local all all peer`, sans changer les
deux règles existantes :

```text
local  polypbase  polypbase_hist_import  peer map=polypbase_hist_import_map
```

Ajouter ce seul mapping :

```text
polypbase_hist_import_map  polypbase  polypbase_hist_import
```

L'ancien login `polypbase` continue à utiliser la règle peer existante, y compris
pour le dump. Ne pas ajouter `trust`, de règle réseau ou de mapping générique.
Dans une session SQL administrative indépendante, inspecter les fichiers et les
rôles en lecture seule, sans afficher les options pouvant contenir des secrets :

```sql
BEGIN READ ONLY;
SHOW hba_file;
SHOW ident_file;
SELECT rule_number, file_name, line_number, type, database, user_name, auth_method,
       ARRAY(SELECT option FROM unnest(options) AS option WHERE option LIKE 'map=%') AS maps,
       error IS NOT NULL AS invalid
FROM pg_hba_file_rules ORDER BY rule_number NULLS LAST, file_name, line_number;
SELECT map_number, file_name, line_number, map_name, sys_name, pg_username,
       error IS NOT NULL AS invalid
FROM pg_ident_file_mappings ORDER BY map_number NULLS LAST, file_name, line_number;
SELECT oid, rolname, rolcanlogin, rolinherit, rolsuper, rolcreatedb, rolcreaterole,
       rolreplication, rolbypassrls FROM pg_roles
WHERE rolname IN ('polypbase', 'polypbase_hist_import');
SELECT c.oid::regclass, pg_get_userbyid(c.relowner) AS owner,
       c.relrowsecurity, c.relforcerowsecurity FROM pg_class c
JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY c.relname;
COMMIT;
```

Vérifier l'ordre effectif et l'absence d'erreur avant de recharger. Les vues
décrivent les fichiers présents, pas la dernière version effectivement chargée.
Après une autorisation spécifique de rechargement :

```sh
sudo /usr/bin/pg_ctlcluster 18 main reload
```

Pas de redémarrage PostgreSQL : il invaliderait les identités attestées. Après
revue des ACL et du mapping, activer le login pour la fenêtre prévue, dans la
session SQL administrative autorisée :

```sql
ALTER ROLE polypbase_hist_import LOGIN;
```

### Attestation dédiée, sans toucher à PCA

Un opérateur indépendant collecte les champs exacts et construit un JSON revu
dans `/root/historical-temperature-production-target.reviewed.json`, root-owned
0600. Ne pas produire l'attestation à partir du plan à approuver. Champs exacts :

| Champs | Valeurs à établir indépendamment |
|---|---|
| `schema` | `historical_temperature_target_v1` |
| `machine_id` | `/etc/machine-id`, 32 caractères hexadécimaux |
| `configured_host`, `configured_port` | `/var/run/postgresql`, chaîne `5433` |
| `database`, `database_oid` | `polypbase`, OID réel entier positif |
| `role`, `role_oid` | `polypbase_hist_import`, OID réel entier positif |
| `backup_role`, `backup_role_oid` | `polypbase`, OID distinct réel |
| `connection_mode`, `server_address`, `server_port` | `unix`, JSON null, JSON null |
| `postmaster_started_at` | UTC exact `YYYY-MM-DDTHH:MM:SS.USZ` du serveur |
| `server_version_num` | entier exact PostgreSQL 18 |
| `socket_uid`, `socket_gid` | UID/GID OS PostgreSQL vérifiés, entiers positifs |

La collecte SQL utilise les catalogues publics, les OIDs de `pg_database` et
`pg_roles`, et `to_char(pg_postmaster_start_time() AT TIME ZONE 'UTC',
'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')`. Confirmer séparément machine, socket et endpoint.
Auditer les ancêtres root-owned avant installation ; aucun lien, écriture groupe
ou autre, ACL déléguée ou possibilité pour le runtime de remplacer l'attestation.
Dans la session root **séparément autorisée**, pour une installation initiale :

```sh
set -eu
umask 077
test ! -L /etc/polypbase
if test ! -d /etc/polypbase; then
  install -d -o root -g root -m 0755 /etc/polypbase
fi
test ! -e /etc/polypbase/historical-temperature-production-target.json
test ! -L /etc/polypbase/historical-temperature-production-target.json
python3 -m json.tool /root/historical-temperature-production-target.reviewed.json >/dev/null
install -T -o root -g root -m 0644 /root/historical-temperature-production-target.reviewed.json /etc/polypbase/historical-temperature-production-target.json
namei -l /etc/polypbase/historical-temperature-production-target.json
stat -c '%u:%g %a %h %n' /etc/polypbase/historical-temperature-production-target.json
```

Attendu : root 0:0, 0644, un lien. Le fichier PCA n'est ni modifié ni remplacé.
Une rotation historique ultérieure exige une nouvelle revue et autorisation ;
aucun écrasement automatique. Les plans précédents deviennent inutilisables.

### Sources et artefacts

Sept originaux privés à transférer par un canal autorisé hors Git :
`Suivi_2020.xlsx` à `Suivi_2025.xlsx`, et `Suivi_2026_actualisé_v2.xlsx`.
Leur présence n'est pas supposée. Les empreintes attendues restent celles de
`historical_temperature_source.SOURCES` ; pas de réexportation ni nouvelle allowlist.
Auditer les ancêtres avant de préparer séparément les répertoires proposés :

```sh
install -d -o polypbase -g polypbase -m 0700 /srv/polypbase/private/historical-temperature/sources /srv/polypbase/deployments/historical-temperature /srv/polypbase/backups/historical-temperature
```

Ancêtres root/polypbase non inscriptibles par groupe/autres, sans liens ; fichiers
sources/plans/manifests réguliers à un lien, 0600. Refuser les symlinks préexistants
avant `install -d`, ne pas utiliser `/tmp` ou modifier les permissions/rotations des
sauvegardes normales. Prévoir l'espace pour dump complet, sources et reçu.
Le compte OS `polypbase` doit être non-root, sans capacités ni délégation
administrative, hors groupe PostgreSQL et incapable d'écrire dans le répertoire
socket même par ACL. `/usr/bin/setpriv --no-new-privs`, Python déployé et binaires
PostgreSQL 18 sont requis. Ces préparatifs ne sont pas faits par le déploiement.

Le déploiement et les migrations continuent à utiliser le rôle propriétaire
`polypbase` : aucune séparation migration/runtime de l'application n'est nécessaire
pour cette opération ponctuelle. Ne jamais persister l'override d'import dans le
service ou `.env`. Après le futur postcheck, désactiver le login sous autorisation
et retirer uniquement les grants temporaires revus, y compris ceux de colonnes ;
pas de `DROP OWNED` ou révocation globale non analysée.

## Séquence opérateur proposée — ne pas exécuter dans cette session

Les chemins ci-dessous suivent les défauts de déploiement. Ils sont une proposition
à faire approuver, pas un constat de fichiers présents :

- sources : `/srv/polypbase/private/historical-temperature/sources` ;
- plans/manifests : `/srv/polypbase/deployments/historical-temperature` ;
- archives : `/srv/polypbase/backups/historical-temperature`.

Remplacer `ACTOR_PK`, `APPROVED_SHA256` et `INDEPENDENT_REVIEW_REF` par les valeurs
revues. Utiliser un nouveau nom explicite de plan/manifest à chaque nouvelle revue.
Les sorties sont exclusives, sans écrasement. Depuis le **backend déployé**
`/srv/polypbase/app/backend`, exécuter les commandes en session OS `polypbase`
(pas root), avec le préfixe minimal identique ci-dessous. `env -i` supprime les
variables de shell héritées, notamment `PG*` et les switches QA ; Django charge
sa configuration d'installation sans qu'on lise ou affiche `.env`. Aucune
activation de venv, aucun `uv sync`, `migrate` ou changement de settings n'est
nécessaire à l'import. Arrêter au premier refus, sans relâcher un garde.
Ne pas imprimer le contenu privé des plans, reçus ou sauvegardes.

### C. Précontrôle sans écriture scientifique — accord de lecture seule

Ces contrôles n'installent rien et ne créent aucun artefact. Garder les résultats
et la comparaison indépendante dans le dossier opérateur privé. Adapter uniquement
les chemins approuvés ; ne pas lister ou ouvrir un fichier de secrets.

```sh
id
git --no-pager --no-optional-locks -C /srv/polypbase/app status --short --branch
git --no-pager -C /srv/polypbase/app log -1 --format=%H
../.venv/bin/python -c 'import django, openpyxl, psycopg; print("Import dependencies available")'
/usr/bin/pg_dump --version
/usr/bin/pg_restore --version
namei -l /etc/polypbase/historical-temperature-production-target.json
stat -c '%u:%g %a %h %n' /etc/polypbase/historical-temperature-production-target.json
namei -l /var/run/postgresql/.s.PGSQL.5433
readlink /var/run
stat -c '%F %u:%g %a %n' /run/postgresql /run/postgresql/.s.PGSQL.5433
getfacl -cp /run/postgresql
namei -l /srv/polypbase/private/historical-temperature/sources
namei -l /srv/polypbase/deployments/historical-temperature
namei -l /srv/polypbase/backups/historical-temperature
stat -c '%u:%g %a %n' /srv/polypbase/private/historical-temperature/sources /srv/polypbase/deployments/historical-temperature /srv/polypbase/backups/historical-temperature
df -Pk /srv/polypbase
```

Vérifier le commit effectivement déployé et l'absence de modifications locales.
Toujours depuis `backend/`, vérifier aussi les gardes réels sans ouvrir de connexion
SQL ni afficher l'attestation :

```sh
/usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 /usr/bin/setpriv --no-new-privs -- ../.venv/bin/python -c 'from config import pca_protected_artifacts as p; p.read_historical_temperature_attestation(); p.validate_directory("/srv/polypbase/private/historical-temperature/sources"); p.validate_directory("/srv/polypbase/deployments/historical-temperature"); p.validate_directory("/srv/polypbase/backups/historical-temperature"); print("Protected runtime, attestation and directories verified")'
```

Ce contrôle ne remplace pas la vérification indépendante des champs attestés, des
sept fichiers ou des droits SQL. Une absence/refus est un prérequis à résoudre
séparément, pas une invitation à créer ou modifier des chemins pendant ce précontrôle.

`getfacl` est un outil d'inspection opérateur, pas une dépendance Python. Les
contrôles effectifs de l'application refusent également un accès runtime en
écriture au répertoire socket. Après l'accord de lecture SQL, connexion locale
explicite sans charger de fichier psql utilisateur :

```sh
/usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 /usr/bin/psql -X --no-password --host=/var/run/postgresql --port=5433 --username=polypbase_hist_import --dbname=polypbase --set=ON_ERROR_STOP=1 --command="BEGIN READ ONLY; SELECT current_database(), session_user, current_user, current_setting('server_version_num'), inet_server_addr(), inet_server_port(), pg_postmaster_start_time(); SHOW idle_in_transaction_session_timeout; SHOW transaction_timeout; COMMIT;"
```

Les adresses/ports SQL doivent être NULL pour Unix, contrairement au port configuré
5433. Pour **chacune des deux connexions de contrôle du backup**, les timeouts
idle-transaction et transaction doivent être désactivés (`0`) ou au moins
**1 500 s et 3 600 s** respectivement. Le budget idle est dérivé des deux commandes
externes bornées à 600 s (`pg_dump`, puis `pg_restore --list`), plus **300 s de
marge** ; 600 s ou 1 200 s sont insuffisants et refusés avant export/dump. L'apply
conserve ses délais locaux lock/statement de 10 s/120 s.

Le précontrôle lit les valeurs effectives en millisecondes sur l'exporteur et la
connexion backup, sans SET, ALTER ROLE ou modification globale automatique. Si
une valeur est trop courte, arrêter et faire décider séparément une configuration
strictement adaptée aux sessions concernées ; ne pas augmenter les timeouts
globaux ou ceux de toutes les connexions applicatives pour faire passer le garde.
Hashing local et recomputation n'ont pas de borne murale : les seuils finis sont
une admission avec marge, pas une garantie de durée. Les deux sessions sont
revérifiées après l'archive ; toute expiration/transaction avortée empêche le
retour/publication de la preuve. Une archive peut subsister en privé après échec,
mais ne constitue pas une preuve autorisant apply. Le plan est le contrôle read-only complet de cible, schéma, rôle,
permissions métier et baseline. Un plan `ready` ne prouve pas encore que le dump
complet réussira : la phase backup sous son identité distincte doit réussir sans
élévation de privilèges. Avant tout plan, depuis `backend/`, vérifier les gardes
réels dans une transaction explicitement read-only, sans créer d'artefact :

```sh
/usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 DJANGO_SETTINGS_MODULE=config.settings DJANGO_DEBUG=0 POSTGRES_DB=polypbase POSTGRES_USER=polypbase_hist_import POSTGRES_PASSWORD= POSTGRES_HOST=/var/run/postgresql POSTGRES_PORT=5433 POSTGRES_SSLMODE=disable /usr/bin/setpriv --no-new-privs -- ../.venv/bin/python - <<'PY'
import django
django.setup()
from django.db import connection, transaction
from apps.measurements import historical_temperature_target as target
from apps.measurements.historical_temperature_production import check_schema, _check_timeouts
from apps.measurements.historical_temperature_backup_timeouts import check_backup_timeouts
attestation = target.read_target(target.PINNED_ATTESTATION)
target.require_target(attestation)
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    target.verify_target(attestation)
    check_schema()
    _check_timeouts()
    check_backup_timeouts(connection)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_export_snapshot()")
        snapshot = cursor.fetchone()[0]
    with target.backup_database(attestation, snapshot):
        pass
    target.verify_target(attestation, in_snapshot=True)
print("Historical target, UPDATE guards and both backup timeouts verified read-only")
PY
```

Ce contrôle exporte/importe seulement un snapshot de contrôle : aucune écriture
scientifique, archive ou preuve de backup. Examiner les droits effectifs contre
le SQL approuvé, y compris PUBLIC, les six colonnes UPDATE et leurs gardes
statement obligatoires. Aucun test d'UPDATE n'est à faire en production.
Le client ne peut pas certifier peer plutôt que trust : confirmer la règle HBA
chargée et le mapping par l'inspection administrative et les traces opérateur.

### D. Plan réel post-PCA — accord de lecture et création du plan distinct

```sh
/usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 DJANGO_SETTINGS_MODULE=config.settings DJANGO_DEBUG=0 POSTGRES_DB=polypbase POSTGRES_USER=polypbase_hist_import POSTGRES_PASSWORD= POSTGRES_HOST=/var/run/postgresql POSTGRES_PORT=5433 POSTGRES_SSLMODE=disable /usr/bin/setpriv --no-new-privs -- ../.venv/bin/python manage.py historical_temperature_production plan --production \
  --organization-id 1 --actor-id ACTOR_PK \
  --target /etc/polypbase/historical-temperature-production-target.json \
  --raw-directory /srv/polypbase/private/historical-temperature/sources \
  --plan-file /srv/polypbase/deployments/historical-temperature/plan.json
```

Contrôler `ready`, les blocages, le reçu PCA, les compteurs, les associations PCA,
les zéros, les labels normalisés, toutes les exclusions et preuves. Faire relire
le fichier complet et son `plan_hash` SHA-256 par un reviewer indépendant. Ne pas
utiliser un hash de rapport, de source ou de plan QA à la place de ce hash. Une
erreur PCA structurelle peut interrompre la génération avant création du fichier.
Un plan généré mais bloqué est écrit pour inspection et la commande échoue.

### E. Sauvegarde — accord séparé d'écriture d'artefacts

```sh
/usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 DJANGO_SETTINGS_MODULE=config.settings DJANGO_DEBUG=0 POSTGRES_DB=polypbase POSTGRES_USER=polypbase_hist_import POSTGRES_PASSWORD= POSTGRES_HOST=/var/run/postgresql POSTGRES_PORT=5433 POSTGRES_SSLMODE=disable /usr/bin/setpriv --no-new-privs -- ../.venv/bin/python manage.py historical_temperature_production backup --production \
  --organization-id 1 --actor-id ACTOR_PK \
  --target /etc/polypbase/historical-temperature-production-target.json \
  --raw-directory /srv/polypbase/private/historical-temperature/sources \
  --plan-file /srv/polypbase/deployments/historical-temperature/plan.json \
  --expected-plan-hash APPROVED_SHA256 \
  --backup-dir /srv/polypbase/backups/historical-temperature \
  --backup-evidence /srv/polypbase/deployments/historical-temperature/backup.json
```

Le dump complet sous `polypbase` utilise le snapshot exporté par
`polypbase_hist_import` correspondant à la baseline, gardé ouvert pendant
`pg_dump`. Les deux logins se connectent directement à la même cible attestée,
sans changement d'identité de la connexion Django. La preuve lie cible, plan, archive, taille et SHA-256.
L'application refuse une archive modifiée/manquante, une mauvaise cible ou un
mauvais plan, ainsi qu'une preuve future ou datant de plus d'une heure.

**Limite explicite :** `pg_restore --list` prouve la lisibilité de l'archive, pas
une restauration complète (`restoration_tested=False`). Avant l'autorisation
finale, décider de la répétition de restauration dans un environnement distinct,
autorisé et isolé. Ne jamais restaurer dans une base existante pour tester.
Le contrôle local synthétique ne prouve pas la restaurabilité d'un futur dump
privé de production. Ne pas falsifier les marqueurs du manifeste pour y inscrire
une validation extérieure.

### F. Application — nouvelle autorisation explicite sur le hash exact

Confirmer fenêtre d'écritures, sauvegarde valide et revue indépendante, puis
obtenir immédiatement avant l'opération une autorisation spécifique. L'accord de
planification ou de sauvegarde n'autorise pas cette étape.

```sh
/usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 DJANGO_SETTINGS_MODULE=config.settings DJANGO_DEBUG=0 POSTGRES_DB=polypbase POSTGRES_USER=polypbase_hist_import POSTGRES_PASSWORD= POSTGRES_HOST=/var/run/postgresql POSTGRES_PORT=5433 POSTGRES_SSLMODE=disable /usr/bin/setpriv --no-new-privs -- ../.venv/bin/python manage.py historical_temperature_production apply --production \
  --organization-id 1 --actor-id ACTOR_PK \
  --target /etc/polypbase/historical-temperature-production-target.json \
  --raw-directory /srv/polypbase/private/historical-temperature/sources \
  --plan-file /srv/polypbase/deployments/historical-temperature/plan.json \
  --expected-plan-hash APPROVED_SHA256 \
  --backup-evidence /srv/polypbase/deployments/historical-temperature/backup.json \
  --review-ref INDEPENDENT_REVIEW_REF \
  --authorization APPLY_HISTORICAL_TEMPERATURES_ORGANIZATION_1:APPROVED_SHA256
```

Ne pas réessayer aveuglément après une erreur ou une perte de connexion. Noter le
reçu éventuel et passer au contrôle read-only. Le texte d'autorisation est un
garde d'intention lié au plan, pas un remplacement des permissions backend.

### G. Postcheck — sans relire les classeurs

```sh
/usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 DJANGO_SETTINGS_MODULE=config.settings DJANGO_DEBUG=0 POSTGRES_DB=polypbase POSTGRES_USER=polypbase_hist_import POSTGRES_PASSWORD= POSTGRES_HOST=/var/run/postgresql POSTGRES_PORT=5433 POSTGRES_SSLMODE=disable /usr/bin/setpriv --no-new-privs -- ../.venv/bin/python manage.py historical_temperature_production postcheck --production \
  --organization-id 1 --actor-id ACTOR_PK \
  --target /etc/polypbase/historical-temperature-production-target.json \
  --plan-file /srv/polypbase/deployments/historical-temperature/plan.json \
  --expected-plan-hash APPROVED_SHA256
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

## Corrections ciblées des findings SQL et timeout

L'architecture inter-rôles et l'attestation PCA restent inchangées. La revue avait
identifié deux findings Medium : UPDATE de colonne utilisables hors audit et
budget idle de 600 s inférieur aux deux commandes externes cumulées. Les gardes
statement décrits en A et le précontrôle effectif des deux sessions décrit en C
corrigent ces deux chemins, sans affaiblir les verrous ni modifier les timeouts
serveur/role automatiquement.

Validation de cette correction, PostgreSQL **18.6 Windows jetable uniquement** :

- **133 tests, 132 réussis, 1 ignoré** (charge optionnelle), 470,651 s : workflow
  historique, nouvelles politiques, concurrence et refus PCA. Dont **14 tests
  réellement sous rôle restreint** : UPDATE/no-op/zéro ligne/CTE/UPSERT/MERGE
  refusés avec 42501, quatre modes de row lock sur six tables acceptés, garde
  désactivé/altéré refusé, impossibilité de le désactiver ou changer le rôle de
  réplication, corrections propriétaire et invalidation pending_review intactes,
  verrou concurrent et rollback sans import partiel, audit et plan périmé.
- Refus réel avant dump d'un idle de 600 s sur l'exporteur ou sur la connexion
  backup ; backup réussi avec 1 500 s idle et 3 600 s transaction sur les deux
  sessions, sans changement des réglages globaux. Une expiration réelle de
  l'exporteur simulée après vérification de l'archive empêche le retour de preuve.
- **290 tests backend ciblés, 239 réussis, 51 ignorés**, 10,714 s, sous
  `config.test_settings` : isolation UI, cible, gardes, budgets, workflow,
  corrections historiques et helpers backup/PCA. Pas de relance inutile de la
  suite complète : les derniers résultats complets sont conservés ci-dessous.
- Probe inter-rôles relancé seul : dump et **restauration réelle réussis**, snapshot
  excluant le commit post-export et zéro conservé. Une première tentative parallèle
  a échoué pendant initdb Windows (fichiers temporaires manquants), puis cleanup
  partiel ; aucun serveur n'avait démarré pour ce probe. Son répertoire exact a
  été nettoyé après vérification, puis la relance séquentielle a réussi. Cette
  tentative n'est pas comptée verte. Tous les clusters/répertoires propres ont
  été arrêtés/supprimés ; aucune base existante touchée.
- Django check et dérive de migrations : réussis. Revue statique ciblée des deux
  correctifs : aucun finding supplémentaire identifié dans la frontière annoncée.

Les scripts SQL restent une préparation d'infrastructure à faire approuver et
exécuter séparément, jamais une migration ou un import automatique. Une validation
native Linux/peer, la revue des ACL effectives, de la nouvelle fonction et de ses
six triggers restent requises avant préparation réelle. Si les timeouts effectifs
sont trop courts, une décision opérateur de configuration limitée aux sessions
concernées est nécessaire ; aucun relâchement automatique n'est prévu. Les seuils
finis ne rendent pas bornés le hashing/recompute ; toute expiration reste bloquante.
Aucun commit, push, déploiement, import réel ou accès VM/Neon/production.

## Validation précédente de la séparation des identités

Aucune VM, production, Neon ou base partagée utilisée ; aucun commit. Exécutions
avec la venv existante et `uv run --no-sync`, sans installation :

- `scripts/probe_pg18_exported_snapshot.py` : **trois exécutions réelles réussies**
  sous PostgreSQL 18.6 Windows. Exporteur non propriétaire/non-superuser sans
  membership, lecteur limité ; `pg_dump` sous un autre login non-superuser
  propriétaire, sans SET ROLE. Restauration réelle dans une autre base jetable :
  la ligne commitée après export est exclue, la mesure zéro conservée et la table
  privée illisible par l'exporteur reste sauvegardée. Chaque cluster est neuf,
  arrêté puis supprimé. Le mode `--peer` Linux du probe n'a pas été validé.
- **4/4 tests avec rôle réellement restreint réussis** : SQL de grants versionné
  exercé dans une base jetable, IDs IDENTITY sans grant de séquence, privilèges
  insuffisants pour lire `auth_permission`, aucun UPDATE global ni membership
  propriétaire ; pipeline complet, biologie/audit, refus autre institution,
  péremption et rollback. Les gardes d'identité Linux restent mockés dans ces
  fixtures Windows ; ces quatre tests ne prouvent pas l'authentification peer.
- Harness élargi historique/cible/rôle restreint/refus PCA : **101 tests,
  100 réussis, 1 ignoré** (charge optionnelle), 434,903 s, PostgreSQL 18.6 jetable.
- Suite backend normale `config.test_settings` : **2 305 tests, 2 024 réussis,
  281 ignorés**, 602,887 s. Les corrections d'isolation précédentes sont conservées.
- Relance configuration UI, cible dédiée, helpers backup/artefacts et PCA :
  **190 tests, 181 réussis, 9 ignorés** (workflow PCA exigeant son propre harness).
  Les nouvelles régressions couvrent chemins/permissions root, absence de fallback
  PCA, deux rôles/OIDs distincts, mauvaises cibles, privilèges, mot de passe,
  snapshot, cleanup et absence de mutation des settings.
- `manage.py check` et `makemigrations --check --dry-run`, sous settings de test :
  réussis, aucune dérive. Clé Django synthétique pour ces commandes hors `test`.
- Les **16 blocs shell** opérateur passent Git Bash `bash -n`, le précontrôle
  Python passe l'analyse syntaxique, les quatre actions passent le parser Django
  avec le chemin historique dédié. Aucun bloc opérateur n'est exécuté par ces
  contrôles. `git diff --check` réussi.
- Une tentative a inclus à tort les neuf tests de workflow PCA dans le harness
  historique : refus de leur garde de harness puis erreurs de flush append-only
  (**18 erreurs rapportées**). Elle n'est pas verte. Relance corrigée ci-dessus,
  sans modifier leurs gardes ni le code PCA. Aucun cluster temporaire conservé.

Reproduction depuis la racine, environnement PostgreSQL **jetable uniquement** :

```sh
uv run --no-sync python scripts/probe_pg18_exported_snapshot.py --bin-dir "C:/Program Files/PostgreSQL/18/bin" --runs 2
uv run --no-sync python scripts/test_historical_temperature_production.py --run --pg-bin "C:/Program Files/PostgreSQL/18/bin" apps.measurements.test_historical_temperature_production apps.measurements.test_historical_temperature_restricted_role apps.measurements.test_historical_temperature_target apps.cultures.test_pca_production.ProductionRefusalTests
```

Sur un poste Linux indépendant avec binaires et dépendances déjà installés, sous
compte non-root (pas sur la VM de production), le probe peer est préparé :

```sh
python3 scripts/probe_pg18_exported_snapshot.py --bin-dir /usr/lib/postgresql/18/bin --peer --runs 2
```

**Limite bloquante avant exécution réelle :** aucun Linux PostgreSQL 18 exploitable
n'a été confirmé localement. WSL répond partiellement mais les outils/pilote
nécessaires manquent ou les appels expirent. Le chemin natif Unix/peer avec
attestation réelle, environnement minimal et dump doit encore être validé dans
un Linux isolé. Les tests simulés ne le remplacent pas. La charge représentative
et la restaurabilité d'un futur dump privé de production restent aussi non prouvées.
Toute modification infrastructure, lecture réelle de plan ou phase d'import exige
encore son autorisation distincte après intégration/revue et déploiement.

## Validation précédente de l'audit d'autonomie

Sans VM, Neon, production ou base partagée ; aucun commit. Sept tests sans DB
couvrent les gardes des sources (sept fichiers maintenus ouverts, refus de
répertoire/fichier, remplacement détecté en sortie, bypass QA existant uniquement,
refus avant extraction et avant archivage/écriture). Avec les trois tests de
refus préconnexion et cinq tests de privilèges existants : **15/15 réussis** sous
`config.test_settings` SQLite, dotenv neutralisé, `POSTGRES_DB=''` et switch
PostgreSQL désactivé. Première tentative impossible dans la venv vide du worktree
(Django absent) ; relance réussie avec la venv déjà installée du dépôt principal,
via `UV_PROJECT_ENVIRONMENT` et `uv run --no-sync`. Aucune dépendance installée.
Relance élargie du module production et des helpers sauvegarde/artefacts PCA :
**153 tests découverts, 111 réussis, 42 ignorés** (preuves de workflow exigeant
PostgreSQL), en 7,360 s :

```sh
POSTGRES_DB='' POLYPBASE_TEST_POSTGRES=0 PYTHON_DOTENV_DISABLED=1 uv run --no-sync python manage.py test apps.measurements.test_historical_temperature_production config.test_database_backup config.test_pca_protected_artifacts --settings=config.test_settings --verbosity=1
```

Depuis `backend/`, avec la venv existante sélectionnée par `UV_PROJECT_ENVIRONMENT`
si nécessaire. `git diff --check` passe sur les fichiers modifiés.

Le test supplémentaire `BackupOrchestrationTests` réussit sans DB : il vérifie
le chemin Unix PostgreSQL 18/5433, les gardes maintenus pendant le dump, la
transaction read-only, le snapshot exporté et `hostaddr=None`. Ces tests vérifient
l'orchestration avec mocks, pas une preuve native Linux. La QA Linux PostgreSQL 18
Unix/peer, rôle restreint, environnement minimal, dump complet et restauration
isolée restent des prérequis avant autorisation réelle.

Résultats locaux de la présente reprise :

- Cause des trois erreurs reproduite sous `config.test_settings` en injectant
  seulement des noms synthétiques après initialisation Django : `SettingsTests.load`
  copiait l'environnement en retirant `PG*`, mais conservait `POSTGRES_DB` et
  `POLYPBASE_TEST_POSTGRES`, notamment chargés par `settings.py` via dotenv. Les
  settings UI les refusent volontairement ; le quatrième test attendait déjà une
  exception et masquait ce défaut. Les settings historiques de test évitaient le
  problème en désactivant dotenv et en vidant `POSTGRES_DB` avant cet héritage.
- Correction limitée au helper des tests : valeurs synthétiques explicites,
  overrides des cas de refus conservés, environnement et loader dotenv restaurés.
  Aucun changement des settings réels, de `.env` ou de la sélection globale DB.
- Quatre tests originaux : réussis normalement puis avec pollution synthétique ;
  module UI complet **45/45 réussis**, dont deux nouvelles régressions d'isolation.
- Harness **PostgreSQL 18.6** jetable indépendant : **57 tests, 56 réussis,
  1 ignoré** (charge optionnelle), 470,074 s. Workflow commande complet, péremption,
  permissions, concurrence, rollback et invariants de conservation couverts. Cluster
  créé pour ces tests uniquement, arrêté et supprimé. Le test Unix sans DB ajouté
  ensuite a été exécuté séparément (**1/1**, puis **16/16** avec les autres gardes).
- Suite backend normale `config.test_settings`, sans neutralisation préalable des
  variables PostgreSQL : **2 278 tests, 2 001 réussis, 277 ignorés**, 1 033,146 s.
  Cette suite précède uniquement l'ajout du test Unix ci-dessus ; elle comprend
  déjà les neuf nouvelles régressions source/isolation.
- `manage.py check --settings=config.test_settings` et
  `makemigrations --check --dry-run --settings=config.test_settings` : réussis,
  aucune dérive. Clé Django synthétique fournie pour ces commandes hors `test`
  car ce worktree n'a pas de configuration privée ; aucune clé réelle utilisée.
- Relance complémentaire finale configuration UI, orchestration Unix, gardes,
  sauvegardes, artefacts et PCA : **145 tests, 136 réussis, 9 ignorés** (workflow
  PCA PostgreSQL nécessitant son propre harness).
- Blocs opérateur vérifiés localement avec Git Bash `bash -n`, sans exécution ;
  arguments des quatre actions validés par le parser de la commande Django.
  La première tentative de syntaxe via `bash` non épinglé a dépassé 10 s
  (résolution Windows/WSL) ; relance réussie avec le binaire Git Bash explicite.
- Tentative de vérification de l'outillage Ubuntu/WSL local interrompue après
  30 s sans résultat : aucune validation native Linux n'est revendiquée.

Les commandes Django ci-dessus ont utilisé la venv existante via
`UV_PROJECT_ENVIRONMENT` et `uv run --no-sync`, sans installation de dépendance.
La volumétrie représentative, la restauration complète et toute opération VM
restent non exécutées.

## Validation locale historique (session précédente)

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
