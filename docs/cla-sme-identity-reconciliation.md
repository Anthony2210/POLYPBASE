# Réconciliation des identités historiques CLA / SME

## Statut et périmètre

Preuves sources établies sur la baseline `a78c1046be9e5cb1658ce467ba6f0a8bc7d29545`, puis contrat de l'implémentation locale actuelle compatible PostgreSQL : `identity_execution.py`, `identity_references.py`, `identity_reconciliation.py` et leurs commandes. La restriction initiale à SQLite en mémoire est dépassée ; SQLite reste un chemin de tests isolés, pas la seule frontière implémentée. Statut : **prêt pour revue indépendante, pas prêt pour exploitation**. Les faits opérationnels et résultats de tests communiqués par l'utilisateur sont attribués comme tels ci-dessous ; cette mise à jour documentaire ne les présente pas comme des vérifications de l'agent.

**Ce document n'autorise aucune opération en production, sur Neon ou sur une base persistante, même en lecture seule.** Aucun accès à une base n'a été effectué pour établir les correspondances ci-dessous. Cette mise à jour ne rouvre pas les classeurs ni le dépôt compagnon et ne lance ni Django, ni SQL, ni tests, ni accès VM. La procédure destinée à un opérateur humain, après accords distincts, est décrite dans [`cla-sme-postgresql-operations.md`](cla-sme-postgresql-operations.md). Code compatible production ne signifie pas prêt pour exploitation : release revue, documents root, sauvegardes réellement restaurées et préflight réel restent requis.

Décision produit explicite actuelle :

- le classeur 2026 V2 fait autorité sur les versions 2026 antérieures ;
- un identifiant corrigé dans un classeur annuel ultérieur peut désigner la même Box physique, mais seulement si la continuité est démontrée ;
- cette possibilité n'autorise aucune équivalence fondée uniquement sur un suffixe, une espèce ou la ressemblance des mesures.

## Sources et empreintes

Répertoire exact des originaux :

`C:/Users/antoc/POLYPBASE-ANALYSES/data/raw/`

Les empreintes SHA-256 ci-dessous ont été calculées sur les octets des fichiers, sans sauvegarde ni modification des classeurs.

| Fichier dans ce répertoire | SHA-256 réel | Comparaison avec une empreinte consignée |
|---|---|---|
| `Suivi_2020.xlsx` | `42f5bebd82917de9fdea51f823b158c0b3869d701ac714c7e8d743c45efc0250` | Conforme |
| `Suivi_2021.xlsx` | `57b3513a962f5f0667646c3f6bd26376c215da9b6c13fe33ddad2bd4895f9dee` | Conforme |
| `Suivi_2022.xlsx` | `4761e642eb1a882a23114bbae267ce92c3f31db2cd64c2e76ae4e4191db861e9` | Conforme |
| `Suivi_2023.xlsx` | `f30e00a2ae97112a6969dae78d4c5541b9e8cd7f98e850d36e2e1094be6a33ce` | Conforme |
| `Suivi_2024.xlsx` | `8f60d451ad2c7f2c37796bb7adf4f509cac5561b9bc9e0b3469235e1324ca330` | Conforme |
| `Suivi_2025.xlsx` | `0776396ad12b6479e53755496b0e6c51ec3a2e1ff581c983f84f82fe943b84fa` | Conforme |
| `Suivi_2026.xlsx` | `070f18b2ff8a37cc2e327d4e682f5dfc0854bb55f6547d46a2a8e3842b825c47` | Calculée ; aucune empreinte antérieure trouvée dans les références inspectées et les fichiers suivis du dépôt principal |
| `Suivi_2026_actualisé.xlsx` | `14befa67441f289153576b5c53de462eaa05b4218df6d5439a44ee432f62cb67` | Conforme |
| `Suivi_2026_actualisé_v2.xlsx` | `0d5cd16eee88845ea51fbd4f122be15c12e5896b5237458573fc432ebda8b160` | Conforme |

Empreintes consignées dans le dépôt principal :

- `backend/apps/measurements/historical_temperature_source.py`, constante `SOURCES` : 2020 à 2025 et V2 2026 ; les sept comparaisons ont été effectuées et sont conformes ;
- `backend/apps/cultures/historical_2026/decisions.py`, empreinte du classeur actualisé ;
- `backend/apps/cultures/historical_2026/manifest_2026.json`, objet `source` : nom et empreinte du classeur actualisé ;
- `backend/apps/cultures/historical_2026/cca_eob_source.py`, constante `V2_SHA256`.

