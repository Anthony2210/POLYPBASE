# Intégration locale de 59 traductions japonaises relues par IA

## État et baseline

**READY FOR INDEPENDENT REVIEW — aucune autorisation de publication.**

- Branche effective : `feat/i18n-ja-ai-reviewed-59`.
- HEAD de départ : `a841519a69f7981ef175797643cc7182147a9909`.
- Worktree propre à l'inspection initiale ; aucun changement préexistant à préserver.
- Aucune opération commit, push, merge, cherry-pick ou déploiement.
- Aucun accès production, Neon ou base partagée. Les seuls tests Django exécutés utilisent `config.test_settings` avec `POSTGRES_DB=''` et ne demandent pas de base.
- Aucun changement de FR/EN, du classeur historique, de `POLYPBASE_CURRENT_STATE.md`, de gettext Django, des permissions, calculs, cycles de création ou logique des sondes.

## Sources et provenance

Les deux CSV joints sont conservés octet pour octet dans ce dossier. Le rapport de passation reste externe ; il n'est pas une entrée libre du générateur.

| Entrée | SHA-256 |
|---|---|
| `POLYPBASE_JA_59_candidates_revisees.csv` | `b82de81f74ce4e7171c5a54e948ee3b9b809f3dceac3de08a95d1406f830b4b7` |
| `POLYPBASE_JA_6_blocked.csv` | `b118377aa744faf4d04b4aced2436b347c20594d66dea66d0edf55c7c7ad8cd6` |
| `POLYPBASE_JA_65_double_revue_pour_Zed.md` (externe) | `9c1b64595fdc33e6cc248f29be8560b400eb30d77de2e59b219619dfa01f73d3` |
| `Traduction Polypbase(4).xlsx` (externe, lecture seule) | `e80b409fbea485d97fbccf66d06da83eb46374883d1e3343f50e51e656b8c307` |

Les propositions initiales viennent de Gemini ; la revue indépendante d'un autre modèle a rendu 52 ACCEPT, 7 REVISE et 6 BLOCK. Il ne s'agit pas d'une approbation humaine. Le propriétaire produit a demandé leur intégration locale, pas leur publication ni leur attribution à Takeru.

Le plus petit prolongement du générateur existant a été retenu : classement historique inchangé depuis le classeur figé, puis couche IA React validée intégralement avant toute écriture. Pas de nouvelle dépendance de production, de second catalogue runtime ou de changement du repli anglais.

- `REUSED` / `ja_validated` : japonais historique humain, avec références au classeur. Les 930 valeurs React restent identiques.
- `AI_REVIEWED` / `ja_ai_reviewed` : texte exact de `ja_reviewed_candidate`, raison `AI_ACCEPT` ou `AI_REVISE`. `ja_validated` et les références au classeur restent vides ; `human_approved=NO` dans la preuve source.
- Le verrou SHA-256 autorise uniquement ce jeu de données exact. Ce n'est ni une signature du modèle, ni une garantie de qualité linguistique. Une modification exige une nouvelle revue explicite et un changement examiné du verrou.
- Une tentative de remplacement d'une entrée humaine ou différée échoue. Une clé bloquée, absente du FR/EN, un placeholder modifié, un garde violé ou une divergence des sources relues provoque un échec avant écriture.
- Une commande échouée ne retire pas les fichiers déjà présents : tout futur changement de source doit résoudre la revue et régénérer le catalogue avant livraison, et non ignorer l'échec.
- Les attributs Git préservent les octets des deux CSV pour rendre le verrou reproductible entre plateformes.

Les tests figent par empreinte l'intégralité des champs historiques du manifeste de départ (après retrait de la nouvelle colonne et restitution des 59 lignes initialement absentes), les 930 paires clé/valeur humaines et le contenu FR/EN normalisé seulement pour les fins de ligne Git. Les 36 lignes différées et toutes les surfaces non React sont ainsi protégées, pas simplement recomptées.

