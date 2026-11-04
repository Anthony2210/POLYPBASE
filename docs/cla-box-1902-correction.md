# Correction ciblée de CLA-JKA-1.009 — préparation locale

## Statut et décision

**READY FOR INDEPENDENT REVIEW — aucune autorisation d'exploitation.**
Aucun accès production, VM, Neon ou base partagée. Aucun commit, push ou déploiement.
Les faits de départ ci-dessous sont ceux communiqués dans la demande, pas un nouveau
constat de production. Les validations utilisent exclusivement des données synthétiques.

Seule la Box **1902**, organisation **1 / Aquarium de Paris active**, est autorisée :

| Champ | Avant exigé | Après |
|---|---|---|
| `global_code` | `CLA-JKA-1.009` | `CLC-JKA-1.009` |
| `strain_id` | **213**, `CLA-JKA-1` | PK cible explicitement revu, `CLC-JKA-1` |
| Species de la souche | **131**, `Cyanea lamarckii` | `Chrysaora lactea` exacte |
| `local_code` | `CLA-JKA-1.09` | **inchangé** |
| `status` | `inactive` | **inchangé** |

Le PK, `box_number`, QR/tags, mesures (zéros et NULL distincts), statuts, dates,
emplacements, mouvements, lignées, transferts et audits antérieurs sont conservés.
Ni fusion ni réactivation ni recréation. `previous_global_code` reste vide : il n'est
pas un alias générique. La souche source partagée 213 et l'espèce 131 ne changent pas.
Aucune autre CLA, SME/SSP ou CCP/SMA n'est corrigée par ce chemin.

## Mécanisme dédié minimal

Le lot `correct_historical_identities --family cla` traite dix Box et renomme aussi
`.010` : **il n'est pas autorisé pour cette demande**. Ses listes et gardes sont intactes.

Le service `historical_2026/cla_box_1902.py` et la commande `correct_cla_box_1902`
utilisent l'opération distincte **`cla_box_1902_identity_20261103_v1`**. Les arguments
obligatoires sont `--organization-id`, `--box-id`, `--source-strain-id`,
`--source-species-id`, `--target-strain-id`. Les quatre premiers exigent respectivement
**1, 1902, 213, 131**, sans recherche de remplacement par code ou adoption d'une autre Box.

Sans `--apply`, simulation seule, aucun audit ou reçu persistant. Le SHA-256 déterministe
lie identité, cible explicite, schéma réel, frontière d'exécution, références, attributions,
provenances, Box propres, dépendances et audits. La sortie opérateur ne contient pas les
notes ou mesures. Le plan complet reste dans le reçu d'application.

L'application exige `--actor-id` d'un administrateur institutionnel actif rechargé sous
verrou et `--expected-plan-hash`. Elle reprend la transaction détenue par
`identity_execution.session`, la barrière PostgreSQL `EXCLUSIVE MODE NOWAIT` sur toutes
les tables managed, puis les verrous et la revalidation du plan avant une mise à jour
conditionnelle exacte de 1902. Elle enregistre les deux codes via `register_box_code` :
verrous CLA/CLC ordonnés, compteurs jamais abaissés, réservations symétriques intactes.
Les collisions globales ou réservations historiques sur l'ancien ou nouveau code bloquent.
Les autres Box et dépendances sont comparées avant/après, hors les deux deltas autorisés.

Un audit Box et un reçu **`cla_box_1902_correction`, `object_id="1902"`** sont ajoutés
atomiquement, avec avant/après, acteur, opération, hash et plan revu. Aucun reçu
`identity_reconciliation` du lot de dix n'est fabriqué. Un état cible sans le reçu exact
est bloqué. Après succès, un nouveau plan revu est sans effet ; l'ancien hash est périmé.
Une erreur de verrou, état, hash ou audit annule toute l'application.

## Références avant application : étape distincte à revoir

La correction ne crée **aucune** référence. Il faut une cible existante explicitement
revue et éligible dans l'organisation, et les attributions exactes CLA/CLC.
La cible globale sans témoin propre préexistant n'est pas adoptée.

