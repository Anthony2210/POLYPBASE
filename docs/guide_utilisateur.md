# Guide utilisateur interne Polypbase

Ce guide accompagne la livraison. Il n'est pas affiché dans l'application.

## Relevés factuels

Une boîte accepte un relevé par date. Une deuxième saisie à la même date met à
jour le relevé existant.

Les Alertes sont abandonnées et ne sont plus une fonctionnalité active. Les
comptages, températures, tendances et écarts à une consigne sont présentés
comme des faits, sans avertissement opérationnel ou biologique inféré. Une
baisse de polypes ou un écart de température ne génère pas d'alerte; il n'y a
plus de parcours de consultation ou de résolution d'alertes.

Une valeur `0` reste une mesure réelle, distincte d'une donnée absente.
L'historique Actions et les traces `AuditLog` restent actifs et sont préservés,
y compris les anciennes actions liées aux alertes. Seul le stockage historique
`Alert` reste dormant; l'abandon de la fonctionnalité ne supprime pas les données.

## Désactiver une boîte

« Désactiver » retire une boîte du suivi actif sans effacer ses relevés, sa
parenté ou les actions associées. Une boîte désactivée peut être réactivée par
un administrateur. Cette action est préférable à une suppression définitive.

## Préparer un transfert

Dans le profil administrateur, ouvrir « Transfert entre structures » :

1. choisir une boîte active et la structure destinataire ;
2. indiquer le nombre de polypes transmis et les précautions dans les notes ;
3. confirmer la préparation ;
4. télécharger le CSV et, si nécessaire, imprimer l'étiquette QR.

Le transfert enregistre une intention : il ne change pas le propriétaire de la
boîte source.

## Importer un transfert CSV

Dans la même section, ouvrir « Importer un transfert CSV » :

1. sélectionner le fichier reçu ;
2. contrôler l'aperçu (structure source, espèce, souche, polypes et conditions) ;
3. choisir la structure et l'emplacement destinataires ;
4. contrôler le code proposé. Il peut être modifié, mais doit rester unique et
   commencer par le code de la souche ;
5. confirmer la création ;
6. utiliser « Ouvrir la nouvelle boîte » pour contrôler le résultat.

Un même transfert ne peut être importé qu'une fois. L'identifiant numérique de
la boîte source n'est jamais réutilisé. La boîte source reste inchangée.

## Rôles

- **Administrateur** : comptes, structures, emplacements, transferts, imports,
  désactivation et réactivation des boîtes.
- **Technicien** : consultation et saisie des données de laboratoire et
  opérations autorisées sur les boîtes de sa structure.
- **Lecteur** : consultation uniquement ; aucune modification.

## Vérification après une mise à jour

Après application des migrations et redémarrage, vérifier un relevé, son
historique Actions, un export CSV, un transfert puis son import. Vérifier
l'absence de parcours d'alertes et d'avertissements inférés des mesures sur les
fiches boîtes et les emplacements. Effectuer aussi un contrôle sur tablette
avant la mise en production.