## Reproduction

Depuis la racine, avec le classeur externe portant l'empreinte ci-dessus :

```sh
python -I -X utf8 docs/translation-review/japanese/build_ja_catalogs.py "C:/Users/antoc/Downloads/Traduction Polypbase(4).xlsx" --check
python -B -X utf8 -m unittest discover -s docs/translation-review/japanese -p 'test_*.py'
```

Sans `--check`, le générateur n'écrit que les sorties différentes. Le contrôle complet a été exécuté avec le vrai classeur, avant puis après extension. Le test de cohérence sans classeur reconstruit les faits historiques depuis le manifeste et vérifie les sorties déterministes ; il ne remplace pas le contrôle du parseur et de l'empreinte du vrai classeur.

## Résultats de validation

Toutes les commandes sont locales. « 0 » désigne le code de sortie effectivement observé. Un timeout n'est jamais compté comme un succès.

| Commande / contrôle | Résultat |
|---|---|
| Générateur original avec vrai classeur, `--check`, avant modification | 0 |
| Génération étendue puis `--check` avec vrai classeur | 0 |
| `python -B -X utf8 -m unittest discover -s docs/translation-review/japanese -p 'test_*.py'` | 0 ; 18 tests |
| `python -B -X utf8 scripts/compile_django_messages.py --check` | 0 ; aucun MO modifié |
| `python -B -X utf8 docs/translation-review/application/check_register.py` | 0 |
| `python -B -X utf8 scripts/check_i18n.py` | 0 ; aucun blocage |
| Tests Python `test_compile_django_messages.py` | 0 ; 26 tests |
| Tests Python `test_check_i18n.py` | 0 ; 22 tests |
| Tests Python `docs/translation-review/application/test_*.py` | 0 ; 62 tests |
| `POSTGRES_DB='' uv run --frozen python -B manage.py test apps.accounts.test_i18n --settings=config.test_settings` depuis `backend/` | 0 ; 5 tests |
| `node --test scripts/test-i18n-japanese.mjs` depuis `frontend/` | 0 ; 21 tests |
| `node --test --test-concurrency=1 scripts/test-i18n-catalogs.mjs scripts/test-i18n-usage.mjs scripts/test-i18n-types.mjs` | 0 ; 243 tests, aucun échec/skip |
| Tests ciblés composants, commande ci-dessous | 0 ; 472 tests, aucun échec/skip |
| `npm run typecheck` | 0 ; première commande terminée du groupe `npm run typecheck && npm run build` |
| `npm run build` | Timeout du groupe à 180 000 ms, après contrôle CSS OK et lancement TypeScript ; commande globale non validée |
| `npx vite build` | 0 ; 2 256 modules, build terminé en 6,85 s |
| `npm run test:i18n` | Timeout à 120 000 ms ; couvert ensuite par les deux commandes ciblées ci-dessus (21 + 243 tests réussis) |
| `npm run test:all` | Timeout à 600 000 ms ; sortie jusqu'au test 1611, pas de bilan final ; ni total final ni code de sortie disponibles |
| `git diff --check` | 0 ; avertissements Git LF/CRLF uniquement |
| QA navigateur / lecteur d'écran | Non exécutée ; aucune preuve visuelle ou vocale revendiquée |

Commande ciblée composants, depuis `frontend/` :

```sh
node --test --test-concurrency=1 scripts/test-create-box-panel.mjs scripts/test-automatic-box-creation.mjs scripts/test-box-intent-lifecycle.mjs scripts/test-mutation-dialogs.mjs scripts/test-chart-readability.mjs scripts/test-historical-temperature.mjs scripts/test-probe-status.mjs scripts/test-probe-interactions.mjs scripts/test-probe-coverage-rendering.mjs scripts/test-audit-presentation.mjs scripts/test-box-movement-inline.mjs scripts/test-subculture.mjs
```

