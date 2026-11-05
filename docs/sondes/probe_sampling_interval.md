# Cadence de référence d'une sonde

La commande `backend/apps/measurements/management/commands/set_probe_sampling_interval.py`
configure uniquement `Probe.sampling_interval_seconds`, utilisé pour la fraîcheur
du contrôle thermique récent (`âge <= 3 × cadence`). Les seules valeurs acceptées
sont **60, 600 et 900 secondes**. Elle ne modifie ni la fréquence matérielle, ni la
collecte cloud, ni les mesures, tranches ou agrégats historiques. Elle fonctionne
pour tous les types de sondes, actives ou inactives, sans réactiver une sonde.

## Autorité et identité

Comme `set_team_membership_visibility` et `set_institution_responsable`, cette
commande opérationnelle exige un **acteur Django superutilisateur actif** désigné
par `--actor-user-id`. Un rôle Admin, Responsable, Technicien, Lecteur ou `is_staff`
ne suffit pas. Le break-glass technique existant n'exige pas de membership dans
l'institution; aucun nouveau privilège produit n'est défini.

Chaque invocation vise une seule sonde : `--organization-id`, `--zone-id`,
`--probe-id` et `--probe-code` sont obligatoires. Les deux relations institutionnelles
(zone et sonde) et l'affectation à la zone sont contrôlées côté serveur. Le code est
une garde supplémentaire exacte et sensible à la casse, sans normalisation ni
sélection par nom, préfixe, voie externe ou groupe. Toute incohérence est refusée.
`--reason` est obligatoire et ne peut pas être vide; ne pas y placer de secret.

## Procédure

Depuis `backend/`, utiliser l'environnement approuvé pour la cible et relever les
identifiants exacts dans un inventaire autorisé. Exemple **fictif**, à adapter :

```sh
uv run python manage.py set_probe_sampling_interval \
  --organization-id 12 --zone-id 34 --probe-id 56 --probe-code 'Probe G' \
  --sampling-interval-seconds 600 --actor-user-id 78 \
  --reason 'Cadence de dix minutes confirmée sur la sonde'
```

Sans `--apply`, la commande valide acteur et cible puis affiche les valeurs avant
et après, sans écriture ni audit. Après vérification et autorisation opérationnelle,
répéter exactement la commande en ajoutant `--apply`. Toute mutation de production
reste soumise à l'autorisation explicite et à la procédure d'exploitation existante;
ce document n'est pas une autorisation.

La transaction verrouille `Organization` → `ThermalZone` → `Probe` et relit la
valeur courante verrouillée. Seule la cadence est enregistrée. L'audit `UPDATE`
porte l'organisation, l'acteur, `object_type="probe"`, le code, les identifiants
sonde/zone, les valeurs réelles `before`/`after`, la raison, `source="management_command"`
et `command="set_probe_sampling_interval"`. Un échec d'audit annule la modification.
Une valeur déjà identique est un non-changement, sans sauvegarde ni audit, y compris
avec `--apply`. Pour revenir à une ancienne cadence, refaire la même procédure
avec l'ancienne valeur et une nouvelle raison; l'historique d'audit reste conservé.

La nouvelle cadence prend effet à l'évaluation serveur suivante; cette commande ne
force pas d'actualisation frontend.

## Validation isolée

Depuis `backend/` (syntaxe shell POSIX) :

```sh
DJANGO_DEBUG=1 POSTGRES_DB='' POLYPBASE_TEST_POSTGRES=0 uv run python manage.py test apps.measurements.test_set_probe_sampling_interval --settings=config.test_settings
```

Ces tests utilisent SQLite en mémoire : ils couvrent les arguments, permissions,
identités exactes, simulations, cadences, audit, idempotence, rollback et préservation
des autres données. Ils ne prouvent pas la sérialisation concurrente des verrous
PostgreSQL; celle-ci exige un environnement PostgreSQL QA isolé.
