# Boîtes, cycle de vie et emplacements

## Boîte et statuts

`Box` est l'unité centrale de culture. Son `global_code` est unique dans la base. Les règles ci-dessous sont mises en oeuvre côté Django, principalement par les services de `backend/apps/cultures/services.py`; une vue ou le frontend ne doit pas les réimplémenter.

| Statut | Sens et transitions actuelles |
|---|---|
| `pending_review` | Boîte issue de l'historique, « À vérifier ». Elle peut être qualifiée `active` ou `inactive`; le flux normal ne la remet pas dans cet état. |
| `active` | Boîte suivie. Une création manuelle normale exige une zone active de la même institution. |
| `inactive` | Suivi arrêté. Aucun nouveau relevé n'est accepté; un relevé historique existant peut encore être corrigé par un utilisateur autorisé. |

Le statut ne doit pas être déduit du seul emplacement ou de la date du dernier relevé. Les suggestions de l'Inventaire restent une aide, jamais une transition automatique.

## Désactivation, qualification et réactivation

La désactivation d'une boîte active exige le motif prévu par le modèle. Elle ferme les emplacements courants, libère le pointeur `Box.thermal_zone` et conserve l'historique. La qualification d'une boîte historique vers `inactive` peut indiquer explicitement que le motif et la date de fin n'étaient pas renseignés, sans inventer de commentaire, de motif ou de date.

La réactivation d'une boîte inactive exige une zone active, choisie explicitement et appartenant à la même institution. Elle ouvre une nouvelle période d'emplacement : l'ancien emplacement n'est jamais restauré automatiquement.

La qualification historique `pending_review -> active` autorise exceptionnellement une boîte sans emplacement. Cette exception ne s'applique pas à la création manuelle normale ni à la réactivation. Une boîte ainsi activée doit rester identifiable comme active sans emplacement jusqu'à une affectation explicite.

## Emplacement courant et historique

- `Box.thermal_zone` pointe vers l'emplacement courant.
- `BoxLocation` conserve chaque période d'emplacement, avec une éventuelle fin historique inconnue.
- `BoxMovement` représente un événement enregistré, sa nature/provenance et son auteur; une nature legacy non établie ne prouve pas un déplacement ordinaire.
- Une boîte inactive n'a pas d'emplacement courant, mais peut conserver un dernier emplacement historique affichable.
- Déplacer, désactiver ou réactiver une boîte ne doit jamais écraser les périodes précédentes.

L'emplacement courant et le dernier emplacement historique sont deux notions distinctes. Les interfaces et exports doivent choisir celle qui correspond à la question posée, et ne pas présenter l'historique comme l'état actuel.

## Déplacements concurrents

Le déplacement d'une boîte est transactionnel. Le service verrouille la boîte et ses périodes ouvertes, puis met à jour pointeur courant, période, mouvement et audit de manière cohérente.

Le contrat d'intention utilise `expected_thermal_zone_id` :

- champ absent : requête invalide, car le client ne fournit pas l'état sur lequel repose son intention;
- valeur `null` : affirmation explicite que la boîte n'a pas d'emplacement courant;
- identifiant : zone que le client pense être l'emplacement courant.

Si l'état a changé entre l'affichage et la confirmation, l'API répond `409` avec le code `box_location_changed`. Le frontend ne doit pas annoncer un succès : il recharge la boîte et les zones. Ce mécanisme complète le verrouillage et ne remplace ni la permission ni le contrôle d'organisation.

## Nature des mouvements et limite hebdomadaire

