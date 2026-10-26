# CCA-EOB V3 — procédure de production gardée, non autorisée

Cette procédure prépare une opération future. Elle **n’autorise ni accès VM, ni
mutation de production, ni déploiement**. Aucun provisionnement, import
d’approbation ou apply ne doit partir automatiquement. La commande QA
`correct_cca_eob_v2`, `require_local_database()` et sa vérification de connexion
restent stricts. `cca_eob_production` est indépendante : aucune migration, aucun
serveur, import ou déploiement ne l’appelle.

> Complément courant : [exception des 72 réattributions sans lien thermique](cca-eob-unlinked-72-handoff.md).
> Il remplace ci-dessous le veto thermique global, uniquement pour le plan
> historique intégral authentifié. Les résultats et comptes anciens ne sont
> pas des preuves courantes. Aucune autorisation de production n'en découle.

## État et périmètre

Le contexte initial indiquait `37587da`; la préparation historique a commencé sur
`d1e4e73`, avec cultures.0015 et measurements.0014. Ces références et les résultats
anciens conservés plus bas sont **historiques**, pas une preuve de compatibilité
avec le `main` courant ni une autorisation de production. La révision réellement
revue, intégrée et déployée devra être relevée avant toute phase. Aucun hash de
plan de production, acteur réel, OID ou identifiant de sauvegarde ne figure ici.

Cibles seules : organisation **1**, boîtes **1835 / CCA-EOB-2.004**,
**1836 / CCA-EOB-2.005**, **1837 / CCA-EOB-2.006**, relevés ISO 2026 appariés par
le manifeste et le classeur V2 SHA-256
`0d5cd16eee88845ea51fbd4f122be15c12e5896b5237458573fc432ebda8b160`.
Les constats read-only initiaux (0/41/41 relevés 2026; zéro température historique)
et les comptes 72/8/4 d'une autre QA sont historiques, pas une matrice approuvée.
L'exception courante exige séparément le véritable plan historique authentifié
de 72 mouvements; ne jamais le reconstruire depuis ces anciens comptes/hashes.

Aucune correction générique ni option `force`. Identités fusionnées/renommées,
doublons, contexte environnemental explicite, références entrantes externes,
correspondances inconnues, conflits, lignées, repiquages, transferts et historiques
institutionnels incohérents bloquent. La présence d'un relevé thermique exige
désormais l'exception authentifiée décrite dans le complément; un lien direct
sur une mesure déplacée bloque toujours. Aucune association ne doit être
invalidée, détachée ou réécrite pour permettre la correction. Seuls `box_id`, les révisions
scientifiques attendues et de **nouveaux** audits métier peuvent changer. Zéro et
NULL restent distincts. Dates, auteurs, précision, provenance, séquences
scientifiques, statuts, zones, emplacements, mouvements et anciens audits restent
identiques. L’environnement est résolu par la boîte cible selon V3, sans histoire
inventée.

## Sources inspectées et intégration en cours

- Contrat SQL : `backend/config/cca_eob_database_role.py`.
- Émetteur hors connexion : `deploy/scripts/cca_eob_role_sql.py`.
- Intégration : `backend/apps/cultures/historical_2026/cca_eob_production.py`,
  commande `backend/apps/cultures/management/commands/cca_eob_production.py`,
  settings `backend/config/cca_eob_production_settings.py`.
- Moteur partagé : `backend/apps/cultures/historical_2026/cca_eob_correction.py`.
- Garde socket CCA seule : `backend/config/cca_eob_socket.py` et ses tests.

Le contrat courant implémente lecteur/writer fixes plus verrou et table
d’approbation possédée par `postgres`. La commande Django expose seulement `plan`
et `apply`. **Autoriser est une action administrative distincte** : le nom CLI
de l’émetteur est `approve` pour le plan, puis `authorize-apply` pour la décision
finale séparée; ni une option de `provision` ni une sous-commande Django `authorize`.

**Les deux constats de revue indépendante ont été corrigés et leurs tests ciblés
passent; la re-revue indépendante reste attendue.** Le constat High
concerne le contournement de l’autorisation finale d’apply; le constat Medium,
la preuve d’archive incomplète. Les résultats antérieurs conservés ci-dessous
sont historiques et ne valident pas ces correctifs. `socket_guard()` utilise le
module CCA, sans dépendre de l’API socket ajoutée à PCA sur `main`. Les métadonnées
complètes des helpers, notamment `COST`, sont vérifiées avant appel. Aucune
autorisation de production n’en découle.

## Choix de sécurité et plan v2

- Production Linux seulement, processus non root sans capacités, `NoNewPrivs=1`,
  namespace UID initial; identités UID/GID et permissions vérifiées.
- `config.cca_eob_production_settings` importe les settings sans lire le `.env`,
  désactive `DEBUG`, impose une seule connexion Django sans routeur : socket
  `/var/run/postgresql`, port `5433`, base `polypbase`, rôle **`polypbase_cca_eob`**,
  PostgreSQL 18, `OPTIONS={"sslmode": "disable"}`. Login Linux homonyme via `peer`,
  sans mot de passe. Ni TCP, tunnel, service libpq, découverte automatique, options
  de redirection ni environnement hérité `PG*`/`POSTGRES_*`. Ne pas modifier
  `pg_hba.conf`; si la règle locale existante ne convient pas, STOP.
- Cible root indépendante au chemin fixe
  `/etc/polypbase/cca-eob-production-target.json`, schéma `cca_eob_target_v2`.
  Vérifier configuration, connexion psycopg effective et identité SQL live :
  base/rôle et OID, session/effective user, démarrage du postmaster, version,
  schéma public, réplication `origin`, primaire. Un redémarrage PostgreSQL invalide
  cible et plan. `machine_id` et propriété de socket sont attestés hors SQL.
- Socket : utiliser **`config.cca_eob_socket.protected_postgresql_socket`**,
  sans modifier `config.pca_protected_artifacts` ni la procédure PCA. Préserver
  les contrôles d’ancêtres root non modifiables, alias root `/var/run` -> `/run`,
  propriétaire UID/GID attesté de `/run/postgresql` et de `.s.PGSQL.5433`, absence
  d’écriture runtime y compris ACL nommées, refus du runtime propriétaire ou membre
  du groupe propriétaire, et détection de remplacement via descripteurs/signatures
  avant/après. La fenêtre englobe connexion, requêtes, transaction et commit.
  Une dérive pendant la transaction provoque son annulation; détectée seulement
  après commit, elle est signalée mais ne peut défaire celui-ci. Ce contrôle ne
  protège pas d’un root hostile ni d’un remplacement après le dernier contrôle.
- Dry-run dans sa propre transaction `REPEATABLE READ READ ONLY`; aucune écriture
  SQL ni audit. Seul un JSON privé exclusif 0600, dans un répertoire 0700, est créé.
- Enveloppe **`cca_eob_production_plan_v2`** : `schema`, `target`, `actor_id`, `plan`.
  Le payload `plan` contient `correction` (plan scientifique V3), `deployed_guards`,
  **`database_snapshot`** (payload brut du lecteur) et **`write_recipe`**
  déterministe (`moves`, `revisions`, `audits`). Le planificateur partagé reçoit
  les données bornées du lecteur, sans SELECT ORM métier du runtime. Les helpers
  `_move_audit` et `_summary_audit` alimentent la recette et le chemin QA local :
  pas de deuxième définition divergente du contenu scientifique des audits.
- Le SHA-256 Python couvre l’enveloppe complète : source, acteur, cible, snapshots,
  recette, migrations et gardes. L’intégration reconstruit le plan courant sous
  verrous et exige une égalité complète avec le document revu, pas de simples
  comptes ou PK. Le writer SQL compare lui-même le snapshot brut courant à celui
  du document approuvé. SQL ne resérialise pas le JSON pour recalculer ce hash.
