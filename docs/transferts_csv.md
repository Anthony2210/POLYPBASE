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

## Transfer v2 — Phase 2B source, dans ce worktree uniquement

Implémenté sur `feat/transfer-v2-protocol`, **non intégré à main**. V1 demeure
le chemin de compatibilité historique disponible, sans modification. V2 utilise
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

Le parser refuse le protocole v1, tout major/minor autre que `2.0`, les champs
inconnus à chaque niveau, champs requis absents, UUID malformés, collections
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

### Toujours non implémenté

Aucun endpoint/UI/adaptateur fichier v2, inbox, receipt, preflight/acceptation,
résolution de Strain destination, allocation X, identité portable de Box ou
lignée portable. Ces phases restent ultérieures.

Le même cœur backend d'acceptation pourrait servir le transfert direct entre
Organisations de la même instance; le transport fichier resterait un adaptateur
pour installations distinctes, échanges hors ligne et systèmes externes.
Cela ne décrit pas une capacité actuelle.
