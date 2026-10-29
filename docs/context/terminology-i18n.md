# Terminologie et traductions FR / EN / JA

À lire pour tout travail sur un texte visible, l'i18n, une traduction ou un terme métier. Les mécanismes i18n (catalogues `frontend/src/i18n/`, parité FR/EN, `CONTENT_LANGUAGES`) sont dans [`frontend-ux.md`](frontend-ux.md).

Sources : décisions produit validées par Anthony et traductions historiques du classeur « Traduction Polypbase », archivé hors dépôt. Les règles terminologiques ci-dessous font référence pour les nouveaux développements. Les modifications réellement appliquées à l'application sont tracées dans docs/translation-review/application/register.csv. Les anciennes propositions de nettoyage ne constituent pas des décisions automatiquement applicables.

## A. Vocabulaire produit confirmé

| Concept | FR | EN | Contexte exact |
|---|---|---|---|
| Institution | Structure | Institution | Entité institutionnelle (`Organization`) uniquement, pas tout emploi du mot « structure ». Les chaînes FR existantes qui disent « institution » ne sont pas réécrites automatiquement. |
| Repiquage | Repiquage, repiquer | Transplant (to transplant, transplanted, a transplant) | Opération biologique qui répartit des polypes d'une boîte parent vers des boîtes enfants (`SubcultureEvent`). Accorder la forme au contexte. |
| Relevé | Relevé | Record | Relevé biologique d'une boîte : comptages de polypes, éphyrules et strobiles. Au plus un par boîte et semaine ISO. |
| Mesure | Mesure | Measurement | Température et salinité : mesure manuelle ou valeur horodatée d'une sonde. Jamais un relevé biologique. |
| Accès | Accès | Access | Droit d'accéder à une structure. |
| Appartenance | Appartenance | Membership | Rattachement d'un compte à une structure avec un rôle (`OrganizationMembership`). |
| Rôle lecteur | Lecteurs | Read-only mode | Vrai rôle utilisateur (`viewer`, consultation seule). Libellé EN accepté tel quel. |
| Responsable | Responsable | Manager (libellé validé G33) | Privilège supplémentaire d'un Admin (`role=admin`, `is_responsable=True`), pas un quatrième rôle technique. Y renoncer conserve le rôle Admin. |
| Statut de boîte `pending_review` | À vérifier | Pending | Libellé de ce statut uniquement (`Box.Status.PENDING_REVIEW`), là où le contexte ne désigne que lui. |

Règles :

- **Refus de permission (règle produit permanente).** Tout message visible de refus de permission invite l'utilisateur à contacter un administrateur, dans chaque langue d'interface : « Merci de contacter un administrateur. » / « Please contact your administrator. ». Ne pas ajouter l'invitation si elle est déjà présente, ne pas détailler les permissions manquantes, et ne pas l'ajouter à une erreur qui n'est pas un refus d'autorisation (validation, conflit, règle métier, erreur technique). Cette règle de texte ne change pas la sémantique d'autorisation du backend.
- **Record ≠ Measurement.** Comptages biologiques : Record. Température et salinité : Measurement. Ne jamais remplacer un mot globalement sans vérifier ce que la clé affiche réellement. Un même mot FR peut relever des deux contextes. Par exemple, la famille d'audit « Mesures » est mixte et reste à nommer.
- **Transplant et identifiants techniques.** `subculture`, `SubcultureEvent`, `subcultureAction`… restent des identifiants de code et ne sont pas renommés. Le mot anglais « Subculture » n'apparaît jamais dans un texte visible pour cette opération.
- **Access ≠ Membership.** Désactiver une appartenance retire l'accès à cette structure seulement.
- **Rôles produit** : Admin (Administrateurs), Technicien (Techniciens), Lecteur (Lecteurs), plus le privilège Responsable. Le superutilisateur Django n'est pas un rôle produit.
- **Absence et zéro** : `0` est une valeur mesurée et garde son chiffre (« 0 polyps », ポリプ0). « Inconnu / Unknown », « Non renseigné » et « Aucun relevé / No records » désignent une absence. Ce n'est jamais `0`.

Exemples :

| Correct | Incorrect | Pourquoi |
|---|---|---|
| Latest record (relevé biologique) | Latest measurement | Comptages : Record |
| New salinity measurement | New salinity record | Salinité : Measurement |
| Save this transplant / Only an active box can be transplanted. | Save this transplanting / Subculture saved | Forme accordée, pas « Subculture » |
| Transfer between institutions | Transfer between organizations | Entité : Institution |
| Manual temperature for {zone} recorded | Transplant created from {code} | Sens et variable de la clé FR |
| Demote to Read-only mode | Promote to Read-only mode | Rétrogradation, libellé de rôle accepté |
| Ce compte ne peut pas créer de repiquage. Merci de contacter un administrateur. | Ce compte ne peut pas créer de repiquage. | Refus de permission sans invitation |

## B. Traductions validées à préserver

