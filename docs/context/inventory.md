# Inventaire des boîtes

## Périmètre

L'Inventaire se trouve dans Administration et suit donc la règle desktop-only décrite dans [`frontend-ux.md`](frontend-ux.md). Il sert surtout à retrouver, analyser et qualifier les boîtes historiques `pending_review`. Sa liste est paginée et filtrée côté serveur; elle ne doit pas charger toutes les boîtes pour construire la page.

Le point d'entrée frontend principal est `frontend/src/components/BoxInventoryAdminSection.tsx`. L'API et le queryset dédiés vivent dans `backend/apps/cultures/`. Les règles de statut restent centralisées dans les services de cycle de vie, pas dans ce composant.

## Liste et filtres

L'interface actuelle permet notamment :

- recherche par code global, code local ou espèce;
- statuts Toutes, À vérifier, Actives et Inactives;
- emplacement courant, y compris sans emplacement;
- année de création calculée côté serveur et limitée à l'organisation active;
- dans « À vérifier » uniquement, absence de relevé ou dernier relevé antérieur à un seuil;
- date de référence initialisée à la date locale du jour et modifiable;
- seuil d'âge choisi par l'utilisateur en mois calendaires complets.

Le filtrage, les compteurs et les ensembles sélectionnables doivent tous utiliser l'organisation active côté serveur. Un changement de filtre doit rendre clairement le nouvel ensemble; il ne doit jamais laisser une sélection implicite issue d'un autre périmètre.

Pour la cohorte historique explicitement reconnue dont la date technique d'insertion ne représente pas la création réelle, la date affichée comme « création » utilise le premier relevé disponible. Cette exception est implémentée dans le queryset d'Inventaire et couverte par tests; ne pas la généraliser à d'autres lots ou dates sans décision métier.

Un relevé postérieur à la date de référence n'est pas « plus ancien que X » et ne reçoit pas d'âge négatif. Une boîte sans relevé n'a ni date ni ancienneté inventée.

Les indicateurs de synthèse sont intentionnellement masqués. Ne pas les réactiver sans décision produit explicite.

## Aide à la qualification

L'aide est :

- disponible seulement lorsque le filtre de statut vaut `pending_review`;
- repliable sans modifier filtres ni sélection;
- fondée sur la date de référence et le seuil choisis;
- explicable à partir de faits visibles;
- dépourvue de score opaque;
- incapable de modifier automatiquement un statut.

La sous-ligne d'explication n'apparaît que lorsque l'aide et la ligne la justifient. Une date ancienne ou l'absence de relevé peut attirer l'attention. Un ancien relevé `0/0` peut renforcer une explication, mais `0/0` est une donnée réelle et ne qualifie jamais automatiquement une boîte. Voir [`measurements-integrity.md`](measurements-integrity.md).

## Qualification individuelle

Une boîte `pending_review` peut être qualifiée active ou inactive via les services backend. Les formulaires doivent expliquer les conséquences sans réimplémenter les règles :

- active avec emplacement explicite lorsque disponible;
- exception historique active sans emplacement, sans zone inventée;
- inactive avec le mécanisme prévu lorsque le motif historique n'était pas renseigné;
- aucune fausse date de fin, aucun faux motif et aucun faux commentaire.

Les lignes actives ou inactives ne sont pas éligibles à ce flux. Le backend revalide toujours le statut au moment de l'opération.

## Sélection et actions groupées

Seules les boîtes `pending_review` sont sélectionnables. Les modes actuels sont sélection ligne par ligne, page visible ou ensemble explicitement filtré.

- La sélection peut traverser la pagination et son compteur reste visible.
- Tout changement de filtre la vide avec un retour utilisateur clair.
- « Tout sélectionner » signifie l'ensemble répondant aux filtres explicites, recalculé côté serveur; il ne repose pas sur les seules lignes déjà chargées.
- Le serveur refuse une sélection filtrée supérieure à 500 boîtes et ne tronque jamais silencieusement.
- Au moment de l'action, le backend revalide organisation, existence, statut et éligibilité de chaque identifiant.

Les qualifications groupées vont uniquement de `pending_review` vers `active` ou `inactive`. Elles réutilisent les services de cycle de vie. Chaque boîte est atomique : un échec laisse cette boîte inchangée mais n'annule pas les réussites des autres. Le rapport identifie réussites et échecs avec leur cause, puis la liste, les filtres et compteurs sont rafraîchis depuis le serveur.

Avant une activation groupée, la confirmation distingue les boîtes avec et sans emplacement et avertit explicitement sur les secondes. Aucun emplacement n'est attribué automatiquement. L'inactivation historique utilise le mécanisme de motif manquant prévu par le modèle et n'invente aucune information.

## Chargement et aperçu

Le queryset de liste utilise des sous-requêtes et chargements ciblés pour les informations de dernier relevé et de dernier emplacement. Éviter d'ajouter une requête par ligne ou de charger le détail complet de toutes les boîtes.

`BoxTrackingPreview` demande le détail d'une boîte seulement à l'ouverture de l'aperçu et charge le graphique paresseusement. L'aperçu doit :

- distinguer emplacement courant et dernier emplacement historique;
- conserver les valeurs zéro;
- afficher séparément loading, erreur, absence de relevé et données disponibles;
- ne pas remonter la page lors du changement de boîte ou d'état ciblé.

## Tests et vérifications

Préserver les tests des filtres serveur, années limitées à l'organisation, calcul d'âge calendaire, dates futures, absence de relevé, valeurs `0/0`, sélection paginée, limite de 500, revalidation, succès partiels et lazy loading. Une modification visuelle importante doit être vérifiée dans un vrai navigateur desktop; l'Administration n'a pas à être rendue utilisable sur tablette.

Commandes et environnement QA : [`development-deployment.md`](development-deployment.md).
