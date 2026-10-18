# Correcteur local CCA-EOB / Excel V2

## Périmètre et statut

Ce mécanisme ne concerne que les relevés ISO 2026 des codes exacts
`CCA-EOB-2.004`, `CCA-EOB-2.005` et `CCA-EOB-2.006`, dans l'organisation
explicitement fournie. Il ne corrige aucune autre famille, valeur biologique,
date, attribution d'observateur, statut ou localisation.

**Décision produit explicite d'Anthony :** V2 est la source de vérité pour
l'identité biologique des relevés 2026 concernés. Après réattribution, leur
contexte environnemental est résolu via la boîte cible par les exports et
graphiques existants, même si la zone diffère ou reste non résolue. Tous les
`BoxLocation`, `BoxMovement`, `ThermalZone` et les températures scientifiques
existants restent immuables dans cette opération, ainsi que `Box.thermal_zone`.
Aucun emplacement, mouvement, lien historique ou température n'est inféré depuis
Excel; ses blocs de température ne prouvent pas une occupation.

Ce document définit le contrat V3 implémenté. Statut final au 2026-10-18 :

- Code et tests V3 : revue indépendante verte; en attente de commit et
  d'intégration par Anthony. Les tests PostgreSQL réels (PostgreSQL 17.4 natif
  isolé, port 55439) sont verts, sans test ignoré, et le deadlock de permissions
  membership/organisation est **corrigé** (voir « Transaction, concurrence et audit »).
- Données : les 72 réattributions ont été appliquées **uniquement sur la copie
  locale de test** `polypbase_anomalies_test_20261016` (127.0.0.1:55438), après
  sauvegarde `pg_dump` restaurée et vérifiée sur une instance jetable, puis
  comparaison exhaustive de 56 tables au baseline : seuls les 72 `box_id`, trois
  révisions de boîtes et 73 audits ont changé. Auteurs historiques,
  emplacements, mouvements, zones, températures et W41/W42 sont préservés. Le
  dry-run final donne `REASSIGN_REQUIRED=0`, `ALREADY_MATCHES=80`,
  `OUT_OF_SOURCE=4`.
- Aucune application sur la référence immuable, la production ou Neon. Toute
  application hors de cette copie exige une nouvelle autorisation explicite.

**L'implémentation n'autorise pas une nouvelle application.** Un nouveau dry-run,
sa revue et l'autorisation restent obligatoires. Les
résultats et matrices propres aux données locales restent hors Git, par exemple
dans `.local-docs/` (ignoré). Ne pas versionner le classeur, les identifiants de
connexion, les données restaurées ou les sorties de diagnostic.

La seule source V2 acceptée porte le SHA-256
`0d5cd16eee88845ea51fbd4f122be15c12e5896b5237458573fc432ebda8b160`.
Les octets sont lus une seule fois, vérifiés avant parsing ZIP/XML, puis parsés
en mémoire. Le fichier n'est jamais modifié ni conservé dans un nouveau stockage.

## Correspondance et blocages

- Lecture stricte de `Semaestomeae`, lignes 121–128, identifiants littéraux,
  deux blocs de température, cellules polypes/éphyrules et en-têtes hebdomadaires.
- Exception d'en-tête W20 réutilisée depuis la décision historique existante.
  La colonne E reste un récapitulatif 2025 exclu, sans semaine inventée.
- `0` est une mesure; une paire vide est une semaine non couverte. Une paire
  partielle, deux blocs renseignés pour la même semaine ou une structure
  inattendue arrêtent la préparation.
- Le manifeste historique versionné établit, pour la même semaine et la paire
  de comptes exacte, **un seul code ancien autorisé**. Aucune recherche par
  préfixe, proximité, nom d'espèce ou simple valeur dans toute la base.
- La mesure actuelle doit être unique parmi cet ancien code et la cible exacte.
  Une attribution déjà conforme reste intacte. Une ambiguïté, un témoin absent,
  une mesure supplémentaire dans une semaine couverte ou une incohérence
  date/clé ISO bloque tout le lot.
