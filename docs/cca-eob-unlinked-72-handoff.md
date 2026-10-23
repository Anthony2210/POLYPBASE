# CCA-EOB — exception thermique des 72 réattributions non liées

## Statut et limites

Préparation locale uniquement, **READY FOR INDEPENDENT REVIEW**. Ce document
n'autorise aucun accès, provisionnement, déploiement ou apply en production.
Aucun accès production, Neon, SSH ou base partagée n'a été effectué pendant cette
implémentation. Les tests utilisent des données synthétiques.

Ce complément remplace seulement le veto thermique global décrit dans
[`cca-eob-production-execution.md`](cca-eob-production-execution.md). Toutes ses
autres frontières restent obligatoires : socket protégé, cible attestée, rôle
dédié, acteur administrateur actif de l'organisation 1, recette SQL approuvée,
sauvegarde restaurée, deux décisions administratives distinctes, autorisation
finale courte, transaction unique et retrait des capacités.

Le diagnostic communiqué indique 72 mouvements, zéro lien thermique direct et
une portée courante conforme au plan historique authentifié. Ce résultat est un
prérequis scientifique, **pas une autorisation d'écriture ni une preuve de
fraîcheur lors d'un futur apply**. Le véritable artefact et son empreinte fixe
ne sont pas présents dans le checkout et n'ont pas été reconstitués.

## Cause exacte et solution minimale

Le lecteur SQL calcule `historical_readings_exist` par un `EXISTS` sur toute la
table `HistoricalTemperatureReading`, sans restriction aux 72 mesures. Le
planificateur Python et le writer SQL exigeaient tous deux son absence globale.
Une température sans rapport avec la correction suffisait donc à tout bloquer.

Le trigger `historical_temperature_biology_guard`, lui, interdit déjà de changer
la boîte d'une mesure ayant **un lien direct**, confirmé ou `pending_review`.
Il autorise une réattribution non liée sous `READ COMMITTED`. Aucun trigger,
binding, corps de fonction thermique, migration ou règle de cycle de vie n'est
modifié. Il n'existe ni option force ni désactivation de garde.

Lorsqu'une température historique existe, l'exception exige :

- le plan historique complet, son empreinte fixe et exactement 72 PK distinctes;
- une égalité de l'intégralité du plan scientifique courant avec l'original :
  identités, boîtes source/cible, semaines ISO, comptes, dates, auteurs,
  provenance, contextes, baseline, ordre des mouvements et révisions;
- zéro association thermique sur chacun des mouvements, quel que soit son statut;
- tous les autres contrôles existants, y compris institutions et permissions.

Une seule différence bloque tout. La seule normalisation de représentation
admise est celle des quatre historiques **exactement vides**, encodés `[]` par
le planificateur local historique et `false` par le lecteur de production.
L'original conservé n'est pas modifié. Aucun champ scientifique n'est normalisé.

Les températures des mesures non déplacées, même dans les trois boîtes,
restent intactes. L'exception ne change pas leur identité, statut ou lecture.
Le chemin antérieur sans aucune température historique conserve ses conditions
d'éligibilité; cette exception n'en fait pas un nouveau correcteur générique.

## Témoin historique indépendant, sans nouvelle identité fabriquée

L'opérateur fait installer, après authentification indépendante, le document
protégé au chemin fixe :

`/etc/polypbase/cca-eob-authenticated-historical-plan.json`

Il contient exactement trois clés :

- `schema` : `cca_eob_unlinked_72_v2`;
- `original_plan_hash` : l'empreinte indépendante du **document historique
  complet**, déjà fixée et authentifiée; le nom du champ est conservé;
- `original_document` : le document historique complet et inchangé, incluant
  toute son enveloppe et son évidence. Ne pas y substituer un plan extrait.

L'empreinte indépendante communiquée pour ce document complet est
`7be6a6e8674601e9f0b3429da0d1ec0f748a9443e9b959ab537dba5ff671e7c8`.
Elle n'a pas été vérifiée contre le JSON privé pendant l'implémentation locale.
Le validateur calcule SHA-256 sur la sérialisation historique existante
`json.dumps(original_document, sort_keys=True, default=str).encode()` et exige
l'égalité avec cette identité fournie indépendamment. Il ne faut jamais
remplacer celle-ci par le hash recalculé d'une extraction ou d'une reconstruction.

L'enveloppe suit le schéma existant `cca_eob_production_plan_v2`, avec exactement
`schema`, `target`, `actor_id`, `plan`; cible objet, acteur entier positif et
`plan` objet. **Après authentification du document complet**, le validateur
extrait uniquement `original_document["plan"]["correction"]`, plan scientifique
V3 objet, prêt, sans bloqueur et contenant exactement 72 mouvements distincts.
Ce plan scientifique reste strictement égal à la correction courante, hormis
la seule normalisation des quatre historiques vides déjà décrite. Les anciennes
cibles, gardes et autres évidences restent dans le document authentifié; elles
ne sont ni reconstruites ni remplacées par leurs versions courantes.

