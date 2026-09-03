# Mesures et intégrité scientifique

## Invariant zéro

`0` est une mesure scientifique réelle. Une absence de relevé est représentée par l'absence d'une ligne; une valeur facultative absente reste `null`. Aucun serializer, filtre, graphique, import, export ou fallback frontend ne doit remplacer `0` par du vide ni transformer du vide en `0`.

Un relevé `0/0` existe donc réellement et reste distinct de « aucun relevé ». Tester explicitement les deux cas, y compris les mises à jour vers zéro et les agrégations.

## Relevés biologiques

`BiologicalMeasurement` associe une boîte et une date à des nombres de polypes, d'éphyrules et de strobiles, une salinité facultative, un état de culture, un indicateur d'attention, des notes et un auteur.

La base garantit au plus une ligne par `(box, measured_on)`. Le POST suit le comportement métier actuel :

1. ouvrir une transaction et verrouiller la boîte;
2. créer le relevé et répondre `201`, ou corriger la ligne de la même date et répondre `200`;
3. synchroniser les alertes et écrire l'audit dans la même transaction.

Deux créations concurrentes sont ainsi sérialisées selon la même règle que deux opérations séquentielles : une seule ligne finale, la seconde opération corrigeant la première. La contrainte DB reste la défense finale contre les doublons. Une correction explicite d'un relevé existant passe aussi par le PATCH prévu.

Une boîte inactive refuse un nouveau relevé. Un relevé historique déjà présent peut être corrigé par un utilisateur autorisé. Vérifier le statut sous verrou au moment de l'écriture, pas seulement dans l'interface.

La commande `check_biological_measurement_duplicates` effectue un diagnostic en lecture seule avant l'application d'une contrainte sur une base existante. Elle liste organisation, boîte, date, nombre et identifiants concernés. Elle ne choisit jamais quelle donnée scientifique conserver.

## Températures

`TemperatureMeasurement` stocke une mesure individuelle horodatée provenant d'une `Probe`; l'unicité porte sur sonde et horodatage. `DailyTemperature` stocke l'agrégat journalier d'une zone et est unique par `(thermal_zone, date)`.

La saisie manuelle ajoute une valeur à l'agrégat journalier; elle ne crée pas de ligne individuelle par saisie. L'API verrouille la `ThermalZone`, point de synchronisation existant même avant le premier agrégat, puis la ligne `DailyTemperature` lorsqu'elle existe. Deux premières saisies concurrentes et deux mises à jour concurrentes sont donc sérialisées.

Moyenne, minimum, maximum et compteur doivent représenter toutes les saisies acceptées. L'agrégat, les éventuels changements d'alerte et l'audit appartiennent à la même transaction; une erreur d'audit rollbacke l'opération. Ne pas modifier les règles de précision ou transformer les saisies en historique individuel sans décision métier.

Les modèles acceptent plusieurs types de sondes. Leur présence ne prouve pas qu'une ingestion automatique connectée fonctionne; vérifier les routes et services. Les documents de [`../sondes/`](../sondes/) décrivent des explorations et plans possibles, pas nécessairement le produit actif.

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
- alertes ou effets secondaires;
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
- rollback si alerte ou audit indispensable échoue;
- exactitude moyenne/min/max/count des températures.

## Points d'entrée

- Modèles : `backend/apps/measurements/models.py`.
- Endpoints et serializers utilisés par les boîtes : `backend/apps/cultures/api_views.py`, `backend/apps/cultures/serializers.py`.
- Tests de concurrence : modules `test_*_concurrency.py` dans `backend/apps/cultures/`.
- Affichage et saisie : composants de fiche boîte et graphiques sous `frontend/src/components/`.
- Contrat d'import/export : [`imports-exports.md`](imports-exports.md).