Les premiers essais ont exposé deux problèmes du nouveau harness, corrigés avant les succès ci-dessus : BOM UTF-8 du premier en-tête CSV et empreintes FR/EN devant ignorer les seuls CRLF de checkout. Les CSV originaux n'ont pas été réencodés.

Le script QA global n'a pas été lancé : il impose une suite Django/migrations beaucoup plus large, sans rapport avec ce changement React. Aucune migration, création de données ou opération de base n'a été lancée.

## Fichiers changés

- `.gitattributes` : conservation exacte des deux preuves CSV.
- `docs/context/frontend-ux.md`, `docs/context/terminology-i18n.md` : exception locale IA explicite, bornée et distincte de l'approbation humaine.
- `docs/translation-review/japanese/README.md` : contrat, provenance et compteurs.
- `docs/translation-review/japanese/AI_REVIEWED_59.md` : ce rapport et les inventaires dérivés.
- `docs/translation-review/japanese/POLYPBASE_JA_59_candidates_revisees.csv`, `POLYPBASE_JA_6_blocked.csv` : preuves sources immuables.
- `docs/translation-review/japanese/build_ja_catalogs.py` : validation de la couche IA, rendu combiné et écriture des seules sorties différentes.
- `docs/translation-review/japanese/ja-manifest.csv` : colonne dédiée et statut distinct sur exactement 59 lignes React. La colonne vide ajoutée explique les lignes de diff sur toutes les surfaces ; aucune valeur historique non concernée n'est changée.
- `docs/translation-review/japanese/test_ja_manifest.py`, `test_ai_review.py` : contrats de provenance, conservation et régressions négatives.
- `frontend/scripts/test-i18n-japanese.mjs` : égalité exacte, corrections, replis, placeholders, zéro, unités et empreintes.
- `frontend/src/i18n/ja.ts` : exactement 59 nouvelles paires et commentaire de provenance corrigé.
- `frontend/src/i18n/index.ts` : commentaire uniquement ; logique runtime inchangée.

## Réserves et approbations nécessaires

1. Revue indépendante du diff avant toute décision de publication. Une seconde inspection technique en lecture seule n'a trouvé aucun blocage de correction ou de périmètre ; elle ne remplace pas l'approbation produit finale ni une validation japonaise native.
2. QA japonaise sur desktop et tablette à prévoir : badge `probeSilentBadge`, libellé plus long `probeAggregateAverage`, retours à la ligne des info-bulles et annonces lecteur d'écran. Validation native souhaitable pour les formulations nouvelles, notamment `probeRawReading` et `probeAggregateCoverage`.
3. Les six messages BLOCK requièrent des décisions produit séparées : promesses de reprise automatique après essais bornés, preuve de non-création, promesse de saisie manuelle sans droits, distinction entre sondes liées et sondes configurées pour collecte. Aucun de ces textes FR/EN ni leurs conditions n'est corrigé ici.
4. `probeLastReading` traduit fidèlement la source mais le timestamp affiché est celui de mesure, pas une réception indépendante. `probeNoReadings` est adapté au graphique thermique actuel, pas à un futur affichage générique de salinité.
5. Les 36 différées restent soumises à leur processus historique ; aucun ajout IA ne les résout. Les fonctionnalités abandonnées ou désactivées, notamment l'import CSV de relevés, ne sont ni traduites par cette tâche ni réactivées.
6. Suite frontend globale et commande build globale expirées : résultats ciblés détaillés ci-dessus, aucune affirmation de succès global. Toute publication reste une décision ultérieure explicitement autorisée.

## Inventaires dérivés

Les sections suivantes sont calculées depuis le manifeste généré, les CSV relus et les catalogues réels, et non utilisées comme substitut aux contrôles d'appartenance exacte.

### Compteurs effectifs

| Catégorie React | Nombre |
|---|---:|
| FALLBACK_EN | 143 |
| REUSED | 930 |
| FALLBACK_EN_REVIEW | 36 |
| AI_REVIEWED | 59 |
| Total japonais | 989 |
| Total FR/EN | 1168 |
| Total replis anglais | 179 |

