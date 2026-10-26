# CCA-EOB — examen local des deux ajouts hors source

Date : 2026-10-23. Verdict initial : **BLOCKED**.

> Archive de la première investigation, conservée sans réécriture des constats
> d'alors. Les preuves nominatives et d'audit fournies ensuite ont permis
> l'implémentation décrite dans
> [la revue du delta additif](cca-eob-additive-baseline-review.md).
> Le verdict et les limites courants sont dans ce nouveau rapport.

Ce rapport ne constitue ni une autorisation de production, ni une acceptation des
mesures, ni une transition SQL prête à installer. Aucun accès SSH, production,
Neon ou base partagée n'a été effectué. Aucun commit, push, merge ou déploiement.
Les seules écritures de base effectuées sont celles de tests synthétiques sur un
cluster PostgreSQL 18 jetable créé par le harness existant.

## Cause et limite de la résolution

Le validateur `validate_thermal_exception()` de
`backend/config/cca_eob_database_role.py` authentifie le document historique
complet, puis exige l'égalité de sa correction scientifique avec la correction
courante. Seule la représentation des quatre historiques exactement vides peut
passer de listes vides à des booléens faux. Le writer impose indépendamment la
même égalité avant toute mutation.

Deux ajouts modifient nécessairement `baseline.measurements`, `entries`,
`counts.OUT_OF_SOURCE` et, si les créations ont suivi le chemin applicatif normal,
les révisions de boîte et la recette des révisions. Le refus actuel est donc
attendu. Une égalité des seuls comptes 72/8 ne garantit ni les identités ni la
conservation scientifique.

La résolution sûre exige une exception nominative authentifiée pour les deux
lignes et leur provenance, sans modifier le témoin historique. Cette exception
n'est **pas implémentée**, car les preuves disponibles ne permettent pas encore
d'en fixer le contenu et d'en vérifier la légitimité. Aucun filtre générique
« ignorer les nouvelles mesures hors source » n'a été ajouté.

L'identité historique à conserver intégralement reste :

`7be6a6e8674601e9f0b3429da0d1ec0f748a9443e9b959ab537dba5ff671e7c8`

Le JSON privé original n'est pas présent dans le checkout. Son authentification
en production est une preuve communiquée par le demandeur, non une vérification
rejouée ici. Il ne faut ni demander sa publication dans Git, ni le reconstruire,
ni recalculer une nouvelle identité pour contourner le refus.

## Éléments communiqués et preuves encore nécessaires

Éléments communiqués par le demandeur, non relus sur une base :

| Boîte | Semaine | Création UTC | Révision |
|---|---|---|---|
| 1836 | 2026-10-19 | 2026-10-21 09:37:07.809036 | 24 → 25 |
| 1837 | 2026-10-19 | 2026-10-21 09:44:03.511877 | 24 → 25 |

Le diagnostic communiqué indique 72 → 72 réattributions, 8 → 8 correspondances,
4 → 6 hors source, aucune suppression ou modification historique et deux ajouts.
Ces éléments cadrent le delta recherché; ils ne contiennent pas les lignes et
les événements nécessaires à son authentification indépendante.

Pour chacun des deux ajouts, il manque ici :

1. **PK et ligne complète authentifiée** : `measured_on`, semaine, toutes les
   valeurs scientifiques et métadonnées, séquence, origine, précision temporelle,
   auteur courant, observateur initial et importateur éventuel. Le lundi ISO ne
   permet pas de déduire `measured_on`. Zéro et NULL doivent rester distincts.
2. **Audit de création** : PK, organisation, acteur, horodatage, action, identité
   de boîte et métadonnées complètes liées par `measurement_id`; comparer
   `metadata.valeurs` à l'état initial de la mesure. L'horodatage de l'audit est
   généré séparément : ne pas inventer une égalité exacte avec `created_at`.
3. **Chaîne de modifications éventuelles** : corrections, imports et receipts
   applicables, avec réconciliation entre valeurs de création et valeurs
   courantes. Un audit `entry` ne suffit pas à conclure à une saisie interactive.
4. **Preuve des deux incréments** : comparaison authentifiée des boîtes et de
   leurs séquences avant/après, cohérente avec les événements réellement
   constatés, sans autre mutation à absorber silencieusement.
5. **État relationnel et thermique** : absence de changement incompatible des
   liens, doublons et sources environnementales; confirmer séparément zéro lien
   thermique, confirmé ou pending, sur les 72 mesures à déplacer.

