# Frontend et expérience utilisateur

## Architecture

Le frontend est une application React/TypeScript construite avec Vite. `frontend/src/App.tsx` orchestre l'état global, le routage interne et les chargements partagés de l'espace institutionnel. Les vues et composants spécialisés vivent dans `frontend/src/components/`, les appels réutilisables dans `frontend/src/api/`, les types communs dans `frontend/src/types.ts` et les styles sont agrégés depuis `frontend/src/styles/index.css`.

Le démarrage charge uniquement le profil, les zones et le tableau de bord. La liste complète des boîtes n'en fait pas partie : `data.boxes` ne contient que les boîtes connues (fiches ouvertes, accès récents) et n'est complète que lorsque `boxCollection.status` vaut `ready`. Les routes qui en ont besoin (zones hors historique, étiquettes, recherche saisie) la demandent via `requestBoxCollection` dans `App.tsx` et affichent leur propre chargement ou erreur; `needsFullBoxCollection` dans `utils/boxCollection.ts` en tient la liste. Ne jamais afficher des résultats, des effectifs ou des suggestions calculés sur une liste partielle. La création ordinaire ne charge plus cette collection pour prédire un numéro : le serveur alloue le code dans la transaction de création. Une fiche atteinte par URL ou QR se résout par `/api/boxes/?q=` puis `/api/boxes/<id>/`, sans la liste.

Les extractions M8/M9 sont intégrées : `PilotageView`, `CreateBoxPanel`, leurs composants feuilles, `QrSearchModal` (import eager) et `BoxPage` sont dans `components/`. `BoxPage` possède l'état/interactions locales de fiche, `LatestMovementBanner` son éditeur; `App` conserve mutations, caches, navigation, contexte institutionnel et récupération. La clé de montage de fiche associe institution et boîte. Les tests source/AST doivent suivre le vrai propriétaire après extraction, sans affaiblir les assertions. La recommandation architecturale actuelle est de ne pas poursuivre le découpage pour le seul nombre de lignes, pas une interdiction permanente d'extraction.

Il n'y a pas React Router. Le chemin navigateur est traduit en état de route et manipulé avec l'API History. Avant de modifier la navigation, rechercher les parseurs de chemin, helpers de navigation, gestion de `popstate` et liens internes concernés.

`frontend/src/api/client.ts` gère les requêtes communes, l'en-tête d'organisation et les jetons CSRF. Ne pas disperser une seconde implémentation de ces mécanismes dans un composant.