- Les relevés de semaines non couvertes, dont W42, ne sont ni déplacés, ni
  supprimés. Les mesures d'autres années restent intactes.
- Aucun lundi ISO n'est traité comme preuve d'un jour exact d'observation.
  `measured_on`, `week_start` et `temporal_precision` sont conservés.
- Pour chaque réattribution, les historiques d'emplacement/mouvement, les fins
  historiques inconnues, les liens mouvement-période legacy absents et les zones
  différentes ou manquantes sont admis. Aucune équivalence environnementale entre
  source et cible n'est exigée; une résolution environnementale absente n'est pas
  comblée artificiellement.
- Restent bloquants les références de zones étrangères à l'organisation, les
  périodes mal formées ou chevauchantes et les liens mouvement-période persistés
  contradictoires (notamment boîte, zone ou frontière temporelle incompatible).
  Un lien legacy absent n'est pas une contradiction et n'est jamais reconstruit.
- Une lignée, un repiquage ou un transfert rattaché à ces boîtes exige une
  résolution indépendante : le correcteur bloque plutôt que réécrire un
  snapshot ou décider qu'un historique n'est pas pertinent.

Les classifications du JSON sont `ALREADY_MATCHES`, `REASSIGN_REQUIRED`,
`CONFLICT`, `OUT_OF_SOURCE` et `INDETERMINATE`. `uncovered` décrit aussi les
semaines vides de la source, même lorsqu'aucune mesure PostgreSQL ne leur
correspond. Les lignes appariées contiennent les cellules source, la semaine,
les codes littéral/normalisé/actuel/cible, la PK, les comptes source/actuels et
l'instantané complet de l'ancien état.

## Préparation locale, sans écriture

Depuis `backend/`, utiliser une **session enfant jetable** et la configuration
`config.cca_eob_local_settings`, jamais l'environnement DB par défaut.

L'opérateur fournit le rôle LOCAL vérifié dans `POLYPBASE_CCA_LOCAL_USER` et
son accès privé via le fichier de mots de passe libpq ou la variable de processus
`POLYPBASE_CCA_LOCAL_PASSWORD`, renseignée sans écho ni historique. Fournir aussi
une `DJANGO_SECRET_KEY` propre à cette session locale (jamais la clé de production),
comme l'exige le démarrage Django hors test runner. Ne pas
transmettre de secret dans le chat, une commande littérale ou un fichier Git.

La cible est fixée dans le code à `127.0.0.1:55438`, base
`polypbase_anomalies_test_20261016`. Le mode PostgreSQL est **read-only par
défaut**, y compris si `--apply` est accidentellement fourni. Ne définir aucune
variable autorisant l'écriture pendant la préparation.

```text
uv run python manage.py correct_cca_eob_v2 --settings=config.cca_eob_local_settings --organization-id 1 --workbook "C:\Users\antoc\POLYPBASE-ANALYSES\data\raw\Suivi_2026_actualisé_v2.xlsx"
```

La sortie standard est un JSON `{plan_hash, plan}`; le résumé va sur stderr.
Un plan bloqué est quand même rendu intégralement et la commande termine en
erreur. Sans `--apply`, aucune mesure, révision ou entrée d'audit n'est écrite.

Le garde refuse la base de référence, tout hôte/port/nom non autorisé, les
options de redirection et les services libpq. Il vérifie aussi l'adresse, le
port et la base de la connexion psycopg effective avant les requêtes métier.
Ne pas exécuter de serveur, test runner, migration, seed ou autre commande
avec cette configuration dédiée.

## Après revue indépendante uniquement

1. Vérifier séparément la sauvegarde locale et l'état initial : PK, comptes et
   métadonnées de toutes les mesures, statuts/localisations/mouvements/audits,
   ainsi que les six corrections d'espèces déjà validées.
2. Produire et faire revoir la matrice PostgreSQL réelle. Tout cas indéterminé
   ou conflictuel bloque le lot; aucune application partielle ou option force.