Décisions du compagnon :

`C:/Users/antoc/POLYPBASE-ANALYSES/docs/SPECIES_AAA_ISSUES_AND_PROPOSED_CORRECTIONS.md`

Les entrées D8 et D11, ainsi que les réserves introductives de ce document, sont des décisions et limites de revue ; elles ne constituent pas un registre de mouvements, de filiation ou de contenu physique des boîtes.

## CLA : correspondances démontrées et portée de D11

### Les deux corrections explicites de V2

Toutes les cellules de ce tableau appartiennent à la feuille `Semaestomeae`.

| Box normalisée | `Suivi_2026.xlsx` | `Suivi_2026_actualisé.xlsx` | `Suivi_2026_actualisé_v2.xlsx` |
|---|---|---|---|
| `.009` | `B67 = CLA-JKA-1.09`, bloc 67–70 | `B77 = CLA-JKA-1.09`, bloc 77–80 | `B77 = CLC-JKA-1.09`, bloc 77–80 |
| `.010` | `B71 = CLA-JKA1.10`, bloc 71–74 | `B81 = CLA-JKA1.10`, bloc 81–84 | `B81 = CLC-JKA1.10`, bloc 81–84 |

Une comparaison de toutes les valeurs de cellules sur toute la largeur des blocs actualisé et V2 établit :

- lignes 77–80 : seule `B77` change, de `CLA-JKA-1.09` à `CLC-JKA-1.09` ;
- lignes 81–84 : seule `B81` change, de `CLA-JKA1.10` à `CLC-JKA1.10`.

Dans V2, `A77 = Chrysaora lactea`, avec fusion `A77:A84`. Les deux blocs sont donc explicitement attribués à lactea, et non à Cyanea lamarckii. Entre le classeur 2026 initial et le classeur actualisé, les valeurs des blocs correspondants sont identiques dans les colonnes B–W après décalage de dix lignes. La preuve porte sur une révision traçable des mêmes blocs documentaires ; elle ne repose pas sur la seule similitude de mesures.

Repères supplémentaires, identiques entre actualisé et V2 :

- `.009` : `F77:F78 = 21/70`, semaine 1 ; `AS77:AS78 = 0/0`, semaine 40 ;
- `.010` : `T83:T84 = 9/0`, semaine 15 ; `AD83:AD84 = 56/33`, semaine 25 ; `AO83:AO84 = 0/0`, semaine 36 ;
- `E1 = 2025` et `F2 = 1` : E est une colonne récapitulative de 2025, pas une observation 2026.

Pour `.009`, `Suivi_2025.xlsx` contient `Semaestomeae!B61 = CLA-JKA-1.09`, sous l'espèce `A57 = Chrysaora lactea`. La dernière semaine donne `BE61:BE62 = 19/21`, repris explicitement dans le récapitulatif 2025 de V2, `E77:E78 = 19/21`. Le même identifiant complet, le récapitulatif annuel identifié et la correction explicite de V2 documentent cette continuité.

Aucune `.010` n'a été trouvée dans les classeurs 2020–2025 inspectés. Ses premières mesures documentées ici sont en semaine 15 de 2026. Aucune création ou parenté n'en est déduite.

### Historique lactea `.001` à `.008`

Les suffixes normalisés à trois chiffres ci-dessous représentent les littéraux à deux chiffres des cellules source.

| Suffixe | Cellules d'identifiant `CLA-JKA-1.xx`, feuille `Semaestomeae` |
|---|---|
| `.001` | 2020 `B55` ; 2021 `B63` ; 2022 `B107` |
| `.002` | 2020 `B59` ; 2021 `B67` ; 2022 `B111` |
| `.003` | 2022 `B115` ; 2023 `B61` |
| `.004` | 2022 `B119` ; 2023 `B65` |
| `.005` | 2023 `B69` ; 2024 `B55` |
| `.006` | 2023 `B73` ; 2024 `B59` |
| `.007` | 2024 `B63` ; 2025 `B57` |
| `.008` | 2024 `B67` ; 2025 `B59` |

Ancres d'espèce lactea : 2020 `A55`, 2021 `A63`, 2022 `A107`, 2023 `A61`, 2024 `A55`, 2025 `A57`. Ces références ne justifient ni fusion entre suffixes, ni filiation, ni conversion en Cyanea lamarckii.

