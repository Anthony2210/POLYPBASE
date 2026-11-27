# QA PostgreSQL distante des révisions — TARGETED Phase5A

## Statut et périmètre

**Périmètre : TARGETED Phase5A, jamais une validation du graphe complet.**
Cinq exécutions distantes sont rapportées par l'utilisateur. Le **run #5**
(`c6c63a1`) a réussi **105/105 tests sur le périmètre 80 nœuds**, avec 0018
exclue. Le contrat intégré attendu comporte désormais **83 nœuds approuvés** :
le périmètre 81 nœuds avec `sharing.0001_initial`, plus
`cultures.0019_box_dna_validation` et le merge vide
`cultures.0021_merge_dna_and_history`. Ce dernier reste à créer séparément;
le gate refuse son absence. Ce périmètre n'a pas été exécuté à distance.
Ce document n'est ni une preuve PostgreSQL de cette version intégrée
ni une autorisation de publication ou de déploiement.
Base de la revue précédente : `649eba2`, branche `test/history-revision-postgres-qa`.
Extension ADN : merge local en cours sur `feat/box-dna-validation`.
Aucun commit, staging, push ou lancement Actions n'est effectué par l'agent.
Cette mise à jour adapte uniquement le harnais QA, ses tests, ce document et le
workflow. Aucun modèle scientifique, migration ou moteur métier n'est modifié.

Workflow exact : **History revisions PostgreSQL QA — TARGETED Phase5A**,
`.github/workflows/history-revision-postgres.yml`.

## Déclenchement et isolation

- Uniquement `push` sur `test/history-revision-postgres-qa`; ni `main`, ni PR,
  `pull_request_target`, événement de déploiement ou dépendance à `workflow_dispatch`.
- Runner GitHub-hosted `ubuntu-24.04`, délai global de 20 minutes, token
  `contents: read`, checkout sans identifiants persistants, actions épinglées par SHA.
- Service `postgres:18`, rôle/base synthétiques `history_revision_ci`, port client
  `127.0.0.1:55440`, port serveur `5432`, stockage tmpfs détruit avec le job.
- Authentification trust sans mot de passe, uniquement dans ce service jetable.
  Aucun environnement production, secret Actions, étape deploy ou publisher.
- Conventions reprises du workflow **présent dans ce checkout**
  `historical-movements-postgres.yml` : service 18 éphémère, branche dédiée,
  préflight avant migrations, dépendances verrouillées, logs avec `pipefail`,
  artifacts synthétiques conservés sept jours même après échec.
  Les versions d'actions checkout/uv suivent aussi `ci.yml` de ce checkout.
- `ci.yml` ne lance pas les cinq suites de scripts de ce harnais. Une PR vers
  main, un push main ou un push `feat/box-dna-validation` ne remplace pas ce job.
  Le déclencheur et les gardes d'identité restent inchangés; aucune identité
  hébergée ne doit être simulée pour lancer le harnais PostgreSQL localement.

## Garde-fous fail-closed

`config.history_revision_ci_settings` vérifie avant l'import des settings applicatifs :

- contexte Actions, runner hébergé Linux, événement push et ref exacte;
- opt-in QA dédié, dotenv désactivé, opt-in PostgreSQL local désactivé;
- absence de `backend/.env` et de valeurs héritées `DATABASE_URL`, `PG*`,
  `POSTGRES_*` ou `DB_*` (seul `POSTGRES_DB` vide est requis).

La configuration est fixe : vrai backend PostgreSQL, endpoint/rôle/base ci-dessus,
`TEST.NAME=test_history_revision_ci`, aucun routeur, ni endpoint de repli.
Les connexions, verrous, requêtes et transactions inactives ont des délais bornés.
Le harnais refuse Windows avant d'importer Django.

Avant la première mutation, le préflight vérifie par lecture l'identité réelle,
PostgreSQL 18, l'inventaire des bases et la vacuité du schéma public : aucune relation,
routine ou type applicatif, aucune extension hormis plpgsql. Il refuse de nettoyer
ou réutiliser un service. Il inscrit ensuite un marqueur synthétique sur la base.

