# Documentation fonctionnelle et technique de Polypbase

Ce document sert de trace de reprise pour les personnes qui continueront le projet Polypbase. Il décrit ce qui existe dans l'application, la logique métier associée, les choix d'interface, les différences entre bureau, tablette et téléphone, ainsi que les fichiers de code principaux.

L'application est construite avec une architecture hybride. Django gère la base de données, les règles métier, les droits, les API, les exports et les pages serveur. React gère l'interface principale utilisée par le laboratoire et par les personnes qui consultent les données.

## 1. Organisation générale du projet

Le dépôt est organisé autour de deux parties principales.

Le backend est dans `backend/`. Il contient le projet Django, les applications métier, les modèles, les serializers, les vues API, les services et les tests.

Le frontend est dans `frontend/`. Il contient l'application React, les composants d'interface, les types TypeScript, le client API et les fichiers CSS.

Les documents de conception sont dans `docs/`. Les notebooks d'analyse sont dans `notebooks/`. Les scripts utilitaires sont dans `scripts/`.

Les fichiers les plus importants pour comprendre l'application sont :

| Partie | Fichiers principaux | Rôle |
| --- | --- | --- |
| Entrée React | `frontend/src/App.tsx` | Route les pages, charge les données, gère l'institution active, les actions principales et les modales. |
| Client API | `frontend/src/api/client.ts` | Centralise les appels HTTP, le CSRF et l'envoi de l'institution active. |
| Types frontend | `frontend/src/types.ts` | Décrit les objets manipulés côté React. |
| Styles globaux | `frontend/src/styles/app.css` | Style principal, bureau et tablette. |
| Styles téléphone | `frontend/src/styles/phone.css` | Adaptations spécifiques au téléphone. |
| Détection support | `frontend/src/hooks/useIsDesktopApp.ts` | Sépare les usages bureau des usages laboratoire. |
| Routes API | `backend/config/api_urls.py` | Liste les endpoints utilisés par React. |
| Modèles métier | `backend/apps/*/models.py` | Structure les données Django. |
| API boîtes et emplacements | `backend/apps/cultures/api_views.py` | Gère boîtes, relevés, repiquages, déplacements, emplacements, sondes, alertes et transferts. |
| Services métier | `backend/apps/cultures/services.py` | Contient les opérations complexes exécutées en transaction. |
| API comptes | `backend/apps/accounts/api_views.py` | Gère connexion, profil, langue, membres, rôles et historique global. |
| Exports | `backend/apps/exports/views.py`, `backend/apps/exports/services.py` | Prépare les options, les aperçus et les exports CSV. |

## 2. Logique générale de navigation

L'application React possède plusieurs onglets principaux :

| Onglet | Usage |
| --- | --- |
| Suivi | Rechercher ou scanner une boîte, créer une boîte, accéder aux fiches boîtes. |
| Vue d'ensemble | Repérer les boîtes à relever et suivre les tendances récentes. |
| Emplacements | Consulter les emplacements thermiques, leurs boîtes, leur température, leur capacité et leurs alertes. |
| Exports | Préparer et télécharger des exports de relevés. Disponible sur bureau. |
| Étiquettes | Sélectionner des boîtes et imprimer les étiquettes QR code. |
| Profil | Gérer son compte, la langue, l'institution active et, pour les administrateurs, accéder aux outils d'administration. |

Les onglets sont définis dans `frontend/src/App.tsx` avec deux listes :

```ts
const labTabs: TabId[] = ['pilotage', 'overview', 'zones', 'labels', 'profile'];
const desktopTabs: TabId[] = ['pilotage', 'overview', 'zones', 'exports', 'labels', 'profile'];
```

Cela signifie que l'interface de laboratoire, tablette ou téléphone, est plus courte. Les exports restent réservés à l'usage bureau.

L'administration n'est pas un onglet principal séparé. Elle apparaît dans la page Profil quand l'utilisateur a un rôle administrateur et quand le support est un ordinateur. Ce choix évite de surcharger l'interface laboratoire.

## 3. Gestion multi-institutions

Polypbase prévoit qu'un même utilisateur puisse appartenir à plusieurs institutions. Par exemple, une personne peut avoir un rôle administrateur dans une institution et un rôle lecteur dans une autre.

Après connexion, l'application charge le profil avec `GET /api/profile/`. Ce profil contient les institutions auxquelles l'utilisateur a accès, ses rôles et ses préférences.

La logique côté frontend se trouve principalement dans :

- `frontend/src/App.tsx`
- `frontend/src/api/client.ts`
- `frontend/src/components/ProfileView.tsx`

Le client API ajoute l'en-tête `X-Organization-Id` aux appels vers l'API. Cet en-tête indique à Django quelle institution est active. Cela évite de mélanger les données de plusieurs aquariums dans l'interface.

Si l'utilisateur a plusieurs institutions et qu'aucune n'est encore choisie, l'application affiche un écran de choix d'institution avant d'ouvrir les données. Une fois l'institution choisie, la navigation est limitée à cette institution et les droits sont appliqués selon le rôle associé.

Depuis le profil, l'utilisateur peut aussi changer d'institution active et choisir une institution par défaut. Le bandeau de gauche affiche Polypbase avec le nom de l'institution active.

## 4. Gestion des rôles