### Contradiction avec l'ancienne restriction D11

D11 réserve CLA à `Cyanea lamarckii` et CLC à `Chrysaora lactea`, mais précise aussi « No historical identifier rewrite ». Le diagnostic historique conserve cette restriction et laisse les deux identités non résolues :

- `backend/apps/cultures/historical_2026/decisions.py`, `UNRESOLVED_IDENTITIES["D11_CLA_CLC"]` ;
- `docs/scientific-anomaly-diagnostic-20261015.md` ;
- `docs/context/imports-exports.md`, section de l'import historique 2026.

La décision actuelle déroge à l'interdiction de renommage **uniquement pour les deux correspondances démontrées** :

- `CLA-JKA-1.009` → `CLC-JKA-1.009` ;
- `CLA-JKA-1.010` → `CLC-JKA-1.010`.

Le littéral V2 `CLC-JKA1.10` reste une preuve source conservée ; sa normalisation explicite ne le transforme pas rétroactivement en chaîne source bien formée. Cette dérogation n'est pas une autorisation générale de réécrire les anciens codes CLA.

Pour `.001` à `.008`, le contrat conserve les codes `CLA-JKA-1.001` à `CLA-JKA-1.008` et prévoit leur rattachement à la souche locale lactea existante `CLC-JKA-1`. Il faut donc distinguer code historique de Box et identité biologique de sa souche. Aucun rapprochement avec les boîtes Cyanea utilisant CLA n'est autorisé.

## SME : correction biologique, mais continuité physique bloquée

### Portée de D8

D8 distingue SME / `Stomolophus meleagris` de SSP / `Stomolophus sp.2`. Il corrige l'interprétation des SME historiques concernés en 2020–2023 et indique que la distinction SME/SSP est correcte à partir de 2024. Cela ne signifie pas que tout identifiant SME de toutes les années doit être converti.

L'original 2020 porte littéralement `Stomolophus meleagris` : il ne faut pas remplacer cette preuve par le libellé ultérieur. Les corrections revues de D8 restent séparées des chaînes effectivement inscrites dans les classeurs.

### Inventaire annuel exact

Feuille `Rhizostomae`, sauf en 2023 où le nom exact est `Rhizostomeae`. « Absent » signifie absent de l'inventaire ciblé inspecté, sans conclusion sur la disparition physique de la boîte.

| Suffixe normalisé | 2020, SME | 2021, SME | 2022, SME | 2023, SME | 2024, SSP |
|---|---|---|---|---|---|
| `.001` | `B141` | `B135` | Absent | Absent | Absent |
| `.004` | `B145` | `B139` | `B191` | `B129` | Absent |
| `.005` | Absent | `B143` | `B195` | Absent | Absent |
| `.006` | Absent | Absent | `B199` | Absent | Absent |
| `.007` | Absent | Absent | `B203` | `B133` | Absent |
| `.008` | Absent | Absent | Absent | `B137` | `B157` |
| `.009` | Absent | Absent | Absent | `B141` | `B161` |
| `.010` | Absent | Absent | Absent | `B145` | `B165` |

Ancres littérales d'espèce :

| Année | Cellule | Valeur |
|---|---|---|
| 2020 | `Rhizostomae!A141` | `Stomolophus meleagris` |
| 2021 | `Rhizostomae!A135` | `Stomolophus sp.2` |
| 2022 | `Rhizostomae!A191` | `Stomolophus meleagris` |
| 2023 | `Rhizostomeae!A129` | `Stomolophus meleagris` |
| 2024 | `Rhizostomae!A157` | `Stomolophus sp.2` |

La répétition du même identifiant complet documente les historiques annuels de `.001`, `.004`, `.005` et `.007`. D8 corrige leur interprétation biologique ; il n'invente pas de successeur.

### `.008` à `.010` : candidats, pas équivalences physiques

| Rapprochement candidat | Dernière semaine 2023 | Première semaine 2024 |
|---|---|---|
| SME `.008` → SSP `.008` | `Rhizostomeae!BD137:BD138 = 40/16`, 15 °C ; identifiant `B137` | `Rhizostomae!E157:E158 = 40/5`, 15 °C ; identifiant `B157` |
| SME `.009` → SSP `.009` | `Rhizostomeae!BD143:BD144 = 40/100`, 20 °C ; identifiant `B141` | `Rhizostomae!E163:E164 = 40/130`, 20 °C ; identifiant `B161` |
| SME `.010` → SSP `.010` | `Rhizostomeae!BD147:BD148 = 3/5`, 25 °C ; identifiant `B145` | `Rhizostomae!E167:E168 = 3/0`, 25 °C ; identifiant `B165` |