- Application dans une transaction externe unique `READ COMMITTED` : boîtes →
  organisation → membership/utilisateur → mouvements → périodes → zones → mesures;
  puis `SHARE ROW EXCLUSIVE` sur biologie et `SHARE` sur relevés thermiques.
  Ces verrous bloquent des écritures globales : fenêtre sans écrivains/importeurs.
  `lock_timeout=10s`, `statement_timeout=30s`, transaction bornée à
  `database_backup.TIMEOUT_SECONDS + 300` (actuellement 900 s). Vérification
  archive/checksum et `pg_restore --list` sous verrous, outil externe borné à 600 s.
  Cet outil est **`/usr/lib/postgresql/18/bin/pg_restore`**, jamais le wrapper
  Debian `/usr/bin/pg_restore` (voir « Clients PostgreSQL 18 » ci-dessous).
  Cette revérification Python peut dépasser les cinq minutes de l’autorisation
  finale : le writer SQL refuse alors l’apply, ou la consommation atomique échoue
  si l’échéance est franchie pendant les écritures; toute la transaction est annulée.
  Cet arrêt est délibéré, pas un motif pour prolonger la fenêtre ou réessayer.
  Timeout/deadlock : rollback, diagnostic, jamais retry aveugle ni escalade.
- Triggers thermiques actifs, bindings exacts (table/fonction/schéma, événements,
  colonnes, WHEN, arguments, contraintes, transition tables), corps et paramètres
  attendus d’après migrations. Définitions et métadonnées de catalogue des triggers,
  fonctions historiques et helpers font partie du snapshot : toute dérive entre
  revue et apply bloque. Vérifier aussi les contrôles complets de l’émetteur : ACL,
  propriétaire, langage, volatilité, parallélisme, types/arguments, defaults,
  support, COST/ROWS, etc. Les altérations sont couvertes par les tests de refus;
  ne pas assouplir ces contrôles pour obtenir artificiellement un test vert.

## Frontière PostgreSQL actuelle — remplace la matrice initiale

`polypbase` reste propriétaire des tables métier et des trois fonctions fixes;
ce rôle n’est jamais le login de correction. `polypbase_cca_eob` : `LOGIN`,
`NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT`,
`CONNECTION LIMIT 1`, aucun mot de passe, appartenance dans aucun sens, objet
possédé, autorité assumable ou `CREATE` base/schéma. Refus = STOP, jamais exécuter
la correction en superuser pour contourner la garde.

### Pourquoi conserver la capacité de verrouillage

Constat initial conservé : PostgreSQL exige `UPDATE` pour les verrous de ligne
`FOR UPDATE / NO KEY UPDATE / SHARE / KEY SHARE`, et un UPDATE de table pour les
modes `LOCK TABLE` utilisés ici. Accorder ces droits aux comptes/historiques ou
aux mesures du runtime est rejeté. Les verrous consultatifs non pris par les
écrivains ordinaires et l’absence de verrouillage ne préservent pas la sérialisation.
Un nouveau propriétaire intermédiaire NOLOGIN ajouterait une autorité inutile;
l’exécuteur générique ou superuser est interdit. Le propriétaire métier existant
porte donc la capacité fixe, désormais accompagnée du lecteur et du writer.

### Matrice des privilèges courante

| Objet / capacité du runtime | Droit permis et justification |
|---|---|
| Base `polypbase`, schéma `public` | `CONNECT`, `USAGE` seulement |
| `public.django_migrations` | `SELECT (app, name)` seulement; pas `id`/`applied`, pas SELECT table entière |
| `public.cca_eob_read_v1(bigint)` | `EXECUTE`; lecture fixe bornée, acteur Admin actif organisation 1 |
| `public.cca_eob_lock_v1(bigint)` | `EXECUTE`; protocole fixe de verrous, aucune écriture ni donnée retournée |
| `public.cca_eob_apply_v1(text)` | `EXECUTE`; hash sélecteur d’une approbation indépendante existante |
| Tables scientifiques, comptes, membership, organisation, historique et audit | **Aucun SELECT, UPDATE ou INSERT direct**, même de colonne; aucun DELETE/TRUNCATE/REFERENCES/TRIGGER/MAINTAIN |
| `public.cca_eob_approval_v1` | **Aucun accès direct runtime**, ni lecture ni écriture |
| Séquences | **Aucun USAGE, SELECT ou UPDATE** |

Les fonctions sont `SECURITY DEFINER`, propriétaire `polypbase`,
`search_path=pg_catalog, pg_temp`, relations qualifiées, sans SQL arbitraire ni
identifiant de table/schéma fourni par le caller. Lecteur et writer fixent aussi
`TimeZone=UTC`, `DateStyle=ISO, YMD`. `PUBLIC` ne reçoit pas EXECUTE; ACL limitée
au propriétaire et au runtime, sans délégation. Le corps refuse tout autre
`session_user`. Le verrou exige aussi `READ COMMITTED`, l’acteur Admin actif et
les trois identités de boîtes exactes. Il ne constitue pas une autorisation d’écrire.

Le vérificateur contrôle les privilèges **effectifs**, y compris ceux hérités de
PUBLIC : pas de réglage propre au rôle, objet large, schéma supplémentaire,
fonction definer étrangère, privilège par défaut, séquence, ownership ou membership
permettant un contournement. Ne pas retirer globalement des droits PUBLIC sans
une décision séparée. Le `TEMP` par défaut n’autorise pas de données persistantes;
les relations des capacités restent qualifiées et le search_path fixe.

### Ancienne « Décision requise » — remplacée par la frontière bornée

La matrice initiale accordait SELECT sur neuf tables entières et des écritures
de colonnes au runtime pour l’ORM et les triggers. Ce choix et la question ouverte
sur la lecture inter-institutions sont **supplantés**, pas une permission encore
à accepter. Le design retenu est le **lecteur definer fixe**, plus writer fixe et
approbation indépendante. Aucune activation RLS globale ni modification de PCA.

Le lecteur retourne les seules boîtes du périmètre, leurs mesures et contextes,
les IDs nécessaires à l’attribution, les liens entrants bornés et des indicateurs
de présence pour histoires/références externes. Il ne renvoie pas les tables des
autres institutions ni les comptes complets. Le test global de présence de relevés
thermiques est un booléen bloquant, pas une extraction de températures. Cette
capacité reste une surface privilégiée à relire; design résolu ne signifie pas
validation finale acquise. Les résultats anciens sur les GRANT retirés ne prouvent
pas la matrice actuelle.

### Approbation de confiance et writer one-shot

`public.cca_eob_approval_v1` est une table persistante **possédée par `postgres`**,
marquée et vérifiée : colonnes exactes, singleton primaire vrai, contraintes/index,
absence de RLS, policy, règle, partition, héritage ou trigger non prévu, ACL exactes.
Elle conserve le nom `public.cca_eob_approval_v1`, avec le marqueur exact
**`Polypbase CCA-EOB trusted approval v2`**. Elle contient `singleton`, `plan_hash`,
`document`, `release`, `expires_at`, `consumed_at`, plus trois colonnes nouvelles
**nullable** : `final_authorization` (`jsonb`), `final_authorized_at` et
`final_expires_at` (`timestamptz`). La garde DDL vérifie ces trois colonnes et leur
nullabilité, en plus du catalogue complet. Un ancien schéma/marqueur provisoire
n’est jamais adopté ni migré silencieusement; aucune migration de production
n’est autorisée ici.

`polypbase` n’a que SELECT et UPDATE de **`consumed_at`**; il ne peut installer,
modifier ou prolonger aucune des deux décisions administratives. Le runtime
n’a aucun accès direct. `provision` crée la frontière, **jamais une approbation**.
`approve` ne fait qu’un INSERT d’approbation du plan, avec les trois champs finaux
à NULL : cette première étape seule ne permet aucune mutation scientifique SQL.

