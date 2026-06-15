# Polypbase

Polypbase is a web application for tracking jellyfish polyp cultures at the
Aquarium de Paris.

## Project Structure

```txt
POLYPBASE/
|-- backend/      # Django backend: database, API, auth, admin, exports
|-- frontend/     # React frontend: tablet and desktop user interface
|-- docs/         # Specifications, architecture notes, MCD, decisions
|-- scripts/      # One-off scripts such as Excel import and QR generation
|-- README.md
`-- .gitignore
```

## Python Environment

Python dependencies are managed with `uv`. The virtual environment is local to
each developer machine and must not be committed.

Install `uv` on Windows PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Create the local environment and install the locked dependencies:

```powershell
uv venv
uv sync
```

Add a Python package:

```powershell
uv add package-name
```

Add a development-only package:

```powershell
uv add --dev package-name
```

Commit `pyproject.toml` and `uv.lock` after changing dependencies. Do not edit
`uv.lock` manually.

## Backend

The backend uses Django. It stores the data, exposes API endpoints, manages
users and roles, provides the Django admin, and will generate server-side
exports.

Run backend commands from the `backend/` directory:

```powershell
cd backend
uv run python manage.py check
uv run python manage.py test
```

Create local demo data:

```powershell
cd backend
uv run python manage.py migrate
uv run python manage.py seed_demo_data
```

Start the Django development server:

```powershell
cd backend
uv run python manage.py runserver
```

The demo command is idempotent: it can be run again without creating duplicate
boxes or measurements. It creates three local users for development only:

- `demo_admin` / `polypbase-demo`
- `demo_lab` / `polypbase-demo`
- `demo_viewer` / `polypbase-demo`

## Interface Languages

The interface is French by default. Users can change their interface language
from `accounts/profile/`. For now, Polypbase supports:

- French (`fr`)
- English (`en`)

Template texts use Django's official translation tags:

```django
{% load i18n %}
{% trans "Mon profil" %}
```

Python user-facing texts use Django gettext:

```python
from django.utils.translation import gettext_lazy as _

label = _("Langue de l'interface")
```

Translation files live in:

```txt
backend/locale/fr/LC_MESSAGES/django.po
backend/locale/en/LC_MESSAGES/django.po
```

After editing a `.po` file, compile the messages:

```powershell
cd backend
uv run python ..\scripts\compile_django_messages.py
```

The script exists because the official Django `compilemessages` command needs
GNU gettext, which is not always installed on Windows.

Target apps:

- `accounts`: organization memberships, roles, and permissions.
- `organizations`: aquariums, partner structures, and sharing agreements.
- `taxonomy`: taxa, species, origins, and strains.
- `cultures`: boxes, thermal zones, QR tags, subculture events, lineage, and transfers.
- `measurements`: biological measurements, observations, temperature, salinity, and probes.
- `exports`: Excel imports, import rows, and CSV/XLSX export records.
- `audit`: alerts and action history.

Code identifiers and comments should use simple English. French is kept for
user-facing text and project documentation.

## Backend API

React should use the Django REST Framework endpoints under `/api/`. The first
stable endpoints are:

- `GET /api/health/`: public health check.
- `GET /api/dashboard/`: overview data for the first app screen.
- `GET /api/boxes/`: paginated box list, with `q`, `status`, and `organization` filters.
- `GET /api/boxes/<id>/`: box detail with measurement history and parent-child lineage.
- `GET /api/boxes/<id>/lineage/`: complete accessible family graph for interactive navigation.
- `GET /api/boxes/<id>/measurements/`: biological measurement history for one box.
- `POST /api/boxes/<id>/measurements/`: create or update the measurement for one date.
- `POST /api/boxes/<id>/subcultures/`: create a subculture event and one or more child boxes.
- `GET /api/thermal-zones/`: thermal zones with probes and latest readings.
- `GET /api/exports/options/`: values available in the cumulative export filters.
- `GET /api/exports/measurements.csv`: weekly biological tracking export in CSV format.
- `GET /api/profile/`: current user profile, organizations, and interface language.
- `PATCH /api/profile/`: update account preferences such as interface language.

The CSV export uses one row per ISO week. Each selected box produces three
columns for polyp count, ephyrae count, and temperature. Missing measurements
stay empty, while a recorded zero remains `0`. Filters for organizations,
species, strains, thermal zones, boxes, and dates can be combined.

French prototype endpoints such as `/api/boites/` and `/api/zones/` have been
removed. New frontend code should only use the English DRF routes above.

This repository now uses clean initial migrations for the split Django apps.
If a local development database was created before this refactor, recreate it
or migrate it carefully before relying on existing data.

## Frontend

The React frontend lives in `frontend/`.

Install frontend dependencies:

```powershell
cd frontend
npm install
```

Start the React development server:

```powershell
cd frontend
npm run dev
```

The usual local URL is:

```txt
http://127.0.0.1:5173/
```

The Django backend must also be running so React can call the `/api/...`
endpoints.

The export screen is available in the desktop interface. It is intentionally
hidden from the laboratory tablet navigation.

Check the production build:

```powershell
cd frontend
npm run build
```
