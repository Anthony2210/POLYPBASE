# Box 1902 — préservation des confirmations : constat et arrêt de sécurité

> **Ce dossier conserve les étapes d'arrêt antérieures.** La reprise finale v2 est
> maintenant **READY_FOR_INDEPENDENT_REVIEW** : capacité réelle raccordée à la
> commande dédiée et validée sur schéma Django rempli sous rôles indépendants.
> Résultats finaux : **28/28 PostgreSQL intégrés**, **25/25 preuves d'autorité
> rejouées**, **48/48 régressions d'arrêt/exécution** ; aucun test final ignoré.
> Voir le [compte rendu actuel et la procédure opérateur](box-1902-postgresql-authority-proof.md#actual-v2-workflow-and-local-validation).
> Les mentions BLOCKED ci-dessous décrivent ces étapes historiques, pas le verdict
> actuel. Aucune action production, mutation Git ou autorisation de déploiement.

> **État courant après FIX_REQUIRED : BLOCKED.** Seuls des interverrouillages
> d'arrêt ont été ajoutés, pas une correction sécurisée complète. `apply_plan()`
> refuse avant ouverture de session ; le dry-run est `BLOCKED` avec un nouveau
> marqueur de contrat ; la migration 0018 PostgreSQL refuse installation et retour
> arrière avant tout SQL. **Ce worktree ne peut pas être déployé.**
>
> Voir le [rapport de reprise](#reprise-après-fix_required--arrêt-fail-closed).
> Les sections antérieures ci-dessous sont conservées comme historique ; leurs
> assertions de disponibilité, de tests ou de propriétaires ne décrivent pas
> nécessairement ce nouvel état. Aucun accès production n'a été effectué.

## Verdict au 2026-11-09

**NON IMPLÉMENTÉ — NON PRÊT pour une revue d'acceptation ou une application.**

Cette tâche s'arrête conformément à la clause demandant de ne pas introduire
une exception partiellement sécurisée. Aucun code exécutable, garde SQL, modèle,
migration ou permission n'a été modifié. Le présent document est un constat et
une proposition d'architecture, pas une autorisation ni un mécanisme installé.

L'architecture est réalisable en principe. L'arrêt ne démontre ni impossibilité
technique, ni vulnérabilité constatée en production : l'implémentation complète
et ses preuves sous rôles restreints ne sont pas établies dans cette livraison.
Les validations locales bornées ont expiré avant leur terme. Les propriétaires
et privilèges effectifs de production restent inconnus ; aucun accès distant
n'a été effectué pour les déterminer.

**Ne pas appliquer le chemin v1 existant pour satisfaire la nouvelle décision :
il prévoit et accepte la perte des confirmations.**

## Base locale et périmètre inspecté

- HEAD initial : `eea90d12ab6ece48a5f75635a4b58912befa86bf`, identique à la
  version de production précédemment vérifiée communiquée dans la demande.
- Worktree initial propre ; aucun écart avec ce commit.
- Lecture des règles, du routeur de contexte, des contextes d'intégrité et de
  développement, des documents CLA/SME et de la correction ciblée.
- Inspection du service et de la commande Box 1902, d'`identity_execution.py`,
  d'`identity_reconciliation.py`, des modèles de températures historiques,
  des migrations mesures 0011/0012/0014, des validateurs d'identité et de plan,
  des vérifications SQL et de leurs consommateurs CCA/EOB.
- Tests synthétiques uniquement, dans des clusters PostgreSQL 18.6 temporaires
  neufs sur loopback. Aucun accès production, Neon, sauvegarde réelle ou classeur
  original. Aucun commit, push, merge ou déploiement.

## Décision et faits communiqués

La correction autorisée est exclusivement : organisation **1**, Box **1902**,
`CLA-JKA-1.009` → `CLC-JKA-1.009`, souche **213** → **285**, espèce cible
**164 / Chrysaora lactea**. Le PK, le statut inactif et le code local
`CLA-JKA-1.09` restent inchangés.

Base réelle de l'autorisation : **décision explicite d'Anthony, propriétaire
produit, à qui la confirmation du laboratoire a été communiquée oralement**.
Ce n'est pas une attestation de laboratoire vérifiée indépendamment. Aucun
signataire, rapport, date d'analyse, méthode ou document de laboratoire n'est
inventé. Le `REASON` actuel attribué à Etienne ne décrit pas cette nouvelle base
et devrait être remplacé uniquement dans le nouveau contrat ciblé.

Les faits suivants proviennent de la demande, pas d'une observation locale de
production :

- 41 associations directes confirmées, semaines ISO 12–52 de 2025 ;
- PK des relevés et des évidences : **12173–12213** ;
- `Suivi_2025.xlsx`, feuille `Semaestomeae`, libellé original `CLA-JKA-1.09` ;
- SHA-256 : `0776396ad12b6479e53755496b0e6c51ec3a2e1ff581c983f84f82fe943b84fa` ;
- température en `C61`, cellules biologiques des colonnes Q à BE ;
- absence de décalage semaine/source détecté lors du contrôle communiqué ;
- références présentes, cible 285, dry-run ciblé READY sans conflit.

Ces observations ne constituent pas un manifeste complet de l'état courant.
Il manque notamment les contenus exacts des lignes et de leurs dépendances à
figer dans une nouvelle revue. Les valeurs doivent être relues par la future
procédure autorisée, jamais reconstituées depuis ces seules indications.

## Cause racine et limites du contrat actuel

1. `historical_temperature_box_guard()` de la migration 0012, étendu par 0014,
   invalide les associations confirmées lorsque le code ou la souche change.
   Le comportement est intentionnel pour une mutation d'identité ordinaire.
2. `cla_box_1902._build_plan()` calcule ces invalidations et `apply_plan()` les
   intègre expressément aux différences acceptées après écriture.
3. Le garde d'immuabilité n'autorise que `confirmed` → `pending_review` : une
   reconfirmation artificielle après invalidation serait refusée et contraire
   à la demande.
4. `temperature_source_matches()`, les modèles et le plan thermique comparent
   l'identité source à celle de la Box courante, avec l'exception PCA déjà
   existante. Ils n'ont pas de contrat de correction historique CLA ciblé.
   Conserver seulement les statuts laisserait donc une identité incohérente.
5. L'autorisation root est vérifiée dans Python. PostgreSQL ne dispose pas,
   dans ce chemin, d'une capacité indépendante autorisant la conservation.
   Un hash de plan, un identifiant d'acteur, un audit ou une variable de session
   ne remplace pas cette capacité.
6. `_verify_production()` d'`identity_execution.py` exclut des attributs de
   rôle élevés, mais n'exclut pas la propriété ordinaire des tables/fonctions,
   ni toute propriété assumable par `SET ROLE`. Le code ne permet donc pas
   d'affirmer une séparation d'autorité suffisante. Ce n'est pas un constat
   sur les propriétaires effectifs de production.
7. `_schema_state()` d'`identity_reconciliation.py` lie les définitions des
   triggers, pas les corps, propriétaires et configurations des fonctions
   qu'ils appellent. Un simple hash de ce schéma n'atteste pas le nouveau garde.
8. CCA/EOB vérifie exactement les corps issus de 0012/0014 et inclut les gardes
   dans ses snapshots. Un nouveau corps légitime exige une mise à jour ciblée de
   cette attestation et rend caducs les snapshots/hashes concernés. Les droits
   et exceptions métier CCA/EOB ne doivent pas être élargis.

## Architecture proposée — non implémentée

### Séparation d'autorité

Prévoir une capacité propre à cette correction, sans réutiliser l'autorisation
CLA de dix Box ni une capacité CCA/EOB :

- propriétaire de schéma/gardes distinct du runtime, non assumable par celui-ci ;
- propriétaire technique `NOLOGIN` de la capacité et du registre protégé,
  non assumable par le runtime ou l'exécuteur ;
- rôle d'exécution dédié sans DDL, propriété, privilège de réplication,
  écriture directe sur le registre ou droit de modifier les fonctions ;
- import de l'approbation par une autorité indépendante, inaccessible à
  l'application et à l'exécuteur ; aucune création implicite depuis l'audit ;
- un writer `SECURITY DEFINER` à portée fixe et ACL minimales, objets qualifiés,
  `search_path` sûr, sans SQL ou identifiants arbitraires fournis par l'appelant.

Le provisionnement doit refuser un propriétaire, une fonction, une ACL ou une
appartenance inattendus, plutôt que les remplacer silencieusement. Les noms
et DDL exacts des rôles restent à concevoir et à tester ; aucun rôle n'est créé
par ce document.

### Autorisation liée à l'état et exécution atomique

L'approbation indépendante doit lier une nouvelle version d'opération et de
contrat, l'identité effective de la base, l'exécuteur, l'acteur approuvé, la
fraîcheur, le plan revu, les identités exactes avant/après et le manifeste
complet des **41** associations et évidences. Le hash sert à l'intégrité d'un
document authentifié ; il n'en est pas l'autorité.

Le writer doit prendre les verrous avant revalidation, refuser les changements
concurrents et vérifier l'ensemble exact, pas seulement le nombre ou l'intervalle
des PK. Manifeste absent, incomplet, supplémentaire, modifié ou en attente de
revue : refus. Toute dérive d'identité, référence ou schéma : refus.

Le contexte exceptionnel doit provenir exclusivement d'un état privé créé par
le writer, lié à la transaction et à la session autorisée. Le garde ne doit
omettre que sa branche d'invalidation, pour l'unique transition exacte. Ni GUC,
ni acteur fourni, ni reçu modifiable ne peuvent activer ce contexte. Les autres
changements d'identité conservent le comportement existant, y compris après la
correction ; le reçu ne devient jamais un permis permanent de renommer la Box.

La mise à jour exacte de Box, les réservations de codes nécessaires, le registre
historique, les audits, les postconditions et la consommation unique de
l'approbation doivent être atomiques et imposés par le writer, y compris lors
d'un appel SQL direct. Aucune écriture d'un relevé ou d'une évidence n'est
nécessaire pour conserver les confirmations. Tout échec doit annuler l'ensemble.
Un rejeu doit être refusé sans réarmer l'approbation ; une éventuelle lecture
d'état déjà appliqué ne doit pas constituer une nouvelle autorisation.

### Traçabilité scientifique

Le registre protégé doit conserver : identité originale et corrigée, base réelle
de la décision d'Anthony, acteur, opération versionnée, approbation et plan
revus, ainsi que les associations exactes aux évidences originales.

Le libellé `CLA-JKA-1.09`, les empreintes, cellules, valeurs, dates et semaines
restent intacts dans les évidences. Aucun alias global CLA → CLC. Les validateurs
Python/SQL doivent consulter une projection vérifiable du registre pour les
seuls relevés/évidences autorisés ; deux chaînes de code ne suffisent pas à
établir cette exception. Cela nécessite une évolution explicite du contrat du
plan thermique et de ses consommateurs, pas une relaxation de la fonction de
comparaison de chaînes.

### Migration et prérequis d'exploitation

- Nouvelle migration additive compatible avec un historique non vide ; ne pas
  réécrire 0012/0014 ni appeler leur exigence de stockage vide.
- Déployer les structures et nouveaux gardes fermés par défaut, sans corriger
  les données ni créer d'approbation de correction dans la migration.
- Après utilisation, interdire une inversion qui supprimerait le registre ou
  restaurerait un validateur incapable d'expliquer les évidences conservées.
  Un rollback de release doit lui aussi préserver ce contrat scientifique.
- Vérifier corps, signatures, propriétaires, ACL, configurations, bindings,
  rôles assumables et privilèges effectifs, pas seulement noms et activation.
- Nouveau contrat d'opération et d'autorisation : aucun hash ou accord v1 ne
  peut autoriser ces nouvelles sémantiques. Ne pas changer l'autorisation des
  autres corrections.
- Conserver la frontière existante : maintenance autorisée, sauvegarde fraîche
  indépendamment restaurée, identité effective attestée, approbation root
  courte et revalidée jusqu'avant commit. L'autorisation SQL indépendante
  s'ajoute à cette frontière ; elle ne la remplace pas.
- Faire attester indépendamment la topologie réelle des propriétaires et rôles
  avant tout provisionnement futur. Si le runtime peut altérer ou assumer la
  propriété des gardes/capacités, aucun mécanisme de trigger ne peut prétendre
  le protéger de ses propres écritures SQL.

## Validations exécutées et limites exactes

### Harnais Box 1902 et mesures

Commande depuis la racine :

```sh
uv run python scripts/test_historical_temperature_production.py --run --pg-bin 'C:/Program Files/PostgreSQL/18/bin' apps.cultures.test_cla_box_1902_postgres apps.measurements.test_historical_temperature_postgres
```

Résultat : **expiration à 240 000 ms**, pas de résultat global. PostgreSQL
**18.6**, cluster synthétique neuf ; **47 tests découverts**, **9 tests marqués
`ok`** avant interruption, le test suivant en cours étant
`test_measurement_change_invalidates_review_and_preserves_zero`.

Le premier test réussi est
`test_41_confirmed_historical_readings_allow_only_direct_status_transitions`.
Attention : il prouve le contrat actuel, qui transforme les 41 associations en
`pending_review`, et non la conservation demandée. Cette fixture utilise 2026
et une source synthétique, pas les 41 évidences de 2025 communiquées.

Ces tests utilisent l'utilisateur initial du cluster, pas la future séparation
de rôles. Ils ne prouvent donc pas la sécurité de l'exception envisagée.

Après expiration, `pg_ctl status` a confirmé l'absence de serveur ; seul le
répertoire temporaire de ce cluster a été supprimé. Le journal synthétique est
local/ignoré : `.local-docs/historical-temperature-production-tests.log`.

### Harnais de rôles CCA/EOB existant

Exécution de `apps.cultures.test_cca_eob_role_provisioning` avec
`--settings=config.test_settings --noinput -v 2`, dans un enfant nettoyant les
variables PG*/POSTGRES_*, désactivant dotenv et sélectionnant explicitement le
binaire PG18. Le harnais crée et détruit son propre cluster temporaire.

Résultat : **expiration de l'enfant à 285 s** sous une borne terminal de 300 s ;
**36 tests découverts, 14 marqués `ok`**, dont 11 tests de contrat/émetteur et
3 tests PostgreSQL exécutables. Aucun échec d'assertion signalé avant expiration.
Le test en cours était
`test_direct_sql_malformed_release_and_final_document_never_reach_live_target`.

Les trois tests PostgreSQL réussis :

- `test_administrator_import_requires_exact_document_hash_and_bound_release` ;
- `test_direct_helpers_cannot_mutate_without_a_trusted_bound_approval` ;
- `test_direct_reader_and_writer_refuse_privilege_drift_even_with_forged_approval`.

Ils comprennent de vraies connexions du rôle restreint. Ils ne couvrent pas une
capacité Box 1902, qui n'existe pas, et ne constituent pas une suite CCA/EOB
complètement réussie. Le serveur temporaire a été constaté arrêté et son
répertoire supprimé par l'agent chargé de ce harnais.

### Environnement et contrôle statique

- `docker info` : échec de connexion au pipe du moteur Linux Docker Desktop.
- Démarrage de commande dans Ubuntu WSL : expiration à 15 s, sans résultat.
  Cela ne prouve pas que Linux est durablement indisponible.
- PostgreSQL natif fonctionne ; l'absence de Docker n'empêche pas à elle seule
  de concevoir ou tester des rôles PostgreSQL.
- `git diff --check` initial : réussi. Un contrôle final accompagne la livraison.
- Aucune preuve native Linux supplémentaire de peer/socket/fichiers root n'a
  été obtenue dans cette tâche.

## Preuves encore requises

Aucun des tests d'acceptation du **nouveau mécanisme** n'est livré ou passé :
correction exacte 2025 et préservation des 41 statuts ; égalité intégrale des
mesures/évidences/historiques et témoins inter-organisations ; invalidation
ordinaire ; refus SQL non autorisé et de faux contextes ; rejeu ; concurrence ;
rollback d'audit, d'autorisation et de postcondition ; manifeste incorrect,
évidence manquante et état modifié ; CCA/EOB inchangé sous ses vrais rôles.

Il faut implémenter et terminer ces preuves dans un environnement jetable,
avec rôle propriétaire séparé, runtime ordinaire, exécuteur dédié et autorité
indépendante. Les timeouts ci-dessus doivent être isolés avant de conclure sur
une suite complète ; aucune réussite globale n'est revendiquée.

**Livraison limitée à l'inspection et au présent dossier d'arrêt. L'exception de
préservation n'est pas prête. Attendre une nouvelle implémentation puis une
revue indépendante ; ne pas appliquer la commande v1 en substitution.**

## Implémentation locale 2026-11-09 — capacité protégée à portée fixe

### Architecture retenue

Une capacité unique, distincte du lot CLA de dix Box et de CCA/EOB :

- table de contexte privée `public.box_1902_preservation_context_v1` ;
- table d'approbation protégée `public.box_1902_approval_v1` ;
- writer `public.box_1902_preserve_v1(bigint)` en `SECURITY DEFINER`, `search_path`
  fixe, objets qualifiés, `EXECUTE` non public ;
- trigger `historical_temperature_box_guard()` étendu pour ne sauter sa branche
  d'invalidation que sur la transition exacte décrite par le contexte ;
- consommation atomique de l'approbation, du contexte, de la mise à jour Box et
  de l'audit.

Le writer vérifie l'approbation, verrouille la Box 1902, exige exactement 41
relevés confirmés, insère le contexte, met à jour la Box, supprime le contexte,
consomme l'approbation et écrit l'audit dans la même transaction. Un rejeu ou un
appel direct sans approbation échoue.

### Fichiers modifiés

- `backend/apps/measurements/migrations/0018_box_1902_preservation.py` (nouveau) ;
- `backend/apps/cultures/test_box_1902_preservation_postgres.py` (nouveau) ;
- `backend/apps/cultures/historical_2026/cca_eob_production.py` (attestation
  étendue à `0018`) ;
- `backend/apps/cultures/historical_2026/identity_reconciliation.py` (deux tables
  protégées nommées explicitement dans l'attestation de schéma).

### Tests exécutés

- `apps.cultures.test_box_1902_preservation_postgres.Box1902PreservationPostgreSQLTests` :
  **5 tests, 5 réussis**, sur cluster PostgreSQL 18.6 jetable.
- `apps.cultures.test_cla_box_1902_postgres.ClaBox1902PostgreSQLTests.test_41_confirmed_historical_readings_allow_only_direct_status_transitions` :
  **1 test réussi**, sur le même type de cluster.
- Tests CCA/EOB ciblés (`test_conditional_biology_guard_is_rejected_by_real_runtime_plan`,
  `test_tampered_helper_is_rejected_before_it_is_called`) : **2 tests ignorés**
  faute de harnais PostgreSQL loué explicitement. Ils ne sont pas en échec.

### Prérequis de propriété en production

La topologie vérifiée montre que `polypbase` possède la base, les tables et les
fonctions. Une capacité `SECURITY DEFINER` seule ne protège donc pas contre ce
rôle. Avant tout déploiement, il faut :

- transférer la propriété des tables/fonctions protégées à un propriétaire
  technique distinct, non assumable par le runtime ;
- retirer au runtime la propriété et le droit `CREATE` sur `public` ;
- vérifier les appartenances de rôles et l'absence de `SET ROLE` vers le
  propriétaire ;
- documenter la compatibilité avec les migrations Django futures, qui exigent
  un rôle de migration distinct.

Tant que le runtime possède les objets protégés, aucune sécurité contre ce
runtime n'est revendiquée.

### Blocages restants

- La séparation de propriété en production n'est pas exécutée ni vérifiée.
- Les tests CCA/EOB complets n'ont pas été exécutés dans leur harnais loué.
- Le manifeste exact des 41 évidences de 2025 n'est pas encore figé dans une
  approbation réelle ; le test local utilise une fixture synthétique.
- Aucun commit, push, merge ou déploiement.

## Reprise après FIX_REQUIRED — arrêt fail-closed

### 1. Findings et portée réelle des changements

**Verdict : BLOCKED, pas READY_FOR_INDEPENDENT_REREVIEW.** La demande de
correction sécurisée n'est pas terminée. Application de la condition d'arrêt :
ne pas raccorder un writer dont l'autorisation reste forgeable par son propriétaire.
Aucune impossibilité de réalisation générale n'est revendiquée.

| Revue | État de cette reprise |
|---|---|
| 1 — critique, intégration | Arrêt de `apply_plan()` avant session/SQL ; dry-run explicitement bloqué. Aucun chemin de correction v2 opérationnel. Anciennes approbations inutilisables. |
| 2 — critique, autorité indépendante | Non résolu. Installation de l'ébauche refusée inconditionnellement, y compris pour un propriétaire ou un futur exécuteur. Pas de provisioning livré ou exécuté. |
| 3 — élevé, manifeste | Non résolu. Le hash arbitraire et le simple compte de 41 restent dans le SQL d'ébauche non installable. |
| 4 — élevé, cible | Non résolu. La fixture précédente accepte 285 / `CLC-JKA-2` / Species 132 : ce succès expose le défaut, pas la conformité de la cible. |
| 5 — élevé, cohérence historique | Non résolu. Aucun registre exact de correction ni adaptation des validateurs n'est livré. |
| 6 — SQL/tests | Vérifié : cinq méthodes présentes et cinq exécutées avant interverrouillage ; pas d'erreur SQL `%ROWTYPE` dans le chemin Django testé. Détails ci-dessous. |
| 7 — moyen, transaction/audit | Non résolu pour le futur writer ; aucune correction ne peut être exécutée par le workflow désormais bloqué. |
| 8 — migration/rollback | Arrêt protecteur ajouté. Aucun SQL de la migration n'est exécuté dans les deux sens sur PostgreSQL. Le SQL inverse autonome refuse aussi, sans DROP. Migration compatible non vide et rollback opérationnel à concevoir. |
| 9 — CCA/EOB | Changements préexistants conservés, pas de nouvelle preuve complète d'attestation. Aucun élargissement de privilèges ou d'allow-list CCA/EOB. |

### 2. Fichiers touchés pendant cette reprise

- `backend/apps/cultures/historical_2026/cla_box_1902.py` : refus préalable,
  conflit de plan et marqueur `box_1902_confirmed_preservation_v2_blocked` lié au
  fingerprint. Ce marqueur n'est **pas** une opération v2 exécutable. Le corps
  v1 est conservé mais n'est plus atteint par `apply_plan()`.
- `backend/apps/measurements/migrations/0018_box_1902_preservation.py` :
  installation/retour PostgreSQL bloqués ; SQL d'ébauche conservé pour revue.
- `backend/apps/cultures/test_box_1902_safety_stop.py` : huit tests d'arrêt,
  sans revendication de frontière SQL sous de vrais rôles.
- `docs/cla-box-1902-confirmed-preservation-review.md` : présent rapport.
- `docs/cla-box-1902-correction.md` : avertissement mis à jour.

Les modifications antérieures de `cca_eob_production.py`,
`identity_reconciliation.py` et les cinq tests PostgreSQL de préservation sont
conservés. Migrations déployées 0012 et 0014 inchangées.

### 3. Chemin autorisé de bout en bout

Il n'en existe **pas encore** pour la préservation demandée. Le chemin courant
est `correct_cla_box_1902 --apply` → `apply_plan()` → refus explicite, sans
session, audit, namespace ou UPDATE. Le dry-run conserve sa frontière de lecture
existante et expose `BLOCKED`. Le marqueur supplémentaire change le plan hash ;
un ancien hash, même associé à un acteur et un accord root v1, ne peut activer
l'application.

Le remplacement devra conserver `identity_execution.session()` : cible root,
socket/identité serveur, archive et restauration indépendante, autorisation root
fraîche, transaction et contrôle avant commit ; puis l'Admin institutionnel
actif, le plan exact recalculé sous verrous, le writer protégé, les postconditions
scientifiques et les reçus complets atomiques. Un appel SQL isolé ne remplit pas
ces conditions.

### 4. Contrat de propriété/ACL : absent, pas attesté

La topologie production communiquée est explicitement **non sûre contre le
runtime** : propriétaire de la base, des tables/fonctions protégées et des audits,
avec CREATE public. Aucune lecture distante de confirmation n'a été faite.

Le SQL d'ébauche reste insuffisant : `SECURITY DEFINER` appartenant au migrant,
`GRANT EXECUTE ... TO CURRENT_USER`, approbation insérable par ce propriétaire,
acteur arbitraire, document non authentifié. Ce SQL n'est plus exécuté par la
migration. Son simple maintien dans le fichier n'est pas une protection.

Autre blocage concret : `historical_temperature_box_guard()` est invoker et lit
la table privée de contexte. Retirer les droits runtime sans revoir cet accès
peut casser les changements ordinaires d'identité. Accorder l'écriture du
contexte pour résoudre cela serait un contournement, pas une solution.

Il manque une procédure hors migration Django avec propriétaire NOLOGIN distinct,
émetteur indépendant, exécuteur étroit distinct du runtime, ACL exactes, zéro
escalade via membership/SET ROLE, absence de propriété/DDL runtime, lookup de
contexte sûr, et vérification des corps, bindings, owners, proconfig et ACL.
**Cette topologie proposée n'a pas été implémentée ou testée ici.**

### 5. Manifeste et préservation scientifique

Aucun manifeste complet authentifié n'est livré. Les PK de relevés communiqués
12173–12213, semaines ISO 12–52/2025, fichier `Suivi_2025.xlsx`, feuille
`Semaestomeae`, libellé `CLA-JKA-1.09` et SHA-256
`0776396ad12b6479e53755496b0e6c51ec3a2e1ff581c983f84f82fe943b84fa`
ne remplacent pas un nouveau snapshot approuvé. Les PK de biologie et d'évidence,
contenus immuables et valeurs scientifiques doivent être obtenus de ce snapshot,
pas déduits des PK de relevés. La mention historique ci-dessus assimilant les PK
d'évidence aux PK de relevés n'est pas une preuve utilisable.

Aucune donnée scientifique n'a été corrigée. Aucune réécriture de preuve, alias
CLA/CLC global, reconfirmation ou changement d'une autre Box n'est effectué par
les nouveaux interverrouillages. La décision orale rapportée par Anthony reste
une décision produit, pas une attestation écrite de laboratoire vérifiée.

### 6. Exécutions réelles de cette reprise

**Avant changement**, commande depuis la racine :

```sh
.venv/Scripts/python.exe scripts/test_historical_temperature_production.py --run --pg-bin 'C:/Program Files/PostgreSQL/18/bin' apps.cultures.test_box_1902_preservation_postgres.Box1902PreservationPostgreSQLTests
```

- Cluster neuf PostgreSQL **18.6**, migration complète jusqu'à 0018 réussie.
- **5 exécutés, 5 réussis, 0 échec, 0 erreur, 0 ignoré**, 123,454 s pour les tests.
- Méthodes : `test_exact_correction_preserves_all_41_confirmations`,
  `test_replay_is_rejected_without_new_writes`,
  `test_direct_sql_without_approval_is_rejected`,
  `test_ordinary_identity_change_still_invalidates`,
  `test_other_boxes_and_organizations_are_untouched`.
- Ce résultat sous l'utilisateur initial ne prouve pas la sécurité SQL. Il est
  antérieur aux interverrouillages et n'est pas un succès de la version finale.

Les octets du fichier contiennent bien
`approval public.box_1902_approval_v1%%ROWTYPE;`. Inspection de Django installé :
`DatabaseSchemaEditor.execute(sql, params=())` appelle
`connection.ops.compose_sql(sql, params)` avant `super().execute(sql, None)`.
L'interpolation client réduit `%%` à `%`. Le succès réel de migration sur PG18
confirme ce chemin. Exécuter le texte brut dans psql, sans interpolation Django,
n'est pas équivalent. Aucune correction aveugle vers un `%` simple n'est faite.

**Après changement** :

```sh
# Depuis backend/, environnement enfant explicitement isolé
POLYPBASE_TEST_POSTGRES=0 PYTHON_DOTENV_DISABLED=1 DJANGO_DEBUG=1 ../.venv/Scripts/python.exe manage.py test apps.cultures.test_box_1902_safety_stop --settings=config.test_settings --noinput -v 2
```

- **8 exécutés, 8 réussis, 0 échec, 0 erreur, 0 ignoré**, 1,513 s, SQLite mémoire.
- Tentative précédente avec `config.preflight_settings` : **8 exécutés,
  6 réussis, 2 erreurs, 0 échec d'assertion, 0 ignoré** ; le workflow refuse ce
  module de settings. Relance réussie avec `config.test_settings`, sans mock de
  la frontière de lecture pour les tests de plan.
- Même commande PostgreSQL qu'avant, sur **un second cluster neuf** : sortie 1
  à `0018.forwards`, message `Box 1902 migration 0018 is blocked...`.
  **0 méthode exécutée**, échec de préparation de base intentionnel ; ce n'est
  ni cinq tests ignorés, ni cinq tests réussis.
- Les deux clusters ont été arrêtés et supprimés par le harnais.
- `manage.py check --settings=config.preflight_settings` : réussi.
- `git diff --check` : réussi.
- Pas de timeout pendant ces exécutions. Pas de suite large ni de tests sous
  vrais rôles restreints ; critères A–M non démontrés. Les tests v1 attendent
  encore l'ancien comportement et doivent être réconciliés avec le futur
  remplacement, pas présentés comme des régressions validées ici.

### 7. Prérequis et ordre opératoire futur — aucune action autorisée ici

1. Terminer l'implémentation locale v2 complète, le registre exact et les
   validateurs historiques, puis prouver A–M avec plusieurs vrais rôles PG18.
2. Revue indépendante du code et du provisioning hors migration ; aucune
   migration ne doit transférer silencieusement les privilèges production.
3. Procédure opérateur séparée, autorisée juste avant mutation : sauvegarde
   indépendante/restaurée, maintenance, inventaire de tous les objets et retrait
   des propriétés/DDL runtime, rôles distincts, contrôle des memberships/ACL.
4. Installer la migration remplacée uniquement si l'attestation complète passe ;
   sinon arrêter. Ne pas contourner l'interverrouillage avec `--fake`.
5. Snapshot frais, revue exacte du plan et du manifeste, autorisation root et
   approbation indépendante, puis seulement le workflow dédié versionné.
6. Vérifier atomiquement les postconditions et conserver durablement registre,
   approbation consommée et audits. Aucun reverse destructeur de provenance.

Pas de SQL production, de provisioning distant, de changement de privilèges,
de correction, de commit, de push, de merge ou de déploiement. Attendre la suite
d'implémentation et une nouvelle revue indépendante ; ce rapport n'est pas une
autorisation d'exploitation.