Le writer n’accepte que `reviewed_hash text`. **L’acteur SQL provient de
`approval.document.actor_id`, jamais d’un acteur caller, GUC, JSON caller ou hash
présenté comme autorité.** Le hash est un sélecteur; connaître le hash, être Admin
ou posséder le token CLI ne permet pas de fabriquer l’approbation. L’administrateur
importe le document complet revu et sa release indépendante après contrôle du
fingerprint Python; la comparaison SQL porte sur le JSON de confiance stocké.

L’autorisation finale est installée par un **UPDATE administratif indépendant**,
émis par `final_authorization_sql(document, reviewed_hash, release, expires_at)`
ou la CLI `authorize-apply`. Il exige le hash, le document complet et la release
exactement stockés, une approbation non expirée, `consumed_at IS NULL` et les
**trois champs finaux à NULL**. Il ne crée aucune ligne. L’échéance doit être
future, au plus à cinq minutes et au plus à `expires_at` du plan; elle est
recontrôlée au moment de l’installation SQL. Le JSON final contient exactement
`schema: "cca_eob_final_apply_v1"`, `plan_hash`, `document` complet et `release`
complète, égaux à ceux de l’approbation. Le SQL de cette action exige dans son
corps **`session_user = current_user = postgres`**, même lorsque le préambule de
transport est exclu des tests isolés. Ce n’est pas seulement une garde de CLI.
Aucun renouvellement, réinstallation ou prolongement n’est permis, même après
expiration. Aucun nouvel exécuteur générique n’est ajouté.

**L’ancienne affirmation « approbation initiale + CLI suffisent » est remplacée** :
ni hash SQL, ni token CLI, ni GUC ne constituent l’autorité finale. Les deux
étapes sont installées par l’administrateur, jamais par le runtime.

Le writer valide l’autorisation finale avant les verrous, puis sous verrous,
avec JSON exact, timestamps cohérents et fenêtre bornée. Il exige une ligne
non expirée/non consommée, prend les verrous métier,
verrouille et relit la ligne, puis refuse toute modification concurrente. Il lie
release, acteur, cible et hash; vérifie l’identité live PostgreSQL, y compris OID,
transport, version et **démarrage actuel du postmaster**, primaire et rôle de
session; exige le snapshot exact et les garde-fous courants avant la première
écriture. Recette, cardinalités, mesures/baselines, zéro/NULL, destinations,
révisions et identité des audits sont contrôlés. Il ajoute la preuve d’exécution
aux audits, puis renseigne `consumed_at` par UPDATE atomique qui revérifie
l’expiration du plan **et** `final_expires_at`, **dans la même transaction**. Une erreur annule aussi consommation et audits.
Un succès interdit le replay; une expiration exige une nouvelle décision, jamais
un renouvellement ou une remise à zéro automatique. Le runtime n’a aucun droit
de séquence pour créer les audits; le mécanisme d’identité s’exécute sous le definer.

## Génération SQL sûre — aucune exécution root du checkout live

**Instruction ancienne retirée : ne jamais exécuter en root
`/srv/polypbase/app/deploy/scripts/cca_eob_role_sql.py`, un Python/.venv de cet
arbre, ni un import provenant du checkout mutable par `polypbase`.** Propriété
root du seul fichier d’entrée, revue du hash du script seul, `sudo`, `python -I`
ou copie après génération ne rendent pas sûrs ses imports/dépendances. Le risque
est l’exécution root de code modifiable par le service, avant même la revue du SQL.

Deux voies permises, à préparer délibérément :

1. **Génération hors ligne**, sans accès production, depuis la révision complète
   revue et ses dépendances vérifiées, avec un interpréteur de confiance. Transférer
   seulement les SQL finis, leurs digests et la preuve de revue par un canal approuvé;
   revérifier les octets après transfert, sans Python applicatif sur la VM.
2. **Bundle staged vérifié sous garde root**, par exemple
   `/root/cca-eob-reviewed-bundle`, seulement après vérification indépendante de
   sa provenance et de tous ses fichiers/dépendances. Tous ses ancêtres, fichiers,
   packages, interpréteur et configuration d’import doivent être non modifiables
   par le service/runtime ou un groupe non privilégié, ACL comprises; pas de lien
   vers le checkout ou son venv. Vérifier `/`, `/root`, chaque ancêtre et le contenu
   réel; un `chown` du script seul ne suffit pas. Pas de copie aveugle du live.

L’émetteur utilise la bibliothèque standard et `config.cca_eob_database_role`, mais
cet import passe aussi par le package `config` : vérifier **toute** la fermeture
d’import, notamment `config/__init__.py`, pas seulement deux fichiers. Conserver
la structure relative : `deploy/scripts/cca_eob_role_sql.py` et `backend/config/...`
sous la même racine. **Avec `python -I`, le répertoire du script et PYTHONPATH ne
sont pas ajoutés** : l’émetteur insère explicitement
`Path(__file__).resolve().parents[2] / "backend"`. Ce chemin doit résoudre vers le
backend **du bundle vérifié**, jamais celui du live. Utiliser un Python système
root de confiance et sa bibliothèque standard vérifiée; pas le venv applicatif.

Les chemins `/root/...` suivants sont des emplacements de staging délibérés,
**pas des artefacts déjà présents/revus**. Créer les répertoires sous garde root
0700, vérifier permissions/ACL/ancêtres et absence de sorties préexistantes.
La génération ne se connecte pas à PostgreSQL :

```sh
/usr/bin/python3 -I /root/cca-eob-reviewed-bundle/deploy/scripts/cca_eob_role_sql.py provision > /root/cca-eob-role/provision.sql
/usr/bin/python3 -I /root/cca-eob-reviewed-bundle/deploy/scripts/cca_eob_role_sql.py verify > /root/cca-eob-role/verify.sql
/usr/bin/python3 -I /root/cca-eob-reviewed-bundle/deploy/scripts/cca_eob_role_sql.py rollback > /root/cca-eob-role/rollback.sql
sha256sum /root/cca-eob-role/provision.sql /root/cca-eob-role/verify.sql /root/cca-eob-role/rollback.sql
```

Vérifier chaque code retour et interrompre au premier échec. Faire relire le SQL
**émis complet** indépendamment, consigner digest/révision/dépendances et comparer
ces digests immédiatement avant import. Ne pas piper le générateur vers `psql`.
L’import se fait seulement après accord explicite, comme `postgres` avec
`psql -X -v ON_ERROR_STOP=1`. `/root` n’étant normalement pas traversable par
`postgres`, root peut fournir le SQL revu sur stdin sans exécuter le générateur :

```sh
# Phase de provisionnement seulement après son accord explicite distinct.
runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -h /var/run/postgresql -p 5433 -d polypbase < /root/cca-eob-role/provision.sql
runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -h /var/run/postgresql -p 5433 -d polypbase < /root/cca-eob-role/verify.sql
```

L’émetteur exige primaire local PostgreSQL 18, `polypbase:5433`,
`session_user=current_user=postgres`. Timeouts DDL 10/30 s; transaction annulée
en cas de refus. `provision` reconnaît le rôle marqué, refuse membership/ownership,
session vivante ou helper/table d’approbation altéré, révoque les anciens droits
directs du seul rôle reconnu, installe les trois helpers et la table technique
puis vérifie le catalogue. Ce n’est plus « un rôle et une fonction sans table ».
Une relance n’actualise pas silencieusement un corps différent et ne réactive pas
un rôle `NOLOGIN`. Elle ne convertit pas non plus l’ancienne table provisoire vers
le marqueur `trusted approval v2`; tout schéma antérieur impose STOP et une
décision séparée, pas un ALTER TABLE improvisé.

## Séquence opérateur future obligatoire — aucun feu vert actuel

Chaque mutation exige son accord propre; une autorisation de release ne vaut ni
provisionnement, ni approbation SQL, ni correction scientifique, ni restauration.