Ces preuves doivent rester dans un canal privé approuvé, hors Git et hors
conversation. Leur collecte en production exige une autorisation distincte;
elle n'est pas exécutée ni implicitement autorisée par ce rapport.

## Pourquoi les révisions ne prouvent pas les créations

`BiologicalMeasurement.save()` verrouille la boîte et incrémente
`polyp_state_revision` à chaque sauvegarde. À la création seulement, il affecte
la nouvelle révision à `polyp_state_sequence`
(`backend/apps/measurements/models.py:221-248`). Une création normale depuis 24
est donc compatible avec révision 25 et séquence 25; ce n'est pas une preuve
suffisante de la transition.

Le service de correction conserve l'identité, `created_at` et la séquence, mais
une sauvegarde incrémente aussi la révision. D'autres mutations métier peuvent
également l'incrémenter. Le service de création normal écrit la mesure et son
audit dans la même transaction, mais son audit ne contient pas de couple explicite
de révisions avant/après
(`backend/apps/measurements/services.py:190-238`). Ne pas fabriquer ce couple.

La correction CCA-EOB calcule sa révision finale comme le maximum de la révision
courante augmentée du nombre de mouvements touchant la boîte et des séquences
finales de ses mesures. Il ne faut donc ni revenir de 25 à 24, ni supposer sans
calcul que toute valeur `after` augmente exactement de un
(`backend/apps/cultures/historical_2026/cca_eob_correction.py:343-355`).

## Protections Python et SQL conservées

Aucun code d'exécution, contrat, témoin, privilège SQL, trigger ou migration n'a
été modifié. En particulier restent en place :

- authentification du document complet en Python et exigence d'une empreinte
  historique indépendante pour chacune des deux décisions administratives;
- égalité scientifique intégrale avec l'original, donc refus des ajouts actuels;
- identités et organisation fixes, ordre des mouvements, valeurs brutes et
  séparation entre zéro et NULL;
- verrouillage Box-first, `READ COMMITTED`, verrou de table biologie
  `SHARE ROW EXCLUSIVE` et thermique `SHARE` jusqu'à la fin de la transaction;
- refus des liens thermiques sur les mouvements, y compris `pending_review`;
- comparaison du snapshot brut avant écriture et de toutes les colonnes des
  mesures après écriture; seul le `box_id` prévu peut changer;
- atomicité des mouvements, révisions, audits et consommation d'approbation;
- release indépendante, restauration vérifiée, approbation et autorisation
  finale distinctes, échéances et contrôle du rôle dédié.

Ces protections existantes ne constituent pas une validation des deux ajouts.
Une future exception devra vérifier les preuves en Python **et** en SQL sous
verrous, maintenir les audits stables pendant le contrôle, comparer chaque ligne
historique, puis ne tolérer que les deux ajouts authentifiés et leurs effets
explicitement démontrés. Les 72 PK, destinations, ordre et correspondances
scientifiques devront rester identiques. Toute troisième ligne, dérive de valeur,
révision supplémentaire ou état thermique incompatible devra bloquer tout le lot.

## Transition SQL : aucun artefact installable à ce stade

Le writer n'a pas changé. Aucun artefact v2-vers-nouveau writer ni SHA-256
correspondant n'est produit : il n'existe pas de nouveau corps revu à installer.
Émettre un remplacement maintenant donnerait une fausse impression de préparation.
Les trois tests ci-dessous concernent les protections existantes, **pas** une
transition v2-vers-nouveau writer.

Une fois les preuves disponibles et le nouveau contrat implémenté, la même
livraison devra contenir la transition déterministe exacte : préflight existant,
transaction et timeouts, authentification préalable du corps v2 attendu, de ses
métadonnées, propriétaire et ACL, puis remplacement du seul objet nécessaire et
vérification du nouveau contrat avant COMMIT. Préserver rôle, lock, reader si
inchangé, table d'approbation et triggers. Refuser une définition ancienne
inconnue, sans reprovisionnement destructif ou remise à zéro d'approbation.
Relever ensuite le SHA-256 des octets de l'artefact produit hors ligne.
L'installation restera une opération de production séparément autorisée.

## Lecture du code par le runtime dédié

Le contrat impose `polypbase_cca_eob` sans appartenance aux groupes `polypbase`
ou `postgres`, code et dépendances lisibles mais `.env` illisible
(`docs/cca-eob-production-execution.md:338-344`). Les settings dédiés désactivent
le chargement dotenv et n'ont pas besoin de la clé Django du service normal.

