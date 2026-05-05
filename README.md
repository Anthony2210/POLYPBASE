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

## Backend

The backend uses Django. It stores the data, exposes API endpoints, manages
users and roles, provides the Django admin, and will generate server-side
exports.

Run backend commands from the `backend/` directory:

```powershell
cd backend
..\.venv\Scripts\python.exe manage.py check
..\.venv\Scripts\python.exe manage.py test
```

The current Django code lives in `backend/apps/cultures`. The physical folder
already follows the target architecture, but the Django app label is still
`core` to keep the first migration history compatible. Future developers can
split models into the dedicated apps when the database model is stable.

Target apps:

- `accounts`: users, roles, and permissions.
- `organizations`: aquariums and partner structures.
- `taxonomy`: species, taxa, strains, and provenance.
- `cultures`: boxes, repiquage, parentage, and transfers.
- `measurements`: observations, temperature, salinity, and probes.
- `exports`: CSV and XLSX export logic.
- `audit`: action history and traceability.

## Frontend

The final frontend will live in `frontend/`.