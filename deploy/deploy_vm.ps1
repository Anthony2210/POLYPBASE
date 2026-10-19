[CmdletBinding()]
param(
    [string]$Repository,
    [string]$VmHost = 'polypbase.org',
    [string]$VmUser = 'aquariumparis',
    [string]$SshKeyPath,
    [string]$HostKeyFingerprint = 'ssh-ed25519 255 SHA256:qLQPuLRIqeGa/b5fKNsCOcpF2TJZCmbMB0ZRJ90Ifwk',
    [ValidatePattern('\A[0-9a-f]{40}:[0-9a-f]{64}\z', Options = 'None')]
    [string]$ReviewedMigrationApproval,
    [switch]$PreflightOnly,
    [switch]$UseVerifiedGitHubCi
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
    # PowerShell 5.1 turns captured native stderr into error records, so let stderr
    # flow to the console untouched and rely on $LASTEXITCODE as the success/failure gate.
    if ($Capture) {
        $output = & $FilePath @Arguments
        $exitCode = $LASTEXITCODE
        if ($output) {
            $output | ForEach-Object { Write-Host $_ }
        }
        if ($exitCode -ne 0) {
            throw "$Label failed with exit code $exitCode"
        }
        return (($output | Out-String).Trim())
    }

    # Keep native stdout visible even when callers discard the success stream.
    & $FilePath @Arguments | ForEach-Object { Write-Host $_ }
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "$Label failed with exit code $exitCode"
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

function Get-GitHubRepositorySlug {
    param([string]$RemoteUrl)

    if ($RemoteUrl -cmatch '\A(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?\z') {
        return $Matches[1]
    }
    throw "origin is not a recognizable github.com repository: $RemoteUrl"
}

function Invoke-GitHubApi {
    param([string]$Path)

    # The GitHub CLI uses the operator's own login. No token is stored or accepted here.
    $output = & $script:GhPath api -H 'Accept: application/vnd.github+json' $Path
    if ($LASTEXITCODE -ne 0) {
        throw "GitHub API request failed with exit code ${LASTEXITCODE}: $Path"
    }
    return (($output | Out-String) | ConvertFrom-Json)
}

function Assert-VerifiedGitHubCi {
    param(
        [string]$Slug,
        [string]$TargetCommit
    )

    $requiredJobs = @('Frontend checks', 'Backend checks')
    try {
        $runs = Invoke-GitHubApi -Path "repos/$Slug/actions/runs?head_sha=$TargetCommit&event=push&branch=main&per_page=100"
        $candidates = @(
            @($runs.workflow_runs) | Where-Object {
                $_.head_sha -ceq $TargetCommit -and
                $_.head_branch -ceq 'main' -and
                $_.event -ceq 'push' -and
                $_.path -ceq '.github/workflows/ci.yml' -and
                $_.repository.full_name -ieq $Slug -and
                $_.head_repository.full_name -ieq $Slug
            }
        )
        if ($candidates.Count -ne 1) {
            throw "Expected exactly one official CI push run for $TargetCommit on main, found $($candidates.Count)."
        }
        $run = $candidates[0]
        if ($run.status -cne 'completed' -or $run.conclusion -cne 'success') {
            throw "CI run $($run.id) is not successful (status=$($run.status), conclusion=$($run.conclusion))."
        }

        $jobsResponse = Invoke-GitHubApi -Path "repos/$Slug/actions/runs/$($run.id)/jobs?filter=latest&per_page=100"
        $jobs = @($jobsResponse.jobs)
        if ([int]$jobsResponse.total_count -ne $jobs.Count) {
            throw "CI job list for run $($run.id) is incomplete."
        }
        foreach ($name in $requiredJobs) {
            $matching = @($jobs | Where-Object { $_.name -ceq $name -and $_.head_sha -ceq $TargetCommit })
            if ($matching.Count -ne 1) {
                throw "Expected exactly one CI job named '$name', found $($matching.Count)."
            }
            if ($matching[0].status -cne 'completed' -or $matching[0].conclusion -cne 'success') {
                throw "CI job '$name' is not successful (status=$($matching[0].status), conclusion=$($matching[0].conclusion))."
            }
        }
        return $run
    }
    catch {
        throw "Verified fast mode refused: $($_.Exception.Message) Run without -UseVerifiedGitHubCi for the full local checks."
    }
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
        throw 'SSH key path is required. Set POLYPBASE_SSH_KEY or use -SshKeyPath.'
    }
}
$script:ResolvedKeyPath = (Resolve-Path -LiteralPath $SshKeyPath).Path

