# Guide utilisateur interne Polypbase

Ce guide accompagne la livraison. Il n'est pas affiché dans l'application.

## Relevés et alertes

Une boîte accepte un relevé par date. Une deuxième saisie à la même date met à
jour le relevé existant.

- Une baisse du nombre de polypes par rapport au relevé précédent crée une
  alerte biologique persistante.
- Une température manuelle distante d'au moins 1 °C de la consigne crée une
  alerte de température pour l'emplacement.
- Le bandeau rouge de la fiche signale les alertes actives. Le bouton « Voir le
  détail » ouvre leur contenu.
- Une alerte est automatiquement résolue au retour à la normale.
- Un administrateur ou un technicien peut aussi choisir « Marquer comme
  résolue » après vérification. La confirmation, l'utilisateur et la date sont
  conservés dans l'historique.

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
- **Technicien** : consultation et saisie des données de laboratoire, résolution
  des alertes et opérations autorisées sur les boîtes de sa structure.
- **Lecteur** : consultation uniquement ; aucune modification ni résolution
  manuelle d'alerte.

## Vérification après une mise à jour

Après application des migrations et redémarrage, vérifier un relevé, une
alerte, un export CSV, un transfert puis son import. Effectuer aussi un contrôle
sur tablette avant la mise en production.
