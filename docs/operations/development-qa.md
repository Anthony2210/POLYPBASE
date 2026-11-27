# Development, validation, and deployment

## Minimum inspection

Before making a change:

1. read `AGENTS.md`, the documentation index, and only the relevant domain context;
2. run `git status --short --branch` and review the existing diff;
3. read the models, permissions, services, serializers, views, types, components, and tests actually affected;
4. find all consumers before changing a contract;
5. identify the applicable invariants: zero, organization, permission, history, audit, and concurrency;
6. implement the change within scope, then review the full diff.

Preserve all existing changes. Do not automatically run a migration, an import, or a demo data seed: identify the target database first.

## Isolated backend

`config.test_settings` uses in-memory SQLite by default, but selects a fixed local QA PostgreSQL endpoint if `POLYPBASE_TEST_POSTGRES=1`. SQLite isolation is therefore not unconditional. The imported application settings load `backend/.env` with dotenv: this file can reintroduce a missing variable. In Windows PowerShell 5.1, assigning an empty string to `POSTGRES_DB` may remove the variable; that step alone does not protect commands using the default settings.

For ordinary SQLite tests, open a **disposable child PowerShell session** with `powershell -NoProfile`, then run the following from `backend/` in that session only:

```powershell
# Prevent backend/.env from being read and disable any inherited PostgreSQL opt-in.
$env:PYTHON_DOTENV_DISABLED = '1'
$env:POLYPBASE_TEST_POSTGRES = '0'
$env:POLYPBASE_DEMO = '0'
$env:DJANGO_DEBUG = '1'
$env:EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
$env:EMAIL_DELIVERY_ENABLED = '0'

# Replace the module with the test for the domain you changed.
uv run python manage.py test apps.cultures.test_box_inventory_api --settings=config.test_settings
if ($LASTEXITCODE -ne 0) { throw 'Targeted SQLite tests failed' }


# Unconditionally select SQLite as the final override, even with an inherited PostgreSQL opt-in.
uv run python manage.py check --settings=config.preflight_settings
if ($LASTEXITCODE -ne 0) { throw 'Django configuration check failed' }
uv run python manage.py makemigrations --check --dry-run --settings=config.preflight_settings
if ($LASTEXITCODE -ne 0) { throw 'Migration drift check failed' }
```

`config.preflight_settings` explicitly overrides `DATABASES` to use in-memory SQLite after importing `config.test_settings`. This guarantee applies to Django's database selection; it does not mean that dotenv is not read or that a test launching its own PostgreSQL harness is a SQLite test. Close the child session after the checks, and immediately after a failure; do not modify profiles or environment files.

