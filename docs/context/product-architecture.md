# Produit et architecture

## Finalité et utilisateurs

Polypbase suit des cultures de méduses et d'autres organismes : boîtes, référentiels biologiques, relevés, emplacements thermiques, températures, lignées, transferts et exports. Le produit vient du contexte de l'Aquarium de Paris, mais ses comptes et ses données métier sont structurés pour plusieurs institutions.

Les principaux utilisateurs sont les techniciens et soigneurs de laboratoire, les administrateurs d'une institution et les personnes en consultation scientifique. Au laboratoire, tablette paysage et téléphone servent surtout à rechercher ou scanner une boîte, consulter sa fiche et saisir des données. Au bureau, le desktop porte les vues denses, graphiques, exports et l'administration. Les règles d'interface détaillées sont dans [`frontend-ux.md`](frontend-ux.md).

## Carte technique

| Couche | Emplacement | Responsabilité |
|---|---|---|
| Backend | `backend/` | Django, Django REST Framework, sessions, permissions, règles métier, ORM, transactions, audit, exports et QR |
| Frontend | `frontend/` | React, TypeScript, Vite, navigation, saisie, graphiques et adaptation aux supports |
| API | `/api/` | JSON entre React et Django, authentification de session et protection CSRF |
| Données | PostgreSQL en production | Persistance et contraintes; SQLite est utilisé par les tests isolés ordinaires |
| Déploiement | `deploy/` | Préflight, sauvegarde, mise à jour, build, migrations et contrôles de service |

Le frontend n'utilise pas React Router. `frontend/src/App.tsx` traduit l'URL en état de route et s'appuie sur l'API History. Les appels réutilisables passent par `frontend/src/api/client.ts`, qui gère notamment le contexte d'organisation et les jetons CSRF des mutations. `PilotageView`, `CreateBoxPanel`, `QrSearchModal` et `BoxPage` sont désormais des modules distincts; App conserve orchestration, mutations et caches. La garde institutionnelle n'est pas une garantie de fraîcheur intra-institution; voir [`frontend-ux.md`](frontend-ux.md).

## Domaines backend

- `accounts` : connexion, récupération de mot de passe, invitations, appartenances et rôles.
- `organizations` : institutions et modèles de partenariat.
- `taxonomy` : taxons, espèces, provenances, souches et traductions.
- `cultures` : boîtes, zones, emplacements, mouvements, repiquages, lignées et transferts.
- `measurements` : relevés biologiques, observations, sondes, températures et salinité.
- `exports` : sélection, aperçu, génération de CSV et traces d'import/export.
- `audit` : journal d'actions (`AuditLog`); modèle et stockage historiques `Alert` conservés dormants.

Ce découpage situe le code; il ne remplace pas les frontières métier documentées dans les autres contextes.

## Décision produit : abandon des alertes

Les Alertes ne sont plus une fonctionnalité active : aucun parcours de consultation ou de résolution, ni génération ou synchronisation automatique d'alertes, ne fait partie du contrat produit. Les mesures restent factuelles; une variation de comptage, un écart à une consigne ou une donnée absente ne doit pas produire d'avertissement opérationnel ou biologique inféré.

`AuditLog` et la fonctionnalité Actions restent actifs et doivent préserver la traçabilité, y compris les entrées historiques liées aux anciennes alertes. Le modèle `Alert`, sa table, ses données et ses migrations sont conservés dormants. Leur devenir exige une décision distincte, explicitement autorisée et non destructive; l'abandon produit n'autorise aucune suppression de données ni réécriture de migrations.

## Vocabulaire minimal

| Terme | Sens |
|---|---|
| `Organization` | Institution ou aquarium propriétaire d'un espace de travail |
| `OrganizationMembership` | Appartenance d'un utilisateur à une institution, avec un rôle propre |
| `Box` | Unité centrale de culture, identifiée par un code global |
| `Species` / `Strain` | Espèce partagée et souche opérationnelle normalement institutionnelle; cas legacy explicitement bornés |
| `BiologicalMeasurement` | Relevé d'une boîte, unique par semaine ISO, avec date stockée, précision et observateur/origine distincts |
| `ThermalZone` | Emplacement thermique d'une institution |
| `BoxLocation` / `BoxMovement` | Période d'emplacement et événement enregistré typé; une frontière de période ne prouve pas un déplacement physique |
| `DailyTemperature` | Agrégat journalier de température d'une zone |
| `SubcultureEvent` / `BoxLineage` | Repiquage et relation parent-enfant |
| `AuditLog` | Trace d'une action, de son auteur et de son institution |

Ne pas enrichir les définitions biologiques sans validation métier.

## Configuration et langues

Les réglages propres à un environnement et les secrets sont fournis par variables d'environnement. Les invitations et la récupération de mot de passe utilisent la configuration d'e-mail du backend. Ne jamais lire ou reproduire le contenu d'un `.env`.

L'interface est française par défaut et dispose d'un catalogue anglais. Les contenus taxonomiques peuvent être localisés séparément en français, anglais et japonais. Voir [`frontend-ux.md`](frontend-ux.md) pour les conventions i18n.

## État vivant et frontières de livraison

[`../../POLYPBASE_CURRENT_STATE.md`](../../POLYPBASE_CURRENT_STATE.md) suit les intégrations, travaux locaux, décisions, revues, limites et roadmap datés. Les audits restent des instantanés historiques.

- Import scientifique : CSV canonique et planner read-only intégrés, pas encore d'applicateur/UI générique. `iso_week` sans jour exact est une cible approuvée, pas une capacité de persistance actuelle.
- Transfer v2 : fondations source/résolution/identité/allocateur/lignée intégrées jusqu'à la Phase 5; acceptation/matérialisation destination Phase 6 absente.
- References, Environment, Transfers et Organizations sont désactivés dans Administration, sans désactiver leurs API backend; Équipe/Comptes, Inventaire et Historique restent actifs. Le grand catalogue partagé est abandonné; maintenance AAA contextuelle à concevoir, routine BBB/provenance différée.
- Le ledger Species/AAA du dépôt compagnon contient désormais des réponses d'Étienne. Ces décisions ne sont ni une approbation finale de manifeste ni une autorisation de réécrire les identifiants historiques.

## Pour aller plus loin

- Sécurité institutionnelle : [`organizations-permissions.md`](organizations-permissions.md).
- Cycle de vie des cultures : [`boxes-lifecycle-locations.md`](boxes-lifecycle-locations.md).
- Modèle conceptuel : [`../mcd/schema_bdd.md`](../mcd/schema_bdd.md) et [`../tracabilite_mcd.md`](../tracabilite_mcd.md).
- Installation générale : [`../../README.md`](../../README.md).