### Les traductions IA intégrées

| Clé | Décision | Japonais intégré |
|---|---|---|
| `createBoxRetry` | ACCEPT | 再試行 |
| `createBoxNoLocations` | ACCEPT | 容器を作成できる設置場所がありません。 |
| `createBoxSuccess` | ACCEPT | 容器を作成しました |
| `createBoxOpenCreated` | ACCEPT | 容器情報を開く |
| `createBoxStrainSearch` | ACCEPT | 種、系統、コードで検索 |
| `createBoxStrainNoMatch` | ACCEPT | 検索条件に一致する系統がありません。 |
| `createBoxStrainLabel` | ACCEPT | 系統 |
| `createBoxActiveBox` | REVISE | 個の有効な容器 |
| `createBoxActiveBoxes` | REVISE | 個の有効な容器 |
| `createBoxSelectZone` | ACCEPT | 設置場所を選択 |
| `createBoxCreating` | ACCEPT | 作成中… |
| `createBoxConnectionRetry` | ACCEPT | 今すぐ再試行 |
| `createBoxStorageUnavailable` | ACCEPT | 現在、このブラウザーでは容器を作成できません。ページを再読み込みして、もう一度お試しください。 |
| `createBoxAnother` | ACCEPT | 別の容器を作成 |
| `createBoxAlsoCreated` | ACCEPT | 別の容器も作成されました： |
| `createBoxPreviousUnverified` | ACCEPT | このタブでの以前の作成結果を確認できませんでした。新たな容器の作成は引き続きできません。管理者にお問い合わせください。 |
| `boxLifecycleQualifyTitle` | ACCEPT | 容器の状態を設定 |
| `boxLifecycleQualifySubmit` | REVISE | 状態の設定を確定 |
| `boxLifecycleHistoricalReasonMissingHelp` | ACCEPT | 過去の元データに理由が一度も記録されていなかった場合にのみ、この項目を選択してください。 |
| `mutationRefreshFailed` | ACCEPT | 保存は完了しましたが、関連データを更新できませんでした。再度保存せずに、データを再読み込みしてください。 |
| `movementTimeOutsideWeek` | ACCEPT | 記録された移動と同じ週（月曜～日曜、パリ時間）内の日時を選択してください。 |
| `dailyTemperatureAggregate` | ACCEPT | 水温 |
| `temperatureMultipleSources` | ACCEPT | 複数のソース： |
| `temperatureCombinedSources` | REVISE | 過去データのインポートおよびPolypbase |
| `temperatureHistoricalSource` | REVISE | 過去データのインポート： |
| `subcultureCodePreviewHint` | ACCEPT | このコードはプレビューであり、予約されていません。正式なコードは作成時に割り当てられます。 |
| `probeChartKeyboardDescription` | ACCEPT | 左右の矢印キー：測定値を順に確認。プラス・マイナスキー：拡大・縮小。Escキー：閉じる。ドラッグ：選択範囲を拡大。ダブルクリックまたはダブルタップ：表示期間を元に戻す。 |
| `probeSilentBadge` | REVISE | 測定値未受信 |
| `probeElapsedMinutes` | ACCEPT | {count}分 |
| `probeElapsedHours` | ACCEPT | {count}時間 |
| `probeElapsedDays` | ACCEPT | {count}日 |
| `probeReadingAgo` | ACCEPT | {duration}前 |
| `probeMonitoringError` | ACCEPT | センサーの状態を更新できませんでした。最後に確認できた状態を保持しています。 |
| `probeReadingsTitle` | ACCEPT | センサーの測定値 |
| `probeReadingsAction` | ACCEPT | センサーの測定値を見る |
| `probePeriod` | ACCEPT | 表示期間 |
| `probePeriod_24h` | ACCEPT | 24時間 |
| `probePeriod_7d` | ACCEPT | 7日間 |
| `probePeriod_30d` | ACCEPT | 30日間 |
| `probePeriod_90d` | ACCEPT | 90日間 |
| `probeWindowPrevious` | ACCEPT | 前の表示期間 |
| `probeWindowNext` | ACCEPT | 次の表示期間 |
| `probeResetZoom` | ACCEPT | ズームをリセット |
| `probeNoReadings` | ACCEPT | この期間の水温データはありません。 |
| `probeLatestInWindow` | ACCEPT | 最新 |
| `probeChartSummary` | ACCEPT | この期間の水温は{min}から{max}の範囲です。 |
| `probeInactive` | ACCEPT | 無効 |
| `probeRetry` | ACCEPT | 再試行 |
| `probeLastReading` | ACCEPT | 最後に受信した測定値 |
| `probeNoReadingYet` | ACCEPT | まだ測定値を受信していません |
| `probeManualFallbackAction` | ACCEPT | 水温を手入力 |
| `probeAggregateAverage` | REVISE | 集計対象の測定値の平均 |
| `probePartialCoverage` | ACCEPT | 集計区間の一部のみを表示しています。区間全体の統計には、選択範囲外の測定値が含まれる場合があります。 |
| `probeAggregateCoverage` | ACCEPT | 保存された時間区間と測定時点 |
| `probeMeasurementCount` | ACCEPT | 測定数 |
| `probeRawReading` | ACCEPT | 瞬時測定値 |
| `probeCoveragePreview` | ACCEPT | 部分的なプレビューです。統計は選択範囲全体を網羅していません。 |
| `auditDescriptionCcaEobV3Correction` | ACCEPT | CCA-EOBの測定記録72件を正しい容器に再割り当てしました。環境情報は割り当て先の容器に基づきます。設置場所と移動履歴は変更されていません。 |
| `auditMetaStopReason` | ACCEPT | 無効化の理由 |