`BoxMovement.kind` distingue `ordinary` (transfert entre deux zones), `initial_location` (premier emplacement d'une boîte active sans emplacement, y compris via `move/` depuis `null`), `qualification`, `reactivation` et `unclassified`. `unclassified` signifie « provenance non établie » : toutes les lignes antérieures à la migration `0013`, et valeur par défaut des créations techniques sans nature explicite. Une écriture ORM/SQL peut fournir une nature; elle ne constitue pas à elle seule une preuve de provenance ni un workflow autorisé. Aucune nature n'est déduite après coup.

Au plus un mouvement `ordinary` par boîte et semaine ISO Europe/Paris (lundi-dimanche). `iso_week_start` est dérivé de `moved_at` dans `save()`; depuis `0014`, un `CHECK` impose en base sa correspondance avec le lundi de `moved_at` en Europe/Paris. Une contrainte unique partielle protège l'état final et le verrou de boîte sérialise les écritures. Un second mouvement ordinaire répond `409 box_movement_week_conflict` sans mutation (`conflicting_movement_id`, `iso_week_start`, `iso_year`, `iso_week`); `box_location_changed` reste prioritaire. Création manuelle, repiquage, désactivation et les natures de cycle de vie ne consomment pas ce créneau. Les mouvements `unclassified` n'y participent pas non plus.

`QuerySet.update()` et `bulk_update()` via le manager normal refusent les champs `moved_at`, `kind` et `iso_week_start`. `bulk_create()` ne calcule pas la clé : le caller doit fournir une clé cohérente pour `ordinary`, sinon la base refuse l'écriture. Le `CHECK` et l'unicité PostgreSQL couvrent aussi les upserts, fixtures (`save_base(raw=True)`), le manager de base et les écritures SQL directes. Ces chemins hors service ne garantissent pas permissions, liens de périodes ou audits; ils ne remplacent pas le service métier. `ignore_conflicts=True` peut ignorer une ligne invalide sur SQLite; sur PostgreSQL, il ne masque pas un échec de `CHECK`. `0014` ne répare aucune donnée et échoue si une ligne ordinaire incohérente existe déjà. L'expression `ParisMovementWeekStart` est conservée pour la sérialisation des migrations.

Les nouveaux mouvements référencent les périodes qu'ils ont écrites (`origin_location`, `destination_location`); une origine ambiguë reste `NULL`. Les liens historiques ne sont jamais reconstruits.

## Correction d'un mouvement

`PATCH /api/boxes/<box_id>/movements/<id>/` (service `movement_corrections.correct_box_movement`) exige `expected_revision` et au moins un champ parmi `notes`, `moved_at`, `to_thermal_zone_id`. Administrateurs et techniciens actifs de l'institution, sans délai ni restriction d'auteur. L'identifiant, la nature et l'auteur d'origine sont conservés; chaque correction incrémente `revision` et ajoute un `AuditLog` (`event: box_movement_correction`) avec avant/après, périodes touchées et changement de zone courante. Aucun mouvement n'est créé, supprimé ou fusionné.

- notes : toujours corrigeables, y compris `unclassified`; seule la copie prouvée dans la période de destination suit.
- date : seulement `ordinary` avec liens cohérents, et uniquement dans la semaine ISO Europe/Paris d'origine du mouvement (lundi 00:00 à dimanche 23:59:59, `400 movement_date_outside_week`; jamais une autre semaine, même libre). Strictement entre le début de la période d'origine et la fin de la période de destination, jamais dans le futur, sans autre mouvement ou période chevauchante. `iso_week_start` reste cohérent.
- destination : mouvement terminal uniquement (période de destination ouverte et courante, boîte active, aucun mouvement postérieur); met à jour période et pointeur courant. Autorisée pour `ordinary`, `initial_location`, `qualification`, `reactivation` et pour un `unclassified` dont le lien vers la période de destination est enregistré et cohérent. Un mouvement legacy sans lien (`has_destination_link` faux) reste limité aux notes : aucun lien n'est reconstruit.
- L'API expose `has_origin_link` / `has_destination_link` (booléens, sans identifiants) pour que l'UI ne propose pas de champ qu'elle sait refusé.

Refus `409` : `box_movement_changed`, `box_movement_week_conflict`, `movement_provenance_unresolved`, `movement_date_not_correctable`, `movement_period_link_missing`, `movement_period_mismatch`, `movement_period_overlap`, `movement_chronology_conflict`, `movement_not_terminal`. Refus `400` : validation, `movement_same_zone`, `movement_date_in_future`, `movement_correction_no_change`. L'admin Django générique est en lecture seule pour `BoxMovement` et `BoxLocation`, et `Box.thermal_zone` y est non modifiable pour une boîte existante.

## Présentation de l'historique et correction inline

Le graphique distingue `movement` (`BoxMovement` persisté) de `location_change` (frontière déduite de `BoxLocation`). L'historique Emplacement présente des entrées/sorties de périodes, sans prétendre à autant de mouvements physiques. Le bandeau « Dernier déplacement » de `BoxPage` sélectionne le dernier événement persisté par date puis ID, affiche origine → destination à noms complets et se transforme en éditeur inline. Il n'ouvre pas une modale. Sauvegarde masquée sans changement normalisé, annulation disponible, conflit de révision imposant un rechargement/réexamen explicite. Le backend reste autorité : les liens de périodes ne sont pas tous exposés à l'UI.

L'éditeur utilise Paris et refuse un horaire modifié ambigu/inexistant pendant le DST; une valeur inchangée conserve l'instant original. Les détails graphiques UTC et filtres en jours locaux navigateur sont un autre contrat, pas une décision d'unifier tous les fuseaux. Le sujet du marqueur historique UTC/Paris a été mis en attente; aucun reclassement ni correction historique n'en découle.

## Repiquages et lignées

`SubcultureEvent` décrit un repiquage depuis une boîte parent. `BoxLineage` conserve la relation parent-enfant, notamment créée par ce flux. L'historique de lignée doit survivre aux changements de statut et d'emplacement.

Les nouveaux repiquages sont saisis au moment réel de l'opération, avec horodatage serveur. Chaque allocation enfant est un entier explicite (y compris zéro), `null` ou omise. Si toutes sont connues, l'instantané avant/réparti/après est exact. Sinon, le total réparti et l'après sont `null` : aucun effet quantitatif parent n'est appliqué, et son état autoritaire précédent reste courant. Le parent doit toujours avoir un comptage connu; la somme des allocations connues ne peut pas le dépasser, même en saisie partielle. Les enfants connus reçoivent leur état initial individuel; les inconnus n'ont aucun état initial scientifique inventé. Le service verrouille le parent, vérifie puis incrémente sa révision même en saisie partielle (invalidation d'intention, pas transition), et conserve l'instantané avant, les allocations immuables ordonnées, les boîtes, les emplacements, les lignées et l'audit dans une seule transaction. Un état inconnu n'est jamais zéro. Les anciens événements restent inchangés; aucun historique n'est reconstruit.

