# Polypbase

Polypbase is a web application for tracking jellyfish polyp cultures at the
Aquarium de Paris, with organization-scoped workspaces and backend permissions.

For the dated integration/local-work snapshot, see
[`POLYPBASE_CURRENT_STATE.md`](POLYPBASE_CURRENT_STATE.md). Start domain navigation
with [`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md); dated audits are historical.

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

Create the local environment and install the locked server dependencies:

```powershell
uv sync --locked
```

Install the additional analysis and notebook environment only on machines that
need it:

```powershell
uv sync --group analysis
```

Add a Python package:

```powershell
uv add package-name
```

Add an analysis or notebook package:

```powershell
uv add --group analysis package-name
```

Commit `pyproject.toml` and `uv.lock` after changing dependencies. Do not edit
`uv.lock` manually.

## Backend

The backend uses Django. It stores the data, exposes API endpoints, manages
users and roles, provides authenticated API administration, and generates
server-side weekly CSV exports.

Run backend commands from the `backend/` directory:

```powershell
cd backend
uv run python manage.py check --settings=config.preflight_settings
if ($LASTEXITCODE -ne 0) { throw 'Django check failed' }
uv run python manage.py makemigrations --check --dry-run --settings=config.preflight_settings
```

Preflight settings always select in-memory SQLite, even if the PostgreSQL test
opt-in is inherited. They are not persistent development settings. For targeted
tests and for starting Django against a verified local database, follow the
[development guide](docs/context/development-deployment.md).

`seed_demo_data` is for a verified disposable database with synthetic data only,
never a restored database, Neon, production, or any shared database. The command
requires `DEBUG` or explicit `--allow-non-debug`; neither establishes isolation.
The separate [isolated demo mode](docs/isolated-demo.md) has its own settings and
database requirements and is not the ordinary Vite/Django development setup.

## Interface Languages

The interface is French by default. Users can change their interface language
from `accounts/profile/`. Polypbase supports:

- French (`fr`)
- English (`en`)
- Partial Japanese (`ja`), with English fallback for untranslated or deferred keys.

Historical Japanese translations validated by Takeru are preserved and reused
only where compatible with current FR/EN. The 59 independently AI-reviewed
React additions have separate `AI_REVIEWED` / `ja_ai_reviewed` provenance:
they are not human-approved or Takeru-validated and replace no historical
Japanese. See the [Japanese provenance and fallback contract](docs/translation-review/japanese/README.md).

The React interface catalog is centralized in `frontend/src/i18n/`. French is
the reference catalog, and TypeScript verifies that the English catalog has
the same keys during `npm run typecheck`.

Biological reference translations are separate from interface text and use
`CONTENT_LANGUAGES` in `backend/config/settings.py`. The active Species workspace
manages institutional scientific names and WoRMS references, not the disabled
shared References forms.

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
backend/locale/ja/LC_MESSAGES/django.po
```

After editing a `.po` file, compile the messages:

```powershell
cd backend
uv run python ..\scripts\compile_django_messages.py
```

The script exists because the official Django `compilemessages` command needs
GNU gettext, which is not always installed on Windows. It also builds the
effective Japanese MO using validated Japanese, then approved English, then
the source msgid when English is unavailable; the generated JA PO retains
Japanese provenance only. Do not edit the generated Japanese catalogs by hand.

Target apps:

- `accounts`: organization memberships, roles, and permissions.
- `organizations`: aquariums, partner structures, and sharing agreements.
- `taxonomy`: taxa, species, origins, and strains.
- `cultures`: boxes, thermal zones, QR tags, subculture events, lineage, and transfers.
- `measurements`: biological measurements, observations, temperature, salinity, and probes.
- `exports`: imports and CSV/XLSX export records.
- `sharing`: external read-only Box grants, invitations and frozen captures.
- `audit`: action history (`AuditLog`); historical `Alert` storage retained dormant.

Code identifiers and comments should use simple English. French is kept for
user-facing text and project documentation.

## Product decision: Alerts abandoned

Alerts are no longer an active feature: automatic generation/synchronization,
alert displays, and resolution workflows are outside the active product
contract. Measurements remain factual, with no inferred operational or
biological warnings from counts, trends, missing values, or target deviations.
Input validation and permissions remain in force.

Actions and `AuditLog` are preserved, including historical alert-related entries.
The `Alert` model, table, existing data, and migrations remain dormant pending a
separately authorized, non-destructive decision. This product decision does not
authorize data deletion or migration rewrites. See
[`docs/context/product-architecture.md`](docs/context/product-architecture.md).

## Backend API

Django REST Framework routes are registered in `backend/config/api_urls.py`.
Selected endpoints and their boundaries:

- `GET /api/health/`: public health check.
- `GET /api/dashboard/`: overview data for the first app screen.
- `GET /api/boxes/`: paginated box list, with `q`, `status`, and `organization` filters.
- `GET /api/boxes/<id>/`: box detail with measurement history and parent-child lineage.
- `GET /api/boxes/<id>/lineage/`: complete accessible family graph for interactive navigation.
- `GET /api/boxes/<id>/measurements/`: biological measurement history for one box.
- `POST /api/boxes/<id>/measurements/`: create a reading in a free ISO week; occupied week returns `409 measurement_week_conflict`, even on the same date.
- `PATCH /api/boxes/<id>/measurements/<measurement_id>/`: authorized correction, preserving original observer and audit history.
- `POST /api/boxes/<id>/move/`: move with explicit expected current location; at most one ordinary movement per Paris ISO week.
- `PATCH /api/boxes/<id>/movements/<movement_id>/`: audited correction with required expected revision, not a new movement.
- `POST /api/boxes/<id>/subcultures/`: create a subculture event and one or more child boxes.
- `GET /api/thermal-zones/`: thermal zones with probes and latest readings.
- `POST/PATCH /api/boxes/<id>/dna-validation/` and `POST /api/boxes/<id>/dna-validation/revoke/`: record, edit or revoke a Box-specific evidence URL. Ordinary Box creation also accepts optional `dna_evidence_url`; no evidence is inferred or inherited.
- `POST /api/box-transfers/` and `POST /api/box-transfer-imports/`: retired v1 routes (`410` after authorization; no mutation).
- `/api/box-donations/…`: destination lookup, preflight, proposal creation, incoming/outgoing reads, details and source cancellation; no acceptance.
- `/api/admin/box-shares/…` and `/api/shared-boxes/…`: external sharing APIs; no mounted React share administration or recipient reader.
- `GET /api/exports/options/`: values available in the cumulative export filters.
- `GET /api/exports/measurements.csv`: weekly biological tracking export in CSV format.
- `GET /api/profile/`: current user profile, organizations, and interface language.
- `PATCH /api/profile/`: update account preferences such as interface language.
- `GET /api/taxonomy/references/`: localized species, strains, and configured content languages.
- `POST /api/taxonomy/species/`: create a species and its localized names (administrator only).
- `PATCH /api/taxonomy/species/<id>/`: update a species and its localized names (administrator only).
- `/api/admin/institution-species/…`: institution-scoped Species and Strain workflows. `POST …/resolve/` is retired (`410 institution_species_resolution_retired` after authorization); the legacy `POST /api/taxonomy/strains/` is also retired.
- `PATCH /api/taxonomy/strains/<id>/`: update a strain and its localized names (administrator only).

The CSV export uses one row per ISO week. Each selected box produces three
columns for polyp count, ephyrae count, and temperature. Missing measurements
stay empty, while a recorded zero remains `0`. Filters for organizations,
species, strains, thermal zones, boxes, and dates can be combined.

French prototype endpoints such as `/api/boites/` and `/api/zones/` have been
removed. New frontend code should only use the English DRF routes above.

The initial split-app migration refactor is historical. Later additive migrations
cover provenance/temporal precision, quantitative subculture, weekly movements and
portable transfer foundations. Do not recreate a database containing scientific
history or rewrite migrations to match an old setup guide.

Canonical CSV preparation and the Python planner remain read-only stages.
The current plan schema is **1.3**; its measurement baseline includes
`duplicate_of_id` and `environment_source_box_id`. Versions 1.1/1.2 in dated
milestones describe earlier contracts, not the current schema.

A restricted, atomic exact-date import API is implemented. The unmounted React
import components were removed in PR2; there is no active React import workflow.
The applicator accepts only unblocked `CREATE`, `ALREADY_MATCHES` and
`IGNORED` rows, with source bytes, durable receipt, idempotent
replay and locked plan revalidation. This is not a generic correction/onboarding
applicator: no overwrite, Box creation or identity guessing is enabled. Week-only
`CREATE` and named observers remain blocked; a matching week-only row can be
accepted as an unchanged `ALREADY_MATCHES` no-op. New records still require a
real `measured_on`; week-only persistence is not enabled. See
[`docs/measurement-import-planner.md`](docs/measurement-import-planner.md) and
[`docs/measurement-csv-diagnostics.md`](docs/measurement-csv-diagnostics.md) for
contracts; older local-only status statements there are historical, not deployment
evidence.

Desktop Administration mounts Team, Inventory, Species, Locations, Exchanges
and History. Only shared References and Organizations are disabled. Exchanges
shows outgoing and incoming whole-Box donation proposals; it does not support
CSV v1 or donation acceptance. Transfer v2 package foundations are separate from
these proposals. Backend endpoints enforce their own permissions.

## QR codes

Each box has a permanent QR code that encodes a short public URL:

```txt
{PUBLIC_BASE_URL}/bac/<id>/
```

Scanning it leads to the box sheet. `/bac/<id>/` is a stable login-protected
handoff without Box lookup or SCAN audit. Resolution and successful SCAN audit
occur in `/api/boxes/<id>/scan/` under the explicit active organization;
membership in another organization does not widen that lookup. Printed labels
keep working if the internal route changes. Set `PUBLIC_BASE_URL` in
`backend/.env` to the address phones can reach (the production domain in prod,
the dev server otherwise).

The QR image is available per box at `GET /boites/<id>/qr.svg` and is shown on
the box detail page. The box detail API (`GET /api/boxes/<id>/`) also returns
`scan_url` and `qr_image_url`.

Batch-generate printable label images (git-ignored `data/qr_codes/` by default):

```powershell
uv run python scripts/generate_qr_codes.py
uv run python scripts/generate_qr_codes.py --format png --sync-tags
```

## Frontend

The React frontend lives in `frontend/`.

Install frontend dependencies:

```powershell
cd frontend
npm ci
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

### Test from a phone on the local network

For a visual test on a phone, connect the computer and phone to the same Wi-Fi
network. Start Django against an isolated local database with synthetic data,
then start the frontend with:

```powershell
cd frontend
npm run dev:lan
```

Open the network address shown by Vite on the phone, usually
`http://<computer-ip>:5173/`. The address changes with the Wi-Fi network and
must not be written into the source code. Vite forwards the API requests to
the Django server running locally on the computer.

Camera access on iPhone and iPad requires an HTTPS address. A local HTTP
address is suitable for checking the interface and manual search, but not for
testing QR-code scanning. Use an isolated HTTPS QA/staging address with synthetic
data to test the camera on Safari, not production.

### Temporary HTTPS tunnel

For a short mobile camera test before deployment, start a Cloudflare Quick
Tunnel in a separate terminal while the frontend is running:

```powershell
cloudflared tunnel --url http://127.0.0.1:5173
```

Cloudflare prints a temporary `https://...trycloudflare.com` address. It can
be opened on the phone to test the QR scanner. This address is public and
expires when the tunnel stops, so it must only be used with demonstration
data, never as a deployment solution.

The export screen is available in the desktop interface. It is intentionally
hidden from the laboratory tablet navigation.

Check the production build:

```powershell
cd frontend
npm run build
```

## Production deployment

With explicit authorization, use the guarded deployment workflow from a clean
`main` aligned with `origin/main`:

```powershell
.\deploy\deploy_vm.ps1
```

Use `-PreflightOnly` for local release checks without changing the VM. The release
executor requests a verified backup from the independently installed trusted
broker (`/usr/local/libexec/polypbase-deployment-backup`, as `polypbase_backup`),
updates fast-forward only, builds the release and checks database readiness with
`migrate --check`. Pending migrations stop the release; it does not apply them.
After successful preparation the wrapper restarts the app, validates and reloads
Nginx, and checks the HTTPS endpoints. See the guarded procedure in
[`docs/deploiement_vm.md`](docs/deploiement_vm.md).

## Database updates

Production schema changes require separate explicit approval and the dedicated
migrator (`deploy/scripts/run_dedicated_migrations.sh`), with its own backup and
controls. Do not bypass a failed readiness check with an ad hoc `migrate`. Git
integration/CI does not establish which migrations are applied anywhere. For an
isolated local database, inspect the migration plan and follow
[`docs/context/development-deployment.md`](docs/context/development-deployment.md).

## Internal documentation

- `POLYPBASE_CURRENT_STATE.md`: verified dated state, reviews and remaining work.
- `docs/PROJECT_CONTEXT.md`: domain routing and durable contracts.
- `docs/guide_utilisateur.md`: concise operational procedures for laboratory users.
- `docs/transferts_csv.md`: Polypbase transfer CSV format and import rules.
- `docs/tests_backend.md`: backend test inventory and execution instructions.
