# Diagnostic CSV de relevés — livraison partielle

## Statut et limite d'activation

**PARTIALLY IMPLEMENTED / BLOCKED.** Cette tranche permet de préparer et de
comparer un CSV dans l'institution active. Elle ne permet pas d'importer des
relevés, même lorsque le diagnostic ne détecte aucun blocage.

L'inspection part d'un worktree propre sur `f008862`. Les contrats existants
prévoient une source originale privée, un plan durable, une acceptation et des
reçus idempotents. Leur infrastructure générique n'existe pas. La conservation
pendant 30 jours des sources non appliquées reste une proposition non approuvée
(`context/imports-exports.md`). Aucun délai de conservation ou mécanisme de
suppression automatique n'est introduit ici.

Avant d'activer une application, il reste à définir et implémenter :

- le cycle de vie de la source originale et des preuves dérivées, y compris les
  données personnelles éventuellement contenues dans les cellules ignorées;
- le contrat d'acceptation durable et de reçu : identité d'acceptation,
  autorisation de consultation/application, rejeu et lots sans création;
- la matérialisation fidèle des états facultatifs absents/vides lors de créations,
  sans réutiliser les défauts interactifs ni convertir une absence en zéro;
- une baseline versionnée couvrant précision, observateur original, origine,
  importateur et acteur historique séparément;
- le service d'application atomique avec relecture de la source et des permissions,
  verrouillage cohérent des boîtes, revalidation complète, audit par mesure et
  reçu transactionnel; puis sa validation concurrente sur PostgreSQL jetable.

Les précédents `AuditLog`, import historique 2026 et transferts fournissent des
motifs utiles, mais pas une politique générique de conservation ou d'acceptation.
Leurs conventions ne sont pas transformées implicitement en décision produit.

## Parcours livré

Depuis l'accueil du laboratoire, hors Administration, un membre Admin ou
technicien peut ouvrir **Diagnostiquer un CSV de relevés** :

1. choisir un fichier UTF-8 de 5 Mio maximum et un séparateur explicite;
2. inspecter les en-têtes (ceci ne valide pas les lignes de données);
3. choisir pour chaque colonne un champ canonique ou une exclusion volontaire,
   sans correspondance devinée;
4. confirmer les correspondances **pour le diagnostic uniquement**;
5. calculer un aperçu : comptes, candidats à la création, lignes identiques,
   ignorées, invalides, conflits et blocages, avec références d'enregistrement,
   lignes physiques et colonnes;
6. corriger le fichier ou le mapping et recalculer si nécessaire.

Les résultats sont paginés par 50 lignes dans l'interface. Les états absent,
vide, NULL, zéro et faux restent distincts. Les textes sont traduits FR/EN.
Un avertissement permanent précise qu'aucun relevé n'a été importé et qu'aucune
application n'est disponible. Il n'existe ni bouton de confirmation d'import,
ni résultat de succès d'import, ni endpoint d'application.

Toute modification du fichier, du séparateur ou des correspondances invalide
le résultat précédent et sa confirmation. Changement d'institution, fermeture
et démontage annulent les requêtes; leurs réponses obsolètes ne sont pas publiées.

## Contrat API diagnostique

Deux routes POST uniquement, avec session Django, CSRF et en-tête explicite
`X-Organization-Id` :

- `/api/measurement-import/inspect/`
- `/api/measurement-import/preview/`

Le corps est un `multipart/form-data` avec un seul champ fichier `file` et
`delimiter` (virgule, point-virgule ou tabulation réelle). L'aperçu exige également
`mapping`, un objet JSON en-tête exact vers champ canonique ou `null`, et
`confirmed`, la chaîne exacte `true`. Aucun plan ou hash fourni par le client
n'est accepté comme autorisation.

L'inspection retourne `headers`, `fields`, `issues` et
`application_available: false`. L'aperçu retourne cette même indisponibilité,
les empreintes de préparation et de plan, `source_complete`, les issues fichier,
le résumé du planner et les lignes projetées. Une issue contient un code stable,
un champ canonique éventuel et une colonne éventuelle. Les erreurs de transport
retournent `error_code` avec un statut 400/413; les refus d'accès retournent 403.
Les problèmes CSV restent des diagnostics, pas une autorisation partielle.

`summary.ready` signifie uniquement « aucun blocage du planner détecté ». Cela
ne garantit ni disponibilité de l'application, ni réservation, ni fraîcheur
transactionnelle. Les lectures du planner ne sont pas un snapshot verrouillé.
Un nouveau calcul observe les modifications intervenues depuis le précédent.

## Autorisation, limites et données

