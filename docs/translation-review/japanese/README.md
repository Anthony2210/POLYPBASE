# Interface japonaise : provenance humaine et revue IA

Ce dossier distingue le japonais historique validé par Takeru des ajouts React relus indépendamment par IA, et trace pourquoi les autres clés affichent l'anglais. Une revue IA n'est jamais une validation humaine ou une approbation de Takeru. Il ne concerne ni les contenus scientifiques japonais (`CONTENT_LANGUAGES`), ni le registre FR/EN (`../application/register.csv`, limité au FR/EN par son contrat).

## Référence

- Baseline Git : `d3b3e2b1b06843a93fad1f2983021ceec01a9f52` (`i18n: apply approved French and English translations`).
- Classeur figé, lu sans modification : `Traduction Polypbase(4).xlsx`, SHA-256 `e80b409fbea485d97fbccf66d06da83eb46374883d1e3343f50e51e656b8c307`, feuille « Traductions i18n », lignes identifiées par clé exacte (colonne `Keys`), jamais par numéro de ligne. 1 384 clés, toutes avec un japonais « Fait » ; aucune clé en double, aucune formule.
- Catalogues actuels : 1 233 clés FR et 1 233 clés EN identiques ; 98 msgids Django actifs (inventaire de `scripts/check_i18n.py`).

## Fichiers

| Fichier | Rôle |
|---|---|
| `build_ja_catalogs.py` | Recalcule depuis le classeur figé et les deux CSV IA verrouillés par SHA-256 ; `--check` n'écrit rien et échoue si une sortie diffère. Contient chaque décision sémantique, avec sa raison. Seules les sorties différentes sont écrites. |
| `historical-movement-keys.json` | Liste explicite des 44 clés FR/EN ajoutées pour les mouvements historiques, sans japonais ; délimite l'extension additive dans les tests des empreintes historiques. |
| `ja-manifest.csv` | Une ligne par identité actuelle (React, gettext Django, tables locales, ternaires inline) : statut, raison, valeurs FR/EN actuelles et du classeur, `ja_validated` historique et `ja_ai_reviewed` séparé. |
| `ja-takeru-review.csv` | Les 43 identités différées à soumettre à Takeru, dont 8 questions ouvertes. Le japonais original est conservé, mais jamais affiché avant validation. Aucune proposition japonaise. |
| `ja-obsolete-workbook-keys.csv` | Les 249 clés du classeur absentes du code actuel (217 React, dont les suppressions B03-B06, et 32 msgids Django). Ignorées, jamais restaurées. |
| `test_ja_manifest.py`, `test_ai_review.py` | Contrôlent les appartenances exactes, les provenances, les invariants historiques, les refus de sources périmées et la génération reproductible. |
| `POLYPBASE_JA_59_candidates_revisees.csv` | Source de 52 `ACCEPT` et 7 `REVISE`, `human_approved=NO`. Seule la colonne `ja_reviewed_candidate` est intégrée. Copie octet pour octet de l'entrée relue. |
| `POLYPBASE_JA_6_blocked.csv` | Six `BLOCK` exclus ; aucune formulation japonaise finale autorisée. Copie octet pour octet. |
| `AI_REVIEWED_59.md` | Baseline, empreintes, inventaires complets des ajouts et replis, validations et limites de cette intégration locale. |
| `../../../frontend/src/i18n/ja.ts`, `../../../backend/locale/ja/LC_MESSAGES/django.po` | Catalogues générés. Ne pas les modifier à la main. |

```sh
python -I -X utf8 docs/translation-review/japanese/build_ja_catalogs.py "<chemin du classeur>" --check
python -m unittest discover -s docs/translation-review/japanese -p "test_*.py"
```

## Règle de classement

Une identité actuelle réutilise le japonais seulement si le japonais est « Fait », si le FR et l'EN actuels sont identiques au classeur (espaces de bord retirés, NFC) et si aucun garde ne se déclenche. Tout écart FR/EN ou tout garde exige une décision explicite dans `SEMANTIC_DECISIONS`, sinon le script échoue. Gardes : G18 repiquage = 移植, G36 Read-only mode = 閲覧専用, G33 Manager = 責任者, G28 statut À vérifier/Pending = 確認待ち, G32 Institution de travail = 所属機関, refus de permission avec invitation à contacter un administrateur, placeholders identiques, pas de point médian.

Statuts :