Le déploiement utilise un umask protecteur et fait `uv sync --frozen`; il répare
explicitement les droits du frontend public, mais n'applique pas de politique
persistante équivalente pour la lecture Python par ce runtime
(`deploy/scripts/deploy_release.sh:123-148`). C'est une lacune de préparation;
les modes, ACL, ancêtres et cibles de liens réellement installés ne sont pas
connus ici. Un défaut précis de permissions en production n'est donc pas attesté.

**Solution minimale proposée, à implémenter et valider après inventaire privé des
chemins** : une étape idempotente de droits, versionnée dans le workflow existant,
réappliquée après mise à jour du checkout et synchronisation des dépendances.
Utiliser des ACL nominatives en lecture seule pour le compte dédié, sur une
liste explicite des fichiers de code et de ressources nécessaires, et uniquement
le droit de traversée sur leurs ancêtres. Inclure les cibles résolues de
l'interpréteur, de la bibliothèque standard et des dépendances; ne pas supposer
que `.venv` est autonome. Les exécutables nécessaires restent exécutables.

Ne pas appliquer de lecture récursive ou d'ACL par défaut à `backend`, au checkout
entier ou à un répertoire mixte : cela pourrait rendre des secrets présents ou
futurs accessibles. Exclure `.env`, identifiants, `.git`, sauvegardes, journaux et
artefacts d'autorisation privés. Ne pas élargir l'umask, les groupes ou les droits
d'écriture du runtime. Ne pas copier `.env` dans un runtime séparé.

La validation future doit tester sous le compte dédié, sans connexion DB, la
traversée et les imports nécessaires, l'absence de droits d'écriture et le refus
de lecture des secrets, avant et après un déploiement. Aucun changement de droits
ni test sur la VM n'a été effectué. Cette solution reste une proposition, non un
correctif installé ou validé sous Debian.

## Validation locale réalisée

Trois tests existants, et eux seuls, ont été exécutés depuis `backend/` :

```sh
PYTHON_DOTENV_DISABLED=1 POSTGRES_DB= POLYPBASE_TEST_POSTGRES=0 POLYPBASE_PG18_BIN='C:/Program Files/PostgreSQL/18/bin' uv run python -B manage.py test apps.cultures.test_cca_eob_role_provisioning.ProvisioningSqlExecutionTests.test_tampered_helper_body_is_never_dropped_or_replaced apps.cultures.test_cca_eob_role_provisioning.ProvisioningSqlExecutionTests.test_unrecognized_role_helper_ownership_and_memberships_refuse_provisioning apps.cultures.test_cca_eob_role_provisioning.ProvisioningSqlExecutionTests.test_provisioning_fails_closed_instead_of_queueing_behind_a_table_lock --settings=config.test_settings --verbosity=2
```

Résultat : **3 tests OK, 52,405 s, aucun skip**. Le harness crée un cluster neuf
sur un port loopback libre, applique les migrations réelles, exécute le SQL émis,
puis arrête et supprime le cluster. La base Django par défaut n'est pas utilisée.
Le préflight production n'est pas remplacé pour une installation réelle : le
harness utilise les statements de `body()` sur sa cible synthétique distincte.

Couverture précise : refus d'un corps de helper altéré sans remplacement ni
suppression; refus d'identité de rôle/propriétaire/membership incompatible;
refus borné lorsqu'un verrou de table empêche le provisionnement.

**Non validé** : acceptation des 72 mouvements avec les deux ajouts, conservation
et audits de ces ajouts, refus du troisième ajout par un nouveau contrat,
modifications historiques/révisions/liens thermiques sous ce nouveau contrat,
rollback et concurrence de ce nouveau chemin, transition SQL v2 exacte et droits
runtime Debian. Aucun de ces résultats ne doit être déduit des trois tests.
Aucune suite complète ni QA sur données réelles n'a été lancée.

## Reprise et verdict

Fournir une preuve privée authentifiée des deux lignes, de leurs audits et de la
transition des révisions. Si les audits sont absents ou incompatibles, demander
une décision métier explicite au lieu de présumer leur légitimité. L'acceptation
métier des deux ajouts ne remplace pas leur identification et leur vérification.

Puis implémenter et revoir le delta Python/SQL, son artefact de transition et la
matrice de tests synthétiques demandée, ainsi que le correctif persistant de
lecture runtime après inventaire des chemins.

**BLOCKED — les deux ajouts restent refusés; aucune autorisation n'a été élargie.**