Le runner Django vérifie le marqueur de la base de départ puis l'identité de la
base de test **avant ses migrations**, après création et aux reconnexions du runner.
CREATE/DROP utilisent la base de départ marquée plutôt que la base de maintenance
`postgres`. Une base de test préexistante ou une collision pendant CREATE provoque
un arrêt, sans remplacement automatique, retry, prompt ou DROP de récupération.
`keepdb`, clones parallèles et alias supplémentaires sont interdits.

Ces contrôles protègent ce job de configuration accidentelle; ils ne constituent
pas une preuve cryptographique d'origine ni une frontière contre un propriétaire
malveillant du runner ou du schéma.

## Historique distant rapporté par l'utilisateur

Ces constats sont fournis par l'utilisateur, sans consultation distante des logs
pendant cette tâche :

1. **Run #1 :** défaut PL/pgSQL dans `cultures.0020_history_revision_guards`,
   corrigé ensuite. Ce run n'est pas une réussite de la matrice.
2. **Run #2 :** `cultures.0019_history_revision_storage` et
   `cultures.0020_history_revision_guards` exécutées avec succès, puis arrêt sur
   `measurements.0018_box_1902_preservation` avec `ContractError` :
   https://github.com/Anthony2210/POLYPBASE/actions/runs/43948285336.
   Le succès de 0019/0020 ne démontre pas le succès des catalogues, de l'inversion,
   de la réapplication ou des tests ultérieurs.
3. **Run #3 :** commit `6be1018`, périmètre ciblé appliqué (80 migrations réelles,
   0019 et 0020 incluses, 0018 exclue), puis **105 tests exécutés et
   `FAILED (errors=18)`** :
   https://github.com/Anthony2210/POLYPBASE/actions/runs/43969930237.
   Les 18 erreurs proviennent de **trois causes indépendantes** : le flush
   `TransactionTestCase` rejeté par les triggers `BEFORE TRUNCATE` de
   `measurements.0012` (9 tests, dont 7 erreurs de cascade au `setUp` suivant),
   `DataError` PostgreSQL sur une clé de 65 caractères dans un `varchar(64)`, et
   un 301 HTTPS sur le helper HTTP du test codec.
4. **Run #4 :** commit `0272b3e`, 105 tests exécutés, **104 réussis, 1 échec**
   dans le test codec : l'assertion de lecture seule refusait la requête
   PostgreSQL `(SELECT ...) UNION ALL (SELECT ...)`. Les 9 tests PostgreSQL
   spécifiques, dont l'isolation et les courses, réussissaient :
   https://github.com/Anthony2210/POLYPBASE/actions/runs/43980820110.
5. **Run #5 :** commit `c6c63a1`, **105/105 réussis**, 80 migrations réelles
   validées, 0019/0020 inversées puis réappliquées, 0018 exclue :
   https://github.com/Anthony2210/POLYPBASE/actions/runs/43983041991.
   Ce run est la preuve PostgreSQL du périmètre 80 nœuds. Il ne contenait pas
   `sharing.0001_initial` et ne valide donc pas le graphe intégré 81 nœuds.

**État courant :** le run #5 est le dernier résultat PostgreSQL réussi, sur le
périmètre 80 nœuds de `c6c63a1`. La QA intégrée 83 nœuds attendue, avec
`sharing.0001_initial`, ADN et merge vide, reste à exécuter à distance.
L'exclusion de 0018 isole cette QA synthétique; elle ne corrige ni ne valide son
contrat métier.

## Gate de périmètre et migrations ciblées

Le succès du gate précédemment rapporté concernait le périmètre antérieur,
pas l'extension ADN. Le nouveau gate exige la présence du merge vide et refuse
le graphe à deux feuilles. La fermeture transitive de
`cultures.0020_history_revision_guards` reste distincte du nouveau target merge :
**33 migrations réelles** : `cultures` sur la branche historique (20),
`taxonomy` 0001–0009 (9), `organizations` 0001–0002 (2), `auth.0001_initial` (1)
et `contenttypes.0001_initial` (1). Il ne s'agit pas d'une preuve d'exécution SQL.