1. **Revue et compatibilité avec le main courant** : relever révisions locale,
   `origin/main` et VM réelles, arbre/diff, dépendances, migrations, propriétaire,
   API socket CCA et métadonnées. Achever les correctifs en cours et validations
   fraîches. Revoir diff et SQL émis. Intégrer/déployer seulement sur accord via
   `deploy/deploy_vm.ps1`, jamais copier du code non committé sur la VM. Les anciens
   essais par-dessus `dbd595d` ne dispensent pas de ces contrôles courants.
2. **Inspection read-only autorisée séparément** : services, identité cible, règles
   `peer` sans règle antérieure incompatible, acteur Admin actif organisation 1,
   absence de rôle/objets inattendus. Compte Linux `polypbase_cca_eob` homonyme,
   système sans login interactif/home, uniquement son groupe, sans sudo/groupe
   `polypbase`/`postgres`, code/dépendances lisibles mais `.env` non lisible. Sa
   création et le répertoire d’artefacts 0700 demandent leur accord; contrôler tous
   les ancêtres, UID/GID et ACL, pas seulement le mode final.
3. **Provisionnement séparément approuvé** : génération sûre et import ci-dessus,
   sans approbation dans `provision`. Vérifier login peer réel et refus des accès
   directs comptes/science/audit/approbation/séquences, sans en déduire un droit
   de tester des écritures scientifiques en production.
4. **Sauvegarde fraîche et restauration complète** : pendant la fenêtre approuvée
   sans écrivains, obtenir un dump complet frais vérifié, le restaurer entièrement
   dans une instance jetable indépendante de production et Neon; vérifier toutes
   les tables, contraintes/triggers, comptes, PK et valeurs. Conserver digest,
   taille, rapport privé et stratégie de retour arrière exploitable.
   `pg_restore --list` seul ne suffit jamais. Cette répétition complète reste à faire.
   Dump, liste et restauration utilisent les clients versionnés PostgreSQL 18
   par chemin absolu, jamais `/usr/bin/pg_dump` ni `/usr/bin/pg_restore`.

   **Clients PostgreSQL 18.** Sur la VM, `/usr/bin/pg_dump` et
   `/usr/bin/pg_restore` sont le `pg_wrapper` Debian : sans `--cluster`/`PG*`, il
   choisit la version d'après la configuration locale des clusters (constaté :
   `/usr/bin/pg_restore --version` sélectionne 17.10, l'ancien cluster 17 occupant
   le port par défaut 5432), jamais d'après l'archive ou la cible 18 sur 5433. Un
   client 17 ne peut pas dumper un serveur 18 et n'est pas garanti pour lire une
   archive 18. Le code CCA-EOB appelle donc `database_backup.verify_archive(...,
   client_major=18)`, qui exige `/usr/lib/postgresql/18/bin/pg_restore` : fichier
   régulier non lié, propriétaire root, sans écriture groupe/autres, exécutable,
   ancêtres répertoires réels root sans écriture groupe/autres, et `--version`
   rapportant exactement la majeure 18 (sonde bornée à 30 s, environnement sans
   `PG*`). Tout écart refuse la release, sans repli vers `/usr/bin`. Précontrôle
   opérateur en lecture seule :

   ```sh
   /usr/lib/postgresql/18/bin/pg_dump --version
   /usr/lib/postgresql/18/bin/pg_restore --version
   namei -l /usr/lib/postgresql/18/bin/pg_restore
   stat -c '%F %U:%G %a %h %n' /usr/lib/postgresql/18/bin/pg_dump /usr/lib/postgresql/18/bin/pg_restore
   ```

   Attendu : versions `18.x`, fichiers réguliers `root:root` sans écriture
   groupe/autres, ancêtres root non inscriptibles. Sinon STOP. Le dump frais est
   produit avec `/usr/lib/postgresql/18/bin/pg_dump --format=custom` vers le port
   5433 et vérifié/restauré avec `/usr/lib/postgresql/18/bin/pg_restore`; noter
   les versions des deux clients dans le rapport privé de restauration.
5. **Attestation indépendante de cible** : root collecte les vrais champs ci-dessous,
   jamais copiés aveuglément d’un runtime, puis installe le document au chemin fixe.
6. **Plan frais, revue indépendante de toute la matrice et release root** : suivre
   les phases ci-dessous, sans recycler un hash local ou des comptes attendus.
7. **Approbation SQL indépendante** : générer `approve` depuis les copies revues
   du document **et** de la release, avec expiration explicite future et timezone;
   relire/digester le SQL puis obtenir l’accord avant son INSERT comme `postgres`.
8. **Décision finale immédiatement avant apply** : après accord explicite distinct,
   générer et revoir `authorize-apply`, puis faire installer son UPDATE comme
   `postgres`, avec échéance opérateur explicite future, au plus cinq minutes et
   jamais après l’expiration du plan. Ensuite seulement, apply comme runtime,
   vérification, clôture et retrait des capacités séparément autorisé. Aucun contournement,
   renouvellement, retry, rollback, import d’approbation ou apply automatique.

### Attestation cible

`/etc/polypbase/cca-eob-production-target.json` : root:root, lisible selon besoin
(0644 maximum), `/etc/polypbase` root:root 0755 maximum; aucun symlink, hardlink
multiple ni ancêtre group/world-writable. `IDENTITY_SQL` dans l’intégration donne
les champs SQL; vérifier aussi `/etc/machine-id` et
`stat -c '%u %g' /run/postgresql`. OID, machine-id et démarrage sont inconnus ici.
Ce modèle contient des instructions, **pas un fichier utilisable** : remplacer
les chaînes indicatives par les vrais types numériques/objets requis.

```json
{
  "schema": "cca_eob_target_v2",
  "machine_id": "<machine-id réel, 32 hex minuscules>",
  "configured_host": "/var/run/postgresql",
  "configured_port": "5433",
  "database": "polypbase",
  "role": "polypbase_cca_eob",
  "server_address": null,
  "server_port": null,
  "database_oid": "<entier réel, pas une chaîne>",
  "role_oid": "<entier réel, pas une chaîne>",
  "postmaster_started_at": "<UTC YYYY-MM-DDTHH:MM:SS.ffffffZ>",
  "server_version_num": "<entier PostgreSQL 18 réel>",
  "socket_uid": "<UID réel de /run/postgresql, entier > 0>",
  "socket_gid": "<GID réel de /run/postgresql, entier > 0>"
}
```

### 1. Produire le dry-run réel après autorisation

Depuis `/srv/polypbase/app/backend`, **uniquement comme `polypbase_cca_eob`**, pas
root ni `polypbase`, sans capacités, avec `NoNewPrivs=1`. Ne pas hériter des secrets
ou de l’environnement du service : ces settings CLI n’utilisent pas le `.env`.
Le lanceur privilégié doit abandonner ses droits avant d’exécuter le Python
applicatif (`runuser -u polypbase_cca_eob -- setpriv --no-new-privs -- ...`).
`WORKBOOK`, `PLAN`, `AFTER`, `ACTOR`, `HASH` sont des placeholders privés à remplacer,
pas des arguments réels connus; sorties exclusives dans un répertoire privé 0700.

```text
setpriv --no-new-privs -- ../.venv/bin/python manage.py cca_eob_production plan --production --settings=config.cca_eob_production_settings --organization-id 1 --actor-id ACTOR --target /etc/polypbase/cca-eob-production-target.json --workbook WORKBOOK --plan-file PLAN
```

Le stdout donne hash/résumé; PLAN contient matrice entière, cellules, baseline,
recette et gardes. Un plan bloqué est conservé mais la commande échoue. Faire
revoir correspondances, valeurs, hors-source, révisions, effets environnementaux,
audits, acteur et cible. Ne publier ni JSON ni données dans Git/chat/ticket public.

### 2. Préparer la preuve indépendante de release root privée

