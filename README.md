# Polypbase

Polypbase is a web application for tracking jellyfish polyp cultures at the
Aquarium de Paris. It records cultures, lineage, movements and measurements in
organization-scoped workspaces. Django enforces permissions and business rules;
the React interface is designed primarily for desktop and laboratory tablets.

## Repository

| Path | Purpose |
| --- | --- |
| `backend/` | Django application, API and migrations |
| `frontend/` | React and Vite application |
| `docs/` | User guides, references and operating procedures |
| `deploy/` | Guarded release and database operations tooling |
| `scripts/` | Repository utilities |

## Local development

From the repository root, install Python dependencies with `uv` and frontend
dependencies with npm:

```sh
uv sync --locked
npm --prefix frontend ci
```

Follow the [development and QA guide](docs/operations/development-qa.md) to
configure and start Django against a verified local database, then start Vite:

```sh
npm --prefix frontend run dev
```

Vite normally serves the app at <http://127.0.0.1:5173/> and proxies API calls
to the local Django server. The QA guide covers isolated Django checks, browser
and device testing, and frontend validation. Never point local QA, demo seeding
or trial imports at Neon, production, or another shared database.

## Documentation

- [Documentation index](docs/INDEX.md): start here to find the relevant guide or reference.
- [Project status](docs/STATUS.md): dated evidence about integration and operations, not a standing procedure.
- [Frontend reference](docs/reference/frontend.md): interface architecture, supported layouts and CSS.
- [Terminology and i18n](docs/reference/terminology-i18n.md): user-facing text, catalogs and fallback rules.
- [Deployment and operations](docs/operations/deployment-operations.md): release, backup and migration procedures. Production changes require explicit approval; a normal release does not apply pending migrations.
- [Contributor and agent rules](AGENTS.md): working agreements and safety boundaries.
