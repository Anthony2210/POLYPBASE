# Spécification des transferts CSV Polypbase

## Transfert v1 actuel — compatibilité historique

La version courante est `polypbase.box_transfer.v1`. Le CSV est généré côté
frontend et le parcours actuel lit une seule ligne de données. Préparation et
import sont deux opérations distinctes : la préparation crée un `BoxTransfer`
(planifié) et son audit obligatoire dans une transaction; elle ne déplace pas
la boîte source. L'import crée une nouvelle culture/boîte locale à destination
et n'affecte pas la boîte source.

Le fichier actuel est encodé en UTF-8 avec marque BOM. Les intitulés et champs
v1 décrits ci-dessous restent inchangés; cette documentation ne redéfinit pas
le payload historique.

## Colonnes obligatoires

| Intitulé français | Nom technique | Utilisation |
|---|---|---|
| Format | `format` | Version et validation du fichier |
| Identifiant transfert | `transfer_id` | Traçabilité et détection des doublons |
| Structure expéditrice | `source_organization_name` | Identification de la provenance |
| Code boîte source | `source_global_code` | Référence externe conservée |
| Nom scientifique | `species_scientific_name` | Recherche ou création de l'espèce |
| Code souche | `strain_code` | Recherche ou création de la souche |
| Polypes transférés | `transferred_polyp_count` | Relevé initial de la nouvelle boîte |

Les autres colonnes apportent le destinataire prévu, le préparateur, le nom
commun, l'origine, les parents connus, les consignes, l'état sanitaire, les
notes et le lien QR de la boîte source. Le payload historique inclut des champs
opérationnels qui ne conviennent pas comme futur contrat minimal de partage de
lignée; cela ne change ni leur présence ni leur traitement en v1.

Quand l'interface est française, le fichier utilise les intitulés français.
L'import reconnaît aussi les noms techniques anglais des anciens fichiers.

## Résolution et création destination

L'utilisateur choisit l'organisation et la zone locales. La Strain est résolue
par `Species` + code source `strain_code`; une nouvelle Strain est créée au
nom de l'organisation destination si aucune collision de propriété n'existe.
Une Strain étrangère ou sans organisation avec le même couple est un conflit.
L'import v1 ne requiert pas AAA/`LocalStrainIdentity` et ne transporte pas
`GlobalStrainIdentity`. La boîte créée reçoit un nouveau code local/global selon
les contraintes actuelles (`Box.global_code` reste globalement unique).

L'import crée l'emplacement initial, le relevé initial, l'enregistrement
`BoxTransferImport` et l'audit de destination dans une transaction atomique.
Une erreur annule l'ensemble. La préparation et son audit source sont également
atomiques depuis `6e92674`. La boîte source n'est ni déplacée, ni réaffectée,
ni désactivée par l'import.

## Sécurité et traçabilité

- Le format et les champs obligatoires sont vérifiés avant création; Django
  applique le contrôle d'organisation active et les permissions Admin.
- La replay identity historique est version + nom d'organisation source +
  `transfer_id`. Un rejeu séquentiel conserve la réponse HTTP 400 existante.
  Le rejeu est revérifié après le verrouillage Species; une perte tardive sur
  contrainte est traduite après rollback. Les autres `IntegrityError`s restent
  des erreurs distinctes.
- Les collisions de Strain étrangère ou `organization=NULL` restent des conflits.
- La boîte source et son organisation ne sont pas modifiées par l'import.
- Le payload est conservé dans `BoxTransferImport.source_data`; l'importateur,
  la date, la boîte créée et l'audit de destination sont enregistrés.
- V1 n'embarque ni identité biologique globale ni graphe de lignée portable.

## API

- Préparation : `POST /api/box-transfers/`
- Import : `POST /api/box-transfer-imports/`

L'import reçoit l'unique ligne CSV dans `source_data`, ainsi que les
identifiants locaux de l'organisation et de la zone et le code proposé.

## Transfer v2 — Phase 2B source — DONE et intégrée

Intégrée à `main` au commit `20adf0a` (`feat: add transfer v2 protocol foundation`).
La Phase 2B persiste `TransferEnvelope` / `TransferItem` avec des UUID stables,
le discriminateur `polypbase.transfer` et la version exacte `2.0`. V1 demeure
le chemin de compatibilité historique disponible, séparé et inchangé. V2 utilise
des tables `TransferEnvelope` / `TransferItem` distinctes, sans réinterpréter
`BoxTransfer` ou `BoxTransferImport`.

### Contrat sémantique indépendant du transport

Le module `backend/apps/cultures/transfer_v2_protocol.py` valide des mappings
avec DRF, sans lookup ni mutation en base. Il ne prouve ni authenticité, ni
confiance, ni autorisation. Ce n'est pas un adaptateur JSON/CSV ou une API.