$script:PlinkPath = (Get-Command plink.exe -ErrorAction Stop).Source
$pscpPath = (Get-Command pscp.exe -ErrorAction Stop).Source
$gitPath = (Get-Command git.exe -ErrorAction Stop).Source
$uvPath = (Get-Command uv.exe -ErrorAction Stop).Source
$npmPath = (Get-Command npm.cmd -ErrorAction Stop).Source
if ($UseVerifiedGitHubCi) {
    $script:GhPath = (Get-Command gh.exe -ErrorAction Stop).Source
}

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

    if ($UseVerifiedGitHubCi) {
        Write-Step 'Verify GitHub CI for the exact release commit'
        $remoteUrl = (& $gitPath remote get-url origin).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw 'Could not read the origin remote URL.'
        }
        $ciRun = Assert-VerifiedGitHubCi -Slug (Get-GitHubRepositorySlug -RemoteUrl $remoteUrl) -TargetCommit $targetCommit
        Write-Host "GitHub CI verified: run $($ciRun.id) $($ciRun.html_url)" -ForegroundColor Green
        Write-Host 'Local Django tests and local frontend build will be skipped.' -ForegroundColor Yellow
    }

    $executorPath = Join-Path $repositoryPath 'deploy\scripts\deploy_release.sh'
    $trackedExecutor = (& $gitPath ls-tree -r --name-only HEAD -- deploy/scripts/deploy_release.sh).Trim()
    if ($LASTEXITCODE -ne 0 -or $trackedExecutor -ne 'deploy/scripts/deploy_release.sh') {
        throw 'The deployment executor is not committed in the target release.'
    }

    if (-not [string]::IsNullOrEmpty($ReviewedMigrationApproval)) {
        $reviewedCommit = $ReviewedMigrationApproval.Split(':')[0]
        if ($reviewedCommit -cne $targetCommit) {
            throw 'Reviewed migration approval does not match the target release commit.'
        }
        Write-Host "Requested reviewed migration approval: $ReviewedMigrationApproval" -ForegroundColor Yellow
    }

    Write-Host "Target commit: $targetCommit" -ForegroundColor Green
    Write-Host (& $gitPath log -1 --format='%h %s' $targetCommit)

    # Every local Django command explicitly selects unconditional in-memory SQLite.
    # Empty environment variables can disappear on Windows PowerShell 5.1.
    Invoke-NativeCommand -Label 'Git whitespace' -FilePath $gitPath -Arguments @('diff', '--check')
    Invoke-NativeCommand -Label 'Django system check' -FilePath $uvPath -Arguments @('run', 'python', 'backend\manage.py', 'check', '--settings=config.preflight_settings')
    Invoke-NativeCommand -Label 'Django migration drift' -FilePath $uvPath -Arguments @('run', 'python', 'backend\manage.py', 'makemigrations', '--check', '--dry-run', '--settings=config.preflight_settings')
    if (-not $UseVerifiedGitHubCi) {
        Invoke-NativeCommand -Label 'Django test suite' -FilePath $uvPath -Arguments @(
            'run', 'python', 'backend\manage.py', 'test',
            'apps.accounts', 'apps.audit', 'apps.cultures', 'apps.exports',
            'apps.measurements', 'apps.organizations', 'apps.taxonomy',
            '--settings=config.preflight_settings'
        )
        Invoke-NativeCommand -Label 'Frontend production build' -FilePath $npmPath -Arguments @('--prefix', 'frontend', 'run', 'build')
    }

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
    $prepareCommand = "sudo -n -u polypbase /bin/bash $remoteScript $targetCommit"
    if (-not [string]::IsNullOrEmpty($ReviewedMigrationApproval)) {
        # Parameter validation permits only hex digits and one colon: no shell syntax.
        $prepareCommand += " $ReviewedMigrationApproval"
    }
    Invoke-RemoteCommand -Label 'Backup and prepare release' -Command $prepareCommand | Out-Null
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
}
