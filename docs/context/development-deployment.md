# Développement, validation et déploiement

## Inspection minimale

Avant une modification :

1. lire `AGENTS.md`, le routeur et seulement le contexte de domaine utile;
2. exécuter `git status --short --branch` et examiner le diff existant;
3. lire modèles, permissions, services, serializers, vues, types, composants et tests réellement concernés;
4. rechercher tous les consommateurs avant de modifier un contrat;
5. identifier les invariants applicables : zéro, organisation, permission, historique, audit et concurrence;
6. implémenter dans le périmètre, puis relire le diff complet.

Préserver tout changement existant. Ne pas lancer automatiquement une migration, un import ou des données de démonstration : identifier d'abord la base ciblée.

## Backend isolé

Exécuter depuis `backend/`. `config.test_settings` force une base SQLite en mémoire et évite la base configurée dans l'environnement local.

```powershell
# Remplacer le module par le test du domaine modifié
uv run python manage.py test apps.cultures.test_box_inventory_api --settings=config.test_settings

# Suite backend isolée
uv run python manage.py test --settings=config.test_settings

# Configuration et dérive de migrations
uv run python manage.py check --settings=config.test_settings
uv run python manage.py makemigrations --check --dry-run --settings=config.test_settings
```

Utiliser les tests ciblés en premier. Élargir à la suite complète lorsqu'une règle partagée, une permission, une transaction ou un contrat API transversal change. La liste détaillée des tests existants est dans [`../tests_backend.md`](../tests_backend.md).

Les conclusions dépendant des verrous, contraintes ou transactions PostgreSQL doivent être reproduites sur un PostgreSQL QA isolé. SQLite ne suffit pas. Cet environnement doit être temporaire, distinct de Neon et de la production, puis entièrement nettoyé.

## Frontend

Exécuter depuis `frontend/`.

```powershell
npm run test:api
npm run test:charts
npm run test:inventory
npm run typecheck
npm run check:css
npm run build
```

`npm run build` réexécute le contrôle CSS et TypeScript avant le build Vite. Les scripts ciblés couvrent respectivement la gestion des erreurs API, les fenêtres de graphiques et la logique d'Inventaire; ils ne remplacent pas une QA navigateur pour une interaction visuelle.

Pour une modification substantielle, vérifier dans un vrai navigateur les supports concernés et les états loading, empty, error, zéro, clavier et tactile. Ne pas laisser de capture, serveur, base ou réglage QA temporaire.

## Git et revue

Depuis la racine :

```powershell
git status --short --branch
git diff --stat
git diff
git diff --check
```

Une revue multi-agent reste en lecture seule par défaut. Le reviewer lit les invariants concernés, inspecte le diff et les tests, puis présente les problèmes par gravité. Un correctif nécessite une demande distincte. Éviter deux agents écrivains sur les mêmes fichiers.

Avant un commit demandé, vérifier que tous les fichiers appartiennent au chantier et qu'aucun artefact local n'est inclus. Ne pas commit, push ou déployer sans demande explicite.

## Migrations et données existantes

Avant d'ajouter une contrainte sur des données existantes, diagnostiquer en lecture seule si elles respectent l'invariant. Ne jamais corriger automatiquement des données scientifiques pour faire passer une migration.

## Déploiement

La procédure de référence est :

```powershell
.\deploy\deploy_vm.ps1
```

Le mode `-PreflightOnly` exécute les contrôles locaux sans modifier la VM. Le script doit être inspecté avant usage, car il constitue la procédure gardée actuelle. Il vérifie notamment branche, état Git, alignement avec `origin/main`, espaces, Django, dérive de migrations, tests backend isolés et build frontend.

Sans `-PreflightOnly`, la procédure :

1. refuse les états locaux ou distants incohérents;
2. crée et vérifie une sauvegarde PostgreSQL;
3. avance le dépôt de production en fast-forward vers le commit attendu;
4. synchronise les dépendances;
5. construit le frontend dans une zone intermédiaire;
6. exécute les checks Django, affiche le plan et applique les migrations;
7. collecte les statiques et publie le build;
8. redémarre Polypbase, valide puis recharge Nginx;
9. contrôle HTTPS, page React, commit et services.

Ne pas reproduire manuellement ces étapes lorsqu'elles sont déjà orchestrées. Toute mutation de production demande une autorisation explicite immédiatement avant exécution. En cas d'échec d'une protection, arrêter au lieu de la contourner.

Les détails d'exploitation, de sauvegarde et de secours sont dans [`../deploiement_vm.md`](../deploiement_vm.md).

## Sécurité durable

- Secrets hors dépôt et chargés par l'environnement; ne jamais afficher un `.env`.
- Aucune donnée réelle dans tests, captures, logs partagés ou réponses.
- Aucun test, import, migration d'essai ou démonstration sur Neon ou la production.
- Aucune opération destructive sans sauvegarde, diagnostic et accord explicite.
- Après déploiement, vérifier commit, migrations, services, endpoint health et parcours ciblé sans créer de donnée scientifique uniquement pour le smoke test.
