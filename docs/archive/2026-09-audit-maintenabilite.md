# Audit de maintenabilité - septembre 2026

- **Date de l'audit :** 4 septembre 2026
- **Commit inspecté :** `f0acd7ecbfb5713b6877d3ee79f14bd6e714676e`
- **Périmètre :** code Django/DRF et React/TypeScript de production, tests, historique Git et requêtes visibles par inspection.
- **Statut global :** ouvert, à revalider avant chaque chantier.
- **Priorité globale :** haute sur les concentrations frontend; moyenne sur le backend, mieux protégé par ses tests.
- **Dernière vérification :** revalidation statique et historique Git le 4 septembre 2026.

Cet audit est une photographie, pas une source de vérité permanente. Les nombres de branches ci-dessous proviennent d'un parcours AST local simple : ils servent à comparer les unités du dépôt, pas à imposer un seuil de refactorisation. Les comptes de commits par chemin sont reproduits avec `git rev-list --count HEAD -- <chemin>`; la fréquence récente compte les apparitions du chemin dans les 50 derniers commits du dépôt.

## Synthèse

| Priorité | Hotspot | Mesure actuelle | Protection principale |
|---|---|---|---|
| Haute | `frontend/src/App.tsx` | 4 082 lignes, 100 commits, 27 dans les 50 derniers; `App` 1 090 lignes / 46 branches, `BoxPage` 878 / 55 | TypeScript, build et tests indirects |
| Haute | `frontend/src/components/AdminView.tsx` | 3 523 lignes, 32 commits, 17 dans les 50 derniers | QA métier et backend, peu de tests React |
| Haute | Tests d'interaction frontend | Aucun runner de composants; 17 tests Node ciblent des utilitaires | Build, TypeScript et QA navigateur manuelle |
| Haute | `backend/apps/cultures/api_views.py` | 1 899 lignes, 39 commits, 23 dans les 50 derniers | Suites cultures, inventaire, cycle de vie et concurrence |
| Moyenne | Vues Overview et sérialisation des températures | Toutes les boîtes actives; historiques bornés mais chargés en mémoire; requêtes par emplacement sur une fiche | Périmètre institutionnel et fenêtres temporelles |
| Moyenne | Exports | Service de 442 lignes; génération et prévisualisation en mémoire | 10 tests backend dédiés |

## M-01 - Orchestration concentrée dans App et BoxPage

- **Statut :** ouvert.
- **Priorité :** haute.
- **Constat :** `App` gère session, organisation active, routage, chargements globaux et mutations de plusieurs domaines. `BoxPage` réunit saisie, correction, historique, déplacement, cycle de vie, repiquage, QR et alertes.
- **Preuves / points d'entrée :** `frontend/src/App.tsx:188` (`App`), `frontend/src/App.tsx:2435` (`BoxPage`); 4 082 lignes; 100 commits, dont 27 apparitions dans les 50 derniers commits du dépôt. Heuristique AST : 46 et 55 branches respectivement.
- **Risque :** une évolution locale peut invalider les rafraîchissements, états de chargement ou contrats de composants éloignés. La fréquence de modification augmente ce risque davantage que la taille seule.
- **Suite recommandée :** extraire d'abord un flux métier complet et ses tests, par exemple les mutations de boîte ou le chargement de session. Ne pas découper par taille uniquement.
- **Dernière vérification :** 4 septembre 2026 sur `f0acd7e`.

## M-02 - Administration regroupe plusieurs domaines

- **Statut :** ouvert.
- **Priorité :** haute.
- **Constat :** comptes, zones, sondes, institutions, transferts CSV et journal d'audit résident dans un même fichier. Plusieurs sous-composants restent cohérents individuellement, mais partagent types, formatage et état dans une unité très large.
- **Preuves / points d'entrée :** `frontend/src/components/AdminView.tsx:339`, `:786`, `:1255`, `:1744`, `:2014`, `:2446`, `:3362`; 3 523 lignes; 32 commits, dont 17 apparitions dans les 50 derniers commits du dépôt.
- **Risque :** conflits Git et relecture difficile lorsque deux chantiers Administration évoluent en parallèle.
- **Suite recommandée :** reprendre le modèle déjà utilisé pour `BoxInventoryAdminSection` : un fichier par domaine administratif, avec contrats de props explicites. Commencer seulement lors du prochain changement substantiel du domaine concerné.
- **Dernière vérification :** 4 septembre 2026 sur `f0acd7e`.

## M-03 - API cultures très centrale

- **Statut :** ouvert.
- **Priorité :** haute.
- **Constat :** le même module contient dashboard, overview, boîtes, inventaire, mesures, lignées, zones, températures, transferts et alertes. Les services transactionnels réduisent déjà une partie du risque, mais la frontière des responsabilités reste diffuse.
- **Preuves / points d'entrée :** `backend/apps/cultures/api_views.py`, 1 899 lignes, 39 commits, dont 23 apparitions dans les 50 derniers commits du dépôt. Hotspots mesurés : import de transfert 122 lignes / 14 branches, qualification groupée 96 / 14.
- **Risque :** imports croisés, conventions d'audit dupliquées et revue moins ciblée. Les changements récents de concurrence ont montré que transaction, permission, alerte et audit doivent être considérés ensemble.
- **Tests :** couverture fonctionnelle fournie notamment par 54 tests dans `cultures/tests.py`, 27 dans `test_box_inventory_api.py`, 23 dans `test_box_lifecycle.py` et trois suites PostgreSQL de concurrence.
- **Suite recommandée :** regrouper à terme les endpoints par domaine sans modifier leurs contrats; conserver les écritures sensibles dans les services transactionnels.
- **Dernière vérification :** 4 septembre 2026 sur `f0acd7e`.