- `REUSED` : japonais validé affiché. `reason_code` vaut `EXACT_MATCH`, ou décrit un écart sans changement de sens (`EN_TYPO_ONLY`, `PUNCTUATION_ONLY`, `EN_FORM_ONLY`, `JA_MATCHES_CURRENT`, `GLOSSARY_MATCH`…), sans question terminologique ouverte.
- `AI_REVIEWED` : ajout React explicitement autorisé par la revue IA indépendante (`AI_ACCEPT` ou `AI_REVISE`). Texte uniquement dans `ja_ai_reviewed` ; `ja_validated` et les références au classeur restent vides. Ce statut n'est jamais `REUSED` et ne signifie aucune approbation de Takeru.
- `FALLBACK_EN` : clé absente du classeur et sans ajout IA autorisé ; l'anglais est affiché.
- `FALLBACK_EN_REVIEW` : japonais validé mais incompatible ou incertain avec le FR/EN actuel ; l'anglais est affiché jusqu'à décision de Takeru.
- `FALLBACK_EN_INLINE` : chaîne inline `language === 'fr' ? … : …` ; non branchée sur le japonais (refactorisation séparée), l'anglais est affiché.

## Ajouts IA indépendamment relus (2026-11-04)

L'autorisation produit de cette tâche permet seulement les 59 valeurs relues du CSV fourni, pas une nouvelle approbation humaine. Le générateur classe d'abord le classeur selon les gardes historiques inchangés, puis valide toute la couche IA avant de l'appliquer aux seules clés React `ABSENT_FROM_WORKBOOK`. Toute tentative de remplacer un japonais historique ou différé échoue. Les six clés bloquées restent en anglais.

Les empreintes exactes des deux CSV sont verrouillées dans `AI_INPUT_SHA256` ; les attributs Git préservent leurs octets. Modifier ou ajouter une entrée ne suffit pas à l'autoriser : une nouvelle revue explicite et une mise à jour examinée du verrou sont nécessaires. Ce verrou est un contrôle d'intégrité, pas une signature du relecteur. Le rapport de passation reste externe ; son empreinte et ses réserves sont tracées dans `AI_REVIEWED_59.md`.

Le FR/EN est comparé exactement au texte relu, sans normalisation silencieuse. Toute divergence arrête la génération entière avant écriture : aucun ajout périmé n'est généré. Le catalogue déjà présent n'est pas supprimé par une commande échouée ; une modification future du FR/EN exige donc de résoudre la revue avant de livrer le catalogue. Les gardes glossaire, invitation administrative, placeholders et sémantique scientifique s'appliquent aussi aux ajouts. Django, les tables locales, les questions Takeru et le classeur ne reçoivent aucun ajout IA.

## Résultat

| Surface | Humain réutilisé | IA relue | Absentes sans JA | Différées (revue) | Inline non branchées | Total |
|---|---:|---:|---:|---:|---:|---:|
| Catalogues React | 930 | 59 | 208 | 36 | – | 1 233 |
| gettext Django (msgids actifs) | 81 | – | 11 | 6 | – | 98 |
| Tables locales (`ErrorBoundary`, `InteractiveLineageGraph`, `boxStatus`, `ExportsView`) | 67 | – | – | 1 | – | 68 |
| Ternaires inline | – | – | – | – | 14 | 14 |

React : 989 clés japonaises affichées. Les 244 replis anglais se décomposent en 208 absences (dont les six `BLOCK`) et les 36 différées historiques inchangées. À l'intégration IA initiale, les 202 absences se décomposaient en 59 ajouts IA et 143 absences restantes. Les ajouts ultérieurs comptent 7 clés thermiques, 14 clés de configuration du laboratoire et 44 clés de mouvements historiques, toutes en repli anglais.

React réutilisées : 903 correspondances exactes et 27 écarts compatibles. Les 8 questions ouvertes (`auditActionTransfer`, `auditObjectOrganization`, `auditMetaMembershipId`, `boxInventoryLastKnownLocation`, `boxLifecycleLastKnownLocation`, `adminTransferText`, `movementErrorProvenance`, `zoneMovementExits`) affichent l'anglais actuel jusqu'à validation de Takeru. Django : 66 exactes, 15 écarts compatibles.

### Les 133 identités FR/EN synchronisées sur le classeur

Entre `4866520` et `d3b3e2b`, 153 identités FR/EN ont changé : 133 valent maintenant exactement le texte du classeur, 20 sont des adaptations contextuelles. Le rapport `fr-en-sync-recovery.md` comptait 132 correspondances : l'écart est `adminTransferTarget` FR, rétabli à « Structure destinataire » par B11/B12 après ce rapport. Pour ces 133 identités, le FR/EN actuel est celui que Takeru a traduit : leur japonais est réutilisé. Les 20 adaptations sont classées une par une dans le manifeste (9 réutilisées, 11 différées).

Avant la synchronisation, 158 clés React avaient un EN différent du classeur. Il en reste 50 : 24 réutilisées pour un écart sans changement de sens, 26 différées.

## Maintenance additive des mouvements historiques (2026-11-04)

Les 44 nouvelles clés sont identifiées par comparaison de l'AST FR avec le manifeste existant et vérifiées contre le diff FR/EN. Elles sont insérées dans l'ordre canonique FR avec les valeurs FR/EN actuelles exactes, `FALLBACK_EN` / `ABSENT_FROM_WORKBOOK`, sans valeur japonaise, référence au classeur ou nouvelle revue. `historical-movement-keys.json` fixe explicitement ce lot ; ce n'est pas une liste dynamique de toutes les clés inconnues. Toutes les lignes préexistantes du manifeste restent inchangées.

