# Validation ADN d'une boîte — dossier de revue local

## Périmètre et état

La validation ADN est intégrée au code (PR #19, puis preuve facultative à la
création de boîte dans la PR #23). Elle déclare un lien de preuve propre à la
boîte, sans identification automatique. Ce dossier conserve les résultats de la
revue locale historique; ils n'ont pas été rejoués pour PR3.

## Contrat livré

- Preuve URL obligatoire, HTTP/HTTPS seulement, espaces de bord retirés, maximum 2 000 caractères. Le serveur ne consulte jamais le lien.
- Lors de la création ordinaire, `dna_evidence_url` est facultatif. S’il est fourni, la preuve et son audit sont enregistrés dans la même transaction que la boîte.
- Une validation active par boîte; retrait logique conservant la ligne et les auteurs. Ajouter après retrait crée une autre ligne.
- Aucun héritage de preuve vers les enfants, la lignée, la souche ou l'espèce. Aucun effet sur les relevés, exports, QR, inventaire, graphiques ou alertes.
- Autorisation serveur : utilisateur et membership actifs Admin/Technicien de l'institution active. Un superutilisateur sans ce membership n'obtient pas l'écriture ADN. Lecteur : `403`; objet étranger pour un auteur autorisé : `404`. Les statuts active/inactive/pending_review sont tous acceptés.
- Verrou institution `FOR NO KEY UPDATE`, puis verrou Box, revalidation des droits, mutation et audit atomiques. Les révisions croissent sur toutes les validations de la boîte, y compris révoquées, pour empêcher qu'une ancienne intention modifie une preuve de remplacement.
- Dans le bandeau BoxPage : bouclier et hélice, `?` gris si absent, coche verte si présent. Sans preuve, le Lecteur a un indicateur accessible non interactif.
- Le popover d'une preuve active suit le langage des popovers existants : en-tête titré sans chip ni étiquette d'état redondante, bloc libellé muted + valeur (Preuve, Ajoutée par, Date), séparateur puis action de retrait. Le lien affiche le domaine et une icône de lien externe. Il n'a pas de bouton Fermer : Escape, clic extérieur ou nouveau clic sur l'icône ferment, avec retour du focus. Le crayon à côté du lien ouvre la modale d'édition; le retrait est confirmé via `ConfirmActionModal`.
- La modale d'ajout/édition réutilise les classes partagées `box-dialog-backdrop`, `box-dialog`, `box-dialog-heading`, `box-dialog-form`, `box-dialog-body` et `box-dialog-actions`, avec croix de fermeture, Annuler et Enregistrer. Hors requête en cours, Escape et le clic sur le fond ferment également la modale.
- L'erreur URL ne s'affiche ni à l'ouverture ni pendant la saisie. Elle apparaît au blur d'un champ modifié si l'URL est invalide, ou au submit invalide; ce dernier rend le focus au champ. Une nouvelle saisie efface l'erreur. Si `relatedTarget` est le bouton submit, le blur laisse le submit valider et focaliser directement le champ : insérer l'erreur au blur déplaçait Enregistrer entre `mousedown` et `mouseup` et empêchait le submit. Aucun effet `focusRequest` ni délai de focus n'est nécessaire. Les erreurs serveur restent distinctes de ce déclenchement de validation locale.
- Pas de succès optimiste; le détail est relu après mutation via l'orchestration App/client existante. Un conflit bloque les nouvelles écritures jusqu'à une relecture explicitement demandée; la reprise n'effectue qu'un GET.
- Le domaine du lien est affiché; URL complète en `title`, nouvel onglet et `rel="noopener noreferrer"`. Actions et historique admin réutilisent les résumés métier et liens avant/après de `AuditTimeline`.

### API

| Méthode | Chemin | Résultat |
|---|---|---|
| GET | `/api/boxes/<id>/` | Ajoute `dna_validation` et `can_edit_dna_validation` |
| POST | `/api/boxes/<id>/dna-validation/` | `{evidence_url}` → `201`; doublon actif → `409 dna_validation_exists` |
| PATCH | `/api/boxes/<id>/dna-validation/` | `{evidence_url, expected_revision}` → `200`; inchangé → `400` |
| POST | `/api/boxes/<id>/dna-validation/revoke/` | `{expected_revision}` → `204` |

Une révision périmée, une preuve absente ou une preuve remplacée sur PATCH/retrait produit `409 dna_validation_changed`.
La projection de détail est `null | {id, evidence_url, created_by, created_at, revision}`; `created_by` est une identité lisible, pas un identifiant brut.

## Fichiers

| Domaine | Fichiers |
|---|---|
| Persistance | `backend/apps/cultures/models.py`, `migrations/0019_box_dna_validation.py`, `0021_merge_dna_and_history.py` |
| Service/API | `backend/apps/cultures/dna_validation.py`, `dna_validation_api.py`, `serializers.py`, `backend/config/api_urls.py` |
| Tests backend | `backend/apps/cultures/test_dna_validation.py`, `test_dna_validation_concurrency.py`, `test_dna_validation_migrations.py`, `test_box_creation_dna_evidence.py` |
| Badge et lien | `frontend/src/components/DnaValidationBadge.tsx`, `DnaEvidenceLink.tsx`, `BoxPage.tsx`, `CreateBoxPanel.tsx` |
| Orchestration/types | `frontend/src/App.tsx`, `frontend/src/types.ts`; réutilisation de `api/client.ts`, sans seconde implémentation du transport |
| Audits | `frontend/src/components/AuditTimeline.tsx`, `frontend/src/utils/auditPresentation.ts` |
| Styles | `frontend/src/styles/pages/box-detail.css`, `frontend/src/styles/components/popovers.css` |
| Tests frontend | `frontend/scripts/test-dna-validation.mjs`, `test-audit-presentation.mjs`, `test-box-page-extraction.mjs`, `qa-dna-validation.py` |
| Traductions | Catalogues React FR/EN, PO/MO Django FR/EN, MO japonais généré pour le repli anglais, `docs/translation-review/application/register.csv` |
| Documentation | `docs/context/boxes-lifecycle-locations.md`, `POLYPBASE_CURRENT_STATE.md`, ce dossier |

La migration est uniquement additive : nouvelle table, FK protégées, unicité partielle des lignes actives, contraintes de révision positive et de cohérence de révocation. Aucun historique existant réécrit.
Les méthodes ORM de suppression sont bloquées; ce n'est pas une protection contre du SQL technique privilégié.
Les traductions japonaises validées (`ja.ts` et PO japonais) sont inchangées. Le MO japonais est régénéré par le compilateur existant pour les nouveaux replis anglais.

## Résultats de validation

Ces résultats historiques ont été communiqués lors de la revue de la fiche boîte.
Ils ne valident ni le checkout PR3, ni le formulaire de création ajouté ensuite.

### Backend — résultats historiques, non réexécutés pendant cette reprise

Depuis `backend/`, SQLite explicitement isolé :

```sh
env POLYPBASE_TEST_POSTGRES=0 DJANGO_DEBUG=1 ../.venv/Scripts/python.exe manage.py test apps.cultures.test_dna_validation apps.cultures.test_dna_validation_concurrency apps.cultures.test_dna_validation_migrations --settings=config.test_settings --noinput
```

**23 tests : 16 réussis, 7 ignorés** (verrous PostgreSQL nécessaires). Couverture : ajout/modification/retrait, réattribution interdite, audit et rollback de chaque mutation si l'audit échoue, deux institutions, Lecteur et membership inactif, tous les statuts, URL, révisions, doublon, absence d'héritage, contrainte active et migration/préservation des données.

L'agent backend a également exécuté les tests de détail institutionnel, identité utilisateur, cycle de vie, inventaire et `apps.cultures.tests` : **140 tests, 132 réussis et 8 ignorés**. Une répétition de cette commande élargie par l'agent principal a atteint la limite de 180 secondes sans résumé final; cette répétition ne compte pas comme succès supplémentaire.

PostgreSQL 18 **jetable**, cluster local neuf sur loopback et port dynamique, arrêté/supprimé ensuite : **26/26 réussis**. Groupes exécutés : `test_dna_validation`, `test_dna_validation_concurrency`, `test_box_detail_organization_scoping`, `test_box_move_concurrency`. Schéma créé depuis les modèles avec migrations désactivées dans un module temporaire, pas depuis une base partagée. Les tests de concurrence comprennent double ajout, doubles corrections/retraits, correction contre retrait, intentions anciennes après remplacement, vérification FK d'un audit sous verrou Box et rétrogradation de membership. L'ancien verrou institution `FOR UPDATE` a reproduit le deadlock; `FOR NO KEY UPDATE` passe la régression.

**Limite PostgreSQL :** la chaîne complète de migrations est bloquée par la protection préexistante `measurements.0018_box_1902_preservation` et sa topologie de rôles dédiée. Les résultats PG valident donc contraintes et verrous des modèles actuels, pas cette chaîne de migrations. La migration additive et la préservation des données ont été testées sous SQLite.

```sh
env POLYPBASE_TEST_POSTGRES=0 DJANGO_DEBUG=1 ../.venv/Scripts/python.exe manage.py check --settings=config.preflight_settings
env POLYPBASE_TEST_POSTGRES=0 DJANGO_DEBUG=1 ../.venv/Scripts/python.exe manage.py makemigrations --check --dry-run --settings=config.preflight_settings
```

Résultats : **aucun problème; aucune dérive de migration**.

### Frontend — résultats historiques de la reprise

Depuis `frontend/` :

- `node --test scripts/test-dna-validation.mjs scripts/test-audit-presentation.mjs scripts/test-box-page-extraction.mjs scripts/test-i18n-catalogs.mjs` : **326/326 réussis**, dont **25/25 ADN**.
- `npm run typecheck` : **réussi**.
- Une commande groupée a atteint le **timeout de 180 secondes pendant le build**; elle ne constitue pas un succès global.
- `npm run build`, relancé séparément : **réussi**, contrôles CSS + `tsc` + Vite inclus; Vite **23,27 s**.
- `npm run check:i18n-usage` : **réussi**, **47 exclusions historiques**, aucun orphan non revu.
- `npm run test:all` : **non exécuté pendant cette reprise**. Le timeout historique de 600 secondes ne constitue ni une nouvelle exécution ni un succès de la suite complète. Les anciens timeouts des commandes élargies et de `test:i18n` ne sont pas des résultats actuels.

Depuis la racine, `python docs/translation-review/application/check_register.py` : **réussi**, **1 459 événements dans 31 lots**. À distinguer des tests propres au checker (`python -m unittest discover -s docs/translation-review/application -p 'test_*.py'`) : la tentative documentaire précédente a dépassé 120 secondes après des marqueurs d'échec; une seconde tentative avec arrêt au premier échec a dépassé 60 secondes sans diagnostic final. Ces tests ne sont pas validés et ne remettent pas en cause le succès distinct du checker sur le registre courant.

Contrôle historique uniquement, non réexécuté dans cette reprise : compilation stricte MO `--check` réussie après régénération avec `scripts/compile_django_messages.py`.

### Navigateur et accessibilité — résultats historiques de la reprise

```sh
uv run --no-project --with playwright python frontend/scripts/qa-dna-validation.py --output-dir "<répertoire-qa-hors-dépôt>"
```

Dernière exécution communiquée : **382 contrôles réussis** sur **8 cas** (4 tailles × FR/EN), avec technicien et lecteur : **47 contrôles pour chacun des cas desktop FR/EN**, **48 pour chacun des six autres cas**. **104 captures** ont été produites; le dernier `report.json` est **vert**. Les captures et `report.json` restent hors dépôt.

- Desktop `1440×1000`, tablette tactile `960×600`, téléphone `390×844`, petit écran `320×844`.
- FR et EN; technicien/lecteur, ajout, modification, annulation/retrait confirmé, conflit puis GET sans rejeu, code long, absence de débordement horizontal, cibles de 44px.
- Popover : libellés Preuve/Ajoutée par/Date, absence de bouton Fermer, fermeture par Escape, clic extérieur et nouveau clic sur l'icône, retour du focus. Absence de chip vérifiée dans la QA actuelle.
- Code de boîte sur une seule ligne à toutes les tailles (mesuré par les rects de plage de texte); le badge passe dessous sur petit téléphone plutôt que de couper le code.
- Champ invalide focalisé, erreur liée au champ, focus initial dans le popover, Tab/Shift+Tab, Escape, retour au déclencheur, fond de modale `inert`, préférences de réduction des animations.
- Les popovers réutilisent les styles d'actions existants. Une correction locale empêche la transition globale reduced-motion de retarder leur visibilité et de faire ignorer le focus par Chromium.

**Portée :** vrai composant `BoxPage`, styles et client API réels, props synthétiques et HTTP simulé. Pas de shell App complet, de Django, de base de données ni de données de laboratoire dans ce harness. Ce n'est pas une QA bout en bout ni une preuve sur appareils physiques. Lecteur d'écran, zoom navigateur réel et audit de contraste instrumenté non vérifiés. Serveurs et builds temporaires nettoyés.

## Captures

Choisir un répertoire hors dépôt pour `--output-dir`. Ne pas versionner les PNG
ou `report.json`, ni publier de lien vers un chemin personnel. La revue a couvert
des fixtures avec API simulée, pas un parcours bout en bout Django.

## Corrections visuelles du popover

Le popover ne suit plus un texte brut. Il réutilise le langage existant plutôt qu'un nouveau style :

- En-tête titré et séparateur repris de `box-tracking-preview-heading` (`.dna-validation-heading`).
- Aucun chip d'état dans le popover; la coche verte du badge indique déjà la présence d'une preuve active. Le registre conserve l'ajout de `dnaStateValidated` en B30 (`T1452/T1453`) puis son retrait FR/EN en B31 (`T1458/T1459`), sans effacer l'historique.
- Bloc libellé muted + valeur en `dl`/`dt`/`dd`, comme `box-inventory-suggestion-popover` et le bandeau de fiche.
- Action de retrait alignée sur `row-action-menu button` (danger, `--color-danger-soft`).
- Crayon `Pencil` de `lucide-react`, déjà utilisé pour corriger; icône `ExternalLink` pour le lien.
- Date via `formatDisplayDateTime` (formateur existant, sans secondes).
- Modale alignée sur les classes partagées `box-dialog*`; erreurs URL après blur d'un champ modifié ou submit invalide, pas à l'ouverture ni pendant la saisie. Ces ajustements sont couverts par les résultats historiques ci-dessus. Le blur vers le bouton submit n'insère pas d'erreur avant le clic : le submit valide et focalise directement, sans effet `focusRequest` ni délai.

Fichiers modifiés par ces corrections : `frontend/src/components/DnaValidationBadge.tsx`, `DnaEvidenceLink.tsx`, `frontend/src/styles/components/popovers.css`, `frontend/src/styles/pages/box-detail.css`, `frontend/src/styles/responsive/phone.css`, catalogues `frontend/src/i18n/{fr,en}.ts`, `frontend/scripts/test-dna-validation.mjs`, `test-audit-presentation.mjs`, `frontend/scripts/qa-dna-validation.py`, `docs/translation-review/application/register.csv`.

## Hypothèses techniques et points ouverts

- `created_by` désigne l'ajout initial, pas le dernier éditeur; les corrections conservent auteur/date d'origine et leur propre attribution d'audit.
- Révisions monotones par boîte afin d'éviter le problème de remplacement (ABA); une révocation consomme aussi une révision.
- Les champs futurs (pièce jointe, accession, résultat, laboratoire) ne sont pas ajoutés en V1; le modèle séparé permet une extension additive sans transformer la boîte en preuve scientifique implicite.
- Les contrôles frontend ciblés et la dernière QA navigateur sont verts; cela ne vaut pas validation de `test:all`, non exécuté pendant cette reprise. La chaîne de migrations PostgreSQL reste à vérifier dans un environnement isolé adapté, ainsi que la revue indépendante du diff. Aucun feu vert de déploiement déduit de ces résultats.