Cette fermeture seule ne suffit pas : les fixtures de snapshot de tous les modèles
nécessitent tous les schémas gérés, y compris les tables M2M auto-créées. Les
**12 cibles explicites** retenues sont :

| Application | Cible |
| --- | --- |
| accounts | `0008_interface_language_japanese` |
| admin | `0003_logentry_add_action_flag_choices` |
| audit | `0005_auditlog_deletion_action` |
| auth | `0012_alter_user_first_name_max_length` |
| contenttypes | `0002_remove_content_type_name` |
| cultures | `0021_merge_dna_and_history` |
| exports | `0001_initial` |
| measurements | `0017_bucket_probe_last_measured_index` |
| organizations | `0002_organization_portable_id` |
| sessions | `0001_initial` |
| sharing | `0001_initial` |
| taxonomy | `0009_localstrainnumbercounter` |

L'union attendue de leurs fermetures contient **83 migrations** : accounts 8,
admin 3, audit 5, auth 12, contenttypes 2, cultures 22, exports 1, measurements 17,
organizations 2, sessions 1, sharing 1 et taxonomy 9. Le merge vide compte comme
un nœud réellement appliqué avec callbacks et recorder, sans SQL métier.
Dans l'inventaire attendu de **84 migrations**, seule
`measurements.0018_box_1902_preservation` est exclue. Le gate doit vérifier les
modèles historiques contre runtime, y compris `BoxDnaValidation` et ses FK Box/auteurs;
aucun filtre de modèle ne contourne cette exigence. Ces vérifications ne prouvent
ni les contraintes/index ADN ni sa concurrence, et ne remplacent pas l'inspection
PostgreSQL réelle.

Le nouveau module local `backend/config/history_revision_ci_migrations.py` fige
les **83 noms**, les cibles et l'exclusion; il ne les déduit pas des seuls numéros
ni des feuilles courantes. Les dépendances déclarées et parents résolus des deux
0019, de 0020 et du merge sont épinglés exactement. Un parent supplémentaire
même déjà approuvé, `run_before` ou remplacement est refusé. Le merge doit avoir
`operations=[]`, `initial=None`, `atomic=True`, avec exactement ADN et 0020 comme
parents. Les autres nœuds conservent les contrôles de fermeture existants : ce
n'est pas un manifeste de toutes les arêtes du graphe. Toute modification de
l'inventaire, des dépendances, remplacement ou conflit exige une nouvelle revue.
`migrate_scoped()` utilise la
vraie commande Django `migrate` avec un adaptateur d'exécuteur, conserve les signaux
`pre_migrate`/`post_migrate` et refuse fake/fake-initial. Il compare plan,
callbacks d'exécution réellement commencés/terminés et historique final; un simple
recorder rempli ne prouve pas l'exécution. Il vérifie aussi modèles historiques
contre runtime, tables gérées, colonnes exactes et FK, y compris M2M et cibles de FK.
Ces contrôles ne remplacent pas ceux des fonctions, triggers et contraintes Phase 5A.
L'adaptateur est lié à Django 5.2.17; l'identité du service et le cycle de vie des
bases restent la responsabilité des appelants.

Le runner intercepte uniquement l'appel `migrate` de `BaseDatabaseCreation`
pendant la création de la base de test. Il exige l'appel attendu, une seule fois,
vérifie l'identité puis exécute `migrate_scoped(fresh=True)`, sans syncdb.
L'interception est restaurée immédiatement; les gardes de connexion et de teardown
restent actives. Le harnais utilise le même périmètre pour l'application initiale
et la réapplication après inversion du merge vide puis de 0020. Le harnais
revalide le gate avant cette inversion, exige le recorder approuvé moins ces deux
nœuds et conserve les deux stockages 0019. La réapplication exige exactement
0020 puis merge dans le plan, les callbacks commencés/terminés et le recorder final.
Les catalogues des gardes restent comparés avant/après; le rendu SQL reste fondé
sur l'état historique à 0020, pas sur le merge. Les signaux Django sont conservés.