Les rôles sont portés par le modèle `OrganizationMembership` dans `backend/apps/accounts/models.py`.

Les rôles principaux sont :

| Rôle | Usage prévu |
| --- | --- |
| Administrateur | Gestion des comptes, emplacements, sondes, institutions, transferts et historique. |
| Technicien | Saisie des relevés, déplacements, repiquages et usage laboratoire. |
| Lecture seule | Consultation des données sans modification. |

Côté backend, les droits sont vérifiés dans `backend/apps/accounts/permissions.py` et dans les vues API.

Côté frontend, les formulaires et boutons d'action sont masqués ou désactivés selon le rôle. Par exemple, un compte en lecture seule peut consulter la fiche d'une boîte, son historique, les graphiques, les mouvements et la parenté, mais il ne voit pas les formulaires de saisie ou les actions de modification.

## 5. Connexion et compte

La connexion est gérée par une page React, avec les endpoints Django de session.

Fichiers principaux :

- `frontend/src/components/LoginPage.tsx`
- `frontend/src/components/PasswordResetPage.tsx`
- `frontend/src/components/LoginNotice.tsx`
- `backend/apps/accounts/api_views.py`
- `backend/apps/accounts/serializers.py`

Endpoints utilisés :

- `GET /api/auth/session/`
- `POST /api/auth/session/`
- `POST /api/auth/logout/`
- `POST /api/auth/password-reset/`
- `POST /api/auth/password-reset/confirm/`

La page de connexion permet d'ouvrir une session Django depuis React. Le bouton de déconnexion est disponible dans le profil.

Le système de mot de passe temporaire est prévu dans la gestion des comptes. Lorsqu'un administrateur crée un accès, un mot de passe temporaire est généré. L'idée retenue dans l'interface est que le mot de passe soit envoyé par mail, puis remplacé par l'utilisateur lors de sa première connexion ou via une procédure de réinitialisation.

## 6. Page Suivi

La page Suivi est la page d'entrée principale pour le laboratoire. Elle correspond au composant `PilotageView` dans `frontend/src/App.tsx`.

Elle sert à retrouver une boîte rapidement, soit avec la recherche, soit avec un QR code.

### Bureau

Sur ordinateur, la recherche est mise en avant. L'utilisateur peut chercher une boîte par code, espèce ou souche. Des suggestions apparaissent sous la barre de recherche. Les derniers accès sont affichés pour permettre de rouvrir rapidement les boîtes consultées récemment.

Les derniers accès ne sont pas une liste fixe. Ils viennent du backend via le dashboard et sont liés au compte utilisateur. L'accès à une boîte déclenche un appel à :

```http
POST /api/boxes/<box_id>/access/
```

Cela permet d'avoir une liste cohérente entre plusieurs supports si le même compte utilise l'application sur ordinateur, tablette ou téléphone.

### Tablette

Sur tablette, le scan QR code est prioritaire. L'utilisateur en laboratoire doit pouvoir scanner l'étiquette d'une boîte et ouvrir directement sa fiche.

La recherche reste disponible, mais elle prend moins de place. L'objectif est de limiter le texte et de garder une interface utilisable en manipulation.

Le composant utilisé pour le scanner est :

- `frontend/src/components/TabletQrScanner.tsx`

Le scan caméra dépend du navigateur. Sur téléphone ou tablette, il faut un contexte HTTPS pour que Safari et les navigateurs mobiles autorisent l'accès à la caméra. Pour les tests locaux à distance, un tunnel HTTPS temporaire peut être utilisé.

### Téléphone

Sur téléphone, l'interface est simplifiée. La navigation est en bas de l'écran, avec de gros boutons. Le scan QR code et la recherche restent les deux modes principaux, mais la mise en page évite les panneaux trop larges.

Le style spécifique téléphone est dans :

- `frontend/src/styles/phone.css`

## 7. Création d'une boîte

La création d'une boîte est disponible depuis la page Suivi pour les utilisateurs autorisés.

Fichiers principaux :

- `frontend/src/App.tsx`
- `frontend/src/components/ConfirmActionModal.tsx`
- `backend/apps/cultures/api_views.py`
- `backend/apps/cultures/serializers.py`

Endpoint utilisé :

```http
POST /api/boxes/
```

Le formulaire demande la structure, la souche, l'emplacement, le code, le numéro, la date d'entrée et une note.

Avant création, l'application utilise une modale de confirmation interne à Polypbase. Les boîtes de dialogue natives du navigateur ont été remplacées pour garder une interface plus cohérente et plus claire.

Le code global est la référence principale de la boîte. Les anciennes notions de code local ont été réduites dans l'interface pour éviter de multiplier les identifiants visibles.

## 8. Fiche boîte

La fiche boîte est la page centrale de l'application. Elle est définie par le composant `BoxPage` dans `frontend/src/App.tsx`.

Elle regroupe :

- l'identité de la boîte ;
- son espèce ;
- son état ;
- son emplacement actuel ;
- son QR code ;
- les alertes ;
- le dernier relevé ;
- le formulaire de nouveau relevé ;
- les graphiques ;
- les mouvements ;
- la parenté.

### Bandeau de la boîte

Le bandeau en haut de la fiche donne les informations essentielles.