For full discovery as in CI, see [Backend tests](#découverte-des-tests-backend). Start from a verified local environment with no inherited `DEMO_*` variables, service credentials, or harness configuration. Selecting SQLite governs the Django database: some tests have their own prerequisites or opt-ins. Specialized PostgreSQL jobs have separate procedures.

**QA runner.** From the root, `./.agents/skills/polypbase-qa/scripts/run_checks.ps1` runs Git checks, `check` and `makemigrations --check --dry-run` under `config.preflight_settings`, followed by its backend test selection under `config.test_settings` and the frontend build. It forces `POLYPBASE_TEST_POSTGRES=0` while it runs and restores the previous value even on failure. The selection includes `apps.sharing` and `config.test_api_defaults`, but it does not use CI's discovery without test labels or run the PostgreSQL jobs or `npm run test:all`. The script does not disable dotenv itself: use the child session configured above, return to the root, and check the opt-ins before running it. For a limited change, prefer targeted checks.

Run targeted tests first. Expand to the full suite when a shared rule, permission, transaction, or cross-cutting API contract changes.

Findings that depend on PostgreSQL locks, constraints, or transactions must be reproduced on an isolated QA PostgreSQL instance. SQLite is not enough. This environment must be temporary, separate from Neon and production, and fully cleaned up afterward.

## Local QA with a PostgreSQL copy from the VM

**Run local QA against a local copy only: never against the VM, production, or Neon.** This procedure assumes a copy already restored and authorized for QA in the existing local container `polypbase-postgres-local`; it does not access the VM, extract data, or restore a copy. Verify that Docker uses the local engine, not a remote context. Do not replace this container with a `docker run` or recreate its volume.

**A disposable session is required.** Do not run this procedure in an existing work session. From the root of the intended checkout, open a dedicated child PowerShell process:

```powershell
powershell -NoProfile
```

Run all backend blocks below only in this child session. It inherits the starting environment, but changes to database, Django, host/CSRF, and `PUBLIC_BASE_URL` settings stay within this process and its children; they do not change the original session. Do not use `setx`, write to a profile, or persist these values. After any error, stop the procedure and close this session: do not continue with a partially configured environment.

From the root of the intended checkout, **in this dedicated child session**:

```powershell
Set-Location .\backend

# Discover only the initialized database and role names, never the password.
$containerEnv = docker inspect --format '{{range .Config.Env}}{{if or (eq (index (split . `=`) 0) `POSTGRES_DB`) (eq (index (split . `=`) 0) `POSTGRES_USER`)}}{{println .}}{{end}}{{end}}' polypbase-postgres-local
if ($LASTEXITCODE -ne 0) { throw 'Local container inspection failed' }
foreach ($name in @('POSTGRES_DB', 'POSTGRES_USER')) {
    $entry = $containerEnv | Where-Object { $_.StartsWith("${name}=") } | Select-Object -First 1
    if (-not $entry) { throw "Missing container variable: $name" }
    [Environment]::SetEnvironmentVariable($name, $entry.Substring($name.Length + 1), 'Process')
}
Remove-Variable containerEnv, entry

# Check configured bindings even when the existing container is stopped.
$configuredBindingsJson = docker inspect --format '{{json .HostConfig.PortBindings}}' polypbase-postgres-local
if ($LASTEXITCODE -ne 0) { throw 'Local PostgreSQL binding inspection failed' }
$configuredBindings = $configuredBindingsJson | ConvertFrom-Json -ErrorAction Stop
$postgresBindings = @($configuredBindings.PSObject.Properties | Where-Object { $_.Name -match '^5432/' })
if (-not $configuredBindings -or -not $configuredBindings.'5432/tcp') {
    throw 'No configured PostgreSQL TCP publication; stop QA'
}
foreach ($port in $postgresBindings) {
    if (-not $port.Value) { throw 'Missing explicit PostgreSQL host bindings; stop QA' }
    foreach ($hostBinding in $port.Value) {
        if ($hostBinding.HostIp -notin @('127.0.0.1', '::1')) {
            throw 'Configured PostgreSQL bindings must all be loopback; correct them separately before starting'
        }
    }
}
Remove-Variable configuredBindingsJson, configuredBindings, postgresBindings, port, hostBinding

docker start polypbase-postgres-local
if ($LASTEXITCODE -ne 0) { throw 'Local PostgreSQL container unavailable' }

# After startup, validate actual bindings and discover the host port.
$portBindings = docker port polypbase-postgres-local 5432
if ($LASTEXITCODE -ne 0) { throw 'Local PostgreSQL port unavailable' }
$portBindings
if (-not $portBindings -or @($portBindings | Where-Object { $_ -notmatch '^(127\.0\.0\.1|\[::1\]):[0-9]+$' }).Count -gt 0) {
    throw 'PostgreSQL must be published only on loopback; stop QA and correct the local binding separately'
}
$binding = $portBindings | Where-Object { $_ -match '^127\.0\.0\.1:[0-9]+$' } | Select-Object -First 1
if (-not $binding) { throw 'No IPv4 loopback PostgreSQL binding' }
$env:POSTGRES_HOST = '127.0.0.1'
$env:POSTGRES_PORT = ($binding -split ':')[-1]
$env:POSTGRES_SSLMODE = 'disable'
$env:DJANGO_DEBUG = 'True'
$env:DJANGO_SECRET_KEY = 'django-insecure-local-qa-only-never-production'
$env:DJANGO_SECURE_SSL_REDIRECT = '0'
$env:DJANGO_ALLOWED_HOSTS = 'localhost,127.0.0.1'
$env:DJANGO_CSRF_TRUSTED_ORIGINS = 'http://127.0.0.1:5173,http://localhost:5173'
$env:PUBLIC_BASE_URL = 'http://127.0.0.1:5173'

docker exec polypbase-postgres-local pg_isready -U $env:POSTGRES_USER -d $env:POSTGRES_DB
if ($LASTEXITCODE -ne 0) { throw 'Local PostgreSQL not ready; wait and repeat the readiness check before supplying credentials' }

# Supply the verified CURRENT password of the LOCAL role without echo/history.
$rolePassword = $null
$passwordBstr = [IntPtr]::Zero
try {
    $rolePassword = Read-Host 'Current verified LOCAL PostgreSQL role password' -AsSecureString
    if ($rolePassword.Length -eq 0) { throw 'Local role password required' }
    $passwordBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($rolePassword)
    $env:POSTGRES_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordBstr)
}
finally {
    if ($passwordBstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordBstr)
    }
    if ($null -ne $rolePassword) { $rolePassword.Dispose() }
    Remove-Variable rolePassword, passwordBstr -ErrorAction SilentlyContinue
}

uv run python manage.py check
if ($LASTEXITCODE -ne 0) { throw 'Django check failed' }
uv run python manage.py migrate --plan
if ($LASTEXITCODE -ne 0) { throw 'Migration plan unavailable' }
```

The JSON check of `HostConfig.PortBindings` reads no credentials and runs before startup: `docker port` can return nothing while the existing container is stopped. After `docker start`, checking the actual published ports supplies `POSTGRES_PORT`; password entry then waits for PostgreSQL readiness. If PostgreSQL is still starting, wait and resume at the `pg_isready` check without entering the secret before it succeeds. The DB/USER filter uses the `split` function supported by the [official Docker templates](https://docs.docker.com/engine/cli/formatting/#split); backtick-delimited Go strings in the single-quoted PowerShell argument avoid Windows native quoting issues. Only the two selected entries are returned, never `POSTGRES_PASSWORD`.

`Config.Env.POSTGRES_PASSWORD` describes how the container was initialized, not necessarily the current role password in PostgreSQL after a change or volume restore. Never treat it as authoritative. The discovered `POSTGRES_DB` and `POSTGRES_USER` names must also match the expected local copy. The operator must supply the current password from a verified local source; if it is unknown or authentication fails, stop and have access confirmed. No `ALTER ROLE`, password reset, or role change is part of this procedure. `pg_isready` checks readiness, not password validity; the migration plan then requires a real Django connection.

`DJANGO_DEBUG=True` sets `DEBUG=True` in the repository's current settings; the `DEBUG` environment variable alone is not used. Masked input does not put the secret into a command, its history, or a file. The BSTR is zeroed and freed and the `SecureString` disposed of in `finally`; however, conversion to an environment variable creates an in-memory string that cannot be guaranteed to be erased and is passed to Django/uv child processes. Do not display the environment, enable transcription, record the session, or publish passwords, `.env` files, dumps, data, or sensitive output. The Django key above is deliberately local and unsuitable for production. Process variables take precedence over `backend/.env`; do not read or copy that file. All published PostgreSQL ports must stay on loopback, never `0.0.0.0`, `[::]`, or a LAN address. Otherwise, stop before starting QA and have the binding corrected separately, without recreating the container or volume as part of this procedure.

**No automatic migrations.** If the schema is behind, stop before starting the server:

1. back up the local copy outside the repository in a protected location and verify that it can be restored;
2. inspect the pending migrations (`uv run python manage.py showmigrations --plan`), their operations, and their dependencies in the checkout;
3. diagnose the preconditions in read-only mode on this copy only, without changing measurements to satisfy a constraint;
4. apply only the explicitly approved local migrations, then rerun `check` and `migrate --plan`.

A diagnostic using the current models may itself fail due to a missing column or table: additive dependency migrations may be needed before that diagnostic. Inspect their safety and the current migration graph, then apply them only to the approved copy that was backed up. Do not turn a historical migration sequence into a permanent procedure, use `--fake` to hide a discrepancy, or modify existing migrations.

After validating the schema, start the backend **in the same session**, still from `backend/`:

```powershell
uv run python manage.py runserver 127.0.0.1:8000
```

In a second dedicated child PowerShell session (`powershell -NoProfile`), from the root of the **same checkout**:

```powershell
Set-Location .\frontend
npm run dev -- --host 127.0.0.1
```

Open `http://127.0.0.1:5173/`. The `dev` script starts Vite; here the option limits listening to the local machine (the Vite configuration otherwise listens on `0.0.0.0`). Its proxy points to Django on `127.0.0.1:8000`. Do not expose this copy through a public tunnel or run `seed_demo_data` on it. Keep automated tests on the isolated databases described above.

For a different development directory, only the checkout paths change: reuse the same container, but prepare new dedicated child sessions. Stop the previous servers before switching checkouts; QA writes and migrations affect the copy shared among development checkouts.

**Cleanup after QA or an error:** stop the servers with `Ctrl+C`, then quit both dedicated child sessions with `exit`. Do not remove or overwrite variables in the original session. Closing the sessions discards all process-level QA values — `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_SSLMODE`, `DJANGO_DEBUG`, `DJANGO_SECRET_KEY`, `DJANGO_SECURE_SSL_REDIRECT`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, and `PUBLIC_BASE_URL` — without changing preexisting values in the original session. The temporary password-entry objects are already cleaned up by `finally`. Protect and then clean up local artifacts within the authorized scope, without removing the container or its volume.

### QA on a physical phone or tablet on a trusted LAN

This variant uses only the authorized local copy above. **No public tunnels or access to the VM, production, or Neon. PostgreSQL and Django stay on localhost; only Vite is accessible from the LAN.** The PC and the device must share a trusted private network without Wi-Fi client isolation. HTTP carries sessions and data unencrypted: do not use public or guest Wi-Fi or expose a copy whose authorization excludes this network. Limit any Windows firewall allowance to TCP port `5173`, the private profile, and the QA devices or subnet. Do not open `8000` or the PostgreSQL port or disable the firewall. Development servers are not a secure deployment.

**Additional configuration required for LAN use:** `npm run dev:lan` runs `vite --host 0.0.0.0` on the fixed port `5173` (`strictPort: true`). Even `npm run dev` listens on all interfaces by default in `vite.config.ts`; it does not switch back to localhost. The `/api`, `/accounts`, `/boites`, and `/bac` proxies target `http://127.0.0.1:8000` and do not configure `changeOrigin`, so the browser's LAN host reaches Django. The defaults for `DJANGO_ALLOWED_HOSTS` do not allow it. Vite accepts IP addresses; its `allowedHosts: ['.trycloudflare.com']` entry neither limits access to trusted clients nor allows all LAN DNS names. Use the PC's IPv4 address without a host wildcard or changes to version-controlled configuration.

Stop both servers (`Ctrl+C`). In the **already configured disposable backend child session**, from `backend/`, list the machine's addresses and select the one for the trusted LAN's Wi-Fi or Ethernet interface (not Docker, VPN, or loopback):

```powershell
Get-NetIPConfiguration
$lanAddress = Read-Host 'PC IPv4 address on the trusted QA LAN'
$parsedAddress = $null
if (-not [System.Net.IPAddress]::TryParse($lanAddress, [ref]$parsedAddress) -or
    $parsedAddress.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork -or
    [System.Net.IPAddress]::IsLoopback($parsedAddress) -or
    $lanAddress -eq '0.0.0.0') {
    throw 'Select the actual PC IPv4 address shown for the trusted LAN interface'
}
$lanOrigin = "http://${lanAddress}:5173"
$env:DJANGO_ALLOWED_HOSTS = "localhost,127.0.0.1,$lanAddress"
$env:DJANGO_CSRF_TRUSTED_ORIGINS = "http://127.0.0.1:5173,http://localhost:5173,$lanOrigin"
$env:PUBLIC_BASE_URL = $lanOrigin
$lanOrigin
uv run python manage.py runserver 127.0.0.1:8000
```

Validating the format does not prove that the address belongs to the PC or that the network is trusted: verify the selection in the `Get-NetIPConfiguration` output. Keep `DJANGO_DEBUG=True` and the other local variables prepared above. The CSRF origin contains the scheme and port; allowed hosts do not. Setting them explicitly avoids inheriting production or tunnel values. Do not disable the CSRF middleware. The frontend sends session cookies and `X-CSRFToken` to the Vite proxy via same-origin paths; the phone does not need direct access to Django or additional CORS configuration. In DEBUG mode, cookies are not forced to use HTTPS. Sign in on the LAN origin: a session opened on `127.0.0.1` on the PC is not the session for the LAN address. `PUBLIC_BASE_URL` makes new QR links accessible to the phone; previously generated labels do not change.

In the second session, from `frontend/` in the same checkout:

```powershell
npm run dev:lan
```

On the device, open the origin displayed by `$lanOrigin` and verify that it matches the network address reported by Vite. On the phone, `localhost`/`127.0.0.1` means **the phone**, not the PC. Check sign-in, the active organization, navigation, manual search, empty/zero/error states, and authorized touch interactions on the copy. On tablets, Administration must remain hidden and its URLs must redirect to `/`. For `DisallowedHost`/400, check the allowed IPv4 address; for a CSRF 403, check the exact origin and session rather than removing protections. If Vite is unreachable, check the address, network, isolation, and private firewall rule.

**Camera limitation:** `http://` on a LAN address is not normally a secure context, in Safari on iPhone/iPad or in other mobile browsers. `TabletQrScanner` explicitly rejects an insecure context or the absence of `getUserMedia`. This HTTP QA covers the interface and manual search, **not camera-based QR scanning**. `camera=(self)` on the Django side does not replace HTTPS. Camera validation would require an approved local HTTPS setup with a certificate trusted by the device, which this configuration does not provide. Do not invent a TLS procedure here, bypass browser protections, or use the README's tunnel or production suggestions for this copy.

**Returning to localhost during QA:** stop both servers, then restore the localhost QA values below in the dedicated backend child session. This is neither final cleanup nor a restoration of the starting environment: that environment remains intact in the original session. Do not remove the password before restarting Django.

```powershell
$env:DJANGO_ALLOWED_HOSTS = 'localhost,127.0.0.1'
$env:DJANGO_CSRF_TRUSTED_ORIGINS = 'http://127.0.0.1:5173,http://localhost:5173'
$env:PUBLIC_BASE_URL = 'http://127.0.0.1:5173'
Remove-Variable lanAddress, parsedAddress, lanOrigin -ErrorAction SilentlyContinue
uv run python manage.py runserver 127.0.0.1:8000
```

In the frontend session, rerun `npm run dev -- --host 127.0.0.1` and open `http://127.0.0.1:5173/` on the PC. Remove any temporary firewall allowances created for LAN QA. When finished or after an error, follow the cleanup above: stop the servers and exit both disposable child sessions without touching variables in the original session.

## i18n checks

From the root, without Django or database access:

```powershell
# Check the version-controlled MO files before generating anything
uv run --frozen python -B scripts/compile_django_messages.py --check
uv run --frozen python -B scripts/check_i18n.py
uv run --frozen python -B -m unittest discover -s scripts -p test_compile_django_messages.py
uv run --frozen python -B -m unittest discover -s scripts -p test_check_i18n.py
```

Do not discover all `scripts/test_*.py` files indiscriminately: other QA scripts have PostgreSQL prerequisites unrelated to i18n. The two suites above are targeted, do not use a database, and use only temporary files for their fixtures.

After authorized changes to the PO files, generate the MO files locally with `uv run --frozen python scripts/compile_django_messages.py`, then rerun `--check`. The compiler validates every catalog before writing and explicitly rejects unsupported plurals, contexts, and fuzzy entries. CI generates nothing, so it detects missing or stale MO files. The checker reports raw missing entries separately from explicitly deferred entries: passing validation does not mean language coverage is complete. Django coverage exceptions use exact, catalog-local decisions in `backend/locale/coverage-exception-evidence.csv`, not the legacy translation register.

From `backend/`, run the FR/EN runtime tests without a database:

```powershell
uv run --frozen python manage.py test apps.accounts.test_i18n --settings=config.test_settings
```

The isolated Django suite also discovers these runtime tests. From `frontend/`, run:

```powershell
npm run check:i18n-sections -- --delta
npm run test:i18n
npm run check:i18n-usage
```

The section checker validates catalog and sidecar structure and reports stale source hashes as warnings. `--delta` also checks previously validated values against the merge base of `HEAD` and `origin/main`; make sure `origin/main` and its history are available. `npm run test:all` runs the delta check before discovering test files. The targeted `test:i18n` covers section and catalog integrity, types, Japanese fallback, and the usage detector. The usage check reports statically detectable references; investigate before removing a key. See the [translation review workflow](../reference/terminology-i18n.md#frontend-review-workflow) for status rules.

## Frontend

Run from `frontend/`.

For full validation:

```powershell
npm run test:all
npm run typecheck
npm run build
```

For targeted validation, use the command for the relevant area rather than rerunning these tests after `test:all`:

```powershell
npm run test:api
npm run test:charts
npm run test:inventory
npm run check:css
```

`npm run test:all` discovers `frontend/scripts/test-*.mjs` files and runs the suite with the Node runner, including in CI. Static imports between test modules define the groups: each module runs once, preserving isolation between entry points. The command rejects an empty suite, a cycle with no entry point, or a module shared across groups rather than omitting or duplicating tests. New tests must follow this convention and use direct static imports between sibling files; helpers and the CSS check are not part of discovery. Concurrency is limited to two processes to cap resource use by the TypeScript/VM harnesses. The existing targeted commands remain available.

`npm run build` reruns the CSS and TypeScript checks before the Vite build. The targeted scripts cover API error handling, chart windows, and Inventaire logic, respectively; they do not replace browser QA for visual interactions.

For a substantial change, use a real browser to verify the affected device types, loading, empty, error, and zero states, and keyboard and touch interactions. Do not leave behind screenshots, servers, databases, or temporary QA settings.

## Performance sweep with synthetic data

Prepare a **disposable** instance with no real `.env`, no server-side outbound network access, and an explicitly verified local database. Do not use the production copy described above. On this migrated database only, run `seed_demo_data --volume 150` with the instance's isolated settings (and `--allow-non-debug` only if needed). The volume represents additional boxes with fictional series; rerunning does not add duplicates. Enable `POLYPBASE_LOG_SLOW_API=1` in the Django process: the log warns above 500 ms and reports the count and cumulative duration of SQL queries, without logging SQL or parameters. Disable it after measuring.

Serve the build and API on the same dedicated loopback origin, with the usual security headers. From `frontend/`, install Chromium (`npx playwright install chromium`) and supply `PERF_PASSWORD` or per-role demo passwords in the process environment. Example first pass:

```sh
npm run perf:sweep -- --synthetic --base-url http://127.0.0.1:18173 --box-code DEMO-VOL-0001 --zone-name 'Demo Volume 15 C' --roles admin --viewports desktop
```

Remove the filters to run the matrix of three roles and three widths (390, 820, and 1440 px). `--help` describes the filters, and `--skip-lighthouse` allows a quick pass. QA checks the redirect instead when administration is unavailable on touch screens. Keep screenshots and results under `.local-docs/`, out of Git. Lighthouse scores are local navigation measurements, and detected overlaps are clues to check in screenshots, not proof of compliance. Stop the server and remove the disposable database afterward.

## Git and review

From the root:

```powershell
git status --short --branch
git diff --stat
git diff
git diff --check
```

A multi-agent review is read-only by default. The reviewer reads the relevant invariants, inspects the diff and tests, then reports issues by severity. A fix requires a separate request. Avoid having two agents edit the same files.

Before making a requested commit, verify that every file is in scope and no local artifacts are included. Do not commit, push, or deploy without an explicit request.

## Migrations and existing data

Before adding a constraint to existing data, check in read-only mode whether the data meets the invariant. Never automatically correct scientific data to make a migration pass.


<a name="découverte-des-tests-backend"></a>

## Backend test discovery

- From `backend/`, run `uv run python manage.py test --settings=config.test_settings --noinput` for Django test discovery; add a module (for example, `apps.sharing`) for a targeted check.
- Run `check` and `makemigrations --check --dry-run` with `--settings=config.preflight_settings` in the same verified child session.
- Skipping PostgreSQL tests does not validate PostgreSQL locks, constraints, or migrations. The `backend-postgres` job in the [single workflow](../../.github/workflows/ci.yml) runs its targeted suites against a disposable PostgreSQL 18 service.
- For a cross-cutting change, check permission denials, audit, and rollback. For measurements, cover zero, missing values, concurrency, and weekly conflicts.

On PRs, `changes` selects frontend, backend, PostgreSQL, and deployment checks based on the diff. Documentation and the bundle are always checked. On every push to `main`, all jobs are selected without checking paths, even for a documentation change. In particular, deployment requires `Frontend checks` and `Backend checks` to succeed for the exact commit. SQLite discovery uses two processes. PostgreSQL regression tests remain isolated in a separate job. In `main` branch protection, require only `ci-ok`: jobs skipped by the filter are not failures.

See the separate [deployment procedure](deployment-operations.md); these checks do not authorize any action on the VM.