3. Refaire un dry-run immédiatement avant l'application. Toute différence
   d'empreinte nécessite une nouvelle revue.
4. Obtenir l'autorisation explicite après cette revue. Dans une nouvelle session
   locale dédiée, l'opérateur peut alors activer
   `POLYPBASE_CCA_LOCAL_ALLOW_WRITE=1`, puis ajouter `--apply`, `--actor` (compte
   administrateur réel) et `--expected-plan-hash` (empreinte exactement revue).
   Cette étape n'est pas autorisée par le présent document.
5. Contrôler après application la conservation du nombre/ensemble des PK, de
   chaque champ de mesure autre que `box_id`, des mesures hors source, des mouvements,
   localisations, zones, pointeurs de zone, températures scientifiques, statuts et
   anciens audits. Les exports/graphiques peuvent changer de contexte via la boîte
   cible sans mutation de ces données. Vérifier aussi la décision dans les nouveaux
   audits individuels et le reçu de lot. Refaire le dry-run : aucune réattribution
   ne doit rester. Fermer la session enfant.

## Transaction, concurrence et audit

L'applicateur verrouille les boîtes par PK, puis l'organisation en `NO KEY UPDATE`,
puis les droits de l'acteur, les mouvements par PK, les périodes par PK, les zones référencées par PK et les
mesures par PK. L'ordre boîte → mouvement → période respecte le service de
correction des mouvements; le verrou boîte commun sérialise les déplacements,
qualifications, désactivations et réactivations. Le verrou de zone `NO KEY UPDATE`
protège son appartenance institutionnelle sans bloquer les simples références FK.
Un repiquage verrouillant explicitement cette même zone attend néanmoins; une
opération sur une autre boîte et une autre zone peut continuer. Il ne verrouille jamais l'organisation avant les boîtes : les écritures ordinaires de
mesures verrouillent boîte puis organisation, et un `FOR UPDATE` organisation tenu avant
une boîte peut former un cycle avec les clés étrangères d'un repiquage concurrent.
Le verrou organisation `NO KEY UPDATE`, pris après les boîtes et avant le membership,
suit l'ordre organisation → membership de `OrganizationMembershipDetailAPIView.patch`
(qui prend `FOR UPDATE`) : le PATCH et le correcteur se sérialisent sur l'organisation
au lieu de se croiser sur le membership lors du contrôle de la FK d'audit différée au
commit. `NO KEY UPDATE` ne bloque pas les contrôles FK `KEY SHARE` des repiquages. Le compte
acteur est verrouillé sans bloquer les références FK ordinaires. Les permissions
sont relues en base : compte actif et membership administrateur actif dans
l'organisation ciblée, sans exception pour un superuser étranger.

Le plan est recalculé sous verrous. Son empreinte porte sur l'opération, la
décision `product_decision`, la source, le manifeste, tous les champs des trois
boîtes et de leurs mesures, les dépendances historiques, l'ordre de transfert et
les changements de révision proposés. Le snapshot complet, le fingerprint, les
verrous et les contrôles de péremption ne sont pas allégés par la décision produit.
La baseline `location_context` contient toutes les périodes et tous les mouvements
des trois boîtes, même hors 2026 ou sans lien mouvement-période, ordonnés par PK,
avec tous leurs champs persistés (auteurs, notes, dates, nature, liens, révision),
ainsi que l'identité/appartenance des zones référencées et la version du garde.
La politique de baseline est `target_box_context_preserve_location_history_v2`.
Chaque réattribution expose les PK des périodes/mouvements examinés, les
pointeurs avant/cible et les éventuelles incohérences bloquantes; leur seule
présence n'est plus bloquante. Ajout, suppression ou modification invalident le
hash, même si aucun champ de `Box` n'a changé. Les ensembles vides font partie
du plan. Les références entrantes d'un mouvement d'une autre boîte vers une
période du périmètre sont également empreintées et verrouillées; elles bloquent
comme lien inter-boîtes incohérent, sans exposer les notes de cet événement.
Aucune période ni aucun mouvement n'est modifié, reconstruit ou
requalifié. Un plan périmé est rejeté sans écriture.

