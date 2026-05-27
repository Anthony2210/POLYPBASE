# Intégration de la sonde iMinilide-A dans Polypbase

> Notes issues de l'analyse de la notice `iMinilide-A_NOTICE_UTILISATION_COMPLETE_Ed2.pdf`  
> Date : 2026-05-27

---

## 1. Présentation de la sonde

**Appareil :** Enregistreur autonome de températures iMinilide-A (Microlide)  
**Connexion réseau :** RJ45 Ethernet  
**Mesure :** 1 point par minute, plage -100°C à +250°C  
**Stockage interne :** 400 jours  
**Écran :** Tactile couleur 5"

---

## 2. Méthodes d'accès aux données

### 2.1 Page web locale (sans licence)
- Protocole : **HTTP, port 80**
- Format des données : **XML**
- URL : `http://<IP_sonde>/`
- Accessible uniquement sur le **réseau local** de l'aquarium
- Donne les **valeurs en temps réel** (pas d'historique de courbes)
- Permet d'acquitter les alarmes à distance

```python
import requests
response = requests.get("http://<IP_sonde>/")
# response.content contient du XML à parser
```

### 2.2 ModbusTCP (avec licence Clé Soft)
- Protocole : **ModbusTCP, port 502**
- Permet d'intégrer les données dans un système GTB/GTC
- Référence : `Définition_fonctions_Modbus.pdf` (Microlide)

### 2.3 Logiciel iMinilog (avec licence Clé Soft)
- Communication : HTTP port 80, données en XML
- Téléchargement automatique en local
- Windows uniquement

### 2.4 Cloud Microlide (avec licence Clé Soft)
- URL : www.iminilide.com
- Stocke les **10 derniers jours** de mesures
- Accessible depuis n'importe où (PC, smartphone)
- Acquittement d'alarmes à distance possible
- Rapports automatiques par mail

---

## 3. Ce qui est déjà prévu dans Polypbase

Le modèle `Probe` dans `backend/apps/measurements/models.py` a déjà un type `iminilide` :

```python
class ProbeType(models.TextChoices):
    IMINILIDE = "iminilide", "iMinilide"  # ← déjà présent
```

Les modèles de stockage existent également :
- `TemperatureMeasurement` — une mesure par point de temps
- `DailyTemperature` — agrégats journaliers (min/moy/max)
- `ThermalAnomaly` — anomalies thermiques détectées

Le fichier `backend/apps/measurements/services.py` est vide : c'est là que la logique d'intégration devra être écrite.

---

## 4. Ce qu'il reste à développer

### Étape 1 — Requête HTTP vers la sonde
```python
import requests
import xml.etree.ElementTree as ET

def fetch_probe_data(ip_address: str):
    response = requests.get(f"http://{ip_address}/", timeout=5)
    response.raise_for_status()
    return ET.fromstring(response.content)
```

### Étape 2 — Parser le XML
> ⚠️ Le format exact du XML n'est pas documenté dans la notice.
> Il faut inspecter la réponse sur la vraie sonde physique.

```python
def parse_temperature(xml_root) -> dict:
    # À compléter une fois le format XML connu
    # Exemple hypothétique :
    # <iminilide>
    #   <voie id="1" nom="Zone froide" valeur="12.5" unite="C" />
    # </iminilide>
    pass
```

### Étape 3 — Stocker en base
```python
from apps.measurements.models import TemperatureMeasurement, Probe
from django.utils import timezone

def store_temperature(probe: Probe, temperature_c: float, raw_data: dict):
    TemperatureMeasurement.objects.get_or_create(
        probe=probe,
        measured_at=timezone.now().replace(second=0, microsecond=0),
        defaults={
            "temperature_c": temperature_c,
            "source": "iminilide",
            "raw_data": raw_data,
        }
    )
```

### Étape 4 — Automatiser la collecte
Option simple : management command Django + tâche planifiée Windows

```powershell
# À lancer toutes les minutes via le Planificateur de tâches Windows
cd backend
uv run python manage.py poll_probes
```

---

## 5. Informations à relever sur la sonde physique

À faire lors d'une visite à l'aquarium :

| Information | Où la trouver sur la sonde |
|---|---|
| **Adresse IP** | Écran tactile → Config → Réseau |
| **Format XML brut** | Navigateur → `http://<IP>/` |
| **Nombre et nom des voies** | Écran tactile → Config → Voies |
| **Numéro de série** | Écran tactile → Config (bas de page) |
| **Présence d'une Clé Soft** | Écran tactile → Config → champ "Clé Soft" renseigné et grisé |

Commande PowerShell pour inspecter le XML une fois l'IP connue :
```powershell
Invoke-WebRequest -Uri "http://<IP_sonde>/" | Select-Object -ExpandProperty Content
```

---

## 6. Contrainte réseau importante

La sonde est sur le **réseau local de l'aquarium**.  
Elle est **inaccessible depuis l'extérieur** sans VPN ou configuration réseau spéciale.

| Situation | Accès à la sonde |
|---|---|
| Sur place à l'aquarium | ✅ Accès direct HTTP |
| À distance (avec Clé Soft) | ✅ Via cloud www.iminilide.com |
| À distance (sans Clé Soft) | ❌ Impossible |
| PC branché directement à la sonde | ⚠️ Fonctionne mais IP statique requise, pas de cloud |

---

## 7. Stratégie recommandée pour le développement

1. **Phase dev (à distance)** : utiliser les données fictives de `seed_demo_data`
2. **Phase test (sur place)** : inspecter le XML réel et écrire le parseur
3. **Phase production** : 
   - Si Clé Soft présente → Django interroge le cloud iMinilide
   - Sinon → Django tourne sur un serveur à l'aquarium et interroge la sonde en local

---

## 8. Fonctionnalités nécessitant une licence (Clé Soft)

| Fonction | Sans licence | Avec licence |
|---|---|---|
| Page web locale (temps réel) | ✅ | ✅ |
| Export USB (XLS, PDF, HTML) | ✅ | ✅ |
| ModbusTCP | ❌ | ✅ |
| Logiciel iMinilog | ❌ | ✅ |
| Cloud www.iminilide.com | ❌ | ✅ |
| Envoi de mails d'alarme | ❌ | ✅ |
