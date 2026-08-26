[CmdletBinding()]
param(
    [string]$Repository = 'C:\Users\antoc\POLYPBASE'
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

$previousPostgresDb = $env:POSTGRES_DB
$hadPostgresDb = Test-Path Env:POSTGRES_DB

Push-Location $repositoryPath
try {
    # An explicit empty value prevents python-dotenv from loading the Neon value.
    $env:POSTGRES_DB = ''

    Invoke-Check 'Git status' { git status --short }
    Invoke-Check 'Git whitespace' { git diff --check }
    Invoke-Check 'Django system check' { uv run python backend\manage.py check }
    Invoke-Check 'Django migration drift' { uv run python backend\manage.py makemigrations --check --dry-run }
    Invoke-Check 'Django test suite' {
        uv run python backend\manage.py test `
            apps.accounts `
            apps.audit `
            apps.cultures `
            apps.exports `
            apps.measurements `
            apps.organizations `
            apps.taxonomy `
            --settings=config.test_settings
    }
    Invoke-Check 'Frontend production build' { npm --prefix frontend run build }

    Write-Host "`nPolypbase local checks passed." -ForegroundColor Green
}
finally {
    Pop-Location
    if ($hadPostgresDb) {
        $env:POSTGRES_DB = $previousPostgresDb
    }
    else {
        Remove-Item Env:POSTGRES_DB -ErrorAction SilentlyContinue
    }
}
