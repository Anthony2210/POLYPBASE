# Transfer v2 — Phase 6, jalon 1 : identité globale de Box et épisodes institutionnels

État : implémentation backend interne, **non revue**, sans endpoint, sans UI et
sans transition de propriété active. Ce document ne décrit pas une capacité de
don disponible.

## Décisions métier prises en entrée

- Seule une Box entière peut être donnée; aucun don direct de polypes.
- Un transfert conserve la même Box biologique; l'institution destinataire en
  reçoit sa propre représentation locale, avec son identifiant local.
- L'histoire scientifique reste continue; une seule institution est
  propriétaire opérationnelle à un instant donné; les représentations
  précédentes restent historiques.
- Une continuation de transfert n'est pas un repiquage. Aucune mesure n'est
  fabriquée ni recopiée comme nouvelle observation.
- Accès futurs et permissions sur les descendants : lots ultérieurs séparés.

## Pourquoi une nouvelle identité globale

Aucun identifiant existant ne désigne « la même Box biologique à travers des
institutions » :

| Identifiant | Ce qu'il désigne | Pourquoi il ne convient pas |
|---|---|---|
| `Box.pk` | une ligne locale | change à chaque représentation |
| `Box.global_code` | code opérationnel, unique en base | voir ci-dessous |
| `GlobalStrainIdentity` | une souche biologique | plusieurs Boxes partagent une souche |
| `PortableLineageNode.node_id` | un nœud dans la projection **d'une institution** | identité `(organization, node_id)`; un nœud par Box locale (bridge one-to-one) |

`GlobalBoxIdentity.global_box_id` (UUID, `models.py`, classe
`GlobalBoxIdentity`) comble ce manque. Il n'accorde aucun droit et n'est jamais
une clé de recherche opérationnelle.

### Pourquoi ne pas dupliquer `global_code`

`Box.global_code` est `unique=True` et participe au namespace de codes
(`BoxCodeNamespace`, `register_box_code`), à la réservation des identités
réconciliées (`previous_global_code`) et aux garde-fous historiques PostgreSQL
(`historical_temperature_box_guard`). Une représentation destination doit
recevoir **son propre** code local alloué par le serveur. Réutiliser le code
source violerait l'unicité, confondrait les namespaces d'institutions et
ferait d'un code opérationnel une preuve biologique, ce que le projet refuse.
Aucune contrainte existante n'est modifiée.

### Pourquoi ne pas réaffecter `Box.organization`

Réaffecter la Box source ferait migrer mesures, emplacements, mouvements,
auteurs et audits dans une autre institution, et est déjà bloqué par
`historical_temperature_box_guard` (`measurements/0012`) lorsque des
températures historiques existent. Le modèle retenu laisse chaque ligne
locale dans son institution.

## Modèle

`BoxOwnershipEpisode` (`backend/apps/cultures/models.py`) : une
représentation locale d'une `GlobalBoxIdentity` par une institution.

