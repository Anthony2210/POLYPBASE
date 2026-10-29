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

Le registre est un journal d'événements. Les lignes d'un lot déjà enregistré (dont les 34 lignes de B01 et leurs références de décision) ne sont jamais modifiées, réordonnées ni supprimées. Une erreur se corrige par un nouvel événement dans un lot suivant. Le test `test_h_b01_register_is_unchanged_and_valid` fige une empreinte canonique des 34 lignes B01 (valeurs des champs, indépendante des fins de ligne et des lots ajoutés ensuite) et vérifie qu'elles restent en tête du fichier. Ajouter un lot ne demande donc pas de toucher ce test ; modifier B01 le fait échouer.

Règles vérifiées par `check_register.py` :

- **Identité** d'un événement : `surface` + `key_or_msgid` + `language` + `source_file`. Une même clé sur deux surfaces ou dans deux langues forme deux historiques indépendants.
- **Un seul événement par identité et par lot**, même identique : un doublon ou une contradiction dans un lot est rejeté et ne peut pas masquer l'événement précédent.
- **Chronologie explicite** : `batch` suit `Bnn-AAAA-MM-JJ`. Chaque lot forme un bloc contigu ; les blocs se suivent par numéro strictement croissant et date non décroissante ; un numéro ne désigne qu'un seul lot ; `entry_id` (`Tnnn`) croît strictement dans l'ordre du fichier ; un lot déclare un seul `baseline_git_head`. Si l'ordre est invalide ou ambigu, la continuité n'est pas évaluée et le registre est rejeté.
- **Continuité** : pour deux événements successifs d'une même identité, `initial_value` du nouveau égale `final_value` du précédent. `APPLIED` exige un changement de valeur ; `ALREADY_MATCHING`, `PRESERVED` et `DEFERRED` reportent la valeur sans la changer ; `NOT_FOUND` a des valeurs vides, et l'événement suivant doit partir de l'absence.
- **État courant** : seul le dernier événement de chaque identité est comparé au code. Un ancien `final_value` peut différer du code actuel si un événement ultérieur l'explique.
- Statuts, surfaces, langues FR/EN uniquement, empreintes, variables `{x}` conservées, parité des clés et variables FR/EN des catalogues.