Après sauvegarde fraîche, restauration complète et plan frais revu, l’opérateur
privilégié distinct installe
`/etc/polypbase/cca-eob-production-authorization.json` : root-owned, **privé**, avec
lecture du seul runtime nécessaire, aucun droit d’écriture runtime et ancêtres
root protégés. Ne pas le rendre public pour résoudre un refus de permission;
vérifier les contraintes du lecteur d’artefacts. Par exemple, un fichier 0640
root:groupe-dédié-runtime permet la lecture ciblée sans écriture, sous réserve
d’ancêtres traversables et protégés. Même discipline pour les copies
de revue sous `/root`. Modèle indicatif :

```json
{
  "schema": "cca_eob_release_v1",
  "target": "<objet EXACT de la cible, pas une chaîne>",
  "plan_hash": "<HASH réel de toute enveloppe revue>",
  "actor_id": "<ACTOR entier réel>",
  "archive": {
    "path": "<chemin absolu privé du dump frais>",
    "sha256": "<SHA-256 réel du dump>",
    "size_bytes": "<entier réel>"
  },
  "restoration_verified": true,
  "restore_report": "<référence privée du rapport complet>",
  "review_ref": "<référence de la revue indépendante et de son approbation>"
}
```

Le validateur Python partagé `validate_release()` et la garde SQL exigent
**exactement les huit clés** `schema`, `target`, `plan_hash`, `actor_id`, `archive`,
`restoration_verified`, `restore_report`, `review_ref`. Le schéma est
`cca_eob_release_v1`; cible, acteur entier positif et hash sont liés exactement
au plan. `restoration_verified` vaut strictement `true`; les références de rapport
et de revue sont des chaînes non vides. `archive` contient **exactement** `path`,
`sha256`, `size_bytes` :

- `path` est un chemin de fichier POSIX absolu canonique, pas `/`, sans `//`,
  composant `.` ou `..`, slash final, antislash ni caractère de contrôle;
- `sha256` contient exactement 64 caractères hexadécimaux minuscules;
- `size_bytes` est un entier, jamais un booléen, strictement positif et au plus
  `9223372036854775807` (maximum BIGINT).

La release stockée et celle du JSON final doivent être entièrement identiques,
pas seulement partager leur hash ou leur cible. Une archive incomplète ou
malformée bloque aussi le writer SQL direct.

**Une métadonnée de format valide peut néanmoins décrire de mauvais octets sur
disque. SQL ne peut vérifier ni les octets physiques de l’archive ni la réalité
de la restauration complète.** L’opérateur de confiance doit effectuer les vrais
contrôles; les artefacts root protégés et les vérifications Python restent
obligatoires et inchangés. `true` est une attestation humaine root, jamais à
déclarer avant restauration.
Le code revérifie le fichier réel, taille, digest et `pg_restore --list` (client
PostgreSQL 18 versionné, contrôlé comme ci-dessus) avant et sous verrous, pas le contenu humain du rapport. Dump et ancêtres protégés,
répertoire final privé 0700 root/runtime-owned. La release lie backup, cible,
acteur et hash et doit être identique à celle importée dans l’approbation SQL.
Toute dérive impose arrêt, nouveau plan/revue/preuve; pas de sauvegarde ancienne
recyclée. Ne pas modifier un fichier runtime pour lui donner une autorité root.

### 3. Approuver le plan via approve, puis INSERT administratif distinct

L’opérateur indépendant prépare sous `/root/cca-eob-review` les **copies vérifiées
et revues** du document de plan et de la release root. Le générateur ne doit pas
lire comme autorité un fichier runtime susceptible de changer après revue.
`approve` lit séparément `--document` et `--release`, contrôle le fingerprint Python
complet, le schéma v2, le périmètre prêt et la liaison release/cible/acteur/hash.
`--expires-at` est **obligatoire**, sans défaut : instant futur ISO 8601 avec
fuseau explicite (`Z` ou décalage). L’échéance réelle reste à décider, ne pas
l’inventer. Elle est stockée dans la ligne SQL, pas ajoutée au schéma de release.

Exemple non exécutable tel quel (`HASH`, `EXPIRY_WITH_TIMEZONE` à remplacer;
chemins sous `/root` à créer/stager délibérément) :

```text
/usr/bin/python3 -I /root/cca-eob-reviewed-bundle/deploy/scripts/cca_eob_role_sql.py approve --document /root/cca-eob-review/plan.json --release /root/cca-eob-review/release.json --reviewed-hash HASH --expires-at EXPIRY_WITH_TIMEZONE > /root/cca-eob-role/approve.sql
sha256sum /root/cca-eob-role/approve.sql
```

Cette action **émet seulement du SQL**, sans connexion. Après succès, revue
indépendante des octets exacts, comparaison des digests et accord explicite pour
l’insertion d’approbation, importer :

```sh
runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -h /var/run/postgresql -p 5433 -d polypbase < /root/cca-eob-role/approve.sql
```

C’est un **INSERT, jamais un upsert** : toute ligne singleton existante, même
consommée/expirée, fait échouer. Ni `provision`, ni `approve`, ni Django ne
suppriment/remplacent automatiquement une approbation. Une éventuelle suppression
administrative exige décision séparée, conservation des preuves et nouvelle revue.
Aucun import d’approbation ne vaut accord implicite pour apply. Cet INSERT laisse
`final_authorization`, `final_authorized_at` et `final_expires_at` à NULL : même
un appel SQL direct du writer avec le bon hash doit encore être refusé.

### 4. Nouvelle autorisation explicite immédiatement avant apply

Présenter cible, acteur, hash, matrice/recette, sauvegarde fraîche/rapport,
digest SQL importé, expiration du plan encore valide, compatibilité `main`, fenêtre
et retour arrière. Obtenir **l’accord explicite de cette phase seulement** et une
**échéance opérateur réelle**, future avec fuseau, au plus à cinq minutes et jamais
après celle du plan. Cinq minutes est un plafond technique étroit, ni une échéance
choisie automatiquement ni une autorisation métier par défaut.

Générer `authorize-apply` depuis les mêmes copies indépendamment revues du plan et
de la release, par la voie hors ligne ou le bundle root vérifié décrits plus haut.
La génération ne se connecte pas; ne jamais exécuter le checkout live en root ni
piper le générateur vers `psql`. `--document`, `--release`, `--reviewed-hash` sont
les mêmes arguments que pour `approve`; `--expires-at` est obligatoire.
`FINAL_EXPIRY_WITH_TIMEZONE` ci-dessous doit être remplacé par la vraie échéance,
sans inventer de valeur dans ce runbook :

```text
/usr/bin/python3 -I /root/cca-eob-reviewed-bundle/deploy/scripts/cca_eob_role_sql.py authorize-apply --document /root/cca-eob-review/plan.json --release /root/cca-eob-review/release.json --reviewed-hash HASH --expires-at FINAL_EXPIRY_WITH_TIMEZONE > /root/cca-eob-role/authorize-apply.sql
sha256sum /root/cca-eob-role/authorize-apply.sql
```

Vérifier le code retour, faire relire les octets SQL complets, consigner et
recomparer leur digest immédiatement avant import. Après l’accord explicite
pour cette mutation administrative distincte, root transmet seulement le SQL revu
sur stdin à `postgres` :

```sh
runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -h /var/run/postgresql -p 5433 -d polypbase < /root/cca-eob-role/authorize-apply.sql
```

L’UPDATE doit affecter exactement la ligne du plan revu, non consommée et sans
autorisation finale préexistante; sinon STOP. Aucun INSERT, upsert, renouvellement
ou réinstallation, même expirée. Conserver la preuve et l’échéance installées.
**Seulement après cette installation finale distincte**, comme runtime :

```text
setpriv --no-new-privs -- ../.venv/bin/python manage.py cca_eob_production apply --production --settings=config.cca_eob_production_settings --organization-id 1 --actor-id ACTOR --target /etc/polypbase/cca-eob-production-target.json --workbook WORKBOOK --plan-file PLAN --expected-plan-hash HASH --authorization APPLY_CCA_EOB_ORGANIZATION_1:HASH
```

