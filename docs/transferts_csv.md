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

## FUTURE — direction Transfer v2 (non implémentée)

V1 demeure le chemin de compatibilité historique. V2 devrait utiliser une
enveloppe versionnée construite par le serveur, des identifiants stables de
transfert et d'item, `GlobalStrainIdentity` explicite et des métadonnées
ancestrales portables. Aucun schéma JSON exact n'est décidé ici.

Le même cœur backend d'acceptation pourrait servir le transfert direct entre
Organisations de la même instance; le transport fichier resterait un adaptateur
pour installations distinctes, échanges hors ligne et systèmes externes.
Cela ne décrit pas une capacité actuelle.