Il contient le code global, le nom scientifique, la date de première entrée ou de création, le QR code, l'emplacement actuel, la température disponible, la salinité disponible et les actions principales.

L'état vivant ou inactif n'est pas affiché avec un badge séparé. Il est indiqué par la couleur du bord gauche du bandeau. Cela réduit le bruit visuel tout en gardant l'information visible.

Le QR code ouvre une modale d'étiquette. Cette modale permet de voir le QR code de la boîte, de télécharger une étiquette ou d'ajouter la boîte à la sélection d'étiquettes à imprimer.

Les boutons d'action dépendent du rôle :

- transférer ou déplacer ;
- repiquer ;
- désactiver le suivi ;
- réactiver le suivi ;
- consulter les alertes.

### Dernier relevé

La fiche affiche le dernier relevé connu avec les valeurs de polypes, éphyrules, salinité si disponible et dernier commentaire.

Le commentaire est affiché dans le panneau de dernier relevé pour éviter de le répéter dans plusieurs zones.

### Nouveau relevé

Le formulaire de nouveau relevé permet de saisir :

- la date ;
- le nombre de polypes ;
- le nombre d'éphyrules ;
- la salinité ;
- une observation libre.

Fichiers concernés :

- `frontend/src/App.tsx`
- `frontend/src/components/MeasurementSaveButton.tsx`
- `frontend/src/components/QuickCountButtons.tsx`
- `backend/apps/cultures/api_views.py`
- `backend/apps/measurements/models.py`

Endpoint utilisé :

```http
POST /api/boxes/<box_id>/measurements/
```

Le backend utilise `update_or_create`. Cela veut dire qu'une boîte ne possède qu'un relevé biologique par date. Si un relevé existe déjà pour la même boîte et la même date, il est corrigé au lieu de créer un doublon.

Le bouton d'enregistrement a une animation de maintien. L'utilisateur doit maintenir le bouton pour valider. Ce choix limite les validations accidentelles sur tablette et téléphone. Sur mobile, le retour haptique est déclenché quand le navigateur le permet.

Après enregistrement, le bouton passe dans un état validé avec le texte `Relevé enregistré`.

### Correction d'un relevé

Après enregistrement, l'interface propose une action de correction du relevé enregistré. L'idée est de permettre une correction rapide si une erreur de saisie est détectée immédiatement.

La correction utilise :

```http
PATCH /api/boxes/<box_id>/measurements/<measurement_id>/
```

L'historique d'audit garde une trace de la correction.

### Relevés et graphiques

Le sous-onglet Relevés affiche un graphique d'évolution. Il est géré par :

- `frontend/src/components/BoxInsights.tsx`

Le graphique affiche :

- les polypes ;
- les éphyrules ;
- la température ;
- les événements ;
- les périodes sans relevé.

Un point important est la distinction entre zéro et absence de relevé. Zéro est une valeur biologique possible. Une absence de relevé est représentée comme une interruption ou une zone de manque, pas comme une valeur à zéro.

Le graphique contient des filtres de période et de séries. Il affiche des informations au survol ou au clic selon le support. Les événements de déplacement ou de repiquage peuvent aussi être matérialisés.

### Historique des relevés

Le bouton `Voir relevés` ouvre un historique plus détaillé des relevés. Il permet de lire les valeurs passées sans surcharger la fiche principale.

### Mouvements

Le sous-onglet Mouvements affiche les déplacements de la boîte entre emplacements thermiques.

Les déplacements sont créés via :

```http
POST /api/boxes/<box_id>/move/
```

La logique métier est dans :

- `backend/apps/cultures/services.py`

La fonction `move_box_to_thermal_zone` ferme l'ancien emplacement, crée le nouvel emplacement actif, crée un mouvement et ajoute une entrée d'audit. L'opération est exécutée dans une transaction pour éviter d'avoir une boîte avec un historique incohérent.

### Parenté

Le sous-onglet Parenté affiche le lien parent enfant entre les boîtes issues de repiquages.

Fichiers principaux :

- `frontend/src/components/InteractiveLineageGraph.tsx`
- `frontend/src/components/LineageModal.tsx`
- `backend/apps/cultures/services.py`

Endpoint utilisé :

```http
GET /api/boxes/<box_id>/lineage/
```

Le graphique est interactif afin de rester utilisable même si une boîte a plusieurs enfants ou si plusieurs générations existent.

### Bureau, tablette et téléphone

Sur bureau, la fiche exploite la largeur disponible. Le bandeau, le formulaire, le dernier relevé et les graphiques sont visibles avec plus de détails.

Sur tablette, le formulaire est optimisé pour la saisie rapide. Les boutons de comptage sont plus grands, le bouton d'enregistrement est plus visible et le bandeau est condensé.

Sur téléphone, l'affichage est encore plus réduit. La fiche met en priorité le code de la boîte, l'emplacement, le dernier relevé et la saisie. La navigation est en bas de l'écran.

## 9. Repiquage

Le repiquage permet de créer une ou plusieurs boîtes enfants à partir d'une boîte parent.

Fichiers principaux :

- `frontend/src/components/SubcultureModal.tsx`
- `frontend/src/App.tsx`
- `backend/apps/cultures/api_views.py`
- `backend/apps/cultures/services.py`
- `backend/apps/cultures/serializers.py`

Endpoint utilisé :

