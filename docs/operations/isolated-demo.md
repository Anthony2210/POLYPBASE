# Isolated demo foundation

**Option A has been selected**: host an independent demo instance based on a snapshot of Aquarium de Paris data. Reuse must be expressly authorized, and the contents must be sanitized under an approved agreement.

This document describes a local software foundation, not a deployed or operational instance. The foundation does not authorize the use of production data or access to production, Neon, or a shared database. No process has been implemented for extracting, copying, sanitizing, restoring, or admitting a production snapshot.

## Application contract

Set these variables in the environment **of the dedicated Django process**. Do not copy the standard `.env`. Set `DJANGO_SETTINGS_MODULE=config.demo_settings` and `POLYPBASE_DEMO=1` in the demo launcher. This module refuses to start if the marker is missing, even if no other demo variables are present. The standard module behaves as usual in the absence of demo variables; a demo variable without the explicit mode also causes it to fail to start.

| Variable | Expected value |
| --- | --- |
| `POLYPBASE_DEMO` | `1` (the only value that enables the mode) |
| `DEMO_PUBLIC_ORIGIN` | Dedicated HTTPS origin with a DNS hostname, of the form `https://demo.example.test`, with no path, port, credentials, or wildcard. The example is not a hostname to publish. |
| `DEMO_SECRET_KEY` | Random secret specific to demo Django, 50 to 128 URL-safe characters. |
| `DEMO_DB_NAME`, `DEMO_DB_USER` | Name and role specific to the demo database, prefixed with `demo_`, using lowercase ASCII letters, digits, and `_`. |
| `DEMO_DB_PASSWORD` | Separate random secret, 32 to 128 URL-safe characters. |

Django does not load `backend/.env` in demo mode. It rejects libpq `PG*` variables and uses only the PostgreSQL Unix socket `/run/polypbase-demo/postgresql/.s.PGSQL.5432`, with no fallback to TCP or `POSTGRES_*` configuration. If the socket is missing or the server is unreachable, the connection fails rather than switching to another database. A database name and prefix do not prove the server's identity or isolation. The standard frontend uses same-origin APIs; the FR/EN banner reflects the explicit marker from `/api/auth/session/`, without a second Vite configuration. Do not use the Vite development proxy to launch the demo: it targets the standard development port. Serve the build and Django on the same origin. New QR codes use this Django origin; the foundation does not change any already printed QR codes or stored URLs.

In demo mode, Django disables email delivery (dummy backend, no inherited SMTP settings), iMinilide collection and condensation through the scheduled command, and WoRMS calls. Institutional permissions and isolation are unchanged. The banner does not indicate the provenance or date of the data.

## Accounts and sharing: what the foundation does not disable

Restored password hashes may still let active accounts sign in. Already accepted sharing permissions may remain effective for their recipients if the conditions for access are still met. Tokens that do not depend on the Django key, including sharing invitation secrets checked against a stored hash, may also remain valid. Without configured durations, external shares cannot be created, extended, or reissued; existing permissions and tokens are not revoked.

A demo-specific `SECRET_KEY` and session cookie name separate the two instances' configurations, but they do not disable restored passwords, sharing permissions, or independent tokens. Inherited sessions still need to be checked. The foundation does not check whether database contents are eligible for use and must not be used with a real snapshot until the conditions below are met.

## Items to authorize and verify separately

- Isolate the process and PostgreSQL cluster environment: dedicated OS account and storage, a socket mounted from that cluster alone, controlled directory and permissions, and a role with no access to any other database and no administrative privileges. No sockets, shares, mounts, secrets, libpq services, or volumes from another environment. Do not reuse real secrets, accounts, or sessions.
- Block egress (including PostgreSQL TCP, SMTP, and HTTPS to external services, production, and Neon). Verify the policy from the server and database execution environments. Django mode is neither a firewall nor a mount rule. An external link opened in the user's browser is not part of server egress.
- Do not carry over any production timer, cron job, collection service, backup or maintenance task, or deployment script. The current system backup is independent of Django settings. Restrict operator commands and access separately.
- Use a dedicated origin and TLS proxy, ideally on a different site from production. Enforce the hostname at the proxy, overwrite client-supplied scheme headers, do not route to any standard service, and do not cache session responses. Verify that `/api`, `/bac`, and QR resources point to the demo and that static assets introduce no external dependencies.

## Option A: requirements before proceeding to the next phase

Before processing any source data, obtain explicit authorization for reuse and get approval for a data inventory, a privacy policy, and a sanitization agreement. The inventory must cover scientific and personal data, evidence and its authors, access, shared data, and external links or references.

Before admitting a snapshot to the demo and then opening access, independently verify that:

- Inherited accounts, memberships, privileges, and other access paths have been inspected and disabled in accordance with the approved agreement. No restored password, inherited session, reusable authentication secret, or inherited invitation or reset link allows access to the demo.
- No existing external sharing permission is effective, and no inherited sharing token can grant access. Credentials, secrets, and access authorized for the demo are provisioned separately.
- Isolation has been verified, with no outbound communication or production integration. These checks go beyond the Django safeguards.
- Scientific evidence and historical attribution are preserved without inventing provenance. The agreement distinguishes attribution from access rights and establishes what information about authors can be exposed without compromising privacy.

**The demo instance must remain inaccessible to users** until authorization, sanitization, and isolation have been independently verified and both security and scientific reviews have approved opening access. Never run `seed_demo_data` on a restored database.

## Technical test of the foundation without a snapshot

The procedure does not run `seed_demo_data` automatically. For a test with fictional data only, on an empty, migrated demo database verified to be disposable, the explicit `--allow-non-debug` option permits this command with `--settings=config.demo_settings` without enabling `DEBUG`. Never use it on a snapshot, a shared database, or a production database.

After the test environment has been reviewed and authorized, verify the identity of the socket for an empty PostgreSQL cluster in a disposable environment with no outbound network access. With an empty, migrated database and, if needed, a disposable account, verify connectivity, the FR/EN banner, the blocking of outbound effects, and the tablet redirect from `/administration…` to `/`. This test validates neither snapshot eligibility nor opening access to the demo.