Champs d'enveloppe exclusivement :

- `protocol`: `polypbase.transfer`;
- `protocol_major`: `2`, `protocol_minor`: `0`;
- `transfer_id`: UUID opaque stable généré côté serveur;
- `created_at`: timestamp sérialisé en ISO 8601 UTC;
- `source_institution_id`: snapshot de `Organization.portable_id`;
- `source_institution_name`: snapshot descriptif;
- `destination_institution_id`: UUID optionnel, défaut `null`;
- `destination_institution_name`: affichage optionnel, défaut chaîne vide;
- `items`: collection non vide.

Champs d'item exclusivement : `item_id` (UUID opaque stable),
`source_box_code`, `source_strain_code`, `species_scientific_name`,
`global_strain_id` (UUID biologique obligatoire) et `declared_polyp_quantity`.
L'identité portable d'item est `(transfer_id, item_id)`; l'unicité SQL est
`(envelope, item_id)`. La même Box peut figurer dans plusieurs packages.

Le contrat `2.0` refuse le protocole v1 et tout major/minor autre que `2.0`.
Le dispatch prend également en charge le contrat exact `2.1` décrit plus bas.
Chaque contrat refuse les champs inconnus à chaque niveau, champs requis absents,
UUID malformés, collections
vides et identités d'item dupliquées. Les UUID valides sont normalisés à la
sérialisation. Versions et quantités doivent être des entiers, sans coercition
booléen/chaîne/flottant. La quantité déclarée est requise, de `0` à `2147483647`
(limite structurelle du champ ORM); **zéro reste une vraie valeur**, sans
préjuger de son acceptation opérationnelle. Aucune quantité n'est inférée des
relevés et aucun stock n'est décrémenté.

### Construction source interne et snapshots

`create_source_package(actor=..., source_organization=..., selections=...)`
accepte une collection de mappings `source_box_id` / `declared_polyp_quantity`,
et les snapshots destination optionnels. Il recharge le contexte source,
réutilise l'autorité Admin existante et filtre les Boxes dans cette institution.
Il valide chaque sélection avant persistance. Chaque Strain source doit déjà
avoir une `GlobalStrainIdentity`; aucune identité n'est créée, inférée ou attachée.
L'erreur précise l'item concerné. Aucun critère actif-only n'est ajouté aux Boxes,
cette décision produit restant ouverte.

Le serveur génère les UUID, fige nom institution/codes/noms scientifiques/UUID
biologique/quantité dans des colonnes, puis écrit parent, tous les items et
`AuditLog` obligatoire dans une transaction. Un échec d'item ou d'audit annule
tout. Audit et auteur restent locaux; aucune attribution privée n'est portable.
Les relations source sont protégées contre suppression. La sérialisation lit
ces colonnes, jamais les noms/codes actuels des modèles source. Aucun payload
JSON dupliqué ni service de modification; `editable=False` exclut les champs
des formulaires ordinaires, sans prétendre empêcher un writer ORM/SQL technique.
Ces writers doivent préserver les snapshots établis.

Les PK locaux, utilisateurs, memberships, contacts, emplacements, mesures,
notes, état de cycle de vie, QR, audit, instructions AAA/BBB/X et lignées sont
exclus. Une destination `null` n'autorise personne à accepter le package.
Aucune mutation de Box/Strain/emplacement/relevé ni finalisation de v1.

### Limites à l'intégration de Phase 2B

Cette section décrit l'état de Phase 2B, pas les capacités ajoutées ensuite.
Aucun endpoint/UI v2, receipt/acceptation destinataire, résolution de Strain
destination, représentation locale canonique, diagnostic de namespace,
allocation X, identité portable de Box, lignée portable, inbox de transfert
direct ni adaptateur fichier. Aucune mutation du cycle de vie ou du stock source.
La Phase 3 — résolution destination de `GlobalStrainIdentity`, représentation
locale canonique et diagnostic de namespace — est la prochaine phase technique.

Le même cœur backend d'acceptation pourrait servir le transfert direct entre
Organisations de la même instance; le transport fichier resterait un adaptateur
pour installations distinctes, échanges hors ligne et systèmes externes.
Cela ne décrit pas une capacité actuelle.

## Transfer v2 — contrat exact 2.1 : fondation de lignée source

`create_source_package(..., protocol_version=(2, 1))` sélectionne explicitement
le nouveau contrat interne. Le défaut reste `(2, 0)` et aucun endpoint/UI ne
sélectionne `2.1`. Les autres versions, chaînes, booléens et coercitions sont
refusés. `2.0` conserve ses champs et sa sérialisation, refuse `lineage` en entrée
et accepte les items historiques dont `lineage_snapshot` est `NULL`.

