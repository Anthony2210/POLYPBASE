# Polypbase - règles de travail

## Chargement du contexte

1. Lire ce fichier.
2. Identifier le domaine de la tâche dans [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md).
3. Ouvrir uniquement le ou les fichiers utiles sous `docs/context/`.
4. Inspecter ensuite l'implémentation, les migrations et les tests concernés.

Ne jamais lire tous les fichiers de `docs/context/` par défaut. Une tâche transversale peut en nécessiter plusieurs; une tâche locale, généralement un ou deux.

## Inspection et périmètre

- Examiner `git status`, le diff existant, le code et les tests avant toute modification.
- Préserver le travail déjà présent. Ne pas utiliser `reset`, `restore`, `clean`, `rebase` ou un stash destructif sans nécessité comprise et accord explicite.
- Ne pas imposer une solution technique avant inspection ni ajouter une dépendance de production sans accord.
- Ne jamais inventer une règle biologique, scientifique, opérationnelle ou d'autorisation. Si le besoin, le code, les tests et la documentation ne permettent pas de conclure, demander une décision métier.
- Garder les changements dans le périmètre demandé. Éviter les refactorisations opportunistes et vérifier tous les consommateurs avant de modifier un contrat API.

## Invariants non négociables

- **`0` est une mesure scientifique réelle.** Il n'est jamais équivalent à `null`, `None`, une chaîne vide, une donnée absente ou l'absence d'un relevé.
- Toute donnée appartenant à une organisation doit être filtrée et autorisée côté serveur dans le contexte de l'organisation active. Vérifier querysets, identifiants client, relations, choix, agrégats, exports et actions groupées. Un filtre frontend n'est jamais une permission.
- Django reste l'autorité pour les permissions, validations et transitions métier.
- Préserver mesures historiques, emplacements, mouvements, lignées, auteurs et audits.
- Pour toute écriture métier, évaluer transactions, contraintes, concurrence, alertes et cohérence de l'audit.
- Ne jamais exposer, copier ou committer un secret, un `.env`, des données de production, un mot de passe, un identifiant de base ou une clé SSH.
- Ne jamais utiliser Neon ou la production comme environnement de test, de démonstration ou d'import d'essai. Les migrations de production ne peuvent être exécutées que dans le cadre d'un déploiement explicitement autorisé et de la procédure prévue.

## Interface

- Polypbase est un outil de laboratoire : privilégier rapidité, lisibilité, exactitude et traçabilité; réutiliser les composants, tokens et traductions existants.
- **Administration est desktop-only.** Sur tablette, son entrée est absente et toute URL `/administration...` redirige vers `/` sans rendre d'UI ou de message intermédiaire. Ce garde UX ne remplace jamais les permissions backend. Ne pas rendre Administration responsive sans décision produit explicite.
- Préserver les états loading, empty, error, disabled et focus, l'accès clavier/tactile et la distinction entre vide et zéro.
- Le texte utilisateur passe par l'i18n française et anglaise. Ne pas utiliser le point médian dans l'interface.

## Git et multi-agent

- Éviter deux agents écrivains sur les mêmes fichiers. Ne pas réécrire silencieusement le travail d'un autre agent.
- Une revue est read-only par défaut : inspecter invariants, diff et tests, puis classer les problèmes avant toute correction confiée.
- Ne pas commit, push ou déployer sans demande explicite. Inspecter le diff final avant un commit.

## Validation et production

- Valider proportionnellement au risque : tests ciblés d'abord, puis contrôles élargis si un invariant transversal est touché.
- Selon le domaine, exécuter tests Django isolés, contrôle migrations, TypeScript, CSS, build Vite, QA navigateur et `git diff --check`. Les commandes exactes sont dans [`development-deployment.md`](docs/context/development-deployment.md).
- Toute mutation de production exige une autorisation explicite. Utiliser la procédure `deploy` existante; ne pas reconstruire manuellement ses étapes lorsqu'elle les orchestre.