```http
POST /api/boxes/<box_id>/subcultures/
```

La fonction backend `create_subculture` crée :

- l'événement de repiquage ;
- les boîtes enfants ;
- les liens de parenté ;
- les emplacements initiaux des enfants ;
- les entrées d'audit.

L'opération est exécutée avec `transaction.atomic`. Si une partie échoue, rien n'est enregistré partiellement.

La logique de code global utilise le code de souche et le prochain numéro disponible. Par exemple, si la boîte parent est liée à la souche `FPA-LSP-1` et que le dernier numéro existant est `007`, le code suivant proposé est `FPA-LSP-1.008`. Si `008` et `009` existent déjà, la suite proposée commence après le dernier numéro disponible.

## 10. Déplacement et emplacements

Le déplacement sert à changer l'emplacement thermique d'une boîte tout en conservant l'historique.

Fichiers principaux :

- `frontend/src/components/MoveBoxModal.tsx`
- `frontend/src/components/ZonesView.tsx`
- `backend/apps/cultures/services.py`
- `backend/apps/cultures/models.py`

Le modèle `BoxLocation` garde les périodes de présence dans un emplacement. Le modèle `BoxMovement` garde les événements de déplacement.

Une boîte possède un emplacement courant, mais son historique reste disponible. Cela permet de savoir où elle était avant un changement d'armoire ou de zone.

## 11. Vue d'ensemble

La page Vue d'ensemble aide à suivre les boîtes qui doivent être relevées.

Fichiers principaux :

- `frontend/src/App.tsx`
- `backend/apps/cultures/api_views.py`

Endpoint utilisé :

```http
GET /api/overview/active-boxes/?months=6
```

La logique récente de cette page est la suivante :

- afficher les boîtes suivies dans l'application ;
- ignorer les boîtes désactivées ;
- s'appuyer sur les relevés faits via l'application pour décider qu'une boîte est suivie dans le circuit actuel ;
- montrer ensuite les données des six derniers mois pour donner du contexte, même si certains relevés viennent de données historiques ;
- trier les boîtes par ancienneté du dernier relevé ;
- aider à repérer les boîtes à relever en priorité.

Les cartes affichent le dernier relevé, le nombre de jours depuis ce relevé, l'emplacement et un graphique compact des six derniers mois.

Les filtres permettent de chercher par code, espèce ou emplacement. Les cartes de synthèse et les filtres par emplacement sont cliquables. L'objectif est de permettre aux chercheurs de passer d'une vue globale à une liste de boîtes à traiter sans manipuler trop de menus.

## 12. Emplacements thermiques

La page Emplacements thermiques est gérée par :

- `frontend/src/components/ZonesView.tsx`
- `backend/apps/cultures/api_views.py`
- `backend/apps/cultures/serializers.py`

Endpoints principaux :

```http
GET /api/thermal-zones/
POST /api/thermal-zones/
GET /api/thermal-zones/<id>/
PATCH /api/thermal-zones/<id>/
POST /api/thermal-zones/<id>/temperature/
POST /api/probes/
```

### Liste des emplacements

La liste présente les emplacements triés par température. L'utilisateur peut inverser le tri pour voir les emplacements du plus froid au plus chaud ou l'inverse.

Chaque carte affiche :

- le nom de l'emplacement ;
- l'institution ;
- la température relevée si disponible ;
- la température cible ;
- la salinité ;
- la capacité ;
- les alertes.

Les alertes utilisent le même principe visuel que la fiche boîte : une cloche avec un nombre. L'utilisateur peut cliquer sur les notifications pour voir les points à vérifier.

Les boutons d'ajout d'emplacement et d'ajout de sonde sont visibles pour les administrateurs.

### Fiche emplacement

La fiche d'un emplacement contient :

- un bandeau résumé ;
- le contrôle thermique ;
- le formulaire de température manuelle ;
- la liste des boîtes présentes ;
- les derniers comptages ;
- les sondes associées ;
- l'activité récente.

Le graphique de contrôle thermique compare la température relevée à la température cible. Il affiche aussi les informations nécessaires pour comprendre si la température est dans la plage attendue. Le système est prévu pour accepter plus tard des valeurs venant d'une sonde connectée en continu.

La saisie manuelle de température reste disponible pour les cas où l'API de sonde n'est pas encore branchée ou pour saisir une mesure ponctuelle.

La capacité compte les boîtes actives dans l'emplacement. Les boîtes désactivées restent dans l'historique de localisation, mais elles ne doivent pas fausser la capacité actuelle.

## 13. Alertes

Les alertes existent pour éviter que des situations importantes passent inaperçues.

Fichiers principaux :

- `backend/apps/audit/models.py`
- `backend/apps/cultures/api_views.py`
- `frontend/src/App.tsx`
- `frontend/src/components/ZonesView.tsx`

Modèle principal :

- `Alert`

Types d'alertes visibles :

- baisse de polypes ;
- température trop éloignée de la consigne ;
- salinité manquante ;
- relevés manquants ou zones à vérifier.

Les alertes apparaissent avec une cloche et un compteur. Les modales d'alerte doivent rester sobres et lisibles. Elles indiquent le problème et l'action attendue.