L'acteur, l'institution et le membership actif sont rechargés au serveur. Seuls
Admin et technicien actifs de l'institution explicitement sélectionnée sont
acceptés. Le superutilisateur sans membership produit n'est pas une dérogation.
L'autorisation précède le parsing multipart, y compris pour la vérification CSRF.
Le planner conserve ses recherches de boîtes par code exact dans cette seule
institution. Une référence étrangère est indiscernable d'une référence inconnue.

Un upload handler unique conserve temporairement les octets en mémoire, arrête
l'accumulation au-delà de 5 Mio et ne bascule pas sur un fichier temporaire Django.
Le flux multipart entier est également borné, même sans limite globale Django.
Les limites canoniques restent 10 000 enregistrements, 64 colonnes et 16 384
caractères par champ. Les métadonnées et le mapping sont contrôlés et bornés.
Les limites globales Django peuvent rejeter une configuration volumineuse avant
les limites maximales théoriques du mapping.

Aucune source, plan, profil de mapping ou reçu n'est persisté. Le nom du fichier,
les cellules brutes/ignorées, notes, noms d'observateurs et baselines stockées ne
sont pas renvoyés par l'aperçu. Les en-têtes et valeurs canoniques utiles sont
rendus comme texte React, sans HTML injecté. Les réponses, y compris les erreurs,
portent `Cache-Control: no-store`. Ceci décrit le traitement applicatif; cela
n'invente aucune politique pour les infrastructures externes de journalisation.

Les routes diagnostiques évitent également les écritures indirectes habituelles
de préférence de langue et de session. Les tests surveillent les requêtes SQL
et comparent l'ensemble des tables applicatives avant/après.

## Intégrité et compatibilité

Le contrat canonique 1.0 et le planner 1.1 restent l'autorité de classification.
Un doublon source bloque, même identique. Un zéro occupe une semaine comme toute
autre valeur. Un match n'est jamais une réécriture. Les semaines seules restent
sans jour fabriqué et les candidats CREATE portent le blocage de persistance
existant. Une référence d'observateur nommée reste non résolue et bloquante.

Aucun relevé, audit, séquence scientifique, révision, snapshot de repiquage,
mouvement ou état de boîte n'est modifié. Les POST/PATCH ordinaires, importeurs
legacy, contraintes de précision et résolveurs scientifiques sont inchangés.
Aucun modèle, migration ou dépendance de production n'est ajouté.

## Validation locale effectuée

Toutes les commandes Django ci-dessous utilisent, depuis `backend/`, le préfixe
`POSTGRES_DB='' POLYPBASE_TEST_POSTGRES=0 DJANGO_DEBUG=1` et
`--settings=config.test_settings`, donc SQLite isolé en mémoire :

- `uv run python manage.py test apps.measurements apps.accounts.tests.AccountPreferenceTests apps.accounts.tests.SessionLoginApiTests` : **253 tests réussis**, dont **22 nouveaux** tests d'inspection/API;
- `uv run python manage.py check` : aucun problème;
- `uv run python manage.py makemigrations --check --dry-run` : aucune dérive;
- suite backend complète : 1 504 tests découverts, arrêt à la limite de
  **240 secondes**, sans résultat final; non déclarée validée.

Depuis `frontend/` :

- `node --test scripts/test-measurement-csv.mjs` : **11 tests réussis**;
- `node --test scripts/test-api-errors.mjs scripts/test-pilotage-home.mjs scripts/test-auth-routing.mjs` : **73 tests de régression réussis**;
- contrôle CSS via `npm run check:css` : réussi;
- `node node_modules/typescript/bin/tsc --noEmit` : réussi;
- `node node_modules/vite/bin/vite.js build` : réussi, **2 241 modules** transformés;
- `npm run build` : échoue sur l'exécutable `tsc` absent de `node_modules/.bin`;
  les trois étapes CSS/TypeScript/Vite ont été validées séparément;
- `npm ci --no-audit --no-fund` : limite de **180 secondes**, installation non
  considérée terminée; aucun fichier de dépendances versionné modifié;
- `npm run test:all` : limite de **180 secondes**, sans résultat final;
  suite complète non déclarée validée.

`git diff --check` a été exécuté sans erreur de whitespace. Aucun accès à Neon,
production ou base partagée. Aucun test d'application ou de concurrence import
PostgreSQL : l'applicateur n'existe pas. La disponibilité du moteur Docker n'a
pas pu être confirmée avant le délai limite. Aucune QA dans un vrai navigateur
n'a été effectuée; les tests de composants utilisent le harness Node existant.
Les contrôles du skill QA ont été exécutés manuellement avec les settings isolés,
pas via son script qui lance certains contrôles avec les settings par défaut.

**Le diagnostic est implémenté et testé localement; le parcours complet d'import
n'est pas utilisable.** Attendre une revue indépendante avant toute suite. Aucun
commit, push, merge ou déploiement n'a été effectué.