Le témoin v1, dont le hash portait sur le plan interne, est refusé. Préparer le
nouveau conteneur v2 autour du document privé original **sans modifier ce document
ni son empreinte**; ne pas republier son contenu dans Zed, Git ou un ticket.
Schéma incompatible, empreinte différente ou dérive scientifique : **STOP**.

Les protections existantes de lecture des documents root s'appliquent. Aucun
chemin ni hash de substitution n'est accepté par la commande Django. Le témoin
complet entre dans le nouveau plan de production; il est relu lors de la
reconstruction sous verrous. Les audits enregistrent séparément
`execution_evidence.historical_plan_hash` (identité du document historique
complet, nom conservé), le hash scientifique courant et le hash complet du
nouveau plan de production. L'original demeure dans le témoin
protégé et le document d'approbation conservé après consommation.

Pour **chacune** des décisions `approve` et `authorize-apply`, l'émetteur SQL
exige en plus `--historical-plan-hash`. L'administrateur doit fournir la valeur
depuis la source d'authentification indépendante du document historique complet,
**jamais la copier depuis le plan candidat**. Une substitution cohérente du plan et de son hash embarqué
est ainsi refusée si l'empreinte indépendante reste inchangée. Les empreintes
ne constituent pas seules une preuve de provenance : la confiance dans
l'administrateur indépendant demeure explicite.

## Concurrence, atomicité et conservation

L'ordre existant de verrouillage est conservé. Avant la dernière lecture,
`cca_eob_lock_v1` détient les verrous de lignes puis `SHARE ROW EXCLUSIVE` sur
la biologie et `SHARE` sur les relevés thermiques. Ce dernier interdit les
INSERT/UPDATE/DELETE concurrents jusqu'au commit, même quand aucun lien
n'existe encore. Un `select_for_update` sur un résultat vide ne le remplacerait
pas. Un lien déjà committé est visible à la nouvelle lecture `READ COMMITTED`
et provoque un refus.

Le writer SQL vérifie lui-même le témoin, les 72 mouvements, le snapshot brut,
les identités et l'absence de liens avant toute écriture, indépendamment de
Python. Après les mouvements, il compare toutes les colonnes de toutes les
mesures de la portée à leur état attendu : seul le `box_id` approuvé peut
changer. Il recontrôle l'absence de liens avant les révisions et audits.

Les 72 mouvements, révisions attendues, 72 audits individuels, audit récapitulatif
et consommation de l'approbation sont atomiques. Une erreur, dérive, échéance,
contrainte, deadlock ou timeout annule le lot complet. Zéro reste distinct de
NULL. Aucune température, évidence ou receipt thermique n'est supprimée,
détachée, modifiée ou recréée.

## Procédure future, exclusivement contrôlée par l'opérateur

1. **Revue indépendante du code et de l'artefact.** Relever la révision exacte;
   vérifier la matrice authentifiée des 72 mouvements et son empreinte fixe.
   Confirmer zéro lien sur ces PK, pas seulement un compte global. Toute mesure
   liée ou identité différente impose STOP, jamais un sous-lot de 71.
2. **Sauvegarde fraîche avant mutation.** Selon la procédure existante, produire
   une sauvegarde PostgreSQL complète, vérifier SHA-256, taille et chaîne de
   garde. Ne pas limiter la preuve à `pg_restore --list`.
3. **Restauration réellement isolée et vérifiée.** Restaurer intégralement sur
   une instance approuvée distincte, sans application connectée à la production.
   Contrôler schéma, contraintes, triggers actifs et empreintes/contenus des
   tables. Conserver un rapport indépendant lié à l'archive et à la cible.
   Aucune donnée restaurée ni secret dans Git ou dans ce document.
4. **Installation séparément autorisée.** Utiliser le workflow de déploiement
   existant pour le code. Toute installation des capacités SQL et du témoin
   exige sa propre autorisation. Ne pas modifier les triggers thermiques ni
   élargir les ACL du runtime. Si d'anciens helpers existent, le provisionnement
   refuse leurs définitions différentes : STOP jusqu'à une transition old/new
   précisément revue et approuvée; ne pas neutraliser ce contrôle ni supprimer
   des objets par cascade. Ne pas réutiliser une ancienne approbation.
5. **Nouveau plan complet read-only.** Avec la cible attestée et l'acteur réel,
   suivre la phase `plan` existante. Vérifier le témoin original, les 72 PK,
   toutes les baseline et les gardes déployées. Faire revoir le nouveau plan
   complet et son nouveau hash, sans remplacer l'ancien plan historique.
6. **Preuve de release et première décision.** Lier archive fraîche, rapport de
   restauration, référence de revue, acteur, cible et nouveau hash. Générer
   `approve` avec `--historical-plan-hash` provenant de la source indépendante.
   Installation SQL par l'administrateur, distincte de l'exécuteur runtime.