La contrainte immédiate `(box, week_start)` n'est pas désactivée. L'ordre
topologique déplace d'abord la mesure libérant une destination. Un cycle ou une
destination non libérable bloque, sans mesure temporaire, suppression ou fusion.

Seul `box_id` est modifié sur une mesure. Son ID, sa date, son auteur historique,
ses zéros/NULL, ses horodatages, sa provenance et sa séquence scientifique sont
préservés. Les révisions scientifiques des boîtes touchées augmentent et sont
au moins égales aux séquences conservées à destination, pour empêcher une
ancienne mesure de dépasser une consommation future. Ces révisions sont des
jetons techniques, pas des changements de statut ou d'emplacement.

Chaque transfert produit un nouvel audit UPDATE attribué à l'acteur réel, avec
instantanés complets avant/après, anciennes/nouvelles boîtes, cellules, valeurs
figées au format consommé par l'audit existant, SHA source et empreinte du plan.
Le plan, chaque audit individuel et le reçu d'audit de lot portent explicitement
la métadonnée `product_decision` : décision d'Anthony, autorité de V2 pour les
identités biologiques concernées, résolution environnementale par la boîte cible,
préservation des historiques/zones/températures et absence d'inférence depuis
Excel. Le reçu conserve aussi le plan revu et les révisions. Il atteste une
correction d'attribution biologique, pas une correction d'emplacement ou de
température ni une preuve d'équivalence environnementale. Son couple
`IMPORT / historical_correction` utilise intentionnellement le fallback des types
inconnus de `apps.audit.services` : famille `references`, et non `transfers`.
Les audits individuels `UPDATE / box` portant `measurement_id` sont classés
`measurements`. Les filtres, compteurs et Actions consomment cette classification
serveur. Aucun reclassement global ni nouveau type frontend n'est ajouté; le reçu
reste générique et son type peut apparaître sous l'identifiant brut.
`test_cca_eob_audit` cible les classifications Python/ORM, les API
personnelles/administratives, leurs filtres, métadonnées et l'isolation. Aucun ancien audit n'est modifié. La transition de boîte figure dans la description et les métadonnées;
aucune nouvelle présentation frontend n'est ajoutée. Le résumé Actions actuel
n'expose pas la clé de modification de boîte : la transition doit être revue
dans les métadonnées complètes, et non déduite du seul résumé générique. Tout échec d'audit annule
le lot entier.

Un nouveau plan après succès est idempotent : les lignes conformes ne sont pas
réécrites et aucun audit supplémentaire n'est produit. Rejouer l'ancienne
empreinte est au contraire un plan périmé, donc une erreur.

## Effet environnemental et décision produit V3

L'inspection antérieure des consommateurs décrit les résolutions existantes,
que la décision ne demande pas de modifier :

- `apps.exports.services._thermal_zone_for_date` résout la zone par les périodes
  de la boîte du relevé; seulement en l'absence totale de périodes, il utilise
  `Box.thermal_zone`. Une première période même hors de la date du relevé supprime
  ce fallback. Les jours de frontières sont inclusifs; une fin inconnue ne prouve
  que le jour du début dans ce résolveur.
- `_measurement_temperature` lit l'agrégat quotidien de cette zone à `measured_on`,
  puis sa consigne si l'agrégat manque. `0` n'est pas traité comme absent.
- `_filter_measurements_by_zones` applique la même dépendance à `box_id`; avec
  `include_other_zones`, l'inclusion d'autres relevés de la boîte peut aussi varier.
- `BoxMeasurementTrendAPIView`, le détail boîte et `temperature_history` associent
  aux relevés les périodes/mouvements de la boîte demandée. L'historique thermique
  utilise les occupations UTC, pas le fallback courant du CSV. La seule égalité
  d'une température CSV ne prouverait donc pas l'équivalence des consommateurs.

