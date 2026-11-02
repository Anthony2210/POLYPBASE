# Interface japonaise : réutilisation du japonais validé

Ce dossier trace quelles traductions japonaises validées par Takeru sont affichées dans l'interface, et pourquoi les autres clés affichent l'anglais. Il ne concerne ni les contenus scientifiques japonais (`CONTENT_LANGUAGES`), ni le registre FR/EN (`../application/register.csv`, limité au FR/EN par son contrat).

## Référence

- Baseline Git : `d3b3e2b1b06843a93fad1f2983021ceec01a9f52` (`i18n: apply approved French and English translations`).
- Classeur figé, lu sans modification : `Traduction Polypbase(4).xlsx`, SHA-256 `e80b409fbea485d97fbccf66d06da83eb46374883d1e3343f50e51e656b8c307`, feuille « Traductions i18n », lignes identifiées par clé exacte (colonne `Keys`), jamais par numéro de ligne. 1 384 clés, toutes avec un japonais « Fait » ; aucune clé en double, aucune formule.
- Catalogues actuels : 1 168 clés FR et 1 168 clés EN identiques ; 98 msgids Django actifs (inventaire de `scripts/check_i18n.py`).

## Fichiers

| Fichier | Rôle |
|---|---|
| `build_ja_catalogs.py` | Recalcule tout depuis le classeur ; `--check` n'écrit rien et échoue si une sortie diffère. Contient chaque décision sémantique, avec sa raison. |
| `ja-manifest.csv` | Une ligne par identité actuelle (React, gettext Django, tables locales, ternaires inline) : statut, raison, valeurs FR/EN actuelles et du classeur, japonais validé. |
| `ja-takeru-review.csv` | Les 46 identités différées à soumettre à Takeru, dont 8 questions ouvertes. Le japonais original est conservé, mais jamais affiché avant validation. Aucune proposition japonaise. |
| `ja-obsolete-workbook-keys.csv` | Les 249 clés du classeur absentes du code actuel (217 React, dont les suppressions B03-B06, et 32 msgids Django). Ignorées, jamais restaurées. |
| `test_ja_manifest.py` | Contrôle, sans le classeur, que les catalogues générés sont exactement les lignes `REUSED` du manifeste. |
| `../../../frontend/src/i18n/ja.ts`, `../../../backend/locale/ja/LC_MESSAGES/django.po` | Catalogues générés. Ne pas les modifier à la main. |

```sh
python -I -X utf8 docs/translation-review/japanese/build_ja_catalogs.py "<chemin du classeur>" --check
python -m unittest discover -s docs/translation-review/japanese -p "test_*.py"
```

## Règle de classement

Une identité actuelle réutilise le japonais seulement si le japonais est « Fait », si le FR et l'EN actuels sont identiques au classeur (espaces de bord retirés, NFC) et si aucun garde ne se déclenche. Tout écart FR/EN ou tout garde exige une décision explicite dans `SEMANTIC_DECISIONS`, sinon le script échoue. Gardes : G18 repiquage = 移植, G36 Read-only mode = 閲覧専用, G33 Manager = 責任者, G28 statut À vérifier/Pending = 確認待ち, G32 Institution de travail = 所属機関, refus de permission avec invitation à contacter un administrateur, placeholders identiques, pas de point médian.

Statuts :

- `REUSED` : japonais validé affiché. `reason_code` vaut `EXACT_MATCH`, ou décrit un écart sans changement de sens (`EN_TYPO_ONLY`, `PUNCTUATION_ONLY`, `EN_FORM_ONLY`, `JA_MATCHES_CURRENT`, `GLOSSARY_MATCH`…), sans question terminologique ouverte.
- `FALLBACK_EN` : clé absente du classeur ; l'anglais est affiché.
- `FALLBACK_EN_REVIEW` : japonais validé mais incompatible ou incertain avec le FR/EN actuel ; l'anglais est affiché jusqu'à décision de Takeru.
- `FALLBACK_EN_INLINE` : chaîne inline `language === 'fr' ? … : …` ; non branchée sur le japonais (refactorisation séparée), l'anglais est affiché.

## Résultat

| Surface | Réutilisées | Absentes du classeur | Différées (revue) | Inline non branchées | Total |
|---|---:|---:|---:|---:|---:|
| Catalogues React | 927 | 202 | 39 | – | 1 168 |
| gettext Django (msgids actifs) | 81 | 11 | 6 | – | 98 |
| Tables locales (`ErrorBoundary`, `InteractiveLineageGraph`, `boxStatus`, `ExportsView`) | 67 | – | 1 | – | 68 |
| Ternaires inline | – | – | – | 14 | 14 |

React réutilisées : 903 correspondances exactes et 24 écarts compatibles. Les 8 questions ouvertes (`auditActionTransfer`, `auditObjectOrganization`, `auditMetaMembershipId`, `boxInventoryLastKnownLocation`, `boxLifecycleLastKnownLocation`, `adminTransferText`, `movementErrorProvenance`, `zoneMovementExits`) affichent l'anglais actuel jusqu'à validation de Takeru. Django : 66 exactes, 15 écarts compatibles.

### Les 133 identités FR/EN synchronisées sur le classeur

Entre `4866520` et `d3b3e2b`, 153 identités FR/EN ont changé : 133 valent maintenant exactement le texte du classeur, 20 sont des adaptations contextuelles. Le rapport `fr-en-sync-recovery.md` comptait 132 correspondances : l'écart est `adminTransferTarget` FR, rétabli à « Structure destinataire » par B11/B12 après ce rapport. Pour ces 133 identités, le FR/EN actuel est celui que Takeru a traduit : leur japonais est réutilisé. Les 20 adaptations sont classées une par une dans le manifeste (9 réutilisées, 11 différées).

Avant la synchronisation, 158 clés React avaient un EN différent du classeur. Il en reste 50 : 21 réutilisées pour un écart sans changement de sens, 29 différées.

## Repli et exceptions

- Frontend : `translations.ja` = anglais complété par `ja.ts`. FR et EN sont inchangés. Les dates utilisent `ja-JP`.
- Django : `scripts/compile_django_messages.py` construit le MO japonais effectif à la compilation : japonais validé du PO, sinon anglais approuvé du PO EN, sinon msgid source. Tous les msgids applicatifs actifs sont inclus pour éviter le repli français par défaut de Django. Le PO JA reste un catalogue de provenance japonaise uniquement ; le MO effectif ne constitue pas une validation de Takeru. L'activation Django est standard, sans mutation de catalogue partagé ni de `_fallback`. FR/EN conservent leur compilation et leurs octets. Exception : un msgid source français sans traduction anglaise approuvée (`Provenance non établie`, `Emplacement à la qualification`, anciens libellés de formulaire) reste affiché tel quel, en français, comme aujourd'hui en anglais. Aucune traduction n'est inventée.
- Les messages propres à Django et DRF (validation générique) proviennent de leurs catalogues japonais amont, comme leurs équivalents français aujourd'hui. Takeru ne les a pas validés.