Certaines alertes se résolvent automatiquement quand une nouvelle donnée montre que le problème n'existe plus. Les alertes de température peuvent aussi être résolues par un utilisateur autorisé. Les alertes biologiques liées à la baisse de polypes sont recalculées lors des relevés.

Endpoint de résolution :

```http
POST /api/alerts/<id>/resolve/
```

## 14. Exports

L'onglet Exports est prévu pour l'ordinateur. Il n'est pas affiché dans la navigation laboratoire.

Fichiers principaux :

- `frontend/src/components/ExportsView.tsx`
- `backend/apps/exports/views.py`
- `backend/apps/exports/services.py`

Endpoints utilisés :

```http
GET /api/exports/options/
GET /api/exports/measurements/preview/
GET /api/exports/measurements.csv
```

La page permet de filtrer les données par :

- période ;
- institution ;
- espèce ;
- souche ;
- emplacement ;
- boîte.

Les filtres sont cumulables. Par exemple, il est possible de sélectionner une souche, deux espèces et une période.

L'aperçu graphique affiche les données sélectionnées avant export. Les graphiques distinguent les valeurs nulles réelles des semaines sans relevé. Les données manquantes ne sont pas dessinées comme des zéros.

L'export CSV reprend une logique proche des fichiers historiques Excel, avec des relevés organisés par semaine et par boîte. Il sert à fournir un format exploitable en dehors de Polypbase.

## 15. Étiquettes QR code

La page Étiquettes sert à préparer les étiquettes physiques à coller sur les boîtes.

Fichiers principaux :

- `frontend/src/components/LabelsView.tsx`
- `frontend/src/components/QrLabelModal.tsx`
- `frontend/src/utils/qrLabels.ts`
- `backend/apps/cultures/qr.py`
- `backend/apps/cultures/views.py`

Les étiquettes sont carrées. Elles affichent le QR code au centre, puis le code de la boîte et le nom de l'espèce. Les informations susceptibles de changer, comme l'emplacement, ne sont pas imprimées sur l'étiquette.

La sélection d'étiquettes peut se faire depuis :

- la page Étiquettes ;
- la modale QR code d'une fiche boîte.

La sélection est partagée entre ces deux endroits. Un utilisateur peut donc ajouter plusieurs boîtes depuis leurs fiches, puis ouvrir la page Étiquettes pour imprimer une planche complète.

Les boîtes affichées dans la sélection sont filtrées pour éviter d'imprimer des étiquettes inutiles. La logique actuelle privilégie les boîtes actives avec un relevé récent dans une fenêtre de quinze mois. Cela évite de proposer des boîtes anciennes ou désactivées.

La liste est triée par :

1. emplacement ;
2. espèce ;
3. souche ;
4. code de boîte.

La prévisualisation montre les pages imprimables. Un repère d'emplacement est ajouté avant le premier QR code d'un groupe d'emplacement pour faciliter la découpe.

## 16. Profil

La page Profil est disponible sur bureau, tablette et téléphone.

Fichiers principaux :

- `frontend/src/components/ProfileView.tsx`
- `frontend/src/components/AdminView.tsx`
- `backend/apps/accounts/api_views.py`

La page contient :

- les informations du compte ;
- le bouton de déconnexion ;
- le choix de langue ;
- l'institution active ;
- le choix d'institution par défaut ;
- un lien vers les étiquettes sur mobile ;
- l'espace administrateur si l'utilisateur est administrateur sur ordinateur.

Les noms sont formatés pour être plus lisibles. Le prénom est affiché avec une majuscule et le nom en majuscules, par exemple `Anthony COMBES--AGUÉRA`.

La langue est stockée dans les préférences utilisateur. L'interface prévoit actuellement le français et l'anglais, avec la possibilité d'ajouter d'autres langues ensuite.

## 17. Espace administrateur

L'espace administrateur est intégré à la page Profil. Il est disponible uniquement sur ordinateur pour éviter de surcharger les supports utilisés au laboratoire.

Fichier principal :

- `frontend/src/components/AdminView.tsx`

Les sections sont :

- Comptes ;
- Emplacements et sondes ;
- Institutions ;
- Transferts ;
- Historique.

### Comptes

La section Comptes permet :

- de créer un nouvel accès ;
- de choisir l'institution ;
- de choisir le rôle ;
- de voir les comptes actifs ;
- de changer le rôle ;
- de désactiver ou réactiver un accès.

La création de compte prépare un mot de passe temporaire. Le backend génère ce mot de passe dans `backend/apps/accounts/api_views.py`.

### Emplacements et sondes

Cette section permet :

- de créer un emplacement thermique ;
- de définir sa température cible ;
- de définir sa capacité ;
- de définir une salinité de référence ;
- d'ajouter une sonde ;
- d'associer une sonde à un emplacement ;
- de modifier les capacités et salinités existantes.

Ces actions modifient les modèles `ThermalZone`, `Probe` et `SalinityMeasurement`.

### Institutions

La section Institutions permet de créer et modifier les institutions partenaires.

Le modèle principal est :

- `backend/apps/organizations/models.py`

Les informations affichées sont le nom, le pays, la ville, le contact, l'email, le téléphone et l'adresse postale. Un bouton crayon permet de modifier les informations d'une institution.

### Transferts

La section Transferts prépare les échanges entre structures.

Elle contient :