| Champ | Sens |
|---|---|
| `identity` | identité biologique (FK `PROTECT`) |
| `sequence` | rang chronologique, 1, 2, 3… sans trou |
| `organization` | institution de cette représentation (snapshot immuable) |
| `local_box` | Box locale (one-to-one `PROTECT`) : une Box n'appartient qu'à un épisode |
| `started_at` | début de propriété; `NULL` = inconnu (épisode d'origine legacy), jamais reconstruit |
| `ended_at` | fin de propriété; `NULL` = propriétaire opérationnel courant |
| `arrival_transfer_id`, `arrival_item_id` | provenance Transfer v2 de l'arrivée (UUID portables, pas des FK) |
| `recorded_at` | date d'enregistrement technique |

Exemples :

```text
A1 -> B1 -> C1 : (1, A, A1, fin t1) (2, B, B1, t1..t2) (3, C, C1, t2..)
A1 -> B1 -> A2 : (1, A, A1, fin t1) (2, B, B1, t1..t2) (3, A, A2, t2..)
```

Un retour en A crée une nouvelle Box locale A2 et un nouvel épisode; A1 et son
épisode restent inchangés (`local_box` one-to-one, épisodes immuables).

### Contraintes en base

- `box_ownership_sequence_unique` : `(identity, sequence)` unique → chaîne linéaire.
- `box_ownership_single_current` : index unique partiel `(identity) WHERE ended_at IS NULL`
  → **au plus un propriétaire courant** par identité, y compris pour un writer SQL.
- `box_ownership_arrival_unique` : un item de transfert ne porte qu'une arrivée.
- `box_ownership_sequence_positive` : `sequence >= 1`.
- `box_ownership_arrival_provenance` : origine (`sequence = 1`) sans provenance;
  continuation (`sequence > 1`) avec `transfer_id`, `item_id` et `started_at`.
- `box_ownership_chronology` : `ended_at >= started_at` quand les deux sont connus.
- `local_box` unique (one-to-one) et FK `PROTECT` : une Box représentée ne peut
  être supprimée.

### Invariants transactionnels (`BoxOwnershipEpisode.save()`)

Un `CHECK` ne voit qu'une ligne. Les règles inter-lignes sont validées dans
`save()`, sous verrou de la ligne `GlobalBoxIdentity` (tous les writers d'une
même chaîne sont sérialisés), puis de la Box :

- la Box locale appartient à l'institution de l'épisode et n'est pas un alias fusionné;
- un nouvel épisode commence courant et prolonge la chaîne sans trou;
- une succession change d'institution et commence exactement à la fin du précédent;
- seule la fin d'un épisode courant peut être enregistrée, une seule fois;
- `QuerySet.update()`/`delete()` et `delete()` sont refusés; l'identité est immuable.

**Non garanti par la base :** existence d'au moins un propriétaire courant
(la future acceptation ferme puis crée dans la même transaction; une contrainte
différée PostgreSQL serait possible mais n'est pas ajoutée), contiguïté des
séquences et égalité `organization = local_box.organization` face à
`bulk_create()` ou SQL direct. `verify_ownership_chain()` (lecture seule,
`box_ownership.py`) détecte ces divergences sans les réparer.

### Garde sur la Box

`Box.save()` refuse désormais de changer l'institution d'une Box **déjà
représentée** dans une chaîne. Une Box sans épisode — toutes les Boxes
existantes après migration — garde exactement le comportement antérieur.
`QuerySet.update()` contourne ce garde (comme les autres gardes Python);
`verify_ownership_chain()` signale alors `representation_institution_mismatch`.

### Aucune Box représentée ne devient un alias fusionné

Une Box liée à un épisode, courant **ou historique**, ne peut pas devenir un
alias (`merged_into`) :

- `Box.save()` refuse de renseigner `merged_into` sur une telle Box;
- l'unique écrivain applicatif de `merged_into`, la fusion PCA
  (`pca_identity_unification._apply_reviewed_plan`, utilisée par `apply_plan` /
  `unify_pca_identity` et `pca_production.apply_plan` / `pca_production`),
  écrit par `QuerySet.update()`. Son plan (`_make_plan`) ajoute le blocker
  explicite « global ownership history » pour la Box donneuse, via
  `boxes_with_ownership_history()`; l'inventaire dynamique des dépendances de
  Box bloquait déjà toute Box (donneuse ou canonique) ayant un épisode. La
  fusion échoue alors sans écriture (`Unresolved conflicts…` ou `Stale plan…`).

Concurrence (PostgreSQL, READ COMMITTED) : enrôlement, création d'épisode et
application de la fusion verrouillent **d'abord la ligne Box**. Si
l'enrôlement tient le verrou, la fusion attend puis voit l'épisode et refuse;
si la fusion le tient, l'enrôlement attend puis voit `merged_into` et refuse.
Aucun verrou global ni trigger n'est ajouté; un writer SQL direct sans ce
verrou reste hors garantie.

## Transfert vs repiquage

| | Repiquage | Transfert (futur) |
|---|---|---|
| Biologie | nouvelle Box issue d'un parent | la même Box biologique |
| Persistance | `SubcultureEvent`, `BoxLineage`, allocations | nouvel épisode de la même `GlobalBoxIdentity` |
| Lignée | arête `subculture` | aucune `BoxLineage`; arête portable `transfer` |
| Quantités | instantané avant/réparti/après | aucune; `declared_polyp_quantity` n'est pas une mesure |

Ce jalon ne crée ni `BoxLineage`, ni `SubcultureEvent`, ni relevé, ni mouvement,
ni emplacement, ni audit.

## Migration et données legacy

`0019_global_box_identity` : deux `CreateModel`, puis un `RunPython` sans effet
à l'aller. Aucun champ de `Box` n'est ajouté ou modifié; aucune identité n'est
créée; `global_code`, institutions, mesures, auteurs, emplacements et statuts
sont intacts.

Retour arrière : refusé (`RuntimeError`) si une identité ou un épisode existe,
pour ne pas détruire de provenance; possible seulement sur schéma vide.

Stratégie legacy **forward-only** : `enroll_origin_box(organization=, box=)`
attribue paresseusement et de façon idempotente une identité à une Box locale,
comme épisode d'origine (`sequence = 1`, `started_at = NULL`). Il n'est appelé
par aucun flux actuel. Il refuse :

- une Box d'une autre institution (`box_not_in_organization`);
- un alias fusionné (`merged_into`);
- une Box créée par un import Transfer v1 (`box_identity_provenance_unresolved`) :
  elle peut continuer une Box détenue ailleurs, et v1 transfère un nombre de
  polypes, pas une Box entière. Aucune identité n'est inférée.

## Intégration Transfer v2

Réutilisation, sans sous-système parallèle :

- provenance d'arrivée = `(TransferEnvelope.transfer_id, TransferItem.item_id)`,
  la même identité portable que l'arête `PortableLineageEdge` de type
  `transfer` (`portable_edge_provenance_valid`);
- `TransferItem.source_box` désigne la Box source de l'épisode courant.

Les protocoles `2.0` et `2.1` ne sont **pas** modifiés et ne transportent pas
`global_box_id`. Une future extension versionnée (par exemple `2.2`, non
implémentée, à approuver) devrait au minimum porter, par item :

- `global_box_id` (UUID) de l'identité source;
- `ownership_sequence` de l'épisode source (contrôle de fraîcheur/rejeu);
- l'indication explicite « Box entière » (don), distincte d'une quantité déclarée.

## Consommation par la future acceptation

Dans une transaction unique, sous verrou de l'identité :

1. revalider permissions destination et institution active;
2. vérifier que l'épisode source est courant et que son institution est la source du package;
3. allouer un code local destination (namespace existant) et créer la Box locale,
   sans relevé initial fabriqué;
4. fermer l'épisode source (`ended_at = t`) et créer l'épisode `sequence + 1`
   (`started_at = t`, provenance `transfer_id`/`item_id`);
5. créer l'arête portable `transfer` et l'audit des deux côtés;
6. traiter le cycle de vie de la Box source selon une décision produit à prendre.

## Décisions restant ouvertes

- Statut et emplacement de la Box source après don (inactivation ? motif ?).
- Lecture de l'histoire scientifique antérieure par l'institution destinataire.
- Rattachement éventuel des imports v1 existants (aujourd'hui refusés).
- Moment d'enrôlement de l'origine (préparation du package ? acceptation ?).
- Format exact d'une extension de protocole et interopérabilité inter-installations
  (un `global_box_id` étranger reçu crée-t-il une `GlobalBoxIdentity` locale ?).
- Contrainte différée PostgreSQL « au moins un courant » et trigger de garde de
  `Box.organization` côté SQL.
- Audit de l'enrôlement d'origine (aujourd'hui technique, sans acteur ni audit).

## Validation

- `apps.cultures.test_box_ownership` et `test_box_ownership_migrations` (SQLite).
- `apps.cultures.test_box_ownership_postgres` : concurrence, sur un cluster
  PostgreSQL 18 jetable vide (`config.sharing_pgqa_settings`, schéma issu des
  modèles); ignoré sous SQLite.