**Continuité physique : BLOQUÉE pour les trois rapprochements.** Le classeur 2024 ne fournit pas de colonne récapitulative 2023 explicitement identifiée reliant ancien et nouveau codes. Aucun commentaire pertinent, lien externe de classeur ou annotation ancien code / nouveau code n'a été trouvé dans les sources inspectées. La frontière temporelle D8, les suffixes et les mesures compatibles ne remplacent pas un pont documentaire explicite.

`.001` et `.004` à `.007` n'ont **aucun successeur SSP démontré**. Les séquences de mesures ne prouvent pas une continuation physique ou une filiation :

- `.005` : 2022 semaines 1–22, lignes 197–198 ;
- `.006` : semaines 23–30, lignes 201–202 ;
- `.007` : semaines 31–52, lignes 203–204, puis même code en 2023 jusqu'aux zéros enregistrés de semaine 14 ;
- `.004` : mesures jusqu'à la semaine 30 de 2023 ;
- `.008` : mesures dès la semaine 15 de 2023, donc en chevauchement avec `.004`.

Un zéro est une mesure réelle, pas une absence ni une preuve de fermeture. Aucun renommage inter-suffixes, repiquage ou lien de lignée ne peut être déduit de cette chronologie.

### SSP à partir de 2024

Les SSP `.008`, `.009` et `.010` ne sont plus présents dans les inventaires 2025–2026 inspectés. Les chaînes documentées jusqu'en 2026 sont distinctes :

| Identifiant littéral | 2024 | 2025 | 2026 initial | 2026 actualisé et V2 |
|---|---|---|---|---|
| `SSP-JKA-1.11` | `Rhizostomae!B169` | `B187` | `B177` | `B209` |
| `SSP-JKA-1.12` | `Rhizostomae!B173` | `B191` | `B181` | `B213` |

Récapitulatifs annuels explicites :

- `.11`, 2024 → 2025 : `BD171:BD172 = 100/100` → `E189:E190 = 100/100` ;
- `.12`, 2024 → 2025 : `BD173:BD174 = 50/30` → `E191:E192 = 50/30` ;
- `.11`, 2025 → V2 : `BE189:BE190 = 50/300` → `E211:E212 = 50/300` ;
- `.12`, 2025 → V2 : `BE191:BE192 = 100/70` → `E213:E214 = 100/70`.

Aucun pont documentaire inspecté ne fait de `.11` ou `.12` un ancien SME renommé. Les mesures de `.010` et `.011` se chevauchent déjà en 2024. Les namespaces SME corrects à partir de 2024 restent hors correction : par exemple `Suivi_2024.xlsx`, `Rhizostomae!B155 = SME-ACH-1.01`, sous `A155 = Stomolophus meleagris` ; `SME-JKA-2` reste également hors périmètre.

## Contrat de l'implémentation locale compatible PostgreSQL

### Commandes distinctes et simulation par défaut

Les deux commandes implémentées sous `backend/apps/cultures/management/commands/` ont des plans, opérations et reçus distincts :

```text
prepare_historical_identity_references --organization-id <id> --family cla|sme --source-strain-id <id> [--target-strain-id <id>] [--actor-id <id>] [--expected-plan-hash <sha256>] [--apply]
correct_historical_identities --organization-id <id> --family cla|sme --target-strain-id <id> [--actor-id <id>] [--expected-plan-hash <sha256>] [--apply]
```

Cette syntaxe décrit le code, pas une consigne d'exécution opérationnelle. Sans `--apply`, aucune écriture métier, aucun audit ni reçu persistant. L'organisation, la famille et les références demandées sont explicites. L'application exige un administrateur actif de l'institution désigné par **`--actor-id`**, puis le hash exact du plan revu. `--actor <username>` n'est qu'une compatibilité de la commande de correction pour les tests SQLite isolés ; il est refusé pour l'application PostgreSQL. La préparation permet l'absence de cible demandée seulement pour préparer la référence CLA manquante ; la correction exige toujours une cible existante explicite.

### Frontière d'exécution et autorisations