- un formulaire de transfert sortant ;
- une sélection de boîte inspirée de la page Étiquettes ;
- une institution destinataire ;
- le nombre de polypes transférés ;
- une note ;
- une génération de CSV ;
- un import de CSV de transfert entrant.

Les fichiers backend concernés sont :

- `backend/apps/cultures/api_views.py`
- `backend/apps/cultures/serializers.py`
- `backend/apps/cultures/models.py`

Le document `docs/transferts_csv.md` détaille le format attendu.

### Historique global

L'historique global permet aux administrateurs de voir les actions réalisées sur les données.

Fichiers principaux :

- `frontend/src/components/AdminView.tsx`
- `backend/apps/audit/models.py`
- `backend/apps/accounts/api_views.py`

Endpoint utilisé :

```http
GET /api/accounts/audit-log/
```

L'historique est paginé pour éviter de charger trop d'entrées d'un coup. Il peut être filtré par type d'action et par date. Les entrées sont regroupées par jour. Certaines entrées peuvent être développées pour voir les détails.

Les actions enregistrées incluent notamment :

- création de relevé ;
- correction de relevé ;
- création de boîte ;
- désactivation ou réactivation de boîte ;
- déplacement ;
- repiquage ;
- création ou modification d'emplacement ;
- ajout de sonde ;
- création ou modification de compte ;
- préparation de transfert ;
- résolution d'alerte.

## 18. Journalisation et audit

Le modèle `AuditLog` est défini dans :

- `backend/apps/audit/models.py`

Il sert à garder une trace des modifications importantes. Chaque entrée contient l'institution, l'utilisateur, le type d'action, l'objet concerné, une description et des métadonnées.

La journalisation est importante pour Polypbase, car l'application manipule des données de suivi biologique. Il faut pouvoir comprendre qui a modifié une donnée, quand, et sur quel objet.

Les relevés corrigés sont traités avec attention. Le backend garde une seule entrée lisible par relevé, mais enrichit les métadonnées pour indiquer les valeurs modifiées.

## 19. Gestion de la traduction

L'interface prévoit deux langues : français et anglais.

Côté backend, les fichiers de traduction Django sont dans :

- `backend/locale/fr/LC_MESSAGES/django.po`
- `backend/locale/en/LC_MESSAGES/django.po`

Côté frontend, beaucoup de libellés sont encore gérés dans les composants React, notamment dans `frontend/src/App.tsx`, `frontend/src/components/ExportsView.tsx` et `frontend/src/components/AdminView.tsx`.

Le choix de langue est enregistré dans le profil utilisateur via :

```http
PATCH /api/profile/
```

Pour ajouter une langue, il faudra prévoir une stratégie plus centralisée côté React afin d'éviter de disperser les textes dans trop de composants.

## 20. Choix d'interface

L'interface cherche à rester sobre, lisible et rapide à utiliser.

Les choix principaux sont :

- peu de texte explicatif dans les pages de travail ;
- des titres courts ;
- des actions visibles seulement quand elles sont utiles ;
- des panneaux clairs sans surcharge ;
- des boutons plus grands sur tablette et téléphone ;
- une navigation simplifiée sur mobile ;
- des modales internes à Polypbase plutôt que des boîtes de dialogue natives du navigateur ;
- une couleur d'état discrète sur le bord des fiches ;
- des animations courtes pour accompagner les actions importantes.

Les fichiers de style sont :

- `frontend/src/styles/app.css`
- `frontend/src/styles/phone.css`

Le fichier `app.css` contient le style principal et les adaptations bureau/tablette. Le fichier `phone.css` surcharge l'interface pour le téléphone.

## 21. Animations et interactions

Plusieurs interactions ont été ajoutées pour rendre l'outil plus agréable.

Le bouton d'enregistrement d'un relevé utilise une animation de maintien. Cela limite les clics accidentels. Le composant concerné est :

- `frontend/src/components/MeasurementSaveButton.tsx`

Les boutons d'incrémentation utilisent :

- `frontend/src/components/QuickCountButtons.tsx`
- `frontend/src/utils/stepValue.ts`

Ils permettent d'ajouter rapidement des valeurs de polypes, d'éphyrules et de salinité. Sur tablette, l'appui prolongé permet d'augmenter ou diminuer plus vite.

Le retour haptique est centralisé dans :

- `frontend/src/utils/haptics.ts`

Les animations de chargement et états intermédiaires utilisent :

- `frontend/src/components/PageLoader.tsx`
- `frontend/src/components/SkeletonRows.tsx`

## 22. Backend et modèles de données

Les modèles sont répartis par domaine.

### Comptes

Fichier :

- `backend/apps/accounts/models.py`

Modèles :

- `OrganizationMembership`
- `UserPreference`

Cette app relie les utilisateurs Django aux institutions et stocke les préférences comme la langue ou l'institution par défaut.

### Organisations

Fichier :

- `backend/apps/organizations/models.py`

Modèles :

- `Organization`
- `PartnerInstitution`
- `SharingAgreement`

Cette app représente les aquariums, les institutions partenaires et les accords de partage.

### Taxonomie

Fichier :

- `backend/apps/taxonomy/models.py`

Modèles :

- `Taxon`
- `Species`
- `Origin`
- `Strain`

