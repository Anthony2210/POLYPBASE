# Spécification des transferts CSV Polypbase

## Objectif

Le CSV transporte les informations nécessaires pour recréer une culture dans
une autre installation Polypbase. Il contient une ligne par transfert, encodée
en UTF-8 avec marque BOM pour rester lisible dans Excel.

La version courante est `polypbase.box_transfer.v1`.

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
notes et le lien QR de la boîte source.

Quand l'interface est française, le fichier utilise les intitulés français.
L'import reconnaît aussi les noms techniques anglais des anciens fichiers.

## Création de la boîte destinataire

L'utilisateur choisit la structure et l'emplacement locaux. Polypbase propose
ensuite `<code_souche>.<numéro suivant>`, avec un numéro sur au moins trois
chiffres. Le code est modifiable, mais :

- il doit commencer par le code de la souche et finir par un numéro ;
- il doit être unique dans la base ;
- en cas de conflit, le serveur refuse l'import et renvoie la prochaine
  suggestion disponible.

Le nouvel identifiant numérique est toujours généré par la base destinataire.

## Sécurité et traçabilité

- Le format et toutes les colonnes obligatoires sont contrôlés avant création.
- Un transfert est unique par version, structure source et identifiant source.
- Un second import du même transfert est refusé.
- La boîte source et son organisation ne sont jamais modifiées.
- Le contenu source est conservé dans `BoxTransferImport.source_data`.
- L'importateur, la date, la boîte créée et l'action d'audit sont enregistrés.

## API

- Préparation : `POST /api/box-transfers/`
- Import : `POST /api/box-transfer-imports/`

L'import reçoit la ligne CSV normalisée dans `source_data`, ainsi que les
identifiants locaux de la structure et de l'emplacement et le code proposé.