La décision explicite d'Anthony remplace le garde « aucun historique et fallback
identique ». La réattribution biologique est autorisée sans prouver l'équivalence
des contextes source/cible. Les exports, filtres de zone et graphiques suivent
alors la boîte cible selon leurs résolveurs existants : une zone, une température
résolue ou l'inclusion d'un relevé peuvent changer, ou rester non résolues. Cet
effet de lecture est accepté; il n'autorise aucune écriture environnementale.
Une température absente reste absente et une température réellement égale à zéro
reste zéro. Ni jour exact depuis le lundi ISO, ni lieu, mouvement ou température
depuis les blocs Excel ne sont déduits. Aucun historique thermique n'est copié.

Les périodes fermées, futures ou à fin inconnue, les mouvements historiques
(y compris `unclassified`), les liens legacy absents et les zones différentes ou
manquantes ne bloquent pas à eux seuls une réattribution. Les contrôles
d'intégrité restent requis : références de zones étrangères, périodes mal formées
ou chevauchantes et liens mouvement-période persistés contradictoires bloquent le
lot, sans réparation automatique. Les blocages indépendants de lignée, repiquage
et transfert demeurent inchangés. Un historique sur une boîte non impliquée dans
une réattribution n'est pas à lui seul bloquant; un plan entièrement déjà conforme
reste idempotent en présence d'historiques.

L'opération est versionnée `cca_eob_v2_20261016_v3` et sa politique de baseline
est `target_box_context_preserve_location_history_v2`, avec `product_decision`
dans le plan et les audits individuels/de lot. La commande, ses options et la
configuration locale dédiée restent inchangées; son résumé précise l'effet de
lecture via la boîte cible sans mutation environnementale.

**Toutes les empreintes de plan antérieures à ce contrat V3 sont invalides**,
quelle que soit leur version, notamment
`54c6cd0bad84a0fab4c07d2e72b52fb03d9c07d4e2eab25336f19e201666a62b`.
Aucune nouvelle empreinte de plan n'est fournie ou déduite ici. Le SHA-256 du
classeur V2 et le fingerprint du manifeste restent des identifiants de source,
pas une autorisation de réutiliser un ancien plan. Un nouveau dry-run après
implémentation V3, sa revue indépendante et une autorisation explicite restent
obligatoires avant toute application. Le dry-run V3 réel sur la copie locale a
produit l'empreinte `2b08dddd35a5a85cac04b02c9dc7b241238444e348f9e37f8d96347175041571`
(72 `REASSIGN_REQUIRED`, 8 `ALREADY_MATCHES`, 4 `OUT_OF_SOURCE`), appliquée
sur cette seule copie; elle est consommée et ne vaut pas autorisation pour une autre base.

## Tests isolés

Les tests qui imposaient l'absence totale d'historique ou l'égalité des zones
sont adaptés à la décision. `test_cca_eob_environment_consumers` couvre les
contextes cibles différents ou non résolus dans les exports et graphiques,
la conservation exacte des historiques, zones, sondes et températures brutes ou
quotidiennes (y compris zéro). Les suites de correction et d'audit couvrent les
cas admis/bloquants, `product_decision` aux deux niveaux d'audit, les snapshots,
les rejets de plans périmés et le rollback après échec du reçu final.

```text
POSTGRES_DB='' POLYPBASE_TEST_POSTGRES=0 DJANGO_DEBUG=1 uv run python manage.py test apps.cultures.test_cca_eob_source apps.cultures.test_cca_eob_correction apps.cultures.test_cca_eob_locations apps.cultures.test_cca_eob_environment_consumers apps.cultures.test_cca_eob_correction_concurrency apps.cultures.test_cca_eob_command apps.cultures.test_cca_eob_audit --settings=config.test_settings --noinput --verbosity=2
```