Django `StateApps` reconstruit les PK implicites des tables M2M avec le
`DEFAULT_AUTO_FIELD` global, contrairement à l'AppConfig runtime d'auth. La
comparaison normalise uniquement la largeur AutoField/BigAutoField de ces PK
auto-générées; noms de colonnes, relations et autres champs restent stricts.
Ce n'est pas une équivalence des types SQL de toutes les tables en production.

Le workflow ajoute trois contrôles offline :
`test_history_revision_ci_migrations.py`, `test_history_revision_ci_isolation.py`
et `test_history_revision_ci_responses.py`. Ils exécutent de vraies migrations
Django sur SQLite `:memory:`, des doubles de catalogue et des réponses HTTP, dans
des processus isolés, sockets interdits, sans settings applicatifs ni dotenv. Ces
contrôles ne valident pas PL/pgSQL, les verrous, les triggers ni les catalogues
PostgreSQL. Leur ajout au workflow ne signifie pas qu'ils ont déjà réussi dans
Actions.

## Correction des trois échecs du run #3

### 1. Isolation des `TransactionTestCase`

Django nettoie ces tests avec `TRUNCATE`. `measurements.0012` et `0013` installent
des triggers `BEFORE TRUNCATE` sur les tables scientifiques historiques; ces
triggers sont corrects et **ne sont ni désactivés ni modifiés**. Le flush échoue,
les fixtures restent, et le test suivant échoue sur des noms d'organisation
duplicés.

`backend/config/history_revision_ci_isolation.py` remplace ce nettoyage, pour le
seul PostgreSQL jetable, par un reset complet :

1. le runner crée, après les migrations ciblées et avant tout test, un template
   vide `test_history_revision_ci_template` à partir de la base de test;
2. après chaque `TransactionTestCase`, la base de test est fermée, supprimée par
   son nom exact puis recréée depuis ce template;
3. la connexion est rétablie et son identité est revérifiée avant le test suivant;
4. le template est supprimé au teardown, uniquement si son OID créé par ce run
   correspond encore et si son propriétaire est le rôle QA.

Le reset conserve les commits réels, les connexions concurrentes indépendantes,
les FK différées et les gardes d'immutabilité. Il n'appelle pas `flush`, ne
supprime aucune ligne protégée, n'utilise pas `session_replication_role`, ne
désactive aucun trigger, n'utilise pas `WITH (FORCE)` et ne supprime aucune base
inconnue : un nom, un propriétaire ou un OID inattendu fait échouer le job. Le test
de base est lui-même identifié par son OID entre les resets. La connexion de
maintenance marquée et ses contrôles d'identité restent utilisés. Les classes de
concurrence restent des `TransactionTestCase`.

### 2. Exception SQL PostgreSQL

`test_derived_identity_sql_uniqueness_and_key_shape` insère une clé dérivée de
65 caractères. PostgreSQL applique `varchar(64)` avant le trigger de forme et
lève `DataError`, alors que SQLite et les clés de 63/64 caractères lèvent
`IntegrityError`. Seule la valeur de 65 caractères accepte désormais
`(DataError, IntegrityError)`; les autres valeurs continuent d'exiger
`IntegrityError`. Le schéma et les triggers restent inchangés.

### 3. Réponse HTTP du test codec

Le harnais tourne via un script, donc `RUNNING_TESTS` est faux et les réglages de
production activent `SECURE_SSL_REDIRECT`. Le helper partagé envoyait une requête
HTTP : `SecurityMiddleware` répondait **301** avant la vue, sans `Cache-Control`
ni `Vary`. Ce n'est pas un défaut produit. Le test codec envoie maintenant sa
requête avec `secure=True`, vérifie d'abord le statut avec un diagnostic complet,
puis conserve les assertions `Cache-Control`, `Vary` et lecture seule. Aucune
assertion de sécurité HTTP n'est retirée.

## Matrice distante ciblée prévue

