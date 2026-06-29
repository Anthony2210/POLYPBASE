# Plan — Relier l'API des sondes iMinilide à Polypbase

> Complète [notes_integration_iminilide.md](notes_integration_iminilide.md).
> Objectif : récupérer automatiquement les températures des sondes iMinilide et les
> stocker dans Polypbase, en restant développable à distance (sans sonde physique).

---

## 0. État des lieux (ce qui existe déjà)

| Élément | État |
|---|---|
| Modèle `Probe` (type `iminilide`) | ✅ présent — [models.py:70](../../backend/apps/measurements/models.py#L70) |
| `TemperatureMeasurement` (+ contrainte d'unicité `probe`/`measured_at`) | ✅ présent |
| `DailyTemperature`, `ThermalAnomaly` | ✅ présents |
| Données de démo (sondes + mesures) | ✅ [seed_demo_data.py](../../backend/apps/cultures/management/commands/seed_demo_data.py) |
| `services.py` | ⬜ vide — toute la logique va ici |
| Commande `poll_probes` | ⬜ à créer |
| Dépendance `requests` | ⚠️ utilisée dans les notes mais **absente de [pyproject.toml](../../pyproject.toml#L5)** |
| Champ d'adresse réseau sur `Probe` | ❌ **manquant** (`ip_address`/`host`) — bloquant pour interroger la sonde |

---

## Étape 1 — Préparer le modèle et les dépendances

**1a. Ajouter les champs de connexion sur `Probe`** (sinon impossible de savoir quelle IP interroger).

```python
# measurements/models.py — dans class Probe
ip_address = models.GenericIPAddressField(null=True, blank=True)
http_port  = models.PositiveIntegerField(default=80)
```
→ `uv run python manage.py makemigrations measurements && migrate`

**1b. Déclarer `requests`** dans `pyproject.toml` :
```toml
"requests>=2.31",
```
→ `uv add requests` (ou édition manuelle + `uv sync`).

---

## Étape 2 — Couche service (`measurements/services.py`)

Découpage en 3 fonctions indépendantes pour pouvoir tester chacune isolément
et **remplacer le parseur plus tard** une fois le XML réel connu.

```
fetch_probe_xml(probe)      → renvoie la racine XML (I/O réseau, mockable)
parse_iminilide_xml(root)   → renvoie [{channel, temperature_c, measured_at}, ...]
store_measurements(probe, readings)  → écrit en base avec déduplication
```

- **`fetch_probe_xml`** : `requests.get(f"http://{ip}:{port}/", timeout=5)`,
  `raise_for_status()`, `ET.fromstring(resp.content)`. Lève une exception réseau propre.
- **`parse_iminilide_xml`** : ⚠️ **stub** tant que le format réel n'est pas connu
  ([notes §2.1 et §4 étape 2](notes_integration_iminilide.md#L84)). Isoler le parseur
  derrière cette signature stable : seul son corps changera après la visite sur place.
- **`store_measurements`** : `get_or_create` sur `(probe, measured_at)` pour respecter la
  contrainte d'unicité existante ; remplit `source="iminilide"` et `raw_data`.
  Renvoie le nombre de mesures réellement créées.

Fonction d'orchestration `poll_probe(probe)` = fetch → parse → store, qui **attrape et
logge** les erreurs réseau/parse par sonde (une sonde HS ne doit pas bloquer les autres).

---

## Étape 3 — Commande de collecte (`poll_probes`)

`measurements/management/commands/poll_probes.py` :

- itère sur `Probe.objects.filter(is_active=True, probe_type="iminilide")`
  (filtrables par `--probe <code>` ou `--organization`),
- appelle `poll_probe(probe)`, agrège le total créé / erreurs,
- affiche un résumé via `self.stdout` / `self.stderr`.

Lancement manuel : `uv run python manage.py poll_probes`.

**Planification (1 relevé/min côté sonde)** : Planificateur de tâches Windows déclenchant
la commande toutes les 1–5 min ([notes §4 étape 4](notes_integration_iminilide.md#L115)).
Pas besoin de Celery pour ce volume.

---

## Étape 4 — Agrégats journaliers et anomalies (optionnel, après la collecte)

À écrire dans `services.py`, indépendant de la collecte :

- **`rebuild_daily_temperature(thermal_zone, date)`** : agrège les `TemperatureMeasurement`
  des sondes de la zone → `DailyTemperature` (min/moy/max + `measurement_count`),
  `update_or_create` sur `(thermal_zone, date)`.
- **`detect_thermal_anomalies(thermal_zone)`** : compare aux `target_temperature_c` de la
  zone ([cultures/models.py:23](../../backend/apps/cultures/models.py#L23)) ; ouvre/ferme
  un `ThermalAnomaly` selon un seuil d'écart + niveau (`warning`/`critical`).

Peut tourner en fin de `poll_probes` ou via une commande séparée `rebuild_temperatures`.

---

## Étape 5 — Tests (sans sonde physique)

`measurements/tests.py` :

- `parse_iminilide_xml` sur un **XML d'exemple en fixture** (string) → vérifie le mapping.
- `store_measurements` → vérifie création + **idempotence** (rejouer ne duplique pas).
- `poll_probe` avec `requests.get` **mocké** (`unittest.mock`) → pas d'I/O réseau réel.
- agrégats : insérer quelques mesures → vérifier min/moy/max de `DailyTemperature`.

---

## Étape 6 — Bascule sur la vraie sonde (sur place à l'aquarium)

1. Relever IP / format XML / nom des voies ([notes §5](notes_integration_iminilide.md#L126)).
2. Compléter **uniquement** le corps de `parse_iminilide_xml` + la fixture de test.
3. Renseigner `ip_address` sur les `Probe` réelles (admin Django).
4. Activer la tâche planifiée.
5. Prod : si Clé Soft → viser le cloud iMinilide ; sinon serveur sur le réseau local
   ([notes §7](notes_integration_iminilide.md#L159)).

---

## Ordre de réalisation conseillé

1. Étape 1 (modèle + dep) → migration
2. Étape 2 (services, parseur en stub)
3. Étape 3 (commande `poll_probes`)
4. Étape 5 (tests sur mock)
5. Étape 4 (agrégats/anomalies)
6. Étape 6 (sur place, plus tard)

Étapes 1→5 sont 100 % réalisables **maintenant, à distance**, sur données mockées.
Seule l'étape 6 nécessite la sonde physique.
