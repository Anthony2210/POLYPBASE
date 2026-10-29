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
