# Frontend et expérience utilisateur

## Architecture

Le frontend est une application React/TypeScript construite avec Vite. `frontend/src/App.tsx` orchestre l'état global, le routage interne et les chargements partagés. Les vues et composants spécialisés vivent dans `frontend/src/components/`, les appels réutilisables dans `frontend/src/api/`, les types communs dans `frontend/src/types.ts` et les styles sont agrégés depuis `frontend/src/styles/index.css`.

Il n'y a pas React Router. Le chemin navigateur est traduit en état de route et manipulé avec l'API History. Avant de modifier la navigation, rechercher les parseurs de chemin, helpers de navigation, gestion de `popstate` et liens internes concernés.

`frontend/src/api/client.ts` gère les requêtes communes, l'en-tête d'organisation et les jetons CSRF. Ne pas disperser une seconde implémentation de ces mécanismes dans un composant.

## Supports

- **Desktop** : administration, graphiques, exports et analyse dense.
- **Tablette paysage de laboratoire** : recherche, scan, fiche boîte et saisie opérationnelle.
- **Téléphone** : doit rester fonctionnel, sans exiger une refonte profonde pour chaque chantier.

Administration est desktop-only. `frontend/src/hooks/useIsDesktopApp.ts` centralise la détection. En non-desktop, l'entrée de navigation Administration est absente et toute route `/administration...` est remplacée par `/` avant de rendre la vue. Il ne doit exister ni écran admin intermédiaire, ni message demandant d'utiliser un ordinateur, ni tentative de rendre ces pages responsives sans nouvelle décision produit. Cette règle d'affichage ne remplace jamais les permissions backend.

## Principes d'interface

- Concevoir pour un usage répété : dense mais lisible, commandes prévisibles, texte concis.
- Réutiliser les tokens CSS, composants, icônes et motifs d'interaction existants.
- Préserver la position de défilement lors d'une mutation ciblée; éviter les rechargements globaux sans nécessité.
- Éviter les changements de mise en page qui gênent l'usage répétitif.
- Une action doit rester compréhensible sans hover. Prévoir focus visible, clavier, zones tactiles suffisantes et états disabled.
- Distinguer clairement chargement, liste vide, erreur réseau, donnée absente et donnée égale à zéro.
- Ne pas afficher deux fois une information déjà évidente dans le même bloc.
- Ne pas dupliquer dans React une permission, transition ou validation dont Django est l'autorité.

Pour une évolution importante, inspecter le résultat dans un vrai navigateur aux supports réellement concernés. L'Administration se vérifie sur desktop; sur tablette, vérifier seulement l'absence du menu et la redirection. Supprimer ensuite captures et artefacts QA temporaires.

## Internationalisation

L'interface utilise `frontend/src/i18n/` avec le français par défaut et l'anglais. Le catalogue français définit les clés; TypeScript vérifie la parité du catalogue anglais. Tout nouveau texte visible doit être ajouté dans les deux langues, sans chaîne parallèle codée directement dans le JSX.

Les contenus taxonomiques ont leurs propres langues configurées par `CONTENT_LANGUAGES`, actuellement français, anglais et japonais. Ne pas confondre langue de l'interface et langue du contenu scientifique.

Les identifiants et commentaires de code restent en anglais simple. Ne pas utiliser le point médian dans le texte d'interface.

## Validation frontend

Choisir le test ciblé correspondant au domaine, puis exécuter le build de production. `npm run build` inclut contrôle CSS, TypeScript et Vite. Les commandes et la QA navigateur sont détaillées dans [`development-deployment.md`](development-deployment.md).

Une modification du frontend doit aussi relire le contrat API et le contexte métier concerné : l'UI ne peut pas déterminer seule la signification de `0`, d'un statut ou d'une organisation.
