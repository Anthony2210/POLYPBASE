# Registre des traductions appliquées à l'application

Ce dossier trace ce qui a réellement été examiné et modifié dans les textes FR/EN de l'application (catalogues React, `.po` Django, chaînes inline). Il ne concerne ni le classeur Google Sheets, qui n'est plus modifié, ni le japonais.

## Trois notions distinctes

| Notion | Où elle vit |
|---|---|
| Décisions et corrections B01 | `register.csv` : références de décision, valeurs avant/après, statuts et justifications. Les propositions historiques AC01–AC28 sont archivées hors dépôt. |
| Valeur présente dans le code | les sources elles-mêmes (`frontend/src/i18n/*.ts`, `backend/locale/*/LC_MESSAGES/django.po`, fichiers inline) |
| Modification effectivement faite dans l'application, ou décision de ne pas la faire | `register.csv` (ce dossier) |

Une proposition historiquement approuvée ne prouve pas que l'application a été modifiée. register.csv distingue les changements appliqués, les valeurs déjà conformes, les exceptions préservées et les corrections différées. check_register.py vérifie leur cohérence avec le code actuel.

## Référence de départ (lot B01)

- Git `HEAD` : `6b9813f7c2ece99ecc8fbbb67670697afceeede5`.
- Feuille historique : `../cleanup/Traduction Polypbase(4).xlsx`, SHA-256 `e80b409fbea485d97fbccf66d06da83eb46374883d1e3343f50e51e656b8c307`. C'est une preuve de référence, pas une source synchronisée. `sheet_value` reprend la cellule de cette version, sans les espaces de début et de fin.

## Colonnes de `register.csv`

`entry_id`, `batch`, `surface` (`react`, `django`, `inline`), `key_or_msgid` (clé exacte ou msgid Django exact), `language`, `source_file`, `initial_value`, `final_value`, `sheet_value`, `decision_ref`, `status`, `reason`, `baseline_git_head`, `baseline_sheet_sha256`.

Pour Django, la valeur est celle que voit l'utilisateur : le `msgstr`, ou le msgid lui-même si aucune entrée n'existe.

Statuts :

- `APPLIED` : la valeur a été modifiée dans ce lot (`initial_value` ≠ `final_value`).
- `ALREADY_MATCHING` : le code avait déjà la valeur approuvée.
- `PRESERVED` : la valeur diffère de la feuille et est volontairement conservée par décision.
- `DEFERRED` : examiné, non modifié faute d'approbation applicative ou d'usage réel ; à reprendre.
- `NOT_FOUND` : la clé ou le msgid n'existe pas dans la source indiquée. `initial_value` et `final_value` restent vides (la chaîne vide représente l'absence). Restriction temporaire : `NOT_FOUND` est refusé pour `surface=inline`, car le vérificateur ne sait pas prouver de façon fiable qu'une chaîne inline est absente d'un fichier. Il reste vérifié pour `react` (clé absente du catalogue) et `django` (aucune entrée dans le `.po`). Cette restriction sera levée seulement avec une détection de présence fiable au niveau source.
- `REMOVED` (depuis B03) : la clé a été volontairement supprimée d'un catalogue React. Réservé à `surface=react`, avec le catalogue de la langue (`frontend/src/i18n/fr.ts` ou `en.ts`). `initial_value` est la valeur exacte avant suppression, non vide ; `final_value` est vide et signifie que l'identité est absente ; `sheet_value` reste vide. La suppression FR et la suppression EN d'une même clé forment une paire dans le même lot. Seul `NOT_FOUND` peut suivre un `REMOVED` : tout autre événement, y compris un second `REMOVED`, contredit la suppression. Une clé dont le dernier événement est `REMOVED` et qui réapparaît dans le catalogue est rejetée. Restaurer une clé demandera un nouveau statut explicite.

## Lot B01 (2026-10-28) en bref