Les écrans React de partage externe ne sont plus montés. Le [contrat API de partage](organisations-permissions-partage.md#partage-externe-en-lecture-seule) est distinct des propositions de don.

## Fraîcheur des requêtes : gardes intégrés et limites

Le garde de génération institutionnelle empêche une réponse d'un ancien contexte de remplacer le nouveau. Dans une même institution, `App.tsx` utilise désormais une révision par boîte, l'invalidation ciblée du GET de détail en vol dans `api/client.ts`, un watermark pris au départ de la mutation et une publication gardée, revérifiée lorsque React applique l'updater. Le watermark couvre aussi les IDs découverts dans les réponses de création/repiquage. Une réponse de mutation concurrente dont la fraîcheur ne peut pas être établie n'est pas publiée directement : une relecture réconcilie le détail. Les révisions sont nettoyées au changement de génération; le client cesse aussi de partager les anciens GET de détail au changement d'institution. L'invalidation ne supprime pas les promesses déjà attendues : les gardes de publication restent indispensables.

Les collections de zones passent par `readZoneCollection` / `refreshZones` : pagination complète, IDs dédupliqués, publication de la collection entière seulement si sa génération et sa révision restent courantes, y compris lors de l'application différée par React. Une lecture remplacée devient un no-op; un échec de page conserve la collection complète précédente. Les mutations de zone et les lectures ciblées ont leurs propres gardes. `refreshZoneSalinityCapability` ne relit que le GET d'historique de salinité de la zone et met à jour `latest_salinity` sans changer les membres de la collection (`null` si vide, jamais zéro inventé). Les GET `/api/thermal-zones/...` de ce chemin utilisent `cache: 'no-store'`, sans rejoindre un GET partagé antérieur. `refreshRecovery` reste un slot unique de reprise de lectures : ne jamais rejouer automatiquement une écriture.

Ces frontières sont des jetons client, **pas un ordre de commit serveur** ni une garantie globale contre toute écriture concurrente. Les tests `frontend/scripts/test-box-read-freshness.mjs`, `test-zone-read-freshness.mjs` et `test-zone-refresh-pagination.mjs` couvrent notamment réponses inversées, zéro, publication différée et reprise GET seule. Leur présence n'atteste aucun déploiement.

## Supports

- **Desktop** : administration, graphiques, exports et analyse dense.
- **Tablette paysage de laboratoire** : recherche, scan, fiche boîte et saisie opérationnelle.
- **Téléphone** : doit rester fonctionnel, sans exiger une refonte profonde pour chaque chantier.

Le layout tablette utilise un rail latéral permanent de 88px, pas une navigation haute : largeur de 760 à 1023px, étendue jusqu'à 1180px avec `pointer: coarse`, hors layout téléphone (moins de 760px, ou portrait jusqu'à 900px). Les hooks `useIsTabletLayout` / `useIsPhoneLayout` et `styles/responsive/tablet.css` portent ce contrat.

Administration est desktop-only. `frontend/src/hooks/useIsDesktopApp.ts` centralise la détection : largeur minimale de 1024px **et** `hover: hover` **et** `pointer: fine`; la largeur seule ne suffit pas. En non-desktop, l'entrée de navigation Administration est absente et toute route `/administration...` est remplacée par `/` avant de rendre la vue. Il ne doit exister ni écran admin intermédiaire, ni message demandant d'utiliser un ordinateur, ni tentative de rendre ces pages responsives sans nouvelle décision produit. Cette règle d'affichage ne remplace jamais les permissions backend. Les sections actives sont Équipe, Inventaire, Espèces, Emplacements, Envois et réceptions et Historique. Seuls Référentiels partagés et Institutions sont désactivés; sur ordinateur, une demande directe d'une section désactivée affiche Comptes.

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

Le bouton d'accueil ouvre `CreateBoxPanel` en modale sur desktop/tablette; le téléphone utilise le même composant inline. Admins et techniciens conservent leurs droits. Le formulaire demande la souche existante (`StrainPicker` : recherche insensible aux accents et à la casse, souches utilisables seulement, groupées par espèce, libellé Souche N, code enregistré et nombre de boîtes actives), l'emplacement, la date d'entrée, le nombre initial de polypes (obligatoire, `0` valide), un lien de preuve ADN facultatif et une note facultative, sans code ni numéro modifiable ni présélection. Les descriptions et raisons d'inéligibilité ne sont pas rendues; aucune donnée historique n'est modifiée. Le code définitif n'est affiché qu'après confirmation de création par le serveur. Aucune création de souche n'est proposée depuis ce formulaire; une future création espèce/souche devra s'insérer dans le sélecteur.

