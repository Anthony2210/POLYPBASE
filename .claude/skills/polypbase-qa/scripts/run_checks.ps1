[CmdletBinding()]
param(
    [string]$Repository = (Join-Path $PSScriptRoot '..\..\..\..')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repositoryPath = (Resolve-Path -LiteralPath $Repository).Path
if (-not (Test-Path -LiteralPath (Join-Path $repositoryPath '.git'))) {
    throw "Not a Git repository: $repositoryPath"
}
if (-not (Test-Path -LiteralPath (Join-Path $repositoryPath 'backend\manage.py'))) {
    throw "Polypbase backend not found: $repositoryPath"
}
if (-not (Test-Path -LiteralPath (Join-Path $repositoryPath 'frontend\package.json'))) {
    throw "Polypbase frontend not found: $repositoryPath"
}

function Invoke-Check {
    param(
        [string]$Label,
        [scriptblock]$Command
    )

    Write-Host "`n== $Label ==" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

$previousPostgresOptIn = $env:POLYPBASE_TEST_POSTGRES
$hadPostgresOptIn = Test-Path Env:POLYPBASE_TEST_POSTGRES

Push-Location $repositoryPath
try {
    # A nonempty opt-out prevents dotenv from selecting PostgreSQL for ordinary tests.
    $env:POLYPBASE_TEST_POSTGRES = '0'

    Invoke-Check 'Git status' { git --no-pager --no-optional-locks status --short }
    Invoke-Check 'Git whitespace' { git --no-pager diff --check }
    Invoke-Check 'Django system check' { uv run python backend\manage.py check --settings=config.preflight_settings }
    Invoke-Check 'Django migration drift' { uv run python backend\manage.py makemigrations --check --dry-run --settings=config.preflight_settings }
    Invoke-Check 'Django test suite' {
        uv run python backend\manage.py test `
            apps.accounts `
            apps.audit `
            apps.cultures `
            apps.exports `
            apps.measurements `
            apps.organizations `
            apps.sharing `
            apps.taxonomy `
            config.test_api_defaults `
            --settings=config.test_settings
    }
    Invoke-Check 'Frontend production build' { npm --prefix frontend run build }

    Write-Host "`nPolypbase local checks passed." -ForegroundColor Green
}
finally {
    Pop-Location
    if ($hadPostgresOptIn) {
        $env:POLYPBASE_TEST_POSTGRES = $previousPostgresOptIn
    }
    else {
        Remove-Item Env:POLYPBASE_TEST_POSTGRES -ErrorAction SilentlyContinue
    }
}