Le test réel de lecture Excel est optionnel si le fichier externe n'est pas
présent. Les tests métier utilisent des données synthétiques et des sources
patchées; aucune connexion à la copie historique n'est nécessaire.
Les tests de verrous exigent l'opt-in PostgreSQL QA existant de
`config.test_settings`, sur `127.0.0.1:55439`, base jetable
`test_subculture_qa`, jamais la copie historique ni la référence.

### Commande PostgreSQL QA exacte

Une instance PostgreSQL native jetable (`initdb` dans un répertoire dédié, écoute
`127.0.0.1:55439`) est équivalente au conteneur ci-dessous; c'est celle qui a servi
à la QA finale. Prérequis pour la variante Docker : moteur Docker **local** disponible, port `55439` libre ou instance QA
jetable déjà identifiée et vérifiée, aucune donnée historique dans cette instance.
Ne pas réutiliser ou remplacer le conteneur de la copie historique. Pour une
nouvelle instance vide sans volume persistant (authentification trust limitée au
loopback, uniquement pour cette QA jetable) :

```text
docker run --rm -d --name polypbase-cca-eob-qa -p 127.0.0.1:55439:5432 -e POSTGRES_DB=subculture_qa -e POSTGRES_USER=subculture_qa -e POSTGRES_HOST_AUTH_METHOD=trust postgres:17
docker exec polypbase-cca-eob-qa pg_isready -U subculture_qa -d subculture_qa
```

Attendre un retour `accepting connections`. Depuis `backend/`, exécuter dans une
session enfant jetable, avec le shell `sh` :

```sh
PGCONNECT_TIMEOUT=5 POLYPBASE_TEST_POSTGRES=1 POSTGRES_DB='' uv run python manage.py test apps.cultures.test_cca_eob_correction apps.cultures.test_cca_eob_locations apps.cultures.test_cca_eob_correction_concurrency apps.cultures.test_cca_eob_membership_lock_protocol apps.cultures.test_cca_eob_audit --settings=config.test_settings --noinput --verbosity=2
```

Équivalent portable pour une session PowerShell enfant :

```powershell
[Environment]::SetEnvironmentVariable('PGCONNECT_TIMEOUT', '5', 'Process')
[Environment]::SetEnvironmentVariable('POLYPBASE_TEST_POSTGRES', '1', 'Process')
[Environment]::SetEnvironmentVariable('POSTGRES_DB', '', 'Process')
uv run python manage.py test apps.cultures.test_cca_eob_correction apps.cultures.test_cca_eob_locations apps.cultures.test_cca_eob_correction_concurrency apps.cultures.test_cca_eob_membership_lock_protocol apps.cultures.test_cca_eob_audit --settings=config.test_settings --noinput --verbosity=2
```

Django crée/détruit `test_subculture_qa`. Contrôler toutes les lignes de
concurrence : elles doivent toutes finir par `ok`, pas `skipped`. Aucun effectif
courant n'est garanti ici. Ne pas considérer SQLite comme preuve des verrous.
Après la QA, si et seulement si le conteneur ci-dessus a été créé pour cette session :

```text
docker stop polypbase-cca-eob-qa
```

### Validation V3 locale

**Validation finale PostgreSQL (2026-10-18)** : PostgreSQL 17.4 natif isolé sur
`127.0.0.1:55439`; suite CCA-EOB et verrous 75 tests, consommateurs et membership
50 tests, non-régression comptes/mesures/boîtes 578 tests, tous réussis, 0 ignoré.
Le test permanent `test_cca_eob_membership_lock_protocol` reproduit l'interleaving
correcteur / PATCH des droits (échec 40P01 avec l'ancien ordre, réussite avec le
nouveau). Les résultats SQLite et Docker ci-dessous sont l'historique antérieur.

- Tests synthétiques CCA-EOB sur SQLite : **112 découverts, 101 réussis,
  11 ignorés**. Ces 11 scénarios de concurrence PostgreSQL ne constituent donc
  pas une preuve des verrous.