`identity_execution.py` conserve les tests sous `config.test_settings` avec SQLite réellement en mémoire et un chemin PostgreSQL 18 jetable attesté par son lanceur. Il implémente aussi une frontière PostgreSQL opérationnelle distincte : settings applicatifs existants **`config.settings`**, `DEBUG=False`, une seule base `default`, sans routeurs, entrée en autocommit, authentification peer par socket Unix local attesté par root. Aucun TCP, tunnel, hôte distant, Neon, credentials ajoutés ou variable `PG*` héritée n'est accepté. Le runtime non privilégié et l'identité effective du serveur doivent satisfaire les contrôles de la procédure opérateur.

- Simulation PostgreSQL : transaction **`REPEATABLE READ READ ONLY`**, sans verrous de lignes ni barrière de tables. L'attestation root de la cible reste nécessaire ; le mode lecture n'est pas une permission d'accès pour l'agent.
- Application PostgreSQL : transaction détenue par le service, **`READ COMMITTED READ WRITE`**, autorisation root distincte et liée à l'opération exacte, la famille, l'organisation, la cible, l'acteur et le hash du plan.
- Documents épinglés : `/etc/polypbase/cla-sme-production-target.json` et `/etc/polypbase/cla-sme-production-authorization.json`, propres à CLA/SME, jamais réutilisation de ceux de PCA.
- Pour **chaque famille et chaque phase appliquée**, accord humain distinct, nouveau document root, sauvegarde fraîche dont la restauration indépendante est attestée, archive protégée vérifiée. Autorisation et sauvegarde ont une fraîcheur maximale d'une heure, sans date future ; cible et autorisation sont revérifiées avant commit. La preuve root de restauration ne signifie pas que le correcteur restaure lui-même une sauvegarde.
- Fenêtre de maintenance sans écrivains : avant tout verrou de ligne, préparation et correction prennent **`EXCLUSIVE MODE NOWAIT` sur toutes les tables Django managed, tables intermédiaires incluses, ainsi que `django_migrations`**. Les SELECT ordinaires restent possibles, pas les écrivains ni SELECT FOR UPDATE. Un verrou indisponible fait échouer la transaction ; pas de relance automatique ni de politique de retry déduite des tests.

Chaque famille et chaque phase reste atomique séparément. Après une préparation appliquée, produire un **nouveau plan de correction**, son nouveau hash, une nouvelle sauvegarde / restauration attestée et une nouvelle autorisation ; une autorisation de préparation n'autorise pas la correction. Aucun accès VM ou opérationnel par l'agent n'est impliqué.

### Références explicites et opérations autorisées

La correction exige une souche cible existante **éligible dans l'institution**, avec code et espèce exacts ; elle ne crée pas les références manquantes. La détention institutionnelle n'est plus obligatoire pour une cible globale déjà éligible grâce à des Box propres préexistantes. Une référence étrangère non éligible reste refusée. La préparation additive séparée ne contourne aucune ambiguïté : elle crée uniquement les références exactes absentes autorisées par son plan revu.

#### Faits communiqués par l'utilisateur, non vérifiés par l'agent

Pour l'organisation 1 : dix CLA sont signalées sur la source **213** `CLA-JKA-1` / `Cyanea lamarckii`, avec absence de Species `Chrysaora lactea` et de cible `CLC-JKA-1`. Huit SME approuvées sont signalées sur la source **245** `SME-JKA-1` / `Stomolophus meleagris`. La cible SME globale **247**, `SSP-JKA-1`, Species **145** `Stomolophus sp.2`, est signalée comme déjà éligible par **cinq Box propres initiales**, dont SSP `.008`–`.010`. L'absence des attributions AAA `CLA`, `CLC`, `SME`, `SSP` est également communiquée. Ces observations ne remplacent pas le préflight après release ; leurs PK ne sont pas des constantes générales du service. Après correction SME, le nombre de témoins peut augmenter : contrôler les cinq PK initiales, pas exiger que le total reste cinq.

#### Préparation additive `cla_sme_references_20261027_v1`

