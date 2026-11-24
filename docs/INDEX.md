# Documentation de Polypbase

Polypbase est une application Django REST Framework et React pour le suivi de cultures dans plusieurs institutions. Ouvrir uniquement les pages utiles à la tâche, puis vérifier l'implémentation, les migrations et les tests.

## Sources de vérité

1. Décision métier explicite applicable à la tâche, dans le respect des invariants d'[AGENTS.md](../AGENTS.md).
2. Code, migrations et tests pour le comportement de la version étudiée. Un comportement observé n'est pas forcément une règle voulue : en cas de divergence, demander une décision.
3. Références ci-dessous pour les contrats; [État](ETAT.md) pour les constats et réserves à une date donnée; [Décisions](DECISIONS.md) pour les arbitrages.
4. Les [archives](archive/README.md) sont des preuves datées, pas des instructions courantes. Les [sources de conception](sources/) ne constituent pas un schéma déployé.

## Choisir par tâche

| Tâche | Document propriétaire |
|---|---|
| Comprendre le produit, les applications et le dépôt | [Architecture](reference/architecture.md) |
| Rechercher une route Django | [Carte des API générée](reference/api.md) |
| Compte, rôles, invitation, périmètre et partage externe | [Organisations, permissions et partage](reference/organisations-permissions-partage.md) |
| Créer, qualifier, déplacer, réactiver une boîte, suivre ses identifiants, son ADN ou sa lignée | [Cycle de vie des boîtes](reference/boites-cycle-de-vie.md) |
| Lire, simuler ou conserver les révisions historiques | [Historique et révisions](reference/historique-revisions.md) |
| Relevés, précision, zéro, température, sonde ou salinité | [Mesures, températures et sondes](reference/mesures-temperatures-sondes.md) |
| CSV, planner, export ou don institutionnel | [Imports, exports et transferts](reference/imports-exports-transferts.md) |
| Espèces, souches, codes AAA/BBB et WoRMS | [Taxonomie et espèces](reference/taxonomie-especes.md) |
| React, routing, ergonomie ou CSS | [Frontend](reference/frontend.md) · [Organisation des styles](../frontend/src/styles/README.md) |
| Texte visible, catalogues FR/EN/JA, contrôle des usages | [Terminologie et i18n](reference/terminologie-i18n.md) · [Registre applicatif](translation-review/application/README.md) |
| Modèles Django, contraintes et traçabilité | [Modèle de données](reference/modele-donnees.md) |
| Installation pour développer | [Démarrage](../README.md) |
| Tests, QA navigateur et migrations en environnement isolé | [Développement et QA](operations/developpement-qa.md) |
| Release, sauvegarde, migrateur dédié ou collecte iMinilide sur la VM | [Déploiement et exploitation](operations/deploiement-exploitation.md) |
| Démonstration et conditions d'isolation | [Démo isolée](operations/demo-isolee.md) |
| Utiliser Polypbase au laboratoire ou en administration | [Guide utilisateur](utilisateur/guide.md) |

### Annexes techniques (à ouvrir au besoin)

- [Mouvements historiques](reference/annexes/historical-movements.md)
- [Fondation des espèces institutionnelles](reference/annexes/institution-species-foundation.md)
- [Repiquage quantitatif](reference/annexes/quantitative-subculture.md)