7. **Approbation explicite immédiatement avant apply.** Fenêtre coordonnée sans
   écrivains/importeurs ni maintenance de schéma. Nouvelle décision séparée
   `authorize-apply`, même empreinte historique indépendante, échéance au plus
   cinq minutes. Seulement après l'accord explicite, employer la commande
   d'apply existante avec le nouveau hash et sa confirmation exacte.
8. **Dernières garanties transactionnelles.** Ne pas contourner la reconstruction
   sous verrous, la comparaison totale du document, les gardes de release/cible,
   le contrôle SQL des liens ou les postchecks. Aucun retry automatique après
   deadlock, timeout ou expiration : diagnostic, nouvelle vérification de
   fraîcheur et nouvelle décision opérateur selon la procédure existante.
9. **Contrôles post-commit indépendants.** Comparer au baseline sauvegardé :
   exactement les 72 `box_id` attendus, toutes les autres colonnes biologiques
   identiques, révisions conformes, 73 nouveaux audits valides, anciens audits
   inchangés, approbation consommée et non rejouable. Vérifier l'identité des
   températures/évidences/receipts et de leurs statuts, des emplacements,
   mouvements, zones, auteurs et données des autres institutions. Contrôler
   contraintes et définitions/bindings/activation des triggers inchangés.
   Ne pas attendre un dry-run idempotent vert : l'exception exige encore l'état
   initial exact des 72 et refusera normalement après correction.
10. **Clôture et retour arrière.** Archiver les preuves hors Git et retirer les
    capacités selon la procédure existante. En cas d'anomalie après commit,
    arrêter les écritures et faire décider un retour arrière par l'opérateur;
    ne pas exécuter de réattribution inverse improvisée. Toute restauration ou
    mutation supplémentaire nécessite une nouvelle autorisation explicite.

## Validation locale et risques résiduels

- SQLite : 200 tests exécutés, résultat OK, dont 66 tests PostgreSQL ignorés.
- PostgreSQL 18 natif, cluster neuf, données entièrement synthétiques : sélection
  finale de 38 tests, **38 réussis, zéro échec/erreur/skip**. Elle couvre les 27
  `ProductionWorkflowTests`, les 7 `ThermalExceptionContractTests` et quatre tests
  SQL ciblés, dont le précédent cas de timeout revérifié avec succès.
- Après la dernière mise en forme des corps SQL et l'ajustement de l'appel du
  test de concurrence : les deux tests de succès des 72 et d'exclusion de
  création thermique concurrente ont été rejoués, **2 réussis, zéro skip**.
  Chaque cluster synthétique a été arrêté puis supprimé.
- Réattribution des 72 avec température sur une mesure inchangée; comparaison
  complète des colonnes et tables; zéro préservé; liens confirmés/pending;
  insertion concurrente exclue; plans périmés; institutions et permissions;
  audits; rollback tardif; refus SQL direct même avec snapshot rafraîchi;
  corruption d'une autre colonne par trigger détectée et rollbackée.
- Contrôles Django et absence de dérive des migrations : OK. Aucun changement
  frontend, traduction, migration ou cycle de vie. `git diff --check` : OK.
- Les diagnostics de l'éditeur signalent notamment des imports Django/psycopg
  non résolus dans son environnement, ainsi que des diagnostics de typage/style
  hors périmètre. Ils ne sont pas présentés comme un contrôle statique vert;
  l'exécution utilise l'interpréteur virtuel vérifié. Le nouvel accès de test à
  `__wrapped__` a été remplacé par un appel ordinaire puis revérifié sur PostgreSQL.
- Les premiers essais d'infrastructure native ont échoué avant les tests puis
  été nettoyés. Une première campagne partielle avait un échec de fixture corrigé
  et un timeout de connexion; les deux cas passent dans la sélection finale.
  La totalité du module PostgreSQL n'a pas été rejouée sur le diff final.

Les verrous de table ont un impact global temporaire sur les écritures. L'ordre
de prise de verrous peut encore produire un deadlock avec un import concurrent;
la sécurité repose sur le rollback complet, pas sur une promesse d'absence de
deadlock. Les tests d'insertion concurrente prouvent l'exclusion une fois les
verrous obtenus, pas tous les entrelacements d'acquisition. La fenêtre calme
reste obligatoire. Les données thermiques peuvent recevoir des append externes
hors fenêtre; distinguer cela d'une écriture du correcteur lors des comparaisons.

La QA TCP Windows ne valide pas la frontière socket Linux de production.
L'authenticité et la compatibilité de l'artefact réel devront être confirmées
par la revue opérateur; elles ne sont pas inférées des fixtures synthétiques.
Aucune décision produit nouvelle n'est nécessaire pour les mesures non liées
selon la décision V3 existante. Un lien sur l'une des 72, ou une divergence
scientifique, bloque cette voie et exige une décision séparée, jamais une
invalidation thermique automatique.
