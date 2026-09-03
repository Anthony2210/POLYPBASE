# Produit et architecture

## Finalité et utilisateurs

Polypbase suit des cultures de méduses et d'autres organismes : boîtes, référentiels biologiques, relevés, emplacements thermiques, températures, lignées, alertes, transferts et exports. Le produit vient du contexte de l'Aquarium de Paris, mais ses comptes et ses données métier sont structurés pour plusieurs institutions.

Les principaux utilisateurs sont les techniciens et soigneurs de laboratoire, les administrateurs d'une institution et les personnes en consultation scientifique. Au laboratoire, tablette paysage et téléphone servent surtout à rechercher ou scanner une boîte, consulter sa fiche et saisir des données. Au bureau, le desktop porte les vues denses, graphiques, exports et l'administration. Les règles d'interface détaillées sont dans [`frontend-ux.md`](frontend-ux.md).

## Carte technique

| Couche | Emplacement | Responsabilité |
|---|---|---|
| Backend | `backend/` | Django, Django REST Framework, sessions, permissions, règles métier, ORM, transactions, audit, exports et QR |
| Frontend | `frontend/` | React, TypeScript, Vite, navigation, saisie, graphiques et adaptation aux supports |
| API | `/api/` | JSON entre React et Django, authentification de session et protection CSRF |
| Données | PostgreSQL en production | Persistance et contraintes; SQLite est utilisé par les tests isolés ordinaires |
| Déploiement | `deploy/` | Préflight, sauvegarde, mise à jour, build, migrations et contrôles de service |

Le frontend n'utilise pas React Router. `frontend/src/App.tsx` traduit l'URL en état de route et s'appuie sur l'API History. Les appels réutilisables passent par `frontend/src/api/client.ts`, qui gère notamment le contexte d'organisation et les jetons CSRF des mutations.

## Domaines backend

- `accounts` : connexion, récupération de mot de passe, invitations, appartenances et rôles.
- `organizations` : institutions et modèles de partenariat.
- `taxonomy` : taxons, espèces, provenances, souches et traductions.
- `cultures` : boîtes, zones, emplacements, mouvements, repiquages, lignées et transferts.
- `measurements` : relevés biologiques, observations, sondes, températures et salinité.
- `exports` : sélection, aperçu, génération de CSV et traces d'import/export.
- `audit` : alertes et journal d'actions.

Ce découpage situe le code; il ne remplace pas les frontières métier documentées dans les autres contextes.

## Vocabulaire minimal

| Terme | Sens |
|---|---|
| `Organization` | Institution ou aquarium propriétaire d'un espace de travail |
| `OrganizationMembership` | Appartenance d'un utilisateur à une institution, avec un rôle propre |
| `Box` | Unité centrale de culture, identifiée par un code global |
| `Species` / `Strain` | Espèce et souche du référentiel taxonomique partagé |
| `BiologicalMeasurement` | Relevé daté d'une boîte |
| `ThermalZone` | Emplacement thermique d'une institution |
| `BoxLocation` / `BoxMovement` | Période d'emplacement et événement de déplacement |
| `DailyTemperature` | Agrégat journalier de température d'une zone |
| `SubcultureEvent` / `BoxLineage` | Repiquage et relation parent-enfant |
| `AuditLog` | Trace d'une action, de son auteur et de son institution |

Ne pas enrichir les définitions biologiques sans validation métier.

## Configuration et langues

Les réglages propres à un environnement et les secrets sont fournis par variables d'environnement. Les invitations et la récupération de mot de passe utilisent la configuration d'e-mail du backend. Ne jamais lire ou reproduire le contenu d'un `.env`.

L'interface est française par défaut et dispose d'un catalogue anglais. Les contenus taxonomiques peuvent être localisés séparément en français, anglais et japonais. Voir [`frontend-ux.md`](frontend-ux.md) pour les conventions i18n.

## Pour aller plus loin

- Sécurité institutionnelle : [`organizations-permissions.md`](organizations-permissions.md).
- Cycle de vie des cultures : [`boxes-lifecycle-locations.md`](boxes-lifecycle-locations.md).
- Modèle conceptuel : [`../mcd/schema_bdd.md`](../mcd/schema_bdd.md) et [`../tracabilite_mcd.md`](../tracabilite_mcd.md).
- Installation générale : [`../../README.md`](../../README.md).
