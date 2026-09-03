# Routeur de contexte Polypbase

Polypbase est une application Django REST Framework et React/TypeScript destinée au suivi de cultures de méduses et d'autres organismes. Créée dans le contexte de l'Aquarium de Paris, elle est structurée pour plusieurs institutions et sert au laboratoire comme au bureau.

Ce fichier est une carte, pas une documentation à charger avec tous ses liens.

## Chargement progressif

1. Lire [`AGENTS.md`](../AGENTS.md), qui contient les invariants universels.
2. Identifier ci-dessous le domaine de la tâche.
3. Ouvrir seulement le ou les contextes indiqués, jamais tout `docs/context/` par défaut.
4. Inspecter ensuite le code, les migrations et les tests concernés.
5. Consulter une documentation spécialisée uniquement si le contexte de domaine y renvoie ou si la tâche l'exige.

Une modification transversale peut légitimement demander plusieurs contextes. Pour une correction locale, un ou deux doivent généralement suffire.

## Sources de vérité

Par ordre de priorité :

1. décision métier explicite de la tâche actuelle;
2. invariants permanents de [`AGENTS.md`](../AGENTS.md);
3. code, migrations et tests, qui décrivent l'implémentation actuelle;
4. fichiers de `docs/context/`, qui cartographient les contrats connus;
5. documentation spécialisée plus ancienne.

Le code décrit ce qui existe, mais un comportement observé n'est pas automatiquement une règle métier intentionnelle. Si une divergence peut être un bug ou une décision ambiguë, ne pas choisir arbitrairement : demander.

## Selon la tâche, lire

| Tâche | Contexte principal | Complément éventuel |
|---|---|---|
| Architecture générale, API, carte du dépôt | [`product-architecture.md`](context/product-architecture.md) | Frontend si interface concernée |
| Compte, authentification, institution, rôle, permission | [`organizations-permissions.md`](context/organizations-permissions.md) | Architecture générale |
| Boîte, statut, emplacement, transfert, repiquage | [`boxes-lifecycle-locations.md`](context/boxes-lifecycle-locations.md) | Intégrité des mesures |
| Relevé, zéro, température, observation, concurrence | [`measurements-integrity.md`](context/measurements-integrity.md) | Frontend si affichage |
| Inventaire, qualification, sélection groupée | [`inventory.md`](context/inventory.md) | Cycle de vie des boîtes |
| Import, export, CSV, normalisation ou reprise de données | [`imports-exports.md`](context/imports-exports.md) | Mesures ou organisations |
| React, responsive, routage, i18n, accessibilité | [`frontend-ux.md`](context/frontend-ux.md) | Domaine métier touché |
| Migration Django, tests, Git, QA, production | [`development-deployment.md`](context/development-deployment.md) | Domaine fonctionnel touché |
| Audit d'une mutation | Domaine métier concerné | Organisations + intégrité |
| Refonte multi-institution transversale | Organisations + architecture | Tous les domaines réellement touchés |

## Documentation spécialisée

- [`README.md`](../README.md) : installation, lancement et points d'entrée.
- [`guide_utilisateur.md`](guide_utilisateur.md) : parcours fonctionnels.
- [`tests_backend.md`](tests_backend.md) : inventaire détaillé des tests backend.
- [`deploiement_vm.md`](deploiement_vm.md) : exploitation et procédures de secours.
- [`transferts_csv.md`](transferts_csv.md) : contrat des transferts entre institutions.
- [`mcd/schema_bdd.md`](mcd/schema_bdd.md) et [`tracabilite_mcd.md`](tracabilite_mcd.md) : modèle conceptuel et correspondance Django.
- [`sondes/`](sondes/) : documentation exploratoire des sondes; vérifier le code avant de considérer une intégration comme active.

Ces documents peuvent dater d'étapes différentes.
