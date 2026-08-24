[CmdletBinding()]
param(
    [string]$Repository,
    [string]$VmHost = 'polypbase.org',
    [string]$VmUser = 'aquariumparis',
    [string]$SshKeyPath,
    [string]$HostKeyFingerprint = 'ssh-ed25519 255 SHA256:qLQPuLRIqeGa/b5fKNsCOcpF2TJZCmbMB0ZRJ90Ifwk',
    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Write-Step {
    param([string]$Message)
    Write-Host "`n== $Message ==" -ForegroundColor Cyan
}

function Invoke-NativeCommand {
    param(
        [string]$Label,
        [string]$FilePath,
        [string[]]$Arguments,
        [switch]$Capture
    )

    Write-Step $Label
    if ($Capture) {
        $output = & $FilePath @Arguments 2>&1
        $exitCode = $LASTEXITCODE
        if ($output) {
            $output | ForEach-Object { Write-Host $_ }
        }
        if ($exitCode -ne 0) {
            throw "$Label failed with exit code $exitCode"
        }
        return (($output | Out-String).Trim())
    }

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Invoke-RemoteCommand {
    param(
        [string]$Label,
        [string]$Command,
        [switch]$Capture
    )

    $arguments = @(
        '-batch',
        '-hostkey', $HostKeyFingerprint,
        '-i', $script:ResolvedKeyPath,
        "$VmUser@$VmHost",
        $Command
    )
    return Invoke-NativeCommand -Label $Label -FilePath $script:PlinkPath -Arguments $arguments -Capture:$Capture
}

function Test-PublicEndpoint {
    param(
        [string]$Label,
        [string]$Uri,
        [int]$Attempts = 1
    )

    Write-Step $Label
    $lastError = $null
    for ($attempt = 1; $attempt -le $Attempts; $attempt += 1) {
        try {
            $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 15
            if ($response.StatusCode -eq 200) {
                Write-Host "HTTP 200 $Uri" -ForegroundColor Green
                return $response
            }
            $lastError = "Unexpected HTTP status $($response.StatusCode)"
        }
        catch {
            $lastError = $_.Exception.Message
        }

        if ($attempt -lt $Attempts) {
            Start-Sleep -Seconds 2
        }
    }
    throw "$Label failed: $lastError"
}

function Show-RemoteDiagnostics {
    Write-Host "`nDeployment diagnostics:" -ForegroundColor Yellow
    try {
        Invoke-RemoteCommand -Label 'Service status' -Command '/usr/bin/systemctl status polypbase --no-pager' | Out-Null
    }
    catch {
        Write-Host $_.Exception.Message -ForegroundColor Yellow
    }
    try {
        Invoke-RemoteCommand -Label 'Recent application logs' -Command 'sudo -n /usr/bin/journalctl -u polypbase -n 80 --no-pager' | Out-Null
    }
    catch {
        Write-Host $_.Exception.Message -ForegroundColor Yellow
    }
}

if ([string]::IsNullOrWhiteSpace($Repository)) {
    $Repository = Split-Path -Parent $PSScriptRoot
}
$repositoryPath = (Resolve-Path -LiteralPath $Repository).Path

if ([string]::IsNullOrWhiteSpace($SshKeyPath)) {
    if (-not [string]::IsNullOrWhiteSpace($env:POLYPBASE_SSH_KEY)) {
        $SshKeyPath = $env:POLYPBASE_SSH_KEY
    }
    else {
        $SshKeyPath = Join-Path $HOME 'OneDrive\Documents\polypbase-vm-anthony.ppk'
    }
}
$script:ResolvedKeyPath = (Resolve-Path -LiteralPath $SshKeyPath).Path

$script:PlinkPath = (Get-Command plink.exe -ErrorAction Stop).Source
$pscpPath = (Get-Command pscp.exe -ErrorAction Stop).Source
$gitPath = (Get-Command git.exe -ErrorAction Stop).Source
$uvPath = (Get-Command uv.exe -ErrorAction Stop).Source
$npmPath = (Get-Command npm.cmd -ErrorAction Stop).Source

if (-not (Test-Path -LiteralPath (Join-Path $repositoryPath '.git'))) {
    throw "Not a Git repository: $repositoryPath"
}
if (-not (Test-Path -LiteralPath (Join-Path $repositoryPath 'backend\manage.py'))) {
    throw "Polypbase backend not found: $repositoryPath"
}
if (-not (Test-Path -LiteralPath (Join-Path $repositoryPath 'frontend\package.json'))) {
    throw "Polypbase frontend not found: $repositoryPath"
}

$remoteScript = $null
$productionChanged = $false
$previousPostgresDb = $env:POSTGRES_DB
$hadPostgresDb = Test-Path Env:POSTGRES_DB

Push-Location $repositoryPath
try {
    Write-Step 'Release preflight'
    $branch = (& $gitPath branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -ne 'main') {
        throw 'Deployments are only allowed from the main branch.'
    }

    $statusLines = @(& $gitPath status --porcelain --untracked-files=all)
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not inspect the Git worktree.'
    }
    $unsafeChanges = @(
        $statusLines | Where-Object {
            if ($_ -notmatch '^\?\? (.+)$') {
                return $true
            }
            $path = $Matches[1].Replace('\', '/')
            return $path -ne 'PROJECT_CONTEXT.md' -and -not $path.StartsWith('docs/')
        }
    )
    if ($unsafeChanges.Count -gt 0) {
        $details = $unsafeChanges -join [Environment]::NewLine
        throw "Commit or remove application changes before deploying:`n$details"
    }

    Invoke-NativeCommand -Label 'Refresh origin/main' -FilePath $gitPath -Arguments @('fetch', '--quiet', 'origin', 'main')
    $targetCommit = (& $gitPath rev-parse HEAD).Trim()
    $originCommit = (& $gitPath rev-parse origin/main).Trim()
    if ($LASTEXITCODE -ne 0 -or $targetCommit -notmatch '^[0-9a-f]{40}$') {
        throw 'Could not resolve the local release commit.'
    }
    if ($targetCommit -ne $originCommit) {
        throw "Local main is not aligned with origin/main. Push the commit before deploying."
    }

    $executorPath = Join-Path $repositoryPath 'deploy\scripts\deploy_release.sh'
    $trackedExecutor = (& $gitPath ls-tree -r --name-only HEAD -- deploy/scripts/deploy_release.sh).Trim()
    if ($LASTEXITCODE -ne 0 -or $trackedExecutor -ne 'deploy/scripts/deploy_release.sh') {
        throw 'The deployment executor is not committed in the target release.'
    }

    Write-Host "Target commit: $targetCommit" -ForegroundColor Green
    Write-Host (& $gitPath log -1 --format='%h %s' $targetCommit)

    $env:POSTGRES_DB = ''
    Invoke-NativeCommand -Label 'Git whitespace' -FilePath $gitPath -Arguments @('diff', '--check')
    Invoke-NativeCommand -Label 'Django system check' -FilePath $uvPath -Arguments @('run', 'python', 'backend\manage.py', 'check')
    Invoke-NativeCommand -Label 'Django migration drift' -FilePath $uvPath -Arguments @('run', 'python', 'backend\manage.py', 'makemigrations', '--check', '--dry-run')
    Invoke-NativeCommand -Label 'Django test suite' -FilePath $uvPath -Arguments @(
        'run', 'python', 'backend\manage.py', 'test',
        'apps.accounts', 'apps.audit', 'apps.cultures', 'apps.exports',
        'apps.measurements', 'apps.organizations', 'apps.taxonomy',
        '--settings=config.test_settings'
    )
    Invoke-NativeCommand -Label 'Frontend production build' -FilePath $npmPath -Arguments @('--prefix', 'frontend', 'run', 'build')

    if ($PreflightOnly) {
        Write-Host "`nPreflight passed. Production was not changed." -ForegroundColor Green
        return
    }

    Write-Step 'Upload verified deployment executor'
    $shortCommit = $targetCommit.Substring(0, 8)
    $remoteScript = "/tmp/polypbase-deploy-$shortCommit.sh"
    & $pscpPath -batch -q -hostkey $HostKeyFingerprint -i $script:ResolvedKeyPath $executorPath "${VmUser}@${VmHost}:$remoteScript"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not upload the deployment executor."
    }

    Invoke-RemoteCommand -Label 'Protect uploaded executor' -Command "/usr/bin/chmod 0755 $remoteScript" | Out-Null
    $localHash = (Get-FileHash -LiteralPath $executorPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $remoteHashOutput = Invoke-RemoteCommand -Label 'Verify uploaded executor' -Command "/usr/bin/sha256sum $remoteScript" -Capture
    $remoteHash = ($remoteHashOutput -split '\s+')[0].ToLowerInvariant()
    if ($localHash -ne $remoteHash) {
        throw 'The uploaded deployment executor failed its SHA-256 verification.'
    }

    $productionChanged = $true
    Invoke-RemoteCommand -Label 'Backup and prepare release' -Command "sudo -n -u polypbase /bin/bash $remoteScript $targetCommit" | Out-Null
    Invoke-RemoteCommand -Label 'Restart Polypbase' -Command 'sudo -n /usr/bin/systemctl restart polypbase' | Out-Null
    Invoke-RemoteCommand -Label 'Validate Nginx configuration' -Command 'sudo -n /usr/sbin/nginx -t' | Out-Null
    Invoke-RemoteCommand -Label 'Reload Nginx' -Command 'sudo -n /usr/bin/systemctl reload nginx' | Out-Null

    $healthResponse = Test-PublicEndpoint -Label 'Wait for public API health' -Uri 'https://polypbase.org/api/health/' -Attempts 12
    $health = $healthResponse.Content | ConvertFrom-Json
    if ($health.status -ne 'ok' -or $health.service -ne 'polypbase') {
        throw 'The public health endpoint returned an unexpected payload.'
    }

    $homeResponse = Test-PublicEndpoint -Label 'Check frontend home' -Uri 'https://polypbase.org/'
    if ($homeResponse.Content -notmatch 'id=["'']root["'']') {
        throw 'The public homepage does not contain the React root element.'
    }
    if ($homeResponse.Headers['X-Content-Type-Options'] -ne 'nosniff') {
        throw 'The public response is missing X-Content-Type-Options: nosniff.'
    }
    if ($homeResponse.Headers['X-Frame-Options'] -ne 'DENY') {
        throw 'The public response is missing X-Frame-Options: DENY.'
    }

    $overviewResponse = Test-PublicEndpoint -Label 'Check internal frontend route' -Uri 'https://polypbase.org/overview'
    if ($overviewResponse.Content -notmatch 'id=["'']root["'']') {
        throw 'The internal frontend route does not return the React application.'
    }

    Write-Step 'Check HTTP to HTTPS redirect'
    $redirectCheck = & curl.exe --silent --show-error --output NUL --write-out '%{http_code} %{redirect_url}' 'http://polypbase.org/'
    if ($LASTEXITCODE -ne 0 -or $redirectCheck -notmatch '^30[18] https://polypbase\.org/') {
        throw "Unexpected HTTP redirect: $redirectCheck"
    }
    Write-Host $redirectCheck -ForegroundColor Green

    $deployedCommit = (Invoke-RemoteCommand -Label 'Verify deployed commit' -Command 'sudo -n -u polypbase git -C /srv/polypbase/app rev-parse HEAD' -Capture).Trim()
    if ($deployedCommit -ne $targetCommit) {
        throw "Production commit mismatch: expected $targetCommit, got $deployedCommit"
    }
    Invoke-RemoteCommand -Label 'Verify active services' -Command '/usr/bin/systemctl is-active polypbase nginx postgresql polypbase-backup.timer' | Out-Null

    Write-Host "`nDeployment completed successfully." -ForegroundColor Green
    Write-Host "Commit: $targetCommit"
    Write-Host 'URL: https://polypbase.org'
}
catch {
    Write-Host "`nDEPLOYMENT STOPPED: $($_.Exception.Message)" -ForegroundColor Red
    if ($productionChanged) {
        Show-RemoteDiagnostics
        Write-Host 'No database restore or migration rollback was attempted.' -ForegroundColor Yellow
    }
    throw
}
finally {
    if ($remoteScript) {
        try {
            Invoke-RemoteCommand -Label 'Remove temporary executor' -Command "/usr/bin/rm -f $remoteScript" | Out-Null
        }
        catch {
            Write-Host "Could not remove temporary executor: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
    Pop-Location
    if ($hadPostgresDb) {
        $env:POSTGRES_DB = $previousPostgresDb
    }
    else {
        Remove-Item Env:POSTGRES_DB -ErrorAction SilentlyContinue
    }
}