Les codes enfants sont imposés par le serveur : `BoxCodeNamespace` réserve les suffixes consécutifs au-dessus du maximum connu du préfixe réel, sans remplir les trous. Les autres écritures ordinaires de codes participent au même verrou via `Box.save()`; ce compteur est distinct de celui des souches.

Le repiquage ne crée aucun relevé biologique artificiel et ne consomme aucun créneau hebdomadaire. Les corrections ultérieures restent autorisées sans recalcul des instantanés. Si le parent atteint zéro, l'interface propose seulement ensuite une désactivation facultative avec les permissions et le service de cycle de vie existants, dans une transaction indépendante.

Le contrat détaillé, les consommateurs inspectés et la stratégie de concurrence sont dans [`../../backend/QUANTITATIVE_SUBCULTURE.md`](../../backend/QUANTITATIVE_SUBCULTURE.md).

Ne pas déduire une parenté d'une ressemblance de codes.

### Transfert inter-institutions

**V1 actuel :** l'import crée une nouvelle Box locale à destination; il ne déplace ni ne réaffecte la Box source et ne crée aucune relation `BoxLineage` de transfert.

**V2, fondation source intégrée :** la Phase 5 projette la lignée connue dans des nœuds/arêtes portables et fige un snapshot source en protocole 2.1 opt-in (défaut 2.0). Elle ne matérialise ni n'accepte une Box destination; aucune arête de continuation de transfert n'est créée par un workflow d'acceptation. Phase 6 reste future. Parentage/repiquage et arêtes de transfert sont des sémantiques distinctes. Une Strain identique n'implique pas une lignée; aucune parenté ne doit être inférée d'une ressemblance de codes. Les métadonnées de lignée inter-institutions ne doivent jamais donner accès aux fiches opérationnelles étrangères de Box.

## Règles de modification

- Valider organisation, permission, statut et zone côté serveur.
- Réutiliser les services de cycle de vie et de déplacement.
- Préserver périodes d'emplacement, mouvements, auteurs et audits.
- Ne pas rétablir implicitement une ancienne zone.
- Tester les transitions autorisées et refusées, les objets de deux institutions et les erreurs en milieu d'opération.
- Pour un changement concurrent, vérifier le comportement sur PostgreSQL QA isolé; SQLite ne suffit pas à conclure sur les verrous.

## Points d'entrée

- Modèles : `backend/apps/cultures/models.py`.
- Services : `backend/apps/cultures/services.py`.
- API et serializers : `backend/apps/cultures/api_views.py`, `backend/apps/cultures/serializers.py`.
- Déplacement UI : `frontend/src/components/MoveBoxModal.tsx`; correction inline : `LatestMovementBanner.tsx`, orchestration dans `App.tsx`.
- Diagnostic en lecture seule de l'historique d'emplacement : commande `diagnose_box_location_history` (`backend/apps/cultures/location_history_diagnostics.py`). Il constate sans réparer ni déduire de mouvement.
- Diagnostic en lecture seule par institution des candidats hebdomadaires, provenances non établies, liens mouvement-période, périodes ouvertes multiples, pointeurs et zones étrangères : commande `diagnose_box_movement_weeks` (`backend/apps/cultures/movement_week_diagnostics.py`).
- Correction de mouvement : `backend/apps/cultures/movement_corrections.py`.
- Inventaire et qualification : [`inventory.md`](inventory.md).
- Mesures acceptées selon le statut : [`measurements-integrity.md`](measurements-integrity.md).
- Transferts inter-institutions par CSV : [`../transferts_csv.md`](../transferts_csv.md).
