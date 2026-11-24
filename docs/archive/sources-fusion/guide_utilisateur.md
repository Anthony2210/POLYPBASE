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

## Consulter ou renseigner une preuve ADN

Le badge à côté du code de la boîte indique si un lien de preuve ADN a été
renseigné. Le badge permet de consulter le lien, son auteur et sa date d'ajout.
Un administrateur ou un technicien de la structure active peut ajouter,
modifier ou retirer la preuve. En cas de conflit, relire la fiche avant de
réessayer. Le retrait conserve la trace de la preuve dans l'historique.

Dans « Nouvelle boîte », le lien de preuve ADN est facultatif. Saisir une
adresse HTTP ou HTTPS. Polypbase ne vérifie pas le contenu de la preuve et ne
la transmet ni à l'espèce, ni à la souche, ni aux boîtes enfants.

## Administration sur ordinateur

Les sections Équipe, Inventaire, Espèces, Emplacements, Envois et réceptions et
Historique sont accessibles aux administrateurs. Les sections Référentiels
partagés et Institutions sont désactivées. Une fiche Espèce historique sans
profil est consultable, mais ne permet ni de modifier le nom ou la référence
WoRMS, ni de créer une souche.

## Proposer le don d'une boîte entière

Dans **Administration > Envois et réceptions**, choisir **Envoyer**, puis une
boîte et la structure destinataire. Vérifier l'éligibilité et l'historique de
la boîte, puis confirmer l'envoi de la proposition. Les deux structures peuvent
consulter les détails de la proposition dans leurs listes respectives. La boîte
reste sous le contrôle de la structure source et son historique n'est pas
partagé avec le destinataire. Il n'y a pas de bouton d'acceptation ni d'annulation
(annulation source par API seulement).

Le don est le seul type d'envoi proposé dans l'interface. Les écrans de partage
externe ne sont pas accessibles et l'ancien parcours de transfert par CSV est
retiré. Cela ne retire pas l'export CSV de suivi des relevés.

## Rôles

- **Administrateur** : équipe, emplacements, espèces, propositions de don,
  désactivation et réactivation des boîtes, selon les droits de la structure active.
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