| Étape | Assertions et preuves |
| --- | --- |
| Préflight | SHA exact, identité, versions PostgreSQL/psycopg/libpq, base initialement vide |
| TARGETED Phase5A | 12 cibles, fermeture figée de 83 noms; ADN, histoire 0019/0020 et merge réellement non appliqués au départ; migrate sans fake, callbacks réels et recorder exact; aucune migration restante **dans ce périmètre**, 0018 explicitement exclue |
| Drift | `makemigrations --check --dry-run` sur le service |
| Catalogues initiaux | Dix triggers et fonctions activés; corps installés comparés à 0020 et attributs d'exécution vérifiés; contraintes nommées, expressions et colonnes; index uniques partiels; cibles et différabilité des FK |
| Inversion | Plan exact **merge vide puis 0020**; maintien des deux stockages 0019 et de tout autre nœud approuvé; absence des dix triggers et fonctions |
| Réapplication | Plan/callbacks exacts **0020 puis merge vide**; recorder approuvé restauré; catalogues restaurés identiques |
| Isolation | Reset depuis un template migré vide après chaque `TransactionTestCase`; aucun `TRUNCATE`, trigger désactivé ou base inconnue; OID du template et de la base de test vérifiés; preuve `isolation.json` |
| Intégrité | 34 tests de modèles/stockage actuellement découverts : ORM immutable, SQL UPDATE/DELETE, heads valides/invalides, prédécesseurs, unicité séquence/idempotence, institutions, suppression Box/auteur et attribution conservée |
| Codec/composition | 14 tests existants de codec/intégration et 47 de composition pure; absence de consommation active de l'overlay |
| Transactions PostgreSQL | Neuf tests du socle et une régression supplémentaire des fonctions installées, décrits ci-dessous |

`sqlmigrate` enregistre le DDL de 0019; pour 0020, RunPython est opaque à sqlmigrate.
La preuve de son exécution multi-statements avec le driver installé est le succès
réel de migrate, les fonctions/triggers extraits des catalogues et leurs tests
comportementaux, jamais la seule génération de SQL.

### Concurrence et transactions

`test_history_revision_postgres_ci.py` utilise `TransactionTestCase` et des connexions
thread-local distinctes. Quatre courses vérifient trois PID distincts (coordinateur
et deux écrivains), attendent un blocage réellement observé par `pg_blocking_pids`,
puis vérifient les données après commit/rollback :

1. Deux successeurs concurrents : un seul commit; rejet unique `23505` du perdant.
2. Premier successeur annulé : le second reprend les slots uniques et avance le head.
3. Deux heads successifs : avancement vers le troisième élément après attente.
4. Réécriture périmée après attente : rejet structurel `23514`, aucun recul du head.

Les barrières, sondages, SQL, connexions et joins ont des limites explicites.
Deadlock `40P01`, lock timeout `55P03` et statement timeout `57014` sont des échecs,
jamais assimilés à un conflit attendu. Les fichiers `concurrency-*.json` contiennent
les PID, SQLSTATE et résultats persistés uniquement après les assertions finales.

Les autres tests vérifient : rollback atomique identité/révision/head puis retry;
FK différée invalide rejetée au commit avec rollback; référence réparée avant commit;
garde de prédécesseur immédiat malgré FK différées; préservation après commit/replay
de toutes les tables sources synthétiques. Ce dernier test inclut BoxMovement,
BoxLocation, lignées, observations biologiques, sondes, occupation courante,
auteurs et audits. Zéro, null et absence de relevé restent distincts.
La fixture PostgreSQL utilise des identifiants d'institution auto-alloués.

### Corrections ciblées après revue indépendante

Le socle requis est figé par **103 identités explicites**, et non par le seul total :
33 stockage, 14 codec/intégration, 47 composition, 9 PostgreSQL. `build_suite()`
refuse un module ou test requis absent, les erreurs du loader et `_FailedTest`,
avant la création de base. Le résultat enregistre les identités commencées,
terminées et réussies : chacune des 103 est exigée à ces trois étapes.
Des tests supplémentaires sont autorisés, mais ne compensent jamais une identité
manquante. Échec, erreur, skip ou expected failure font toujours échouer le job.
La commande Django propage un résultat rejeté en code de sortie non nul.