### Six clés BLOCK, anglais conservé (6)

- `createBoxConnectionPending`
- `createBoxRecoveryUnavailable`
- `createBoxNotCreated`
- `probeSilentDescription`
- `probeSilentNoReadingDescription`
- `probeNoCollectedProbe`

### Différées historiques, anglais conservé (36)

- `auditFamilyMeasurements`
- `auditActionSubculture`
- `auditActionTransfer`
- `auditObjectOrganization`
- `auditMetaMeasurementId`
- `auditMetaMembershipId`
- `auditValuePendingReview`
- `adminSuperuserNavigation`
- `adminPaginationLabel`
- `boxInventoryLastKnownLocation`
- `boxLifecycleLastKnownLocation`
- `boxActivateForbidden`
- `createBoxNotes`
- `adminTransferText`
- `manageDemoteToViewer`
- `manageErrorMembershipAdminRequired`
- `manageErrorResponsableRequired`
- `manageErrorActiveResponsableRequired`
- `organizationChoiceIntro`
- `organizationMenuTitle`
- `measurementCountsInvalid`
- `movementKindUnclassified`
- `movementErrorProvenance`
- `movementErrorForbidden`
- `temperatureOutsideScale`
- `historicalTemperaturePeriod`
- `overviewEmpty`
- `subcultureNoZones`
- `zoneSalinityLatestTitle`
- `zoneSalinityNoReading`
- `zoneSalinityCreateAction`
- `zoneSalinityEditAction`
- `zoneSalinityCreateTitle`
- `zoneSalinityEditTitle`
- `zoneSalinityEditExpired`
- `zoneMovementExits`

### Autres clés absentes, anglais conservé (137)