Si réellement manquantes et sans ambiguïté, réutiliser **séparément** la préparation
additive existante `prepare_historical_identity_references`, famille `cla`, organisation
1, source 213, opération **`cla_sme_references_20261027_v1`** :

- Species exactement `Chrysaora lactea`, seulement si absente ; candidat approximatif
  ou multiple = arrêt, jamais correction implicite de taxonomie ;
- attributions institutionnelles manquantes `CLA → Cyanea lamarckii` et
  `CLC → Chrysaora lactea` ;
- si strictement nécessaire, souche détenue par organisation 1 `CLC-JKA-1`,
  `number=1`, `origin_code="JKA"`, avec `LocalStrainIdentity` CLC ;
- provenance biologique inconnue : `origin_id` et `provenance_code_assignment_id`
  NULL ; aucune identité globale ou filiation inférée.

Ne pas préallouer le PK cible. Relever `prepared_target_strain_id`, puis produire un
**nouveau plan ciblé 1902**. Revoir l'intégralité du plan de préparation avant de
l'autoriser : aucune référence existante ni Box n'y est modifiée. Cette phase ne vaut
jamais autorisation du lot CLA ou de la correction ciblée. Son hash, sa sauvegarde et
son autorisation sont distincts de ceux de la correction.

## Frontière future de production — non exécutée

Le nouveau nom d'opération est explicitement admis par `identity_execution.py`, sans
exception aux protections existantes. Pour la correction ciblée, le document root
exige l'opération **`cla_box_1902_identity_20261103_v1`**, l'organisation **1**, la famille
technique **`cla-box-1902`**, le PK cible explicite, l'acteur, le hash et la cible attestée.
Une autorisation du lot complet ou de préparation ne peut pas être réutilisée.
Les PK source et Box fixes sont aussi liés au plan revu et vérifiés par le service.

La procédure de sécurité de `cla-sme-postgresql-operations.md` reste obligatoire :
accord humain explicite immédiatement avant chaque mutation, release revue, maintenance,
PostgreSQL 18 peer local attesté, runtime non privilégié, documents root protégés,
vérification de l'identité effective de la base, sauvegarde fraîche avec restauration
indépendante attestée et archive vérifiée, autorisation courte et recontrôles avant commit.
Les chemins et schémas JSON root restent ceux de CLA/SME ; aucune nouvelle clé, aucun
credential, aucun contournement des réservations ou de la frontière n'est introduit.
**Ce document n'est pas une procédure d'exécution autorisée ni un accord production.**
La séquence future spécifique doit être revue indépendamment avant toute utilisation.

## Compatibilité avec le lot CLA futur

Après la correction ciblée, le lot complet existant voit 1902 corrigée mais sans son
propre reçu et reste **BLOCKED**. C'est voulu et testé : il ne peut pas adopter
silencieusement le reçu ciblé ou corriger les neuf autres Box au titre de cette demande.
Aucune modification du contrat historique de dix Box n'est introduite ici.
Une future réconciliation complète nécessitera une revue et une implémentation explicites
de reconnaissance de ce prédécesseur exact, ou une autre décision métier autorisée.
Ne jamais contourner le blocage ni fabriquer de reçu du lot de dix.

## Validation synthétique

Les tests `test_cla_box_1902` et `test_identity_execution` couvrent l'état exact,
la préparation additive distincte, les sentinelles des autres CLA/SME/SSP/CCP/SMA,
zéro/NULL, QR/historique, collisions, dérive du hash, droits, rollback, idempotence,
blocage du lot complet et frontière root simulée (opération, sauvegarde, revalidation).
`test_cla_box_1902_postgres` ajoute les preuves PostgreSQL de concurrence, NOWAIT,
transaction réellement read-only et rollback. Les résultats exécutés sont consignés
dans le rapport de livraison ; aucun de ces tests ne vérifie l'état réel de production.