- 21 `APPLIED` : 11 EN et 3 FR dans les catalogues React, 5 EN et 2 FR dans les `.po` Django.
- 5 `ALREADY_MATCHING` : AC11, AC22, AC23, AC24, AC26.
- 2 `PRESERVED` : `movementKindUnclassified` (AC25), `auditValuePendingReview` (AC28).
- 6 `DEFERRED` : AC17 (clé sans usage), AC10 (formulaire Django inutilisé), libellés `pending_review` côté React (hors approbation AC14), libellé Django `Armoire` (non exposé par l'API).
- Aucune clé supprimée ni restaurée, aucun msgid source réécrit.

## Méthode pour un lot suivant

1. Inspecter la clé, son usage et la décision exacte (`docs/context/terminology-i18n.md`).
2. Modifier la source si la décision est confirmée et sûre. Conserver les variables `{x}`. Après un `.po` : `cd backend; uv run python ../scripts/compile_django_messages.py`.
3. Ajouter à la fin de `register.csv` une ligne par identité examinée, avec un nouveau `batch`, le `HEAD` de départ et `entry_id` à la suite.
4. Lancer depuis la racine `python docs/translation-review/application/check_register.py`, puis les tests de non-régression `python -m unittest discover -s docs/translation-review/application -p "test_*.py"`. `--show <clé>` affiche l'historique d'une clé.

## Historique en ajout seul

Le registre est un journal d'événements. Les lignes d'un lot déjà enregistré (dont les 34 lignes de B01 et leurs références de décision) ne sont jamais modifiées, réordonnées ni supprimées. Une erreur se corrige par un nouvel événement dans un lot suivant. Le test `test_h_b01_register_is_unchanged_and_valid` fige une empreinte canonique des 34 lignes B01 (valeurs des champs, indépendante des fins de ligne et des lots ajoutés ensuite) et vérifie qu'elles restent en tête du fichier. Ajouter un lot ne demande donc pas de toucher ce test ; modifier B01 le fait échouer. `test_h_b02_register_is_unchanged` fige de la même façon les 86 lignes B02 (T035–T120), juste après B01.

Règles vérifiées par `check_register.py` :

- **Identité** d'un événement : `surface` + `key_or_msgid` + `language` + `source_file`. Une même clé sur deux surfaces ou dans deux langues forme deux historiques indépendants.
- **Un seul événement par identité et par lot**, même identique : un doublon ou une contradiction dans un lot est rejeté et ne peut pas masquer l'événement précédent.
- **Chronologie explicite** : `batch` suit `Bnn-AAAA-MM-JJ`. Chaque lot forme un bloc contigu ; les blocs se suivent par numéro strictement croissant et date non décroissante ; un numéro ne désigne qu'un seul lot ; `entry_id` (`Tnnn`) croît strictement dans l'ordre du fichier ; un lot déclare un seul `baseline_git_head`. Si l'ordre est invalide ou ambigu, la continuité n'est pas évaluée et le registre est rejeté.
- **Continuité** : pour deux événements successifs d'une même identité, `initial_value` du nouveau égale `final_value` du précédent. `APPLIED` exige un changement de valeur ; `ALREADY_MATCHING`, `PRESERVED` et `DEFERRED` reportent la valeur sans la changer ; `NOT_FOUND` a des valeurs vides, et l'événement suivant doit partir de l'absence ; `REMOVED` part de la dernière valeur connue et ne peut être suivi que d'un `NOT_FOUND`.
- **Lecture des catalogues React** : analyse lexicale stricte, sans exécution. Seule la forme réelle des catalogues est acceptée (imports de types, un unique `export const` d'objet littéral, clés identifiants ou chaînes, valeurs chaînes, commentaires). Toute autre construction (spread, clé calculée, valeur non littérale, interpolation, code hors de l'objet) fait échouer le contrôle : une mise en forme inhabituelle ne peut pas faire passer une clé présente pour absente.
- **État courant** : seul le dernier événement de chaque identité est comparé au code. Un ancien `final_value` peut différer du code actuel si un événement ultérieur l'explique.
- Statuts, surfaces, langues FR/EN uniquement, empreintes, variables `{x}` conservées avec leur multiplicité, parité des clés et variables FR/EN des catalogues.

## Lot B02 (2026-10-29)

Baseline Git : `947105e605f979017e5915f80820ed356c411e4b`. Les 34 événements B01 restent inchangés, en tête du fichier ; B02 ajoute **86 événements, T035–T120** :

- **21 `ALREADY_MATCHING`** : 18 entrées FR dont le msgid est déjà français, et les trois validations de dates EN. L'entrée était absente, mais gettext affichait déjà le même texte source. La couverture explicite augmente sans changement du texte affiché.
- **16 `APPLIED`** : invitation à contacter un administrateur sur sept clés React, dans les deux langues, et sur le refus Django `The parent box is outside the active organization.`, dans les deux langues. Aucun msgid ni contrôle de permission modifié.
- **49 `DEFERRED`** : identités langue/msgid encore absentes, examinées pour les exemptions de couverture exactes. Une reprise d'un report B01 documente ici le nouveau périmètre de contrôle, pas une nouvelle approbation de traduction.

Références d'approbation : demande explicite du writer B02, parties 1A/1B (`B02-safe-identities`), 1C/1D et règle permanente de refus (`B02-permission-refusals`), et exclusion explicite des formulations non approuvées (`B02-scope-deferred`). `sheet_value` reste vide pour tous les événements B02. `baseline_sheet_sha256` conserve uniquement la référence historique figée de B01 : aucune nouvelle lecture, valeur ou approbation de feuille n'est alléguée.

Le message de structure active est un `PermissionDenied` dans `cultures/services.py`. Les événements B02 corrigent cette classification sans réécrire la description historique de T030.

### Couverture et exemptions

Inventaire de production : **88 msgids distincts**. Avant B02 : 51 absences FR et 19 EN. Après B02 : **33 FR et 16 EN**, soit 49 identités manquantes, toutes explicitement exemptées. Ce résultat ne signifie ni traduction intégrale ni conformité de tous les messages utilisateur.

`scripts/check_i18n.py` distingue couverture brute, absences exemptées et erreurs bloquantes. Chaque exemption fixe la langue, le msgid, les chemins source et le nombre d'occurrences, un motif et une référence de décision. Elle exige le dernier événement `DEFERRED` de l'identité exacte (`django`, msgid, langue, fichier PO). Une nouvelle absence, une portée différente, une référence incorrecte ou une exemption devenue inutile bloque le contrôle. Cette liste est technique : le registre reste l'unique historique des décisions et valeurs.

Sont encore différés : FR des trois erreurs de dates ; rôle Django `Lecture seule` / `Viewer` ; choix Django `Armoire`, provenance et Transplant ; gardes d'intégrité sans traduction approuvée ; anciens formulaires scientifiques et choix non exposés par les clients courants. Les métadonnées DRF peuvent toutefois exposer certains choix : l'absence d'affichage React n'établit pas leur inaccessibilité.

Restent également hors correction : `movementErrorForbidden` partagé entre 401 et 403, `csvErrorPermission`, protections de parcours et du dernier responsable actif, `manualTemperatureForbidden` sans usage trouvé, permissions caméra et refus de repiquage non gettext dans les vues API. Aucun de ces cas n'est implicitement approuvé par un contrôle vert.

### Contrôles de B02

- Compilateur PO singulier strict, avec fichier/ligne dans les erreurs, multiligne et échappements UTF-8. Pluriels, contextes, fuzzy, entrées obsolètes et syntaxe inconnue sont explicitement refusés, pas ignorés. Tous les catalogues sont validés avant la première écriture.
- `--check` recompile seulement en mémoire et compare les octets MO versionnés ; il n'écrit jamais. La CI contrôle les fichiers existants sans les régénérer au préalable.
- Extraction AST des imports Django `gettext`/`gettext_lazy` et des balises littérales `trans`/`translate` (option `as` prise en charge). Tests, migrations et caches sont exclus de l'inventaire de production. Appels dynamiques, indirections et formes non prises en charge échouent explicitement.
- Placeholders Django : champs de formatage avec conversion/spécification, paramètres printf nommés ou positionnels et types, multiplicité et échappements. Les pourcentages de prose restent valides ; les flags PO `python-format` et `c-format` activent le contrôle printf strict. Il ne s'agit pas d'un support complet GNU gettext.
- Frontend : AST TypeScript sans exécution du catalogue, parité, doublons, clés/valeurs vides, espaces périphériques, point médian, variables simples `{name}` et multiplicité, formulations contextuelles protégées et neuf refus classés (sept B02, deux B01).
- Aucun bannissement global de `Organization`, `Subculture` ou `Measurement`. Les recherches terminologiques générales et la classification de nouveaux refus restent consultatives.

Les commandes de validation sont dans `docs/context/development-deployment.md`. Pour une modification future, vérifier aussi le diff FR/EN contre les nouveaux événements : le checker contrôle les identités enregistrées mais ne prouve pas à lui seul l'exhaustivité du diff. Aucune suppression ou restauration de clé, aucun statut `REMOVED`, aucune synchronisation de feuille ni modification japonaise dans B02.

## Lot B03 (2026-10-29)

Baseline Git : `f50765ee07a8e6add7db5b11652f4e141acc194d`. Les 120 événements B01/B02 restent inchangés ; B03 ajoute **320 événements `REMOVED`, T121–T440**, un par clé et par langue, FR puis EN pour chaque clé.

- Suppression de **160 clés React inutilisées** dans `fr.ts` et `en.ts` : 1 353 → 1 193 clés par langue. Les autres paires clé/valeur, leur ordre et leur formulation sont inchangés ; aucun composant applicatif n'est modifié.
- Ensemble exact autorisé par la demande du writer B03 (`B03-safe-removal`), revalidé avant suppression par AST TypeScript sur `frontend/src` et `frontend/scripts` : aucune chaîne, aucun identifiant, aucun fragment de template, aucune concaténation ni expression régulière ne désigne ces clés ; les appels de traduction dynamiques et les six chemins de traducteur non typés tirent leurs clés de tables littérales. Les occurrences de `prototype` sont des accès `Object.prototype`.
- `initial_value` reprend la valeur exacte avant suppression ; `sheet_value` reste vide ; `baseline_sheet_sha256` conserve la référence historique figée de B01, sans nouvelle lecture ni approbation de feuille.
- Hors périmètre, conservés : les 132 clés `csv*` encore utilisées par `MeasurementImportPanel` et ses tests, `auditSummaryAlertResolved`, `adminTransferText`, `manualTemperatureForbidden`, les candidats utilisés seulement par des tests, les autres candidats réservés à des lots ultérieurs, les entrées gettext Django et les traductions différées de B02. Aucune règle bloquante générale sur les clés inutilisées n'est introduite, et les traducteurs non typés ne sont pas retypés.

## B03 final : trois lots indépendants (2026-10-29)

Baseline vérifié : `dc69aceda71cbd6b802ef5b3090b2ccc4d8d9647`, branche `chore/i18n-b03-final`, worktree initialement propre et identique aux références locales `main` et `origin/main`. Pas de synchronisation réseau de ces références ni de modification d'un autre worktree.

Le schéma existant exige des numéros de lots strictement croissants (`Bnn-AAAA-MM-JJ`) : les noms de travail B03-2, B03-3 et B03-4 sont donc associés respectivement à **B04**, **B05** et **B06**, sans modifier le checker ni l'ancien lot B03.

| Lot de travail | Identifiant du registre | `decision_ref` | Événements | Clés supprimées par langue | Clés restantes FR / EN |
|---|---|---|---|---:|---:|
| B03-2 | `B04-2026-10-29` | `B03-2-audit-measurements` | T441–T502 | 31 | 1 162 / 1 162 |
| B03-3 | `B05-2026-10-29` | `B03-3-box-creation` | T503–T534 | 16 | 1 146 / 1 146 |
| B03-4 | `B06-2026-10-29` | `B03-4-test-only` | T535–T558 | 12 | 1 134 / 1 134 |

**Total : 59 suppressions par catalogue, 118 nouveaux événements, 558 événements au total.** Chaque paire est enregistrée FR puis EN. Les valeurs initiales sont celles du baseline Git exact ; les valeurs finales et de feuille sont vides. Le hash de feuille est seulement la référence historique B01, pas une nouvelle consultation ou approbation de feuille. L'autorisation est la demande explicite du writer B03 final, conditionnée à la revalidation des usages.

### Revalidation des candidats

Inspection ciblée des composants, hooks, helpers, tests, scripts et composants conservés non montés, puis analyse AST TypeScript de 222 fichiers frontend source/scripts, sans erreur de parsing. Les appels dynamiques, tables littérales, accès directs aux catalogues et six signatures de traducteur non typées ont été inspectés sans les refactoriser. Une revue indépendante a refait la comparaison des catalogues/événements au baseline et l'inspection AST des consommateurs.

**B03-2 — 31 `SAFE_REMOVE`, aucun candidat retenu :**

```text
auditAccountActive auditAccountDisabled auditDescriptionFromApp
auditSummarySpeciesCreated auditSummarySpeciesUpdated
auditSummaryStrainCreated auditSummaryStrainUpdated
auditDetailOrganization auditDetailType auditDetailDescription
auditDetailValues auditDetailChanges auditCorrectionMarker
auditCorrectionDetails auditRelationInitialPolyps
manualTemperatureSaved weeklyMeasurementRecorded editLastMeasurement
editLastMeasurementHelp overviewLatestReading overviewNoMeasurement
overviewTemperature overviewWithEphyrae salinityShort zoneSalinityMissing
noZoneChart recentMeasurementMissing temperatureManualReading
temperatureContinuousReading temperatureSamples measuredTemperature
```

- `auditPresentation.ts` sélectionne les descriptions actuelles, les tables de métadonnées et les libellés de réactivation/désactivation, pas ces anciennes clés de résumé/détail. Les noms d'espèce et codes de souche restent interpolés dans les descriptions actuelles.
- Les corrections et valeurs avant/après restent affichées par `AuditTimeline`. Les variantes complètes `auditRelationInitialPolypsZero/One/Many` restent utilisées et conservées ; la clé sans suffixe n'est pas construite dynamiquement.
- `BoxPage`, `OverviewView`, `ZonesView` et leurs helpers utilisent les libellés actuels de relevé, température et salinité. Les états sans donnée, les agrégats et les données historiques ne sont pas modifiés.
- Les occurrences de `measuredTemperature` sont des propriétés/variables numériques, notamment une assertion de zéro scientifique dans `test-alert-removal.mjs`, pas des demandes de traduction. Elles restent inchangées.

**B03-3 — 16 `SAFE_REMOVE`, aucun candidat retenu :**

```text
confirmDetailStrain confirmDetailLocation quickStrainSearch
createBoxGlobalCode createBoxNumber createBoxNumberMismatch createBoxOrigin
createBoxIdentityShortLocal createBoxIdentityShortSpecies
createBoxIdentityShortOrigin createBoxIdentityShortNumber
createBoxIdentityShortFormat createBoxIdentityUnusableShort
createBoxIdentityUnusable latestMovementEmpty latestMovementUnavailable
```

- `CreateBoxPanel` ne demande ni code ni numéro modifiable et n'ajoute pas de confirmation. `CreatedBoxIdentifier` affiche le code confirmé par le serveur. Les validations, intentions immuables, reprises et gardes de fraîcheur restent inchangées.
- `StrainPicker` utilise `createBoxStrainSearch` et filtre les identités inutilisables sans afficher ces anciens libellés de raison. La provenance reste une donnée recherchable.
- Le composant conservé `QuickStrainCreator` et les helpers/tests de `boxCode` ont aussi été inspectés : ils ne consomment aucune de ces clés. Leur conservation n'est pas remise en cause.
- `LatestMovementBanner` ne rend pas de bannière pour un historique vide, indisponible ou limité au cycle de vie, sauf feedback de récupération qui emploie d'autres clés. Les tests de ces états et de récupération sont inchangés.

**B03-4 — 12 `TEST_ONLY_SAFE`, aucun arbitrage produit requis :**

Les sept clés suivantes ne servent qu'à des assertions négatives. Leurs assertions sont conservées intégralement et ne nécessitent pas la présence des traductions :

```text
boxInventorySelectAllFiltered boxInventorySelectVisiblePage quickStrainAdd
weeklyUpToDate zoneSummaryAlive latestCounts zoneActivityTitle
```

Les cinq autres dépendances de test ont reçu les seuls ajustements justifiés :

| Clé | Ancien couplage | Ajustement et contrat conservé |
|---|---|---|
| `manualSalinityAction` | Liste de présence de `test-zone-detail.mjs` | Retrait de cette entrée uniquement ; actions actuelles de création/correction de salinité et capacités serveur inchangées. |
| `temperatureMeasurementCount` | Même liste | Retrait de cette entrée uniquement ; nombre de mesures, moyenne/mesure, plage disponible et distinction zéro/absence inchangés. |
| `zoneMovementHistoryAction` | Même liste | Retrait de cette entrée uniquement ; les entrées actuelles d'historique utilisent les directions entrée/sortie. |
| `zoneMovementHistoryBack` | Liste de présence de `test-zone-history.mjs` | Retrait de cette entrée uniquement ; le bouton courant utilise `back`, navigation et couverture historique inchangées. |
| `moveDialogMovedAt` | Liste traduite dynamiquement dans `test-move-box-modal.mjs` | Retrait de la clé et de sa seule attente positive. Les textes de date restent dans la fixture pour vérifier leur absence du formulaire ; absence des champs date et payload exact sans date inchangés. |

Aucun candidat n'est `REFERENCED`, `UNCERTAIN` ou `TEST_ONLY_REQUIRES_DECISION` après revalidation. Les 47 suppressions des deux premiers lots sont fondées sur l'absence prouvée de consommateur ; les 12 du dernier lot sur l'analyse précise de leurs seuls usages de test, pas sur un objectif de compte.

### Intégrité et contrôles locaux

- Les événements **T001–T440 sont inchangés**, comparés au baseline par champs et contenu hors conversion de fins de ligne du checkout. Les empreintes B01/B02 restent intactes ; le test historique B03 borne désormais T121–T440 plutôt que toute la fin du fichier, et ajoute leur empreinte canonique figée au baseline.
- Comparaison programmatique après chaque lot : ensemble exact des suppressions, parité FR/EN, valeurs et ordre restants identiques, événements en bijection avec les suppressions, valeurs initiales exactes, paires dans leur lot, IDs séquentiels. Checker et 60 tests du registre passent après chacun des trois lots.
- Les 132 clés `csv*`, `auditSummaryAlertResolved`, `adminTransferText` et `manualTemperatureForbidden` restent identiques. Aucun composant applicatif, gettext Django, japonais, traduction différée B02, règle scientifique, permission ou donnée n'est modifié.
- Tests frontend ciblés après B03-2 : **511 réussis** ; après B03-3 : **472 réussis** ; après B03-4 : **295 réussis**. Ces ensembles se recouvrent et ne constituent pas un total de tests distincts.
- Validation finale : `npm run test:i18n` et `npm run build` réussis (CSS, TypeScript, Vite). `npm run test:all` a été lancé mais interrompu par la limite de 300 secondes pendant la suite, après le test 1 130 sans bilan final : **validation complète non acquise**, aucune affirmation de 2 205 tests réussis.
- Contrôles gettext sans base : compilation MO `--check`, inventaire `check_i18n.py` (0 erreur bloquante), 25 tests compilateur et 20 tests inventaire réussis. Tests runtime `apps.accounts.test_i18n` : **5 réussis**, avec `POSTGRES_DB` vide et `config.test_settings`.
- Revue indépendante read-only : aucun défaut identifié ; 37 tests des trois fichiers modifiés et 60 tests du registre réussis. Elle confirme aussi les 84 assertions négatives inchangées des six fichiers de test pertinents.

Aucun commit, push, merge, cherry-pick, déploiement, accès Neon ou production, ni modification de feuille. Arrêt pour revue indépendante ; une nouvelle exécution de la suite complète avec un délai supérieur reste à autoriser.