- `csvOpen`
- `csvClose`
- `csvTitle`
- `csvPreviewHelp`
- `csvSourceHelp`
- `csvImportConfirmation`
- `csvRetentionHelp`
- `csvConfirmImport`
- `csvApply`
- `csvApplying`
- `csvApplyUnavailable`
- `csvReceiptTitle`
- `csvAccepted`
- `csvReplayed`
- `csvReceiptId`
- `csvAcceptedAt`
- `csvCreated`
- `csvSourceHash`
- `csvReceiptHelp`
- `csvDownloadReceipt`
- `csvDownloadSource`
- `csvDownloading`
- `csvErrorDownload`
- `csvErrorStale`
- `csvErrorApply`
- `csvFile`
- `csvDelimiter`
- `csvComma`
- `csvSemicolon`
- `csvTab`
- `csvInspect`
- `csvInspecting`
- `csvPreviewing`
- `csvMapping`
- `csvMappingHelp`
- `csvUnselected`
- `csvIgnore`
- `csvConfirmMapping`
- `csvPreview`
- `csvPreviewTitle`
- `csvDiagnosticReady`
- `csvDiagnosticBlocked`
- `csvSourceIncomplete`
- `csvSummaryTotal`
- `csvSummaryIgnored`
- `csvSummaryCreate`
- `csvSummaryMatches`
- `csvSummaryConflicts`
- `csvSummaryUnknown`
- `csvSummaryInvalid`
- `csvSummaryBlocked`
- `csvValuesHelp`
- `csvNoRows`
- `csvPagination`
- `csvPrevious`
- `csvNext`
- `csvPage`
- `csvRecord`
- `csvLines`
- `csvColumn`
- `csvPrecision`
- `csvWeekOnly`
- `csvExactDate`
- `csvAbsent`
- `csvNull`
- `csvBlank`
- `csvTrue`
- `csvFalse`
- `csvFieldBox`
- `csvFieldDate`
- `csvFieldYear`
- `csvFieldWeek`
- `csvFieldPolyps`
- `csvFieldEphyrae`
- `csvFieldStrobila`
- `csvFieldSalinity`
- `csvFieldStatus`
- `csvFieldAttention`
- `csvFieldNotes`
- `csvFieldObserver`
- `csvOutcomeIgnored`
- `csvOutcomeInvalid`
- `csvOutcomeUnknown`
- `csvOutcomeCreate`
- `csvOutcomeMatches`
- `csvOutcomeConflict`
- `csvOutcomeBlocked`
- `csvOutcomeUnknownCode`
- `csvErrorSize`
- `csvErrorPermission`
- `csvErrorMapping`
- `csvErrorConfiguration`
- `csvErrorRequest`
- `csvIssueUtf8`
- `csvIssueNul`
- `csvIssueHeaders`
- `csvIssueHeaderMissing`
- `csvIssueMalformed`
- `csvIssueColumns`
- `csvIssueFieldLimit`
- `csvIssueRows`
- `csvIssueMappingIncomplete`
- `csvIssueMappingDuplicate`
- `csvIssueMappingRequired`
- `csvIssueMappingTime`
- `csvIssueWidth`
- `csvIssueUnknownField`
- `csvIssueSourceString`
- `csvIssueRequired`
- `csvIssueFormula`
- `csvIssueInteger`
- `csvIssueCountRange`
- `csvIssueDecimal`
- `csvIssueDecimalRange`
- `csvIssueChoice`
- `csvIssueBoolean`
- `csvIssueDate`
- `csvIssueWeek`
- `csvIssueDateWeek`
- `csvIssueTime`
- `csvIssueVersion`
- `csvIssueIncomplete`
- `csvIssueDateConflict`
- `csvIssueValueConflict`
- `csvIssueUnknownBox`
- `csvIssueDuplicate`
- `csvIssueContradictory`
- `csvIssueInactive`
- `csvIssueWeekPersistence`
- `csvIssueChronology`
- `csvIssueObserver`
- `csvIssueUnknown`
- `adminWorkInProgress`
- `boxInventoryActiveWithoutLocation`
- `boxInventorySuspectedInactive`
- `boxInventorySummary`
- `taxonomySpeciesCodeHelp`