- CLA : si l'absence exacte et sans candidat ambigu est confirmée, créer Species `Chrysaora lactea`, les attributions institutionnelles manquantes `CLA` → `Cyanea lamarckii` et `CLC` → `Chrysaora lactea`, puis la Strain détenue `CLC-JKA-1` (`number=1`, `origin_code="JKA"`) et sa `LocalStrainIdentity` liée à CLC. Provenance biologique inconnue : `origin_id = NULL`, `provenance_code_assignment_id = NULL`. Aucun `GlobalStrainIdentity`, aucune provenance JKA ni filiation n'est inféré. Relever le PK produit `prepared_target_strain_id`, sans le préallouer.
- SME : préparer seulement les attributions institutionnelles SME / SSP exactes manquantes ; utiliser la cible globale 247 existante et éligible si le préflight le confirme. Aucune création de Species ou Strain SME, aucune appropriation de 247, aucune `LocalStrainIdentity` ni identité globale ajoutée à cette référence globale.
- Aucune Box ni référence préexistante n'est modifiée par cette préparation. **La source partagée 213 reste entièrement intacte pour tous ses consommateurs**, y compris les autres organisations : espèce, code, propriétaire, identités et provenance inchangés.

#### Correction `cla_sme_identity_20261027_v2`

| Famille | Cible existante exigée | Opérations admises |
|---|---|---|
| `cla` | Souche éligible `CLC-JKA-1`, espèce `Chrysaora lactea` ; la préparation d'une cible absente crée une souche détenue | Rattacher les Box revues `.001`–`.010` à cette souche ; conserver les codes CLA `.001`–`.008` ; renommer seulement `.009` et `.010` en CLC, sur la même Box |
| `sme` | Souche éligible `SSP-JKA-1`, espèce opérationnelle `Stomolophus sp.2` ; cible globale 247 communiquée, sans modification de cette référence | Rattacher les huit Box revues `SME-JKA-1.001`, `.004`, `.005`, `.006`, `.007`, `.008`, `.009`, `.010` à cette souche ; conserver tous leurs codes SME |

Chaque famille constitue un plan et une transaction atomique séparés. Une application CLA ne déclenche pas SME ; aucun succès partiel à l'intérieur d'une famille n'est permis. Les listes sont fermées : aucun traitement par préfixe de toutes les Box CLA ou SME.

### Blocages obligatoires

- Référence source ou cible absente, ambiguë ou non éligible dans l'institution, attribution AAA manquante / incompatible, identité locale incohérente ou identités globales établies contradictoires. Une absence exacte impose une préparation séparée ; un candidat approximatif ou une référence conflictuelle impose une revue, jamais une réparation implicite.
- Vraie collision de renommage CLA : ancienne Box et Box au code CLC cible simultanément présentes pour `.009` ou `.010` → **échec, jamais fusion**. Codes réservés, état fusionné et état cible sans reçu exact restent bloquants.
- À distinguer : la présence de SSP `.008`–`.010` correspondant aux suffixes SME est **observationnelle**, via `corresponding_ssp_present`. Elle ne bloque plus à elle seule la correction biologique des SME conservant leur code. Les Box et historiques SSP restent séparés, intacts et inclus dans le snapshot ; `physical_continuity = NOT_ASSERTED`. L'équivalence physique, la fusion et le renommage SME → SSP restent non démontrés et bloqués.
- Dépendance, état de fusion, référence ou audit incompatibles avec le snapshot revu ; changement intervenu entre préparation et application ; empreinte attendue absente ou différente.
- Acteur inexistant, inactif ou non administrateur autorisé dans l'institution ; environnement d'application non admissible.

Une référence manquante ne doit pas conduire à ignorer une partie du lot. Toute ambiguïté impose une décision séparée et un nouveau plan.

### Préservation et audit

- Préserver les PK des Box ; ne pas fusionner de Box et ne pas renseigner `merged_into`.
- **Ne pas renseigner `previous_global_code`** : ce champ porte des sémantiques de fusion / résolution historique chez des consommateurs existants. Il n'est pas un alias générique de renommage.
- Conserver `local_code` sans modification. Conserver les littéraux source, les cellules, les empreintes et les anciens codes comme preuves explicites dans le plan et le nouvel audit / reçu, sans réécrire la preuve originale.
- Ne modifier ni supprimer les anciens audits ; ajouter uniquement les traces propres à l'opération appliquée.
- Aucun changement de comptes scientifiques, dates, semaines ISO, précision, observateurs, températures, statuts, dates de création / fermeture, emplacements, mouvements ou liens de lignée / repiquage / transfert.
- Les compteurs techniques des namespaces CLA/CLC sont verrouillés dans le même ordre dès l'allocation et jamais abaissés. La réservation protège symétriquement les deux paires exactes CLA/CLC `.009` et `.010` ; ce n'est ni un alias général ni une fusion. Les sauvegardes ORM périmées ne peuvent pas restaurer l'ancienne souche sur le périmètre protégé. Le manifeste ancien reste scellé et son rejeu ne doit pas recréer l'ancienne identité ; la protection HS suit aussi `CLC-JKA-1.009` sans autoriser sa désactivation.
- Les anciens audits conservent leur `object_id` littéral : aucune réécriture de la preuve pour maintenir une navigation par ancien code. Le reçu garde le lien PK / ancien code / nouveau code. Le diagnostic général daté conserve sa classification conservatrice historique ; cette commande dédiée est la référence pour le nouveau plan, pas une extension du diagnostic ni de l'import scellé.
- Dans la phase de correction, le rattachement biologique et les deux renommages autorisés sont les seules mutations métier. Ils ne certifient aucune continuité environnementale et n'inventent aucune histoire physique.