Le token CLI n’est ni secret ni autorité : approbation SQL du plan **et décision
finale administrative encore valide**, release root, permissions Admin rechargées
sous verrous, snapshot courant et identité live sont requis ensemble. Un appel
SQL direct ne dispense pas de la décision finale. La revérification Python de
l’archive sous verrous peut durer jusqu’à 600 s : si elle dépasse l’échéance finale
(plafond de 300 s), le refus SQL et le rollback sont la sécurité attendue. Ne pas
relancer ni prolonger automatiquement pour contourner cet échec. Aucun bypass ou
auto-approval pour résoudre un refus.

L’installation de `authorize-apply` est la **dernière décision d’autorisation** :
dès son commit, le login dédié peut appliquer exactement cette opération, y
compris par appel SQL direct. Ne pas l’installer en attendant un autre consentement
qui ne serait vérifié que par la CLI. Le plafond borne les vérifications et la
consommation dans le writer, pas l’heure d’un COMMIT arbitrairement retardé par un
client SQL direct après le retour du writer. La procédure officielle conserve sa
transaction courte/bornée et son contrôle socket; ne pas ouvrir de transaction
manuelle pour maintenir des verrous ou différer la clôture.

### 5. Vérification, clôture et retrait des capacités

Comparer toutes les tables au checkpoint : mêmes PK/valeurs sauf `box_id` approuvés,
révisions exactes, nouveaux audits individuels/de lot avec acteur, source, décision
V3 et preuves; aucun ancien audit, compte, historique, température ou autre
institution changé. Vérifier `consumed_at` par l’opérateur indépendant. Refaire
`plan` vers un **nouveau** fichier AFTER : zéro `REASSIGN_REQUIRED`, mêmes lignes
conformes/hors-source attendues. Ne pas rejouer apply en production pour le tester;
l’ancien plan est périmé et l’approbation consommée doit interdire le replay.
Conserver les preuves privées, fermer la session, rouvrir les écritures seulement
après validation et révoquer la release via une phase privilégiée autorisée.

Le retrait du provisionnement **ne défait pas la science**. Après conservation des
preuves et accord distinct, importer le `rollback.sql` revu/digesté via la même
voie `postgres` (pas un générateur live root). Première transaction : vérifier
marque/absence d’objets runtime, passer `NOLOGIN`, commit. Seconde : refuser toute
session vivante, vérifier helpers/table puis `DROP OWNED ... RESTRICT`, retirer
helpers, table d’approbation et rôle. Jamais terminer une autre session, ni CASCADE.
Si une session subsiste, le rôle reste NOLOGIN; fermer délibérément puis relancer.
Une dérive qui fait échouer la première transaction peut laisser LOGIN : ne pas
présumer la révocation réussie. Supprimer le compte Linux seulement sur accord.
Une restauration ou annulation scientifique reste une décision séparée
explicitement autorisée, jamais automatique.

## QA et limites actuelles

Tests synthétiques dans PostgreSQL 18 **neuf, jetable, local**, backend et lease
forts `config.historical_temperature_pgqa_settings`; jamais Neon/production comme
banc de test. Modules : `apps.cultures.test_cca_eob_production`,
`apps.cultures.test_cca_eob_role_provisioning`, `config.test_cca_eob_socket`.
Le module provisioning utilise `POLYPBASE_PG18_BIN` pour lancer son cluster isolé;
sans cette variable un skip ne vaut pas preuve. Le préambule de production est
vérifié en refus sur TCP; le reste du SQL émis est exercé sur le cluster jetable.

Les mocks de sources/attestations n’établissent pas la réalité du peer Linux,
UID/GID, ACL, NoNewPrivs, socket ou fichiers root de la VM. Les contrôles Linux
CCA doivent être validés et l’inspection autorisée reste obligatoire. Séparation
owner/runtime, privilèges effectifs et métadonnées restent bloquants, jamais des
suppositions. La commande Django ne provisionne rien.

## Archive des résultats et constats historiques — pas une preuve courante

Les sections ci-dessous sont conservées pour traçabilité. Leurs nombres, verdicts
locaux et expressions « aucun GRANT excédentaire » ou « non résolu » décrivent
**leurs révisions de préparation seulement**. Leur matrice avec accès directs,
fonction unique et socket PCA est supplantée par le contrat ci-dessus. La décision
de lecture est désormais résolue par le lecteur fixe. Les anciens tests verts ne
prouvent ni writer, ni approbation one-shot, ni intégration socket CCA actuelle.
Le statut de validation courant est ajouté après cette archive.

## Résultat de validation locale de cette préparation (historique)

- PostgreSQL natif **18.6**, cluster neuf à données synthétiques, backend QA fort,
  owner/runtime distincts et lease : **36 tests CCA-EOB passés**, zéro échec,
  erreur ou skip; **55 tests d'artefacts protégés passés**, zéro skip.
- Refus de cibles/permissions, dry-run PostgreSQL read-only et immutabilité de
  toutes les tables, hashes périmés, conflits, acteurs non autorisés, différences
  autorisées exactes, audit et rollback tardif vérifiés. Les clauses WHEN,
  restrictions de colonnes et bindings de fonctions dans un autre schéma sont
  rejetés sur PostgreSQL réel. Deux connexions vérifient le blocage/rollback des
  nouvelles références environnementales, doublons et températures par timeout.
- Régression supplémentaire réelle, hors mocks : rôle ordinaire non propriétaire
  avec CREATE public, membership runtime `INHERIT FALSE, SET TRUE`; refus confirmé,
  puis retrait du grant/membership/rôle et acceptation du runtime sain avant suite.
- Migrations **uniquement dans les clusters QA neufs**, contrôles Django et dérive
  de migrations verts. Clusters de cette tâche arrêtés/supprimés, ports 55439/55440
  vérifiés libres. Aucun accès VM, production, Neon ou autre cluster existant.
- Un runner délégué a également exécuté un smoke-test legacy sur SQLite en mémoire
  (117 tests, 17 skips PostgreSQL), contrairement à la restriction PostgreSQL seule
  demandée pour cette tâche. Il n'a pas été répété et ne constitue **aucune preuve
  PostgreSQL**; les résultats de référence ci-dessus proviennent exclusivement des
  suites PostgreSQL isolées et des contrôles d'artefacts sans SQL.
- Revue indépendante read-only : trois constats corrigés (bindings complets des
  triggers, propriété assumable, CREATE assumable via SET); aucun blocage de code
  restant identifié lors de la dernière relecture. La matrice de production et la
  restauration complète restent non validées et aucune autorisation n'est donnée.

### Validation du rôle dédié et de la capacité de verrouillage (historique)

- Cluster PostgreSQL natif **18.6** neuf et jetable (port local libre, jamais Neon,
  production ni cluster existant), harnais QA fort et lease : **50 tests de
  `apps.cultures.test_cca_eob_production` passés (étape précédente; voir ci-dessous pour 58), zéro échec, erreur ou skip**
  (39 existants conservés, 7 sur la fonction et les privilèges, 4 sur le SQL émis).
  Même module rejoué avec les noms de production (propriétaire `polypbase`, runtime
  `polypbase_cca_eob`) : tous passés avant la dernière retouche du script d'émission.
- Fonction : seul `polypbase_cca_eob` l'appelle (propriétaire refusé); acteur nul,
  zéro, négatif, inexistant, non admin, inactif, membership inactive ou d'une autre
  organisation refusés (42501); `REPEATABLE READ` et `SERIALIZABLE` refusés (25001);
  boîte renommée ou d'une autre organisation refusée (23514). Deux logins réels
  vérifient que les 10 familles de lignes du périmètre sont verrouillées et que 9
  lignes voisines (autre organisation, autre membre, autre boîte, autre zone) ne le
  sont pas; les deux verrous de table sont présents; tout est libéré au rollback sans
  écriture. Deux appelants simultanés se sérialisent sur le verrou de boîte (55P03),
  sans deadlock. Six altérations (invoker, `search_path`, volatilité, `EXECUTE` à
  PUBLIC, corps modifié, fonction supprimée) arrêtent le plan et l'apply avant tout appel.