Le validateur régénère les dix corps de fonctions en appelant **le code de 0020**
avec les modèles historiques du graphe de migrations et un éditeur qui capture
le SQL sans l'exécuter. Il compare le corps complet à `pg_proc.prosrc`, que
PostgreSQL conserve tel que fourni entre les délimiteurs SQL, à l'espace extérieur
près. Il ne compare pas la déclaration reformatée par `pg_get_functiondef` et ne
maintient aucune copie indépendante des prédicats. Langage, signature, attributs
d'exécution et lien OID entre trigger et fonction publique sont aussi contrôlés.
Les OID sont validés en direct mais exclus de la comparaison avant/après
réapplication, puisqu'ils sont réalloués. Les triggers doivent être BEFORE ROW,
avec l'événement exact, sans WHEN, UPDATE OF, arguments ou substitution par un
constraint trigger. Aucun contrôle existant n'est retiré.

Une régression PostgreSQL supplémentaire altère les vraies fonctions dans des
transactions annulées : prédicat modifié, condition auteur retirée, retour changé,
retour anticipé avec `RAISE EXCEPTION` toujours présent, attribut SECURITY DEFINER
fonction supprimée et trigger avec WHEN (false). Chaque altération doit être rejetée, puis le catalogue
restauré vérifié. **Elle est collectée mais non exécutée localement.** La découverte
réelle actuelle comporte **105 tests : 34 modèles/stockage, 14 codec,
47 composition et 10 PostgreSQL**. Le socle figé de **103 identités** reste inchangé;
les deux ajouts ne compensent jamais une identité requise absente. Toutes les
identités collectées, y compris les ajouts, doivent démarrer, terminer et réussir.
La découverte
n'est pas une preuve d'exécution ni de réussite de ces 105 tests.

## Lecture des résultats Actions

L'artifact `history-revision-postgres-<run_id>-<run_attempt>` ne contient que :
SHA, logs du harnais/tests synthétiques, DDL, identité/versions, catalogues,
résultats de migrations, compteurs de tests et résultats de concurrence.
Pas de dump d'environnement, credentials, sauvegarde ou données de production.

`tests.json` distingue run/passed/failed/errors/skipped/expectedFailures/
unexpectedSuccesses et les listes d'identités collectées, commencées, terminées,
réussies ou manquantes. Un skip ou expected failure fait échouer la matrice distante.
Une étape échouée reste échouée; les logs conservent l'erreur du driver/migration.
Les étapes de résumé/upload utilisent `always()` sans `continue-on-error`.
**Artifact ou résumé seul n'est jamais une preuve de succès.** Si un JSON manque,
l'étape correspondante n'est pas prouvée; inspecter les conclusions du job et logs.
Une annulation ou panne du runner peut aussi interrompre l'upload.

Si une migration révèle un défaut PostgreSQL réel : conserver la reproduction
synthétique, arrêter et demander une correction ciblée séparée. Ne pas modifier
0019/0020, désactiver un garde ou assouplir un test pour obtenir du vert.

## Validation locale initiale

Les validations locales historiques ci-dessous n'ont utilisé ni Docker, ni
PostgreSQL, ni service distant, ni Actions, ni base partagée. Elles précèdent les
runs distants rapportés plus haut et ne valident pas la version ciblée actuelle.
Les commandes Django sont lancées dans un processus enfant avec
`POLYPBASE_TEST_POSTGRES=0`, `PYTHON_DOTENV_DISABLED=1`, `DJANGO_DEBUG=1` et
`DJANGO_SETTINGS_MODULE=config.preflight_settings`; une assertion exige SQLite
`:memory:` avant tout check/test.

- Python 3.12.13 : Django check sans problème, drift sans changement.
- Suite Phase 5A + module PostgreSQL : **103 découverts, 94 réussis,
  9 ignorés exclusivement car PostgreSQL requis**, aucun échec.
- Contrôles hors base : **22 tests settings/workflow** et **17 tests de catalogue/
  harnais** réussis en groupes ciblés. PyYAML 6.0.3 est un outil éphémère, pas une
  nouvelle dépendance de production.
- Une première commande complète de tests statiques a atteint 180 s sous Windows;
  les groupes ciblés suivants ont terminé. Un premier probe Python avait échoué
  faute de PyYAML, avant toute validation Django/base.
- Syntaxe Python, YAML et whitespace des fichiers nouveaux vérifiés; `git diff --check`
  complété par un contrôle explicite des fichiers non suivis.