## M-04 - Coûts de lecture croissants avec l'historique

- **Statut :** ouvert, impact à mesurer avec des volumes représentatifs.
- **Priorité :** moyenne.
- **Constat :** Overview matérialise sans pagination toutes les boîtes actives de l'organisation sélectionnée, y compris les boîtes anciennes ou sans relevé récent. Les historiques biologiques, thermiques et de localisation restent bornés par la fenêtre demandée, entre un et douze mois, mais sont chargés en mémoire. `BoxDetailSerializer.get_temperature_history` exécute une requête de températures pour chaque emplacement historique. Le fallback des anciens audits peut aussi exécuter une requête de mesure par entrée dépourvue de `measurement_id`.
- **Preuves / points d'entrée :** `backend/apps/cultures/api_views.py:624-705`, `backend/apps/cultures/serializers.py:322-345`, `backend/apps/accounts/api_views.py:866-881`.
- **Risque :** temps de réponse et taille de payload potentiellement croissants avec le nombre de boîtes actives, de relevés, de mouvements et d'audits historiques. Aucun problème de performance n'est confirmé sur les volumes de production par cette inspection statique.
- **Suite recommandée :** ajouter des tests de nombre de requêtes et mesurer latence, taille de payload et mémoire avec un jeu QA représentatif avant toute optimisation. Précharger les températures par zone/période et traiter le fallback legacy en lot seulement si les mesures montrent un coût réel.
- **Dernière vérification :** 4 septembre 2026 sur `f0acd7e`.

## M-05 - Faible couverture des interactions React

- **Statut :** ouvert.
- **Priorité :** haute.
- **Constat :** le frontend ne possède ni Vitest/Jest ni bibliothèque de test de composants. Les trois scripts Node exécutent actuellement 17 tests d'utilitaires : 4 pour les erreurs API, 7 pour l'inventaire et 6 pour les fenêtres et emplacements de graphiques. Ils ne couvrent pas le rendu, les effets, modales, chargements ou redirections des gros composants.
- **Preuves / points d'entrée :** `frontend/package.json`, `frontend/scripts/test-api-errors.mjs`, `frontend/scripts/test-box-inventory.mjs`, `frontend/scripts/test-chart-window.mjs`. Le travail récent sur les graphiques a renforcé ce dernier script sans ajouter de runner de composants ou d'interactions.
- **Risque :** les régressions d'enchaînement restent principalement détectées par QA navigateur manuelle, surtout dans `App`, `BoxPage`, `AdminView`, `ExportsView` et `ZonesView`.
- **Suite recommandée :** introduire un socle minimal de tests de composants seulement avec accord sur la dépendance. Prioriser garde Administration, changement d'institution, conflit de transfert, saisie/correction de mesure et erreurs réseau.
- **Dernière vérification :** 4 septembre 2026 sur `f0acd7e`.

## M-06 - Exports et prévisualisation couplés au format CSV

- **Statut :** couplage accepté; impact volumétrique non mesuré.
- **Priorité :** moyenne.
- **Constat :** la prévisualisation reconstruit puis reparcourt le CSV complet. Cette approche garantit aujourd'hui que l'aperçu et le fichier utilisent la même sélection, y compris l'ancrage sur les mesures pertinentes pour les filtres, mais lie le graphique aux colonnes et aux règles du format d'export.
- **Preuves / points d'entrée :** `backend/apps/exports/services.py:97` pour la génération et `:197` pour la prévisualisation; 442 lignes; 10 tests dédiés.
- **Risque :** coût mémoire potentiel et divergence difficile à diagnostiquer si le format CSV évolue. Le couplage est actuellement explicite et testé; aucun problème de performance en production n'est établi.
- **Suite recommandée :** ne pas refactorer maintenant. Si des mesures représentatives montrent une limite, produire un modèle intermédiaire commun, puis rendre CSV et aperçu depuis ce modèle.
- **Dernière vérification :** 4 septembre 2026 sur `f0acd7e`.

## Complexité légitime

- `AdminAuditLogListAPIView.get` : 111 lignes / 24 branches, mais validation des filtres, pagination et enrichissement sont linéaires et commentés. Le fallback legacy doit être mesuré avant découpage.
- `move_box_to_thermal_zone` : 88 lignes / 13 branches. La transaction, le verrouillage, la détection d'état périmé, l'historique et l'audit forment une seule opération métier cohérente et testée sous PostgreSQL.
- `AdminBoxInventoryBatchQualifyAPIView.post` : 96 lignes / 14 branches. Les succès partiels par boîte sont une règle produit; découper sans préserver les transactions individuelles rendrait le flux moins lisible.
- `BoxTransferImportAPIView.post` : 122 lignes / 14 branches. La validation d'un contrat externe justifie plusieurs branches; la priorité reste aux tests de contrat.

## Ordre de travail proposé

1. Ajouter une couverture d'interaction minimale autour des flux les plus risqués.
2. Extraire progressivement les responsabilités d'`App.tsx` et d'`AdminView.tsx` au fil des prochains chantiers.
3. Mesurer Overview, détail de boîte et audits avec des volumes QA avant toute optimisation.
4. Séparer les domaines de `cultures/api_views.py` lorsque les contrats disposent de tests ciblés suffisants.

Ne pas entreprendre un découpage massif simultané du frontend et du backend : cela supprimerait les repères précisément là où les tests d'interaction sont les plus faibles.