Le classeur figé étant absent du dépôt, aucune lecture externe privée ni régénération réelle depuis le classeur (y compris `--check`) n'a été exécutée pour cette maintenance. Le classement indique ici un ajout absent du manifeste de référence, conformément à la règle de repli ; il ne prétend pas à une nouvelle vérification du classeur. Le test de génération utilise uniquement les faits historiques déjà présents dans les CSV et un lecteur de classeur simulé ; il ne remplace pas le contrôle réel du classeur et de son SHA-256.

Pour l'intégration avec le `main` courant, les tests retirent uniquement les 44 clés de ce lot et les 21 clés thermiques/configuration du laboratoire explicitement énumérées dans `LATER_FALLBACK_KEYS` avant de vérifier les empreintes FR/EN originales et celle du manifeste historique ; aucune empreinte historique n'est remplacée. Les 930 valeurs humaines, les 59 ajouts IA, les 36 clés React différées et les empreintes octet pour octet des CSV de revue restent verrouillés. Les catalogues japonais React et Django ainsi que les CSV de revue ne sont pas modifiés. Toute évolution future hors de ce lot exige une maintenance explicitement examinée, pas une remise à zéro des snapshots.

### Registre FR/EN de l'intégration courante

Le `main` `0ff64ba` conserve ses entrées `T001–T886` octet pour octet, notamment les 14 entrées thermiques `T845–T858` et les 28 entrées Administration `T859–T886`, avec leurs statuts et réserves. Les 88 entrées historiques de `04012fa` sont ajoutées en `T887–T974` : `B13/T845–T878` devient `B14/T887–T920`, `B14/T879–T912` devient `B15/T921–T954`, et `B15/T913–T932` devient `B16/T955–T974` (date inchangée : 2026-11-04). Seuls les identifiants et lots changent ; valeurs, décisions, raisons et références de provenance sont conservées. La renumérotation `T859–T946` de l'ancien worktree ne convient plus, car elle entrerait en collision avec Administration.

## Repli et exceptions

- Frontend : `translations.ja` = anglais complété par `ja.ts`. FR et EN sont inchangés. Les dates utilisent `ja-JP`.
- Django : `scripts/compile_django_messages.py` construit le MO japonais effectif à la compilation : japonais validé du PO, sinon anglais approuvé du PO EN, sinon msgid source. Tous les msgids applicatifs actifs sont inclus pour éviter le repli français par défaut de Django. Le PO JA reste un catalogue de provenance japonaise uniquement ; le MO effectif ne constitue pas une validation de Takeru. L'activation Django est standard, sans mutation de catalogue partagé ni de `_fallback`. FR/EN conservent leur compilation et leurs octets. Exception : un msgid source français sans traduction anglaise approuvée (`Provenance non établie`, `Emplacement à la qualification`, anciens libellés de formulaire) reste affiché tel quel, en français, comme aujourd'hui en anglais. Aucune traduction n'est inventée.
- Les messages propres à Django et DRF (validation générique) proviennent de leurs catalogues japonais amont, comme leurs équivalents français aujourd'hui. Takeru ne les a pas validés.

## Réexamen de 10 candidats (2026-11-03)

Dix identités différées ont été relues contre le code, le registre et les décisions de terminologie. Aucun japonais n'a été écrit ni modifié ; les valeurs « Fait » du classeur sont reprises telles quelles.

Réutilisées (3) :

- `boxInventoryQualificationAid` (状態設定のヒント) : FR inchangé ; 状態設定 est déjà le japonais réutilisé pour « qualifier » (`auditSummaryBoxQualified`, `movementKindQualification`).
- `createBoxTitle` (容器を作成) : titre de la fenêtre de création de boîte ; même action que `createBoxOpen`, déjà réutilisé.
- `createBoxSubmit` (作成) : bouton de validation de la même fenêtre ; même action.

Restent différées, anglais affiché (7) :

- `auditValuePendingReview` et `movementKindUnclassified` : exceptions EN contestées (registre T022/T019/T763/T764) ; la première est partagée avec le statut de boîte (G28 = 確認待ち).
- `organizationChoiceIntro` : FR réécrit (AC18), question Q067.
- `movementErrorForbidden` : le japonais invite à contacter l'administrateur, pas le FR/EN actuel (401/403 partagés).
- `historicalTemperaturePeriod` : 対象期間 traduit « Période possible », libellé retiré ; le sens actuel est « Mesuré pour la période ».
- `subcultureNoZones` : le japonais n'exprime pas « actif » ni « zone thermique ».
- `local:...InteractiveLineageGraph.tsx#labels.truncated` : FR « lignée » devenu « famille » ; 系譜 est la lignée.

`createBoxNotes` reste différée (« facultatif » perdu).