## Validation locale des corrections de preuves

Sur Python 3.12.13 / Django 5.2.17, sans PostgreSQL ni Docker :

- 30 tests settings/workflow/collecte réussis, aucun skip : socle et extras acceptés;
  vide, module absent, test renommé/manquant, erreurs de collecte, skip, failure,
  error, failfast et compteur masqué rejetés; propagation du code d'échec vérifiée.
- 22 tests catalogues/harnais réussis, aucun skip : corps altérés, conditions retirées,
  fonctions absentes/remplacées/surchargées, mauvaise association OID et attributs
  changés rejetés. Les dix corps réels de 0020 sont rendus hors base, avec connexions
  et sockets interdits; la conservation réelle par PostgreSQL reste à tester.
- Collecte Django réelle des quatre modules : 105 identités, toutes les 103 requises
  présentes. Cette collecte n'exécute aucun test PostgreSQL.
- Django check et drift sur preflight SQLite mémoire : sans problème/changement.
- 94 tests Phase 5A SQLite réussis, aucun skip. Les dix tests PostgreSQL ne sont pas
  sélectionnés pour exécution locale.

## Validation locale du périmètre ciblé

Sans PostgreSQL, Docker, connexion distante ni Actions :

- Découverte Django réelle : **105 identités**, 95 SQLite-compatibles et 10
  PostgreSQL; socle figé de 103 conservé sans modification.
- **26 tests** de graphe, migrations et schéma réussis sur SQLite `:memory:`,
  sockets interdits; migrations réelles, signaux, flush et reverse/reapply inclus.
- **40 tests** settings/workflow/runner/collecte réussis, sans skip.
- **22 tests** catalogue/harnais réussis, sans skip.
- Django check sans problème et `makemigrations --check --dry-run` sans changement.
- **95 tests Phase 5A réussis, sans skip**, après application réelle des 81
  migrations ciblées sur SQLite `:memory:`. Les dix tests PostgreSQL ne sont pas
  exécutés localement; leur découverte n'est pas leur validation.
- Première invocation de la suite migrations interrompue à 180 s; relance bornée
  terminée. Deux invocations offline demandaient un package psycopg-binary absent
  du cache et ont échoué avant les tests; les contrôles SQLite ont été relancés
  sans ce driver inutile.
- YAML chargé et syntaxe des sept étapes shell vérifiée avec `bash -n`; syntaxe
  Python et whitespace vérifiés. Une dernière réinvocation combinée de ces
  contrôles après retouche du résumé a expiré à 30 s sans sortie; ce timeout
  n'est pas compté comme une validation réussie. `git diff --check` a réussi
  séparément.
- Les contrôles ont identifié et corrigé deux défauts d'intégration du harnais :
  passage du proxy Django au lieu du wrapper concret et comparaison trop stricte
  des PK M2M implicites décrite ci-dessus. Aucun contrat métier n'a été modifié.
- Corrections du run #3 : **26 tests** migrations/schéma, **40 tests**
  settings/workflow/runner, **22 tests** catalogue/harnais, **3 tests** réponses
  SQL/HTTP et **2 tests** isolation réussis hors base. La suite Phase 5A SQLite
  reste à **95 réussis, aucun skip**. Ces contrôles locaux précèdent le run #5,
  qui a ensuite validé le périmètre PostgreSQL 80 nœuds.

## Validation locale de l'extension ADN — 2026-11-17

Les cinq suites ont été lancées séparément depuis la racine, sans PostgreSQL,
sans lancement du harnais hébergé et sans mutation Git :

```sh
uv run --frozen python -B -m unittest discover -s scripts -p test_history_revision_ci.py -v
uv run --frozen python -B -m unittest discover -s scripts -p test_validate_history_revision_ci.py -v
uv run --frozen python -B -m unittest discover -s scripts -p test_history_revision_ci_migrations.py -v
uv run --frozen python -B -m unittest discover -s scripts -p test_history_revision_ci_isolation.py -v
uv run --frozen python -B -m unittest discover -s scripts -p test_history_revision_ci_responses.py -v
```

