# Frontend et expérience utilisateur

## Architecture

Le frontend est une application React/TypeScript construite avec Vite. `frontend/src/App.tsx` orchestre l'état global, le routage interne et les chargements partagés. Les vues et composants spécialisés vivent dans `frontend/src/components/`, les appels réutilisables dans `frontend/src/api/`, les types communs dans `frontend/src/types.ts` et les styles sont agrégés depuis `frontend/src/styles/index.css`.

Le démarrage charge uniquement le profil, les zones et le tableau de bord. La liste complète des boîtes n'en fait pas partie : `data.boxes` ne contient que les boîtes connues (fiches ouvertes, accès récents) et n'est complète que lorsque `boxCollection.status` vaut `ready`. Les routes qui en ont besoin (zones hors historique, étiquettes, transferts d'Administration, recherche saisie) la demandent via `requestBoxCollection` dans `App.tsx` et affichent leur propre chargement ou erreur; `needsFullBoxCollection` dans `utils/boxCollection.ts` en tient la liste. Ne jamais afficher des résultats, des effectifs ou des suggestions calculés sur une liste partielle. La création ordinaire ne charge plus cette collection pour prédire un numéro : le serveur alloue le code dans la transaction de création. Une fiche atteinte par URL ou QR se résout par `/api/boxes/?q=` puis `/api/boxes/<id>/`, sans la liste.

Les extractions M8/M9 sont intégrées : `PilotageView`, `CreateBoxPanel`, leurs composants feuilles, `QrSearchModal` (import eager) et `BoxPage` sont dans `components/`. `BoxPage` possède l'état/interactions locales de fiche, `LatestMovementBanner` son éditeur; `App` conserve mutations, caches, navigation, contexte institutionnel et récupération. La clé de montage de fiche associe institution et boîte. Les tests source/AST doivent suivre le vrai propriétaire après extraction, sans affaiblir les assertions. La recommandation architecturale actuelle est de ne pas poursuivre le découpage pour le seul nombre de lignes, pas une interdiction permanente d'extraction.

Il n'y a pas React Router. Le chemin navigateur est traduit en état de route et manipulé avec l'API History. Avant de modifier la navigation, rechercher les parseurs de chemin, helpers de navigation, gestion de `popstate` et liens internes concernés.

`frontend/src/api/client.ts` gère les requêtes communes, l'en-tête d'organisation et les jetons CSRF. Ne pas disperser une seconde implémentation de ces mécanismes dans un composant.

Pour tout travail important de design/refonte UI, consulter [`frontend-style.md`](frontend-style.md) avant d'introduire un nouveau motif visuel ou d'interaction : il recense les références et règles de réutilisation POLYPBASE; les composants, styles et tests actuels restent l'autorité d'implémentation.

## Fraîcheur des requêtes : garanties et limite actuelle

Le garde de génération institutionnelle empêche une réponse d'un ancien contexte de remplacer le nouveau. **Il ne garantit pas la fraîcheur dans une même institution.** À la base `ec81a18`, le client partage les GET en vol par institution + chemin; `App` n'utilise pas encore l'invalidation ni une barrière par Box. Une lecture pré-mutation ou une réponse de mutation retardée peut republier une projection ancienne, y compris après correction à zéro. L'audit transmis a reproduit le défaut frontend, sans démontrer de perte serveur.

**M10.1 reste local/non committé**, avec gardes de détail/publication, invalidation ciblée, nettoyage par génération et watermark de mutation. Le High a été confirmé corrigé; la dernière correction Medium attend sa revue indépendante. Ce patch n'est pas une garantie de main. M10.2 (collections de zones paginées) est un chantier distinct non intégré; conserver la publication d'une collection complète, les IDs dédupliqués et les gardes d'institution. `refreshRecovery` reste un slot unique et ne doit pas rejouer automatiquement une écriture. Statuts et preuves : [`../../POLYPBASE_CURRENT_STATE.md`](../../POLYPBASE_CURRENT_STATE.md).

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

## Création ordinaire de boîte

Le bouton d'accueil ouvre `CreateBoxPanel` en modale sur desktop/tablette; le téléphone utilise le même composant inline. Admins et techniciens conservent leurs droits. Le formulaire demande la souche existante, l'emplacement, la date d'entrée obligatoire et une note facultative, sans code ni numéro modifiable. La création rapide de souche reste réservée aux Admins et conserve ses règles de nommage actuelles.

`POST /api/boxes/automatic/` alloue et persiste le code côté serveur. Une intention confirmée conserve son UUID et son payload dans la session de l'onglet, par utilisateur et institution; après envoi, les champs restent verrouillés jusqu'au résultat ou à une résolution explicite. `/api/boxes/automatic/resolve/` retrouve la boîte créée ou annule durablement une clé absente avant d'autoriser la correction du brouillon. Un échec de résolution ne libère jamais la clé. Le succès affiche le code engagé et propose d'ouvrir la fiche. Contrat, transactions et limites de validation : [`../../backend/AUTOMATIC_BOX_CREATION.md`](../../backend/AUTOMATIC_BOX_CREATION.md).

## Fiche boîte et événements

« Dernier déplacement » suit « Dernier relevé » et désigne le dernier `BoxMovement` persisté, pas la dernière frontière `BoxLocation`. Correction inline, origine → destination à noms complets, une flèche sans chevrons individuels; Save masqué sans changement normalisé, Cancel disponible. La saisie Paris conserve un instant inchangé et refuse les heures modifiées ambiguës/inexistantes; le conflit de révision exige une relecture explicite. Aucun délai de correction de mesure ne se transfère aux mouvements : Django valide capacités, périodes et chronologie.

Les graphiques distinguent `movement` enregistré et `location_change` dérivé. Timestamp complet en détail, comparaison d'identité à la milliseconde JavaScript, placement sur axe journalier; période filtrée en jours locaux navigateur, dernier jour inclus. Ce contrat ne force pas Paris sur tous les graphiques. Overview utilise les périodes, sans recevoir les mouvements persistés. Son payload initial est borné, mais la fenêtre par défaut charge l'historique complet si nécessaire, avec loading/error/retry. Une future semaine seule exige un rendu d'intervalle, pas un point à lundi inventé.

## Internationalisation

L'interface utilise `frontend/src/i18n/` avec le français par défaut et l'anglais. Le catalogue français définit les clés; TypeScript vérifie la parité du catalogue anglais. Tout nouveau texte visible doit être ajouté dans les deux langues, sans chaîne parallèle codée directement dans le JSX.

Les contenus taxonomiques ont leurs propres langues configurées par `CONTENT_LANGUAGES`, actuellement français, anglais et japonais. Ne pas confondre langue de l'interface et langue du contenu scientifique.

Les identifiants et commentaires de code restent en anglais simple. Ne pas utiliser le point médian dans le texte d'interface.

## Validation frontend

Choisir le test ciblé correspondant au domaine, puis exécuter le build de production. `npm run build` inclut contrôle CSS, TypeScript et Vite. Les commandes et la QA navigateur sont détaillées dans [`development-deployment.md`](development-deployment.md).

Une modification du frontend doit aussi relire le contrat API et le contexte métier concerné : l'UI ne peut pas déterminer seule la signification de `0`, d'un statut ou d'une organisation.
