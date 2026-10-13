# Mesures et intégrité scientifique

## Invariant zéro

`0` est une mesure scientifique réelle. Une absence de relevé est représentée par l'absence d'une ligne; une valeur facultative absente reste `null`. Aucun serializer, filtre, graphique, import, export ou fallback frontend ne doit remplacer `0` par du vide ni transformer du vide en `0`.

`BiologicalMeasurement.strobila_count` accepte `NULL` : « non mesuré » pour les relevés historiques dont la source ne mesure pas les strobiles (import 2026). `0` reste une mesure réelle et les zéros existants ne sont jamais réécrits. La saisie courante est inchangée (défaut `0`, `null` refusé par l'API et le formulaire). Total du tableau de bord : `null` si des relevés existent sans aucune valeur connue.

Un relevé `0/0` existe donc réellement et reste distinct de « aucun relevé ». Tester explicitement les deux cas, y compris les mises à jour vers zéro et les agrégations.

## Relevés biologiques

`BiologicalMeasurement` associe une boîte et une date à des nombres de polypes, d'éphyrules et de strobiles, une salinité facultative, un état de culture, un indicateur d'attention, des notes et un auteur.

La base garantit au plus une ligne par boîte et semaine ISO. Le POST interactif suit le comportement métier suivant :

1. ouvrir une transaction et verrouiller la boîte;
2. créer le relevé et répondre `201` si la semaine est libre;
3. répondre `409 measurement_week_conflict` sans mutation si un relevé existe déjà dans la semaine, y compris à la même date;
4. écrire l'audit dans la même transaction, sans génération ni synchronisation d'alertes.

Deux créations concurrentes sont ainsi sérialisées selon la même règle que deux opérations séquentielles : une seule ligne finale et un conflit pour la seconde. La contrainte DB reste la défense finale contre les doublons. Toute correction d'un relevé existant passe exclusivement par le PATCH prévu.

Une boîte inactive refuse un nouveau relevé. Un relevé historique déjà présent peut être corrigé par un utilisateur autorisé. Vérifier le statut sous verrou au moment de l'écriture, pas seulement dans l'interface.

La commande `check_biological_measurement_duplicates` effectue un diagnostic en lecture seule avant l'application d'une contrainte sur une base existante. Elle liste organisation, boîte, date, nombre et identifiants concernés. Elle ne choisit jamais quelle donnée scientifique conserver.

## Observateur et origine des relevés

La fondation backend distingue l'attribution scientifique du dernier acteur d'écriture :

- `user` conserve son contrat historique : acteur de création, puis acteur de la dernière correction ordinaire. `user_identity` reste inchangé; les audits ENTRY/UPDATE identifient leurs acteurs respectifs.
- `observer_status` vaut `known`, `not_recorded` ou `legacy_unestablished`. `original_observer` est une référence de compte facultative, distincte de `user`.
- `origin` vaut `direct_entry`, `file_import` ou `legacy_unclassified`. `import_format` est vide hors import fichier; seul `csv` est actuellement prévu. `imported_by` est une référence de compte facultative, jamais un observateur implicite.

Le POST ordinaire capture une seule fois le compte authentifié comme observateur connu et établit l'origine `direct_entry`. Le PATCH conserve ces champs, y compris pour un relevé legacy ou un compte observateur supprimé. Les champs de provenance ne font pas partie de l'allowlist d'écriture ordinaire : les valeurs client sont ignorées, comme les anciens champs d'identité non modifiables. Aucun sélecteur d'observateur, endpoint d'attribution ou applicateur d'import n'est ajouté.

La migration `0008_observer_origin` ajoute uniquement des champs et contraintes : tous les relevés existants restent `legacy_unestablished` / `legacy_unclassified`, avec comptes nuls et format vide. Elle ne copie jamais `user`, ne relit aucune source et n'appelle pas le `save()` courant. Les chemins historiques CSV/2026, transfert v1, seed et administration conservent ces défauts pour leurs créations; ils ne constituent pas une preuve automatique d'origine ou d'observateur. Les champs sont non éditables dans les formulaires/admin générés.

Les nouvelles références de compte, ou leur remplacement, sont validées contre l'appartenance à l'institution de la boîte lors de `clean()` et `save()`. Le service ordinaire exige toujours le rôle actif autorisé. Une référence historique inchangée ne bloque pas une correction si le membership a ensuite disparu. Les écritures techniques par `QuerySet.update()`, `bulk_create()` ou SQL contournent cette validation relationnelle Python; tout futur applicateur doit donc appliquer explicitement la portée institutionnelle et ses permissions.

### Contrat de lecture et conservation d'identité

Le serializer existant ajoute `observer_status`, `origin`, `import_format`, `original_observer_identity`, `original_observer_display`, `imported_by_identity` et `imported_by_display`. Les identités sont celles des comptes vivants; l'affichage réutilise prénom + nom en majuscules, puis email, jamais le username technique. Aucun identifiant de compte supplémentaire ni snapshot personnel n'est exposé ou stocké.

La suppression d'un compte met ses références à NULL sans changer le statut d'attribution ou l'origine, ni la révision scientifique. `known` avec une référence NULL signifie une attribution établie dont le compte n'est plus disponible, **pas** un nom externe stocké. Ne pas le requalifier implicitement en `not_recorded` ou en legacy. Aucun nom/email n'est conservé par cette fondation après suppression.

Pour le futur frontend, `not_recorded` correspond à « Observateur non renseigné » / « Observer not recorded ». La ligne « Importé par » est réservée à `origin=file_import` et utilise le format réellement enregistré; elle est absente pour `direct_entry`. Ne pas déduire une origine CSV d'une date ancienne ou d'un compte absent. Le traitement visuel de `legacy_unestablished` et de `known` sans compte reste à préciser; aucune présentation frontend n'est modifiée ici.

La persistance des noms d'observateurs externes et d'instantanés de comptes supprimés attend une politique produit explicite de conservation des données personnelles. Aucun faux compte n'est créé. L'import applicator et ses métadonnées de source fiables restent hors périmètre. Le planner existant conserve son contrat versionné : son ancien `MeasurementBaseline.observer_id` représente encore `measurement.user_id`, pas le nouvel observateur scientifique; sa migration contractuelle sera une tâche séparée.

## Fondation de précision temporelle (sans activation semaine seule)

`temporal_precision` est un champ backend non éditable, exposé en lecture seule par le serializer de relevés :

- `legacy_unverified` : une date est stockée, mais son statut de date réelle d'observation n'a pas été établi par cette fondation. Ce statut ne prouve ni une date erronée ni une précision semaine seule. Il inclut les lignes existantes, même d'origine `direct_entry`, et les créations techniques/imports hors service ordinaire.
- `exact_date` : le service de création ordinaire a reçu une date d'observation explicite validée. Il s'agit du contrat de saisie, pas d'une vérification indépendante de la véracité scientifique de la date.

La migration additive `0009_temporal_precision`, après `0008_observer_origin`, attribue le défaut `legacy_unverified` sans inférence sur le jour de semaine, l'origine, l'auteur ou l'observateur. Aucun `RunPython` ni appel au `save()` courant : dates, clés ISO, IDs, comptes, zéros, NULL, séquences, timestamps, révisions de boîte, audits et snapshots de repiquage sont conservés. Même un lundi synthétique 2026 n'est ni certifié ni converti.

Le POST ordinaire exige toujours `measured_on` et établit `exact_date` côté serveur. Le champ n'est pas dans l'allowlist d'écriture; les valeurs client sont ignorées comme pour les champs de provenance. Le PATCH partiel, vide ou corrigeant explicitement la date conserve la précision existante, l'observateur et l'origine. **Question produit ouverte :** une correction explicite de date legacy suffit-elle à établir une observation exacte, et sous quelles preuves/permissions ? Aucun changement de précision implicite ni endpoint de conversion n'est ajouté.

`measured_on` reste NOT NULL; `week_start`, l'unicité `(box, week_start)`, le calcul ISO dans `save()`, les tris scientifiques, la priorité du résolveur et les audits restent inchangés. La contrainte `measurement_temporal_precision_supported` n'autorise que les deux valeurs ci-dessus, y compris hors validation Python : `iso_week` est refusé même accompagné d'un lundi fabriqué. Cette phase conserve l'interprétation runtime historique des dates legacy; elle ne résout pas rétroactivement leur incertitude.

### Prérequis exacts pour une future activation semaine seule

Une future ligne `iso_week` doit porter `measured_on=NULL` et une clé de semaine ISO explicite (`week_start`, lundi utilisé uniquement comme clé, jamais comme date d'observation). L'affichage sera par exemple « Semaine 40 · 2026 », sans jour inventé. Cette représentation n'est **pas persistable** dans cette phase.

Avant de l'activer, il faut :

1. Faire évoluer la nullabilité de `measured_on`, les choix et la contrainte de précision; imposer les combinaisons exact/legacy/semaine autorisées et valider les clés ISO. Découpler `save()`/`week_start_for()` d'une date obligatoire tout en conservant l'unicité hebdomadaire. Définir les transitions de précision et leur audit sans inférence ni conversion historique automatique.
2. Décider puis implémenter la résolution scientifique des observations connues seulement à la semaine, notamment avant/après un repiquage au sein de la même semaine. Adapter `cultures/polyp_state.py`, ses requêtes/préchargements, le calcul et les snapshots des repiquages, sans imposer arbitrairement une priorité.
3. Adapter les contrats API/serializers, corrections, historique/timeline, latest measurement, filtres/fraîcheur d'inventaire, Actions/audit et formulaires/admin. Ils utilisent encore une date obligatoire ou des clés fondées sur cette date.
4. Adapter les types et consommateurs frontend (saisie, dates, tris, graphiques, inventaire, étiquettes), ainsi que les exports (périodes, emplacement historique, température journalière). Définir les règles des semaines chevauchant une période ou un mouvement, au lieu de choisir lundi.
5. Versionner si nécessaire les baselines/fingerprints du planner : la baseline actuelle exige une date et ne contient pas la précision. Définir les comparaisons legacy/exact/semaine et un applicateur d'import autorisé, institutionnel, verrouillé, atomique et audité, préservant absence/NULL/0 et la provenance Phase 4A. Examiner les imports historiques et les transferts séparément, sans réinterpréter leurs conventions existantes.
6. Valider migrations, contraintes, transactions et concurrence sur PostgreSQL jetable isolé, puis les consommateurs et frontières temporelles. Ne retirer le blocage `week_precision_persistence_required` du planner qu'une fois l'ensemble prêt.

Aucun de ces changements futurs, ni changement d'UI, graphiques, exports ou import historique, n'est implémenté par la fondation.

## État courant et repiquage quantitatif

`latest_measurement` reste le dernier relevé ordinaire et conserve ses règles de fraîcheur et de semaine ISO. `current_polyp_state` est une notion distincte : le serveur résout une observation absolue, un résultat absolu de repiquage parent ou l'allocation initiale absolue d'un enfant. Il ne soustrait jamais toutes les allocations historiques d'un dernier relevé.

Un repiquage quantitatif ne crée aucun `BiologicalMeasurement`, ni éphyrule ou strobile fictif. Il peut coexister avec un relevé ordinaire dans la même semaine, pour le parent comme pour les enfants. Les écritures ordinaires et les repiquages partagent le verrou de boîte et une séquence de sources; les révisions opaques invalident les intentions périmées. Corriger un relevé ancien ne réécrit ni l'instantané ni les allocations du repiquage accepté. L'historique typé distingue les relevés éditables des événements de repiquage non éditables.

Un repiquage partiel (au moins une allocation enfant `null` ou omise) conserve l'avant connu mais a un total et un après `null`. Sa révision invalide les intentions sans transition scientifique. Tous les chemins du résolveur, y compris les sous-requêtes de priorité des relevés et les préchargements, ignorent ces événements comme sources parent et les allocations inconnues comme initialisations enfant. L'historique conserve l'événement partiel sans point quantitatif après; un enfant inconnu n'a pas d'entrée d'initialisation inventée. Zéro reste une initialisation connue. Les règles hebdomadaires et de correction restent inchangées.

Voir [`boxes-lifecycle-locations.md`](boxes-lifecycle-locations.md) et le contrat backend pour les détails du repiquage et du résolveur.

## Températures

`TemperatureMeasurement` stocke une mesure individuelle horodatée provenant d'une `Probe`; l'unicité porte sur sonde et horodatage. `DailyTemperature` stocke l'agrégat journalier d'une zone et est unique par `(thermal_zone, date)`.

La saisie manuelle ajoute une valeur à l'agrégat journalier; elle ne crée pas de ligne individuelle par saisie. L'API verrouille la `ThermalZone`, point de synchronisation existant même avant le premier agrégat, puis la ligne `DailyTemperature` lorsqu'elle existe. Deux premières saisies concurrentes et deux mises à jour concurrentes sont donc sérialisées.

Moyenne, minimum, maximum et compteur doivent représenter toutes les saisies acceptées. L'agrégat et l'audit appartiennent à la même transaction; une erreur d'audit rollbacke l'opération. Ne pas modifier les règles de précision ou transformer les saisies en historique individuel sans décision métier.

Les modèles acceptent plusieurs types de sondes. Leur présence ne prouve pas qu'une ingestion automatique connectée fonctionne; vérifier les routes et services. Les documents de [`../sondes/`](../sondes/) décrivent des explorations et plans possibles, pas nécessairement le produit actif.

## Présentation factuelle et abandon des alertes

Les Alertes ne sont plus actives. Les comptages, températures, agrégats et écarts à une consigne restent des faits mesurés ou calculés, sans avertissement opérationnel ou biologique inféré. Une baisse de polypes, une valeur zéro, une salinité absente ou un écart de température ne déclenche pas d'alerte. Une consigne n'est pas un seuil d'alerte.

L'abandon des alertes ne retire ni les validations de saisie, ni les permissions, ni l'audit des mesures. `AuditLog` et Actions restent préservés. Le stockage historique `Alert` reste dormant, avec ses migrations et données, dans l'attente d'une décision distincte explicitement autorisée et non destructive; voir [`product-architecture.md`](product-architecture.md).

## Observations et notes

`Observation` est un modèle daté, typé, avec notes et auteur. Il est administrable et utilisé dans certains jeux de démonstration ou tests, mais aucune route DRF dédiée n'est actuellement déclarée. Les notes d'un relevé biologique ne sont pas des `Observation`.

Ne pas afficher ou filtrer une « dernière observation » en supposant qu'elle est alimentée par l'API. Vérifier d'abord le parcours réel. Cette distinction explique notamment pourquoi l'Inventaire ne doit pas utiliser ce modèle comme indicateur sans fonctionnalité dédiée.

## Intégrité transactionnelle

Pour toute écriture de mesure, inspecter ensemble :

- objet et date ciblés;
- permission et organisation de la boîte, zone ou sonde;
- validations scientifiques;
- unicités et contraintes DB;
- objets verrouillés et ordre des verrous;
- effets secondaires autorisés, sans génération ni synchronisation d'alertes;
- `AuditLog` et réponse API;
- rollback en cas d'échec.

Une contrainte protège l'état final mais ne fournit pas à elle seule une réponse API cohérente. Un verrou ne remplace pas une contrainte lorsque l'invariant doit rester vrai hors endpoint. Les conclusions de concurrence doivent être reproduites sur PostgreSQL QA isolé sous le moteur pertinent.

## Tests à préserver

- valeurs positives, `0`, `0/0`, absence et mise à jour vers zéro;
- création puis correction du même jour;
- contrainte directe hors API;
- opérations concurrentes sur PostgreSQL;
- refus des objets d'une autre institution;
- statut de boîte autorisant ou refusant l'écriture;
- rollback si l'écriture de l'audit échoue;
- absence de génération, synchronisation ou résolution d'alertes lors des créations et corrections de relevés et des saisies de température;
- préservation des données historiques `Alert` et des entrées `AuditLog`, sans les traiter comme des alertes actives;
- exactitude moyenne/min/max/count des températures.

## Points d'entrée

- Modèles : `backend/apps/measurements/models.py`.
- Endpoints et serializers utilisés par les boîtes : `backend/apps/cultures/api_views.py`, `backend/apps/cultures/serializers.py`.
- Tests de concurrence : modules `test_*_concurrency.py` dans `backend/apps/cultures/`.
- Affichage et saisie : composants de fiche boîte et graphiques sous `frontend/src/components/`.
- Contrat d'import/export : [`imports-exports.md`](imports-exports.md).