`2.1` conserve les champs d'enveloppe et d'item de `2.0`, avec
`protocol_minor=1` et un bloc `lineage` obligatoire par item :

```text
lineage:
  root_node_id: UUID
  nodes:
    - node_id: UUID
  edges:
    - edge_id: UUID
      source_node_id: UUID
      target_node_id: UUID
      relationship_type: subculture | sexual_reproduction | historical_import | other | transfer
      transfer_id: UUID  # required only for transfer
      item_id: UUID      # required only for transfer
```

Aucun autre champ n'est accepté, y compris dans chaque nœud et arête. Pour une
arête non `transfer`, les deux champs de provenance doivent être absents, pas
`null`. La provenance d'une arête `transfer` désigne un transfert antérieur,
pas le package actuellement préparé. La préparation ne crée aucune continuation
destination ni arête de transfert sortant.

Le parser reste sans accès DB et sans autorisation. Il valide les UUID
normalisés, les identités uniques dans chaque graphe, l'existence de la racine
et des extrémités, l'absence de boucle/cycle et le chemin dirigé de chaque nœud
vers la racine. Une même arête répétée dans plusieurs items doit conserver
exactement extrémités, type et provenance; l'union des graphes ne peut pas être
cyclique. Plusieurs racines et familles déconnectées sont autorisées.
Les nœuds sont triés par `node_id`, les arêtes par `edge_id`, sur leurs UUID
canoniques. Limites techniques par item : 250 nœuds (convention du graphe local)
et 1 000 arêtes; tout dépassement échoue sans troncature.

La projection appartient à une institution : identités `(organization, node_id)`
et `(organization, edge_id)`, réutilisables dans plusieurs projections. Les
bridges locaux sont nullable et one-to-one, créés paresseusement, sans backfill.
Un UUID n'autorise jamais un lookup opérationnel global. Les écritures internes
valident l'institution, les extrémités et les associations établies; une
contradiction échoue sans correction ni réaffectation.

La construction suit seulement les prédécesseurs explicites locaux et déjà
connus dans cette projection. Elle n'infère rien des codes, Species, Strain ou
GlobalStrainIdentity, n'inclut ni frères ni descendants inutiles et refuse une
BoxLineage conduisant à une Box étrangère. Les ancêtres étrangers connus restent
des nœuds de projection sans Box artificielle. Un graphe limité à sa racine est
valide : aucune ascendance supplémentaire connue n'est transportée.

Avant de figer les champs scientifiques source, `2.1` applique `eligible_strains`
à la Strain : propriété source ou éligibilité historique sans propriétaire.
Le comportement source `2.0` n'est pas modifié. Le snapshot de lignée ne porte
que les champs ci-dessus : aucun code, nom d'institution, statut, volume, mesure
(y compris zéro), emplacement, mouvement, date d'événement, note, motif,
utilisateur, audit, PK, URL ou permission. Les champs source de l'item restent
séparés; `declared_polyp_quantity` n'est pas une preuve de lignée.

`TransferItem.lineage_snapshot` conserve le graphe JSON validé à la création.
La sérialisation lit ce snapshot, jamais le graphe courant, et refuse une
persistance `2.1` sans snapshot valide. Les anciens packages ne sont pas enrichis.
Les écritures ORM/SQL techniques doivent préserver les snapshots et bridges
établis; aucun writer public de projection n'est ajouté.

Sur PostgreSQL, le build `2.1` possède une transaction locale `SERIALIZABLE`,
avec au plus cinq tentatives complètes en cas de conflit de sérialisation,
deadlock ou course sur une contrainte d'identité/bridge. Une transaction
PostgreSQL déjà ouverte par l'appelant est refusée : son isolation et son retry
ne peuvent pas être garantis ici. Toutes les lectures partagent le snapshot DB;
la configuration globale d'isolation et les writers de lignée restent inchangés.
Les contraintes SQL arbitrent les identités concurrentes, sans verrou de
processus. Bridges, enveloppe, items, snapshots et audit obligatoire sont
atomiques. SQLite valide le fonctionnel, pas cette garantie de concurrence.

Les FK de projection utilisent `PROTECT`, y compris les bridges Box/BoxLineage
et les extrémités. Désactivation/réactivation restent inchangées; un reset
historique destructif visant une histoire projetée est refusé et rollbacké,
jamais rendu possible par suppression de la connaissance portable.

Cette phase n'ajoute ni receipt/import de package, ni acceptation, ni mutation
de stock/cycle de vie, ni allocation X ou création destination.
