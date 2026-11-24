# Polypbase - règles de travail

## Chargement du contexte

1. Lire ce fichier.
2. Identifier le domaine de la tâche dans l'[index de la documentation](docs/INDEX.md).
3. Ouvrir uniquement les pages utiles. Consulter [l'état du projet](docs/ETAT.md) si la tâche dépend d'un état daté; ne pas le traiter comme une règle durable.
4. Inspecter ensuite l'implémentation, les migrations et les tests concernés.

## Inspection et périmètre

- Examiner `git status`, le diff existant, le code et les tests avant toute modification.
- Préserver le travail déjà présent. Ne pas utiliser `reset`, `restore`, `clean`, `rebase` ou un stash destructif sans nécessité comprise et accord explicite.
- Ne pas imposer une solution technique avant inspection ni ajouter une dépendance de production sans accord.
- Ne jamais inventer une règle biologique, scientifique, opérationnelle ou d'autorisation. Si le besoin, le code, les tests et la documentation ne permettent pas de conclure, demander une décision métier.
- Garder les changements dans le périmètre demandé. Éviter les refactorisations opportunistes et vérifier tous les consommateurs avant de modifier un contrat API.
- Garder les identifiants, commentaires et docstrings du code en anglais simple. Tout texte visible utilisateur passe par le système i18n français/anglais existant.

## Invariants non négociables

- **`0` est une mesure scientifique réelle.** Il n'est jamais équivalent à `null`, `None`, une chaîne vide, une donnée absente ou l'absence d'un relevé.
- Toute donnée appartenant à une organisation doit être filtrée et autorisée côté serveur dans le contexte de l'organisation active. Vérifier querysets, identifiants client, relations, choix, agrégats, exports et actions groupées. Un filtre frontend n'est jamais une permission.
- Django reste l'autorité pour les permissions, validations et transitions métier.
- Préserver mesures historiques, emplacements, mouvements, lignées, auteurs et audits.
- Pour toute écriture métier, évaluer transactions, contraintes, concurrence et cohérence de l'audit.
- Ne jamais exposer, copier ou committer un secret, un `.env`, des données de production, un mot de passe, un identifiant de base ou une clé SSH.
- Ne jamais utiliser Neon ou la production comme environnement de test, de démonstration ou d'import d'essai. Les migrations de production suivent une procédure dédiée avec autorisation explicite distincte du déploiement applicatif.

## Interface

- Polypbase est un outil de laboratoire : privilégier rapidité, lisibilité, exactitude et traçabilité; réutiliser les composants, tokens et traductions existants.
- **Administration est desktop-only.** Sur tablette, son entrée est absente et toute URL `/administration...` redirige vers `/` sans rendre d'UI ou de message intermédiaire. Ce garde UX ne remplace jamais les permissions backend. Ne pas rendre Administration responsive sans décision produit explicite.
- Préserver les états loading, empty, error, disabled et focus, l'accès clavier/tactile et la distinction entre vide et zéro.
- Le texte utilisateur passe par l'i18n française et anglaise. Ne pas utiliser le point médian dans l'interface.
- Pour l'architecture de l'interface et du CSS, suivre la [référence frontend](docs/reference/frontend.md).
- Pour tout texte visible, traduction ou terme métier, suivre la [référence terminologie et i18n](docs/reference/terminologie-i18n.md). Le japonais validé n'est jamais écrasé.

## Git et multi-agent

- Éviter deux agents écrivains sur les mêmes fichiers. Ne pas réécrire silencieusement le travail d'un autre agent.
- Une revue est read-only par défaut : inspecter invariants, diff et tests, puis classer les problèmes avant toute correction confiée.
- Ne pas commit, push ou déployer sans demande explicite. Inspecter le diff final avant un commit.

## Validation et production

- Valider proportionnellement au risque : tests ciblés d'abord, puis contrôles élargis si un invariant transversal est touché.
- Selon le domaine, exécuter tests Django isolés, contrôle migrations, TypeScript, CSS, build Vite, QA navigateur et `git diff --check`. Suivre le [guide de développement et QA](docs/operations/developpement-qa.md).
- Toute mutation de production exige une autorisation explicite. Suivre le [guide de déploiement et d'exploitation](docs/operations/deploiement-exploitation.md) et utiliser l'automatisation existante plutôt que de reconstruire ses étapes.

## Skills des agents

Modifier uniquement les sources dans [`.agents/skills/`](.agents/skills/). Depuis la racine, lancer `python scripts/sync_agent_skills.py` pour régénérer le miroir [`.claude/skills/`](.claude/skills/) (copies réelles; les entrées absentes de la source sont supprimées du miroir). Lancer `python scripts/sync_agent_skills.py --check` pour vérifier l'inventaire et les octets sans écriture. Ne pas modifier le miroir à la main.