Le formulaire utilise `POST /api/boxes/automatic/intents/` puis `/intents/execute/` : inscription durable d'une intention immuable liée à l'acteur, l'institution, l'UUID et un secret de reprise indépendant, puis allocation atomique du code côté serveur. Aucune confirmation supplémentaire n'est demandée. Avant toute requête, la session conserve l'UUID brut et une enveloppe v2 complète avec périmètre, secret, paramètres et empreinte d'intégrité. Après interruption ou rechargement, une enveloppe intacte reprend l'inscription avec exactement la même identité et les mêmes paramètres, même si la première inscription n'a jamais atteint Django. Le panneau reprend automatiquement avec délais bornés, aussi à la reconnexion et à la réouverture. Une création engagée est retrouvée; un refus durable libère le brouillon, contrairement à un simple `4xx`. Les UUID nus, anciens enregistrements sans secret, enveloppes corrompues et UUID divergents restent en inspection seule : ni exécution ni annulation automatique, ni remplacement après `unknown`/`pending`. Les boîtes engagées restent visibles même si un autre candidat reste incertain. Aucun identifiant, secret ou contrôle technique n'est montré au chercheur. Voir le [contrat de création](boites-cycle-de-vie.md#création-ordinaire-automatique).

## Administration : références par domaine

- [Espèces : catalogue, fiche et dialogues](taxonomie-especes.md).
- [Envois et réceptions : propositions de don](imports-exports-transferts.md#propositions-de-don-de-boîte-entière).
- [Création, événements et historique de la fiche boîte](boites-cycle-de-vie.md).
- [Tests et QA par support](../operations/developpement-qa.md) ; [catalogues et vocabulaire](terminologie-i18n.md).

## Administration : Espèces

Le catalogue et la fiche utilisent les routes institutionnelles avec `X-Organization-Id`. La navigation utilise l’identifiant du profil ou, si nécessaire, celui de la référence partagée. Après association, l’ancienne URL lit le profil complet et sa révision. Les effectifs et dates de boîtes restent masqués tant que leur collection n’est pas complète. En cas d’erreur, la relance ne change pas d’institution.

Une espèce déjà utilisée par l’institution présente les mêmes actions avec ou sans profil. Ouvrir ou fermer la fiche ou un dialogue n’écrit rien. « Nom scientifique et WoRMS » est prérempli et ne permet pas d’envoyer un état inchangé. Le nom normalisé est limité à 150 caractères et respecte le format validé par Django ; un nom non vide invalide affiche une erreur associée au champ et bloque l’enregistrement, y compris sans référence WoRMS. Le serveur reste l’autorité. En cas de révision périmée, l’interface propose de recharger. « Nouvelle souche » suit `can_create_strain` et `strain_creation_issue`, jamais le code affiché ; après création, la fiche est relue. Voir [les contrats de taxonomie](taxonomie-especes.md#catalogue-institutionnel).

## Motifs visuels à réutiliser

- Page d'entité : retour, libellé de type, titre explicite et actions de même niveau. Pour les détails Espèces, Boîte ou Emplacement, ne pas répéter l'en-tête de section dans la fiche.
- Saisie courte : réutiliser les dialogues de boîte et leurs états de validation; annulation, focus initial et restauration du focus restent explicites. Le mouvement se corrige dans son bandeau, pas dans une nouvelle modale.
- Table opérationnelle : aligner valeurs et dates, garder des lignes compactes et un menu d'actions stable. Un `BoxTrackingPreview` ne charge les données qu'à son ouverture et reste accessible au focus clavier.
- Graphiques : zéro visible, absence et rupture distinctes, pas d'interpolation présentée comme une observation. Survol, focus et consultation tactile doivent porter la même information.
- Partir des [composants et de l'organisation CSS](../../frontend/src/styles/README.md), des tokens et des écrans existants. Ne pas ajouter une variante parce qu'un écran est isolé. Vérifier les grands et petits formats, les libellés traduits, la navigation clavier et le focus visible.

## Cascade CSS

`frontend/src/styles/index.css` fixe les couches `tokens`, `base`, `layout`, `components`, `pages`, `responsive`, `print`, dans cet ordre. Placer les variables communes dans `tokens.css`, les motifs réutilisables dans `components/` et les compositions propres à une vue dans `pages/`. Les adaptations de support appartiennent aux couches `responsive` ou `print`; ne pas créer une feuille hors couche pour surcharger un composant. Utiliser les tokens de couleur, d'espacement et de texte existants avant d'introduire une variante.