- **Japonais** : les 1 384 traductions JA marquées « Fait » ont été validées personnellement par Takeru. Ne jamais les écraser, normaliser, retraduire ni « aligner » automatiquement, même pour appliquer un terme ou une règle de la section A. Toute adaptation japonaise qui demande une nouvelle formulation reste non résolue tant qu'elle n'est pas explicitement approuvée, et n'est jamais appliquée automatiquement. Le japonais n'est pas une langue d'interface : il existe dans le classeur partagé et comme langue de contenu (`CONTENT_LANGUAGES`).
- **Glossaire G36** : Lecteurs / Read-only mode / 閲覧専用 reste tel quel.
- **Statuts** : ne jamais modifier une validation « Fait » ni une cellule Correction. Une Correction est un commentaire de relecteur, pas une valeur approuvée.
- **Valeurs japonaises de référence**, déjà validées : 容器 (boîte), 測定記録 (relevé biologique), 測定値 (mesure), 移植 (repiquage), 機関 (structure en tant qu'entité), 系統 (souche), 系譜 (lignée), 設置場所 (emplacement), 現在の設置場所 (emplacement actuel), 設置期間 (période d'emplacement). Les citer ne les rouvre pas.

## C. Exceptions contextuelles par clé

| Clé | Langue | Valeur à conserver | Ne pas faire |
|---|---|---|---|
| `overviewEmpty` | EN | No living box to display. | remplacer Living par Active |
| `movementKindUnclassified` | EN | Origin unknown (feuille) | remplacer par Provenance not established |
| `adminZoneTypeCabinet` | FR / EN | Emplacement / Rack | harmoniser avec Armoire, Location ou Cabinet |
| `historicalTemperaturePeriod` | FR / EN | Mesuré pour la période : / Measured for the period: | utiliser « Période disponible » / « Available date range » (retiré) : la clé précède la semaine d'une température historique |
| `auditValuePendingReview` | EN | To be checked (feuille) | remplacer par Pending : libellé partagé entre le statut de boîte et le statut des températures historiques |
| G36 Lecteurs | EN / JA | Read-only mode / 閲覧専用 | proposer Viewer ou 閲覧者 |
| Autres « À vérifier » | toutes | selon le contexte | aligner sur Pending : `auditMetaNeedsAttention` (attention d'un relevé) et les actions de vérification sont d'autres concepts |

Les catalogues applicatifs ne sont jamais réécrits automatiquement à partir du classeur historique. Les écarts identifiés lors du nettoyage sont conservés dans les archives privées hors dépôt. Les décisions et modifications réellement examinées dans l'application sont tracées dans docs/translation-review/application/register.csv. Les écarts non arbitrés ne doivent pas être corrigés automatiquement. adminZoneTypeCabinet a été aligné sur Emplacement / Rack dans l'application par décision explicite d'Anthony (2026-10-28).

Toute modification d'un texte FR/EN de l'application, ou toute décision de ne pas le modifier, est tracée dans `docs/translation-review/application/register.csv` et vérifiée par `check_register.py` (voir le README de ce dossier).

## D. Choix linguistiques non résolus

Ne pas les traiter comme normatifs. Réutiliser la valeur actuelle validée et signaler la question à Anthony.

- Formulations anglaises encore proposées dans `translation_review_queue.csv` (statut autre que `confirmed_product_decision`) : aucune n'est approuvée.
- Adaptations japonaises demandant une nouvelle formulation : provenance d'un mouvement, 継代 / 移植 sur une clé d'audit, 機関 / 所属機関, terme de transfert entre structures, 撤去 pour une fin de période, dernier emplacement connu, termes JA d'Appartenance et d'Accès, invitation à contacter un administrateur dans le refus Django de repiquage.
- Institution ou Working institution pour la structure de travail active (`organizationMenuTitle`); « Non renseigné » ou « Non fourni »; nom de la famille d'audit mixte « Mesures »; `auditMetaMembershipId` (« Identifiant accès » désigne une appartenance); `django:gettext:Provenance non établie` (même exception que `movementKindUnclassified` ?).

## Suivi d'implémentation séparé

Vérifier, dans une tâche dédiée, tous les messages visibles de refus de permission (catalogues frontend et messages Django renvoyés par l'API) selon la règle de la section A. Candidats repérés dans la feuille, sans invitation aujourd'hui : `boxActivateForbidden`, `manageErrorResponsableRequired`, `manualTemperatureForbidden` (section EXCLU). Distinguer chaque cas d'une erreur de validation ou de règle métier avant de modifier le texte.

## Nouveau terme ou terme ambigu

1. Chercher la clé, le code qui l'affiche et le contexte métier (`docs/context/` du domaine). Le mot FR seul ne suffit pas.
2. Réutiliser un terme des sections A ou B s'il couvre exactement ce sens.
3. Sinon, ne pas inventer de terminologie produit ni scientifique : poser la question à Anthony. Une nouvelle formulation japonaise reste non résolue.
4. Ajouter tout nouveau texte visible en FR et en EN dans les catalogues, conserver les variables (`{zone}`, `{count}`…) et ne pas utiliser le point médian.
5. Pour le classeur partagé : toujours identifier une ligne par clé exacte, jamais par numéro de ligne; ne jamais restaurer une clé supprimée ni dédupliquer par égalité de texte.