### Plan, empreinte, application et reçu

Le plan déterministe décrit l'opération versionnée, la famille, l'institution, la cible explicite, les mutations exactes et leurs anciens états attendus. L'acteur est rechargé et autorisé sous verrou au moment de l'application, puis enregistré dans les audits et le reçu ; il ne fait pas partie de l'empreinte de simulation. Il doit figer les références source (fichier, SHA-256, feuille, cellules et littéraux), les Box et souches concernées, les références biologiques / locales, les dépendances pertinentes et les audits existants nécessaires à la détection de dérive. Les valeurs nulles et les zéros restent distincts.

La sérialisation canonique, les tris stables et l'absence de données volatiles dans la partie empreintée doivent produire un SHA-256 reproductible. Le hash n'est ni une réservation ni une autorisation de production.

Le snapshot de correction inclut aussi les SSP coexistantes et les témoins propres d'éligibilité d'une cible globale. Le schéma et les définitions PostgreSQL effectives de contraintes, index et triggers sont liés au plan ; les contraintes non validées / non appliquées et les index invalides ou incomplets sont refusés. Les références source, dépendances et audits restent empreintés, avec distinction stricte zéro / NULL.

Lors de l'application PostgreSQL, après la barrière de toutes les tables managed en `EXCLUSIVE MODE NOWAIT`, le service verrouille les références et dépendances concernées, recharge et autorise l'administrateur, reconstitue le plan et compare le hash attendu avant mutation. L'ensemble exact des Box planifiées et verrouillées doit correspondre ; toute dérive bloque la famille entière. Les tests SQLite ne prouvent pas la concurrence PostgreSQL ; les résultats synthétiques PostgreSQL communiqués sont distingués ci-dessous du préflight réel toujours requis.

Les modifications, nouveaux audits et reçu doivent être atomiques. Le reçu doit identifier le plan et son hash, l'acteur, l'institution, la famille, les preuves source et les changements avant / après. Un rejeu avec un nouveau plan revu ne duplique aucun audit. Pour les 18 identités, l'état cible exige un reçu de cette opération avec la correspondance exacte PK / codes / identité après correction. Un état cible sans cette preuve bloque, même sans renommage. L'ancienne empreinte n'est pas une clé de rejeu : elle devient périmée après application.

## Validation et points à compléter

Validation documentaire effectuée : calcul des neuf SHA-256 ; comparaison aux empreintes consignées disponibles ; inspection des originaux avec `openpyxl` en lecture seule au sens de l'absence de sauvegarde ; comparaison exhaustive des valeurs des deux blocs actualisé / V2 ; vérification des cellules et récapitulatifs cités. Aucun service Django ni accès à une base n'a été exécuté pour cette investigation.

### Historique initial — ne décrit pas la dernière implémentation PostgreSQL

Résultats initiaux consignés sous `POSTGRES_DB='' POLYPBASE_TEST_POSTGRES=0`, avec `--settings=config.test_settings --noinput`. Ils sont conservés comme historique, pas comme validation finale du code actuel :

| Groupe | Résultat |
|---|---|
| Réconciliation, gardes, réservations + import CSV legacy, création automatique/résolution, lifecycle, mouvements/corrections, portée des souches, températures et inventaire | **282 tests, OK, 1 ignoré** |
| Import 2026, diagnostic/HS, six-Box CAC/OCE, CCA-EOB correction/commande/source/emplacements/consommateurs environnementaux/audit/additif | **288 tests, OK, 46 ignorés** |
| `manage.py check --settings=config.test_settings` | OK |
| `manage.py makemigrations --check --dry-run --settings=config.test_settings` | Aucune dérive |
| `git diff --check` | OK |
| PostgreSQL réel, concurrence et tests spécifiques PostgreSQL, à ce stade initial seulement | Non exécutés lors de cette validation initiale ; voir les résultats synthétiques plus récents ci-dessous |
| Frontend / navigateur | Non exécutés : aucun changement d'interface |