- Settings/runner : 36 réussites et 4 checks YAML ignorés faute de PyYAML.
  Ces quatre checks ont ensuite réussi séparément avec
  `uv run --frozen --with pyyaml==6.0.3 python -B -m unittest discover -s scripts -p test_history_revision_ci.py -k WorkflowTests -v`.
  Aucun ajout aux dépendances de production ni changement de déclencheur.
- Catalogue/harnais : **26 réussites**, dont le parcours positif du merge et les
  refus de plans, callbacks, recorder et merge non vide.
- Isolation : **2 réussites**; réponses SQL/HTTP : **4 réussites**.
- Migrations du checkout : après création du vrai merge vide
  `0021_merge_dna_and_history`, **32 tests réussis** sur SQLite `:memory:`, sockets
  interdits : migrations réelles, signaux, schéma, métadonnées et parents exacts,
  parent supplémentaire déjà dans la fermeture, inversion et réapplication.
  La première tentative avait atteint son délai de 120 secondes; la relance bornée
  à 600 secondes a terminé en 252 secondes. La vérification synthétique antérieure
  du merge injecté en mémoire n'est pas utilisée comme preuve du checkout.
  Aucun de ces résultats ne constitue une preuve PostgreSQL.

Le marqueur `dna_storage_retained` atteste le maintien de la migration ADN dans
le recorder pendant la répétition, pas un test complet de préservation de données
ADN. Les tests métier/ADN, le merge et les URLs sont traités séparément. Les
preuves locales antérieures ci-dessus restent historiques; elles ne sont pas
réattribuées au nouveau périmètre.

## Passation au mainteneur — après revue indépendante uniquement

1. Examiner les fichiers du périmètre ciblé, son intégration au harnais/runner et le diff complet sur
   `test/history-revision-postgres-qa`. Ne pas synchroniser/rebaser main implicitement :
   l'ascendance de cette branche et les références main peuvent diverger.
2. Après approbation, le mainteneur stage et commit personnellement les fichiers revus.
   Aucun staging/commit n'est réalisé dans cette tâche.
3. Le mainteneur pousse ensuite lui-même :
   `git push -u origin test/history-revision-postgres-qa`.
   Ce push constitue l'activation distante; pas besoin que le workflow soit sur main.
4. Ouvrir Actions → **History revisions PostgreSQL QA — TARGETED Phase5A**, vérifier le SHA exact,
   toutes les étapes, `tests.json`, les catalogues et les quatre résultats de course.
   Le résultat attendu est 105 réussites, zéro échec/erreur/skip, et aucune
   identité requise manquante. Des ajouts légitimes peuvent augmenter ce total.
5. Revue séparée des preuves avant toute décision ultérieure. Ni merge, ni deploy,
   ni activation d'un publisher ne découlent d'un résultat vert.

## Limites restantes

Le run #5 a prouvé le périmètre **80 nœuds** de `c6c63a1` : 105/105 tests,
migrations 0019/0020 inversées et réappliquées, isolation et courses PostgreSQL
réussies. Ni l'intégration précédente 81 nœuds avec `sharing.0001_initial`, ni
le nouveau contrat **83 nœuds** avec ADN et merge vide ne sont validés par ce run.
Le merge vide et les URLs sont intégrés dans le working tree; le conflit d'index
reste volontairement à marquer résolu par le mainteneur. Le reset par template et les catalogues réels de cette
version restent à vérifier sur le service PostgreSQL jetable. Même un futur succès
ne validerait ni le graphe complet, ni `measurements.0018_box_1902_preservation`,
ni une migration sur données de production. Aucun résultat de validation future
n'est considéré acquis. Le conteneur suit
le tag majeur 18 existant (patch flottant; version réelle enregistrée). Le rôle
synthétique possède les privilèges d'initialisation de l'image : pas de preuve de
permissions de production, notamment contre TRUNCATE ou désactivation de triggers.
Les courses bornées ne sont ni un test de charge ni une garantie de protocole futur
publisher. Pas de politique de publication, retries applicatifs, charge réelle ou
compatibilité toutes versions PostgreSQL démontrés. La disponibilité des runners,
des actions épinglées, de l'image et des packages reste à vérifier lors du vrai job.