Cette app sépare les espèces, les souches et les origines. Elle prépare l'objectif de nomenclature commune entre plusieurs structures.

### Cultures

Fichier :

- `backend/apps/cultures/models.py`

Modèles :

- `ThermalZone`
- `Box`
- `BoxLocation`
- `BoxMovement`
- `SubcultureEvent`
- `BoxLineage`
- `IdentificationTag`
- `BoxTransfer`
- `BoxTransferImport`

Cette app contient le coeur métier : boîtes, emplacements, repiquages, parenté, QR codes et transferts.

### Mesures

Fichier :

- `backend/apps/measurements/models.py`

Modèles :

- `BiologicalMeasurement`
- `Observation`
- `Probe`
- `TemperatureMeasurement`
- `DailyTemperature`
- `SalinityMeasurement`
- `ThermalAnomaly`

Cette app contient les relevés biologiques, observations, sondes, températures et salinités.

### Audit

Fichier :

- `backend/apps/audit/models.py`

Modèles :

- `Alert`
- `AuditLog`

Cette app stocke les alertes et l'historique des actions.

### Exports

Fichier :

- `backend/apps/exports/models.py`

Modèles :

- `ExcelImport`
- `ExcelImportRow`
- `DataExport`

Cette app prépare les imports historiques et les exports.

## 23. Endpoints API principaux

Les endpoints sont regroupés dans `backend/config/api_urls.py`.

| Endpoint | Usage |
| --- | --- |
| `/api/health/` | Vérifier que le backend répond. |
| `/api/auth/session/` | Connexion et session courante. |
| `/api/auth/logout/` | Déconnexion. |
| `/api/profile/` | Profil, langue, institution active et préférences. |
| `/api/dashboard/` | Synthèse, derniers accès, dernières actions. |
| `/api/overview/active-boxes/` | Données de la vue d'ensemble. |
| `/api/boxes/` | Liste et création de boîtes. |
| `/api/boxes/<id>/` | Détail d'une boîte. |
| `/api/boxes/<id>/access/` | Enregistrer un accès récent. |
| `/api/boxes/<id>/archive/` | Désactiver le suivi d'une boîte. |
| `/api/boxes/<id>/activate/` | Réactiver une boîte. |
| `/api/boxes/<id>/measurements/` | Liste et création de relevés. |
| `/api/boxes/<id>/measurements/<measurement_id>/` | Correction d'un relevé. |
| `/api/boxes/<id>/subcultures/` | Repiquage. |
| `/api/boxes/<id>/move/` | Déplacement. |
| `/api/boxes/<id>/lineage/` | Graphique de parenté. |
| `/api/thermal-zones/` | Liste et création d'emplacements. |
| `/api/thermal-zones/<id>/` | Détail ou modification d'un emplacement. |
| `/api/thermal-zones/<id>/temperature/` | Ajout d'une température manuelle. |
| `/api/probes/` | Ajout d'une sonde. |
| `/api/box-transfers/` | Préparer un transfert sortant. |
| `/api/box-transfer-imports/` | Importer un transfert entrant. |
| `/api/alerts/<id>/resolve/` | Résoudre une alerte. |
| `/api/organizations/` | Créer une institution. |
| `/api/organizations/<id>/` | Modifier ou supprimer une institution. |
| `/api/exports/options/` | Options de filtres pour les exports. |
| `/api/exports/measurements/preview/` | Aperçu graphique des exports. |
| `/api/exports/measurements.csv` | Téléchargement CSV. |
| `/api/accounts/members/` | Gestion des membres. |
| `/api/accounts/members/<id>/` | Modification d'un membre. |
| `/api/accounts/audit-log/` | Historique global paginé. |

## 24. Différences par support

### Bureau

Le bureau est prévu pour :

- consultation détaillée ;
- exports ;
- administration ;
- gestion des comptes ;
- préparation des transferts ;
- impression d'étiquettes ;
- analyse graphique.

La navigation est latérale. Les pages peuvent afficher plusieurs colonnes.

### Tablette

La tablette est prévue pour :

- scan QR code ;
- consultation rapide d'une boîte ;
- saisie de relevés ;
- repiquage ;
- déplacement ;
- consultation des emplacements.

Les boutons sont plus grands. Les formulaires évitent les champs inutiles. L'objectif est que la saisie reste possible en laboratoire.

### Téléphone

Le téléphone sert surtout à :

- scanner une boîte ;
- consulter une fiche ;
- saisir un relevé si nécessaire ;
- consulter les informations principales.

La navigation est en bas. Les pages sont plus compactes. Les fonctions lourdes restent évitées sur téléphone.

## 25. Points importants pour la reprise

Il faut garder en tête plusieurs règles.

Une valeur zéro n'est pas une absence de relevé. Les graphiques doivent toujours distinguer ces deux cas.

Les boîtes désactivées ne doivent pas être supprimées. Elles restent dans l'historique, mais elles ne doivent pas polluer les vues de travail.

Les actions de modification doivent être journalisées. Si une nouvelle fonctionnalité modifie des données métier, elle doit créer une entrée `AuditLog`.

Les opérations complexes doivent être transactionnelles. C'est déjà le cas pour le repiquage et le déplacement.

L'institution active doit être respectée partout. Les données d'une institution ne doivent pas apparaître dans le contexte d'une autre.