Les contrôles `check` / migrations nécessitent ici `DJANGO_DEBUG=1` pour le démarrage local sans secret ; la première tentative sans ce drapeau a été refusée par le durcissement existant `DJANGO_SECRET_KEY`. Aucun secret réel n'a été lu ou ajouté. Certains premiers appels de tests ont dépassé leur borne ; leurs reprises bornées ont abouti. Les 47 tests ignorés des deux groupes ne sont pas des validations acquises.

Les tests couvrent notamment zéro / NULL, déterminisme et simulation sans écriture, atomicité lors d'un échec du reçu, permissions, institutions étrangères et confidentialité des dépendances, incompatibilité d'identités globales, collisions / cibles préexistantes, événements de lignée hors lot, plans périmés et preuve du reçu pour les états déjà corrigés. Les réservations couvrent aussi les sauvegardes ORM périmées complètes, partielles, différées et construites avec une PK. Une revue technique read-only a identifié des points P2 corrigés et vérifiés par régressions ; la vérification ciblée finale ne signale plus de P1/P2. Cela ne remplace pas la revue indépendante demandée.

### Derniers résultats communiqués et limites opérationnelles

Les résultats suivants ont été réellement obtenus pendant la validation locale finale, sans accès à la VM, à Neon ou à une base partagée :

| Validation | Dernier résultat communiqué | Limite |
|---|---|---|
| PostgreSQL + réservations | **39 tests réussis, 0 ignoré : 17 PostgreSQL + 22 réservations** | Nouveau cluster natif PostgreSQL **18.6**, créé pour cette exécution puis arrêté et supprimé ; aucun cluster existant réutilisé, aucune preuve de production |
| Exécution précédente PostgreSQL | Échec transitoire `NOWAIT`, avant l'exécution réussie ci-dessus | Échec fermé : ne justifie aucun retry automatique, contournement de verrou ou assouplissement de maintenance |
| Suite SQLite finale principale | **336 tests réussis, 0 ignoré** | Réconciliation, préparation, frontière, réservations, import 2026, D11/HS, diagnostic et CAC/OCE |
| Régressions SQLite complémentaires | **269 tests : OK, 48 ignorés** | CCA-EOB, repiquage, création automatique/résolution, lifecycle, import CSV ; ignorés non considérés comme validés |
| Django check / migration drift / `git diff --check` | **OK / aucune dérive / OK** | `config.test_settings` et base isolée |

La procédure [`cla-sme-postgresql-operations.md`](cla-sme-postgresql-operations.md) décrit les commandes exactes, les documents root et les contrôles avant/après. La vérification technique ciblée finale ne signale plus de P1/P2 ; elle ne remplace pas la revue indépendante demandée. Les contrôles PostgreSQL incluent les contraintes manquantes et les CHECK affaiblis sous le même nom, en plus des courses de création, applications concurrentes, refus d'écrivain actif, plans périmés et rollbacks.

Les faits sur 213, 245, 247, les cinq témoins et les AAA manquants sont des observations utilisateur, pas un diagnostic DB de l'agent. La compatibilité PostgreSQL du code ne certifie ni la disponibilité des références sur la cible, ni la préparation des sauvegardes, ni la validité d'un compte ou document root. Avant exploitation : revue indépendante du code et des résultats finaux, release approuvée via le workflow existant, contrôle du commit et du schéma réellement servis, préflight réel séparément autorisé, attestation root et runtime peer sûrs, administrateur réel, fenêtre de maintenance, sauvegardes fraîches réellement restaurées et autorisations distinctes pour chaque famille et phase. Aucun accès VM par l'agent ni application opérationnelle n'a eu lieu pour cette mise à jour.

La seule question scientifique restante est la preuve ou l'attestation explicite de continuité physique SME → SSP pour les candidats 2023/2024. Ni la nouvelle décision générale, ni D8, ni les suffixes ne suffisent à la résoudre.

L'implémentation, les tests synthétiques et ce rapport ne constituent aucune autorisation d'application sur des données opérationnelles.