- Nécessité des privilèges : pour chacun des GRANT de la matrice, la révocation
  isolée fait échouer un vrai plan ou apply en 42501 sans écriture partielle
  (hors `USAGE` sur `public`, redondant avec PUBLIC). Aucun GRANT excédentaire.
- SQL émis exécuté avec `psql -X -v ON_ERROR_STOP=1` sur le cluster jetable (base
  `polypbase`, schéma migré, préambule limité au port local de test) : `provision`,
  `verify`, relance idempotente, `rollback`, relance du `rollback` et nouveau
  `provision`. Dérives détectées par `verify` (privilège de colonne en trop,
  `UPDATE` de table, `CREATE`, appartenance au propriétaire ou à `pg_read_all_data`,
  `EXECUTE` à PUBLIC, `INHERIT`, `CREATEDB`, droit de séquence) : toutes refusées.
  `provision` et `rollback` refusent un rôle sans marque, une fonction altérée ou
  possédée par `postgres`, et une session vivante du rôle.
- Le script écrit le SQL en octets LF : un passage par `print` sur Windows avait
  produit des CRLF dans le corps de la fonction et fait refuser `rollback.sql`, ce
  qui est le comportement sûr, mais inutile à reproduire.
- Limites de ce qui est prouvé : le transport QA est TCP avec mot de passe (le
  harnais l'impose); le rôle jetable a reçu pour les tests seulement un mot de
  passe, `CONNECTION LIMIT` illimité et `EXECUTE pg_control_system()`. L'authentification
  `peer` sur socket Unix, `NoNewPrivs`, la propriété des fichiers `/etc/polypbase` et
  `pg_hba_file_rules` ne sont pas prouvés par ces tests et restent à contrôler lors
  de l'inspection read-only sur la VM. Le chemin QA historique
  (`correct_cca_eob_v2`) a été rejoué sur SQLite en mémoire; il n'a pas été rejoué
  sur PostgreSQL (sa garde n'accepte que ses cibles fixes et le port 55438 est
  interdit).
- Suite backend complète sur SQLite en mémoire (base `d1e4e73` + ces changements) :
  2241 tests, 257 skips PostgreSQL (non comptés comme couverture), **9 erreurs, toutes
  dans `test_pca_production.ProductionWorkflowTests`**, antérieures à ce travail : le
  commit `3d8136c` de `main` ajoute précisément le saut SQLite de cette classe. Sur
  `dbd595d` + ces changements, `test_cca_eob_*`, `test_pca_production`,
  `test_pca_protected_artifacts` et `test_database_backup` passent sur SQLite
  (206 tests, 40 skips PostgreSQL), et le module CCA rejoué sur PostgreSQL 18.6 par-dessus
  `dbd595d` : 50 tests passés, zéro échec, erreur ou skip.

### Constats de la revue indépendante (3 Medium, 2 Low) : corrections locales historiques

- Socket PostgreSQL : voir « Socket PostgreSQL protégée ». Tests : arguments exacts
  `(hôte, port, uid, gid)`, champs `socket_uid`/`socket_gid` exigés avant toute
  connexion, requêtes et connexion à l'intérieur de la fenêtre, altération avant ou
  après, fenêtre unique autour de tout le plan et de tout l'apply jusqu'au commit,
  altération au milieu de la transaction d'apply = annulation complète. Le vérificateur
  Linux lui-même (propriétaire, ACL, remplacement) est celui de PCA et reste couvert
  par `config.test_pca_protected_artifacts`; il n'est pas rejoué ici sur Windows.
- Lecture inter-institutions : relevés thermiques réduits à deux colonnes de lecture
  (vérifié : `id` inutile; `temperature_c` et le reste refusés); autres tables : voir
  « Décision requise », non résolu sans décision produit/sécurité.
- Scripts SQL exécutés : `apps.cultures.test_cca_eob_role_provisioning` crée une
  instance PostgreSQL 18 jetable (répertoire temporaire, port loopback libre,
  migrations réelles, détruite à la fin) et exécute les artefacts émis. Commande :
  `POLYPBASE_PG18_BIN=<répertoire bin PostgreSQL 18> python manage.py test
  apps.cultures.test_cca_eob_role_provisioning --settings=config.test_settings`.
  Sans cette variable la classe est ignorée et ne compte pas comme couverture.
  Le préambule de production (socket, port 5433, login `postgres`) est exécuté pour de
  vrai et refuse ce cluster TCP; le reste est `body()`, c'est-à-dire exactement ce que
  `emit()` ajoute après ce préambule.
- Timeouts : voir les paragraphes `transaction_timeout` et `lock_timeout` ci-dessus.
  Course NOLOGIN/sessions du retour arrière : rollback en deux transactions.
- Couverture des privilèges dangereux : `authority_sql` (réglages de rôle, objets
  larges, schémas, fonctions `SECURITY DEFINER`) dans `verify.sql` et à chaque
  connexion; ACL `EXECUTE` de la fonction dans `verify.sql`.

Résultats (PostgreSQL 18.6 jetable, base `d1e4e73` + changements par-dessus `dbd595d`) :
58 tests de `test_cca_eob_production` (harnais QA fort), zéro échec, erreur ou skip;
9 tests d'exécution des scripts SQL, zéro skip; SQLite (228 tests, 53 skips
PostgreSQL non comptés), `check` et dérive de migrations propres.

Cette préparation ne remplace ni revue indépendante, ni restauration complète,
ni sauvegarde fraîche, ni inspection read-only future, ni autorisation production.

## Validation antérieure — 2026-10-19, historique avant les deux constats

- Base de travail `d1e4e73` avec modifications CCA non committées : PostgreSQL
  **18.6 neuf et jetable**, données synthétiques, rôles séparés et lease fort.
  Dernière exécution complète de la session précédente : **70 tests
  `apps.cultures.test_cca_eob_production` + 22 tests `config.test_cca_eob_socket`
  + 7 tests `CcaEobDatabaseGuardTests` réussis : 99, zéro échec, erreur ou skip.**
  Elle inclut le correctif `original_values={}` des nouveaux audits et la
  régression du moteur ORM QA partagé.
  Le module socket utilise des interfaces Linux simulées, sans base SQLite.
- Provisionnement : **15 tests `apps.cultures.test_cca_eob_role_provisioning`
  réussis, zéro échec, erreur ou skip**. SQL réellement exécuté : provision,
  verify, rollback, import d’approbation et refus de remplacement/replay;
  préambule production exécuté en refus sur le cluster TCP jetable. Commande :
  `POLYPBASE_PG18_BIN=<bin PostgreSQL 18> uv run python backend/manage.py test
  apps.cultures.test_cca_eob_role_provisioning --settings=config.test_settings`.
- Le workflow utilise `config.historical_temperature_pgqa_settings` et
  `IsolatedTemperatureRunner`, après vérification indépendante de l’instance,
  opt-ins explicites et lease exclusif, selon le contrat QA décrit plus haut.
  L’émetteur crée ses propres clusters temporaires migrés; jamais de données ou
  d’instance préexistantes utilisées. Les clusters créés ont été arrêtés/supprimés.
- Vérifications : institutions distinctes avec données réelles synthétiques,
  accès direct refusé, arguments/GUC/hash forgés, absence/expiration/consommation
  d’approbation, dérive de son ACL, identité postmaster périmée, snapshots/recettes
  incohérents, application autorisée, champs scientifiques inchangés hors contrat,
  zéro/NULL, audits et rollback tardif, triggers, concurrence et ordre des verrous.
  Les erreurs intermédiaires socket/COST ont été corrigées puis les suites
  complètes rejouées; elles ne sont pas masquées par des skips.