L'interface téléphone ne doit pas simplement réduire l'interface bureau. Elle doit rester pensée pour une action courte.

L'interface tablette doit rester utilisable en paysage.

Les tests avec la caméra doivent être faits en HTTPS. En local simple, Safari et plusieurs navigateurs mobiles peuvent refuser le scan QR code.

## 26. Fichiers à modifier selon les besoins

| Besoin | Fichiers à regarder en premier |
| --- | --- |
| Modifier la navigation | `frontend/src/App.tsx`, `frontend/src/hooks/useIsDesktopApp.ts` |
| Modifier la page Suivi | `frontend/src/App.tsx`, `frontend/src/components/SearchField.tsx`, `frontend/src/components/TabletQrScanner.tsx` |
| Modifier la fiche boîte | `frontend/src/App.tsx`, `frontend/src/components/BoxInsights.tsx`, `frontend/src/components/QrLabelModal.tsx` |
| Modifier les graphiques de boîte | `frontend/src/components/BoxInsights.tsx` |
| Modifier la parenté | `frontend/src/components/InteractiveLineageGraph.tsx`, `backend/apps/cultures/services.py` |
| Modifier les emplacements | `frontend/src/components/ZonesView.tsx`, `backend/apps/cultures/api_views.py` |
| Modifier les exports | `frontend/src/components/ExportsView.tsx`, `backend/apps/exports/services.py` |
| Modifier les étiquettes | `frontend/src/components/LabelsView.tsx`, `frontend/src/utils/qrLabels.ts`, `backend/apps/cultures/qr.py` |
| Modifier le profil | `frontend/src/components/ProfileView.tsx`, `backend/apps/accounts/api_views.py` |
| Modifier l'administration | `frontend/src/components/AdminView.tsx` |
| Modifier les droits | `backend/apps/accounts/permissions.py`, `backend/apps/accounts/models.py` |
| Modifier les alertes | `backend/apps/audit/models.py`, `backend/apps/cultures/api_views.py` |
| Modifier l'audit | `backend/apps/audit/models.py`, `backend/apps/accounts/api_views.py`, `backend/apps/cultures/api_views.py` |
| Modifier les styles bureau/tablette | `frontend/src/styles/app.css` |
| Modifier les styles téléphone | `frontend/src/styles/phone.css` |

## 27. Commandes utiles

Depuis la racine du projet :

```powershell
uv sync
```

Lancer le backend :

```powershell
cd backend
uv run python manage.py runserver
```

Lancer le frontend :

```powershell
cd frontend
npm install
npm run dev
```

Vérifier le backend :

```powershell
cd backend
uv run python manage.py check
uv run python manage.py test
uv run python manage.py makemigrations --check --dry-run
```

Vérifier le frontend :

```powershell
cd frontend
npm run build
```

Si la base partagée Neon est utilisée, il ne faut pas lancer `migrate`, `loaddata` ou `seed_demo_data` sans accord de l'équipe, car ces commandes peuvent modifier la base commune.

## 28. Limites actuelles et sujets à surveiller

Le fichier `frontend/src/App.tsx` contient encore beaucoup de logique. Il fonctionne, mais il gagnerait à être découpé progressivement si le projet continue.

La traduction côté frontend n'est pas encore entièrement centralisée. Pour ajouter plusieurs langues, il faudra isoler les textes dans un système plus propre.

Les graphiques ont été améliorés, mais ils doivent être testés avec beaucoup de données réelles. Les cas importants sont les longues périodes sans relevé, les valeurs nulles, les températures manquantes et les boîtes désactivées.

Le scan QR code doit être testé sur les vrais supports utilisés par l'Aquarium de Paris. Le comportement dépend du navigateur, des autorisations caméra et du HTTPS.

Les imports et transferts CSV doivent être testés avec des fichiers réels avant utilisation régulière.

Les règles exactes de conservation des comptes, de RGPD et d'hébergement doivent être finalisées avant une mise en production.

## 29. Ce que Polypbase permet déjà

À l'état actuel, Polypbase permet de :

- se connecter avec un compte ;
- choisir une institution active ;
- appliquer des rôles par institution ;
- rechercher une boîte ;
- scanner un QR code quand le navigateur le permet ;
- consulter une fiche boîte ;
- saisir un relevé biologique ;
- corriger un relevé ;
- saisir une salinité dans un relevé ;
- voir le dernier commentaire ;
- visualiser les tendances d'une boîte ;
- distinguer zéro et absence de relevé dans les graphiques ;
- consulter les déplacements ;
- consulter la parenté ;
- repiquer une boîte ;
- déplacer une boîte ;
- désactiver ou réactiver une boîte ;
- gérer les alertes ;
- consulter les emplacements thermiques ;
- saisir une température manuelle ;
- ajouter des emplacements et des sondes ;
- gérer les comptes ;
- gérer les institutions ;
- préparer des transferts ;
- importer un transfert CSV ;
- exporter des relevés ;
- imprimer des étiquettes QR code ;
- consulter un historique global des actions.

Cette base n'est pas encore un produit final, mais elle couvre déjà les principaux parcours nécessaires au suivi de laboratoire : retrouver une boîte, saisir un relevé, conserver l'historique, suivre les emplacements, préparer les repiquages, partager les données et garder la trace des actions.
