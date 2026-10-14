# Guide utilisateur interne Polypbase

Ce guide accompagne la livraison. Il n'est pas affiché dans l'application.

## Relevés factuels

Une boîte accepte au plus un relevé biologique par semaine ISO. Une deuxième
saisie, même à la même date et même avec `0/0`, est refusée sans modification.
Utiliser l'action de correction du relevé existant; elle passe par PATCH et
conserve l'audit. Les relevés semaine seule sans date exacte ne sont pas encore
importables/persistables dans le parcours générique.

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

## Déplacement et correction

Un déplacement ordinaire entre zones est limité à un par boîte et semaine ISO
Europe/Paris. Affectation initiale, qualification et réactivation sont distinctes.
Le serveur vérifie l'emplacement attendu et peut demander de recharger après un
changement concurrent.

« Dernier déplacement » apparaît sous le dernier relevé. Le crayon transforme
le bandeau en éditeur inline; il ne crée pas un autre mouvement. Administrateur
ou technicien autorisé peut corriger sans délai ni obligation d'être l'auteur,
mais Django valide les périodes et la chronologie. Un événement legacy de
provenance non établie permet seulement la correction des notes. En conflit,
recharger et réexaminer : ne pas forcer ni réenregistrer silencieusement.

Une entrée/sortie de période d'emplacement dans l'historique ou le graphique
n'est pas automatiquement un déplacement physique prouvé.

## Préparer un transfert — parcours v1 conservé, section désactivée

La section Transferts est actuellement désactivée dans Administration. Les étapes
suivantes décrivent le parcours v1 conservé dans le code, **pas une action accessible
aujourd'hui dans l'interface**; elles n'autorisent pas à réactiver la section :

1. choisir une boîte active et la structure destinataire ;
2. indiquer le nombre de polypes transmis et les précautions dans les notes ;
3. confirmer la préparation ;
4. télécharger le CSV et, si nécessaire, imprimer l'étiquette QR.

Le transfert enregistre une intention : il ne change pas le propriétaire de la
boîte source.

## Importer un transfert CSV — parcours v1 conservé

Dans le parcours conservé de cette même section désactivée :

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

La préparation d'une release se valide localement avec des données synthétiques :
création/correction de relevé, zéro, historique Actions, déplacement/correction,
repiquage et export CSV. Ne pas tester un parcours désactivé comme s'il était actif.
Après un déploiement explicitement autorisé, les contrôles de service ne doivent
pas créer de donnée scientifique uniquement pour un smoke test. Vérifier
l'absence de parcours d'alertes et d'avertissements inférés des mesures sur les
fiches boîtes et les emplacements. Effectuer aussi un contrôle sur tablette
avant la mise en production.
