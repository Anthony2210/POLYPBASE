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
- `BoxMovement` représente un déplacement et son auteur.
- Une boîte inactive n'a pas d'emplacement courant, mais peut conserver un dernier emplacement historique affichable.
- Déplacer, désactiver ou réactiver une boîte ne doit jamais écraser les périodes précédentes.

L'emplacement courant et le dernier emplacement historique sont deux notions distinctes. Les interfaces et exports doivent choisir celle qui correspond à la question posée, et ne pas présenter l'historique comme l'état actuel.

## Transferts concurrents

Le déplacement d'une boîte est transactionnel. Le service verrouille la boîte et ses périodes ouvertes, puis met à jour pointeur courant, période, mouvement et audit de manière cohérente.

Le contrat d'intention utilise `expected_thermal_zone_id` :

- champ absent : requête invalide, car le client ne fournit pas l'état sur lequel repose son intention;
- valeur `null` : affirmation explicite que la boîte n'a pas d'emplacement courant;
- identifiant : zone que le client pense être l'emplacement courant.

Si l'état a changé entre l'affichage et la confirmation, l'API répond `409` avec le code `box_location_changed`. Le frontend ne doit pas annoncer un succès : il recharge la boîte et les zones. Ce mécanisme complète le verrouillage et ne remplace ni la permission ni le contrôle d'organisation.

## Repiquages et lignées

`SubcultureEvent` décrit un repiquage depuis une boîte parent. `BoxLineage` conserve la relation parent-enfant, notamment créée par ce flux. L'historique de lignée doit survivre aux changements de statut et d'emplacement.

Les nouveaux repiquages sont saisis au moment réel de l'opération, avec horodatage serveur. Chaque allocation enfant est un entier explicite (y compris zéro), `null` ou omise. Si toutes sont connues, l'instantané avant/réparti/après est exact. Sinon, le total réparti et l'après sont `null` : aucun effet quantitatif parent n'est appliqué, et son état autoritaire précédent reste courant. Le parent doit toujours avoir un comptage connu; la somme des allocations connues ne peut pas le dépasser, même en saisie partielle. Les enfants connus reçoivent leur état initial individuel; les inconnus n'ont aucun état initial scientifique inventé. Le service verrouille le parent, vérifie puis incrémente sa révision même en saisie partielle (invalidation d'intention, pas transition), et conserve l'instantané avant, les allocations immuables ordonnées, les boîtes, les emplacements, les lignées et l'audit dans une seule transaction. Un état inconnu n'est jamais zéro. Les anciens événements restent inchangés; aucun historique n'est reconstruit.

Les codes enfants sont imposés par le serveur : `BoxCodeNamespace` réserve les suffixes consécutifs au-dessus du maximum connu du préfixe réel, sans remplir les trous. Les autres écritures ordinaires de codes participent au même verrou via `Box.save()`; ce compteur est distinct de celui des souches.

Le repiquage ne crée aucun relevé biologique artificiel et ne consomme aucun créneau hebdomadaire. Les corrections ultérieures restent autorisées sans recalcul des instantanés. Si le parent atteint zéro, l'interface propose seulement ensuite une désactivation facultative avec les permissions et le service de cycle de vie existants, dans une transaction indépendante.

Le contrat détaillé, les consommateurs inspectés et la stratégie de concurrence sont dans [`../../backend/QUANTITATIVE_SUBCULTURE.md`](../../backend/QUANTITATIVE_SUBCULTURE.md).

Ne pas déduire une parenté d'une ressemblance de codes.

### Transfert inter-institutions

**V1 actuel :** l'import crée une nouvelle Box locale à destination; il ne déplace ni ne réaffecte la Box source et ne crée aucune relation `BoxLineage` de transfert.

**Direction future, non implémentée :** parentage/repiquage et arêtes de transfert sont des sémantiques distinctes. Une Strain identique n'implique pas une lignée; aucune parenté ne doit être inférée d'une ressemblance de codes. Les métadonnées de lignée inter-institutions ne doivent jamais donner accès aux fiches opérationnelles étrangères de Box.

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
- Transfert UI : `frontend/src/components/MoveBoxModal.tsx`.
- Inventaire et qualification : [`inventory.md`](inventory.md).
- Mesures acceptées selon le statut : [`measurements-integrity.md`](measurements-integrity.md).
- Transferts inter-institutions par CSV : [`../transferts_csv.md`](../transferts_csv.md).