- Non-régression ciblée mouvements, historique thermique, diagnostic des périodes,
  exports, audit et source historique : **153 tests réussis**, sans test ignoré.
- `check`, `makemigrations --check --dry-run` et `git diff --check` : réussis;
  aucune migration détectée.
- Script QA global lancé avec `DJANGO_SETTINGS_MODULE=config.test_settings`,
  `POSTGRES_DB=''`, `POLYPBASE_TEST_POSTGRES=0`, `DJANGO_DEBUG=1` : les contrôles
  Django passent; la suite backend globale découvre 1725 tests mais expire après
  240 secondes, sans résultat final. Elle n'est pas déclarée validée et le build
  frontend du script n'a pas été atteint. Aucune interface n'est modifiée.
- Docker local : `docker ps` échouait alors (pipe introuvable); la QA PostgreSQL
  a ensuite été faite avec une instance native, voir ci-dessus.
- Cet état SQLite précède le dry-run et l'application sur la copie locale. Le risque
  de verrouillage membership/organisation mentionné ci-dessous est corrigé.

### Historique de validation du durcissement antérieur (pré-V3)

Les résultats ci-dessous sont conservés comme historique de l'ancien contrat,
pas comme validation courante; leurs effectifs sont uniquement historiques.

- Suite ciblée complète SQLite : **120 découverts, 109 réussis, 11 ignorés**
  (les 11 tests de concurrence réservés à PostgreSQL), aucun échec.
- Non-régression mouvements, historique thermique, exports et audit :
  **72 exécutés, 72 réussis, 0 ignoré**.
- `check` et `makemigrations --check --dry-run` avec `config.test_settings`,
  `POSTGRES_DB=''` et `DJANGO_DEBUG=1` : aucun problème, aucune migration détectée.
- PostgreSQL QA : commande tentée, **0 test exécuté, aucun résultat de concurrence**.
  Échec de connexion `127.0.0.1:55439` : `OperationalError: connection timeout expired`.
  Docker n'était pas joignable, puis les commandes `docker desktop start` et
  `docker ps` ont expiré. Aucun conteneur QA n'a été créé par cette intervention.
  Ce blocage doit être levé avant de déclarer le finding de concurrence résolu.
  - Risque préexistant identifié par inspection, hors des modifications des findings :
    le correcteur verrouille le membership avant d'insérer un audit lié à
    l'organisation, tandis que `OrganizationMembershipDetailAPIView.patch` verrouille
    l'organisation avant de mettre à jour le membership. Une modification concurrente
    des droits de l'acteur peut donc créer un cycle de verrous PostgreSQL
    membership → organisation → membership. Ce scénario n'a pas été reproduit faute
    de QA PostgreSQL; il n'est pas corrigé ici pour ne pas élargir silencieusement le
    périmètre. Il doit être examiné dans la re-review avant toute application.
    **Reproduit puis corrigé ensuite** (SQLSTATE 40P01, PostgreSQL 17.4 natif isolé) :
    ordre de verrous boîtes → organisation (`NO KEY UPDATE`) → membership → compte, et
    régression permanente `test_cca_eob_membership_lock_protocol`.

Lors de ce durcissement antérieur, les scénarios ajoutés ciblaient les changements
de périodes/mouvements sans changement de `Box`, ajouts/suppressions, provenance
et auteurs, conflits institutionnels, fallback identique ou différent, zéros
thermiques, périodes ambiguës et conservation exacte des quatre relevés
synthétiques W41/W42. Les tests PostgreSQL ajoutés ciblaient les deux ordres de
déplacement concurrent, le rollback complet après audit final, et une correction
concurrente de notes/date de mouvement rendant même un plan déjà bloqué périmé
avant toute écriture. Ces descriptions sont historiques; les attentes de blocage
liées à la seule présence d'historiques ou à la différence de zone sont remplacées
par le contrat V3. Les attentes de péremption restent couvertes, y compris
pour un historique désormais autorisé; leur exécution PostgreSQL reste requise.