- Compatibilité : export temporaire read-only de
  **`74ac83ed4f351c818618eef4b90acc3ac30e2f62`** et superposition limitée des fichiers
  CCA vérifiés par SHA-256 : mêmes **99 tests réussis, zéro skip**. Aucun merge,
  rebase, commit ou changement PCA. Exports et runners temporaires supprimés.
- `check` Django et `makemigrations --check --dry-run` réussis contre les clusters
  PostgreSQL jetables, dans le worktree et sur l’export de `main`. `git diff
  --check` propre. Ces résultats ne constituent pas une autorisation de production.
- Limite : pas de transport socket/peer Debian réel testé depuis Windows; les
  tests de refus et de système de fichiers sont simulés. Restent obligatoires :
  revue indépendante du contrat/émetteur et SQL émis, vérification de la révision
  réellement intégrée/déployée, inspection read-only autorisée, sauvegarde fraîche
  et **restauration complète** vérifiée, plan réel revu, release root privée,
  INSERT administratif approuvé avec expiration explicite, puis accord séparé
  immédiatement avant apply. Aucun de ces actes n’est autorisé par ce document.

### Complément antérieur — 2026-10-20 (historique)

La reprise a conservé l’implémentation, sans relancer les 99 tests complets dont
les preuves existaient déjà. Aucun code de production n’a changé pendant cette
validation finale; seuls quatre tests de concurrence ont été ajoutés et le présent
runbook actualisé.

- **15/15 tests SQL exécutables relancés**, zéro échec, erreur ou skip, sur
  PostgreSQL **18.6** neuf : provisionnement, vérification, import réel du SQL
  d’approbation, retrait, idempotence, refus de remplacement/prolongation,
  isolation avec deux institutions, ACL et dérives. Le préambule socket/port de
  production est testé en refus sur TCP; seul ce préambule est exclu des
  exécutions positives des mêmes corps SQL.
- **4/4 nouveaux tests du writer** réussis sur un autre cluster neuf : deux vrais
  logins runtime concurrents ne consomment l’approbation qu’une fois; expiration
  pendant l’attente d’un verrou; baseline modifiée par un écrivain concurrent;
  expiration pendant les audits après les écritures scientifiques. Les trois
  refus laissent l’approbation non consommée, sans écriture partielle.
- L’observation initiale de `pg_stat_activity` était masquée au propriétaire QA
  non privilégié : trois tests ont d’abord échoué à constater l’attente. Leur
  instrumentation utilise désormais les verrous non accordés de `pg_locks`,
  sans accorder de privilège supplémentaire ni modifier l’implémentation.
- `main` a avancé à **`761cd02c120db71ff2f3510942736fd99b50fbc1`**. Le diff depuis
  `74ac83e` ajoute le workflow de températures historiques, sans modifier les
  modèles, migrations ou dépendances CCA existants. Export temporaire read-only,
  superposition SHA-256 des seuls 12 fichiers CCA : **10/10 tests ciblés réussis**,
  zéro échec, erreur ou skip (les quatre nouveaux, application autorisée,
  isolation, expiration, replay/rollback, triggers et moteur QA partagé).
- `check` Django et `makemigrations --check --dry-run` réussis sur les deux
  clusters de workflow, worktree et export du `main` courant. Aucun merge, rebase,
  changement PCA, commit ou accès VM/Neon/production. Clusters et export détruits;
  runner temporaire retiré. `git diff --check` final propre.

**Aucun constat critique ou de correction non résolu identifié lors de cette
validation. Prêt pour revue indépendante, pas pour exécution de production.**
Les limites socket/peer Linux réel, revue indépendante, sauvegarde/restauration
complète, plan frais et autorisations séparées ci-dessus restent inchangées.

## Suivi courant des deux constats indépendants — 2026-10-20

Le verdict favorable précédent est historique : il précédait ces deux constats.
Les correctifs suivants restent soumis à **re-revue ciblée indépendante**, sans
autorisation de production.

| Constat | Contrat correctif | Validation locale |
|---|---|---|
| **High — contournement de l’autorisation finale d’apply** | INSERT plan seul insuffisant; UPDATE final indépendant `postgres`, JSON complet exact, trois champs nullable, fenêtre explicite ≤ 300 s et ≤ expiration du plan, aucun renouvellement; contrôles writer avant/sous verrous et à la consommation atomique | **Corrigé, tests réussis** |
| **Medium — preuve d’archive incomplète** | Validateur Python partagé et garde SQL : huit clés release exactes, trois clés archive exactes, chemin canonique, SHA-256 et taille stricts, liaison complète au plan et à la décision finale | **Corrigé, tests réussis** |

Résultats effectivement obtenus, exclusivement sur PostgreSQL **18.6 neuf et
jetable**, données synthétiques, rôles distincts et lease du harnais fort :

- **84/84 tests `apps.cultures.test_cca_eob_production` réussis**, zéro échec,
  erreur ou skip. Sources synthétiques et IO root/archives simulées; autorisations
  SQL, transactions, triggers, verrous et données scientifiques réels.
- **27/27 tests `apps.cultures.test_cca_eob_role_provisioning` réussis**, zéro échec,
  erreur ou skip. Les SQL émis `provision`, `approve`, `authorize-apply`, `verify`
  et `rollback` sont exécutés pour de vrai. Le préambule de production est testé
  en refus sur TCP et seul ce préambule est exclu des exécutions positives.
  Deux tests SQL d’installation finale/preuve malformée ont également été rejoués
  avec succès après le dernier ajustement de diagnostic du writer.
- Commande SQL : `POLYPBASE_PG18_BIN=<bin PostgreSQL 18> uv run python
  backend/manage.py test apps.cultures.test_cca_eob_role_provisioning
  --settings=config.test_settings --verbosity=2`. Pour le module workflow,
  `IsolatedTemperatureRunner` et `config.historical_temperature_pgqa_settings`
  après vérification indépendante de l’identité, opt-ins et lease, comme décrit
  dans le contrat QA plus haut; aucune base SQLite n’a été utilisée.
- **31/31 tests ciblés réussis**, zéro skip, dans un export read-only du `main`
  courant **`761cd02c120db71ff2f3510942736fd99b50fbc1`** avec superposition
  SHA-256 des seuls 12 fichiers CCA. Sélection : `WriterCapabilityTests`,
  `RootDocumentTests`, `MinimumPrivilegeWorkflowTests`, application autorisée
  complète et relecture de release sous verrous. Aucun merge/rebase ni changement
  PCA, import de températures ou garde QA.
- `check` Django et `makemigrations --check --dry-run` réussis contre les deux
  clusters de workflow (worktree et export de main); `git diff --check` propre.
  Clusters, export et runner temporaire arrêtés/supprimés.

Ces tests couvrent les huit cas négatifs requis et le succès unique : refus avant
la décision finale malgré hash/CLI/GUC forgés; impossibilité pour le runtime
(et le propriétaire métier) d’installer/modifier/prolonger l’autorisation finale;
archives manquantes/malformées et substitutions de digest/taille/release;
expiration, replay, appels concurrents, échec d’audit/trigger et rollback de la
science, des audits et de la consommation. Valeurs zéro/NULL, auteurs, identités
et anciens audits sont préservés.

La relecture a aussi fait corriger le délimiteur du bloc SQL généré pour accepter
sans injection les chaînes contenant `$authorize_apply$`, et aligner les espaces
Unicode Python/SQL des références de preuve. Régressions exécutables ajoutées.
Des assertions de messages héritées ont été ajustées/préservées, puis la suite
complète affectée a été rejouée avec succès; aucun skip ne masque ces étapes.

Un format d’archive valide ne prouve toujours ni ses octets ni sa restauration
physique. Sauvegarde fraîche, restauration complète vérifiée, fichiers root et
socket/peer Linux réels restent à contrôler dans le workflow opérateur indépendant,
uniquement après autorisation séparée.

Aucun accès VM/Neon/production, migration de production, provisionnement, import
SQL ou apply n’est autorisé par cette mise à jour.
