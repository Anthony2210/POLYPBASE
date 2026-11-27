# Dot-source this file to load the local PR commands without running them.

function Invoke-PrTool {
    param([string]$Tool, [string]$Label, [string[]]$Arguments, [switch]$ShowOutput, [switch]$TestMatch)

    # Use native exit codes consistently in both PowerShell editions.
    $PSNativeCommandUseErrorActionPreference = $false
    $command = Get-Command $Tool -CommandType Application -ErrorAction Stop | Select-Object -First 1
    # Only Git diagnostics may print local checkout paths.
    if ($Tool -eq 'git') { $output = & $command @Arguments }
    else { $output = & $command @Arguments 2>$null }
    $code = $LASTEXITCODE
    if ($TestMatch -and $code -eq 1) { return $false }
    if ($code -ne 0) { throw "$Label failed (exit $code)." }
    if ($TestMatch) { return $true }
    if ($ShowOutput) { $output | Out-Host } else { $output }
}

function Get-PrMainCheckout {
    $root = Join-Path $env:USERPROFILE 'POLYPBASE'
    $top = Invoke-PrTool git 'Find main checkout' @('-C', $root, 'rev-parse', '--show-toplevel')
    if ([IO.Path]::GetFullPath($top) -ne [IO.Path]::GetFullPath($root)) {
        throw 'The main checkout is not at the expected location.'
    }
    return $root
}

function Get-PrRepository {
    param([string]$Root)

    $remote = Invoke-PrTool git 'Read origin' @('-C', $Root, 'remote', 'get-url', 'origin')
    $result = Invoke-PrTool gh 'Resolve origin repository' @('repo', 'view', $remote, '--json', 'nameWithOwner') |
        ConvertFrom-Json -ErrorAction Stop
    return $result.nameWithOwner
}

function Get-PrSnapshot {
    param([string]$Repository, [int]$Number)

    Invoke-PrTool gh "Read PR #$Number" @(
        'pr', 'view', "$Number", '--repo', $Repository, '--json', 'state,headRefOid,baseRefName,isDraft'
    ) | ConvertFrom-Json -ErrorAction Stop
}

function Assert-PrOpenOnMain {
    param($Pr, [int]$Number)

    if ($Pr.state -ne 'OPEN' -or $Pr.baseRefName -cne 'main' -or $Pr.headRefOid -notmatch '^[a-f0-9]{40}$') {
        throw "PR #$Number is not open on main with a valid head."
    }
}

function Get-PrCheckContexts {
    param([string]$Repository, [int]$Number, [string]$HeadSha)

    # Query the contexts attached to the PR's last commit, not an unpinned watch.
    $query = @'
query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      state baseRefName headRefOid
      commits(last: 1) { nodes { commit {
        oid
        statusCheckRollup { contexts(first: 100, after: $endCursor) {
          pageInfo { hasNextPage endCursor }
          nodes {
            __typename
            ... on CheckRun {
              name status conclusion databaseId checkSuite { app { slug } }
              isRequired(pullRequestNumber: $number)
            }
            ... on StatusContext {
              context state createdAt isRequired(pullRequestNumber: $number)
            }
          }
        } }
      } } }
    }
  }
}
'@
    $owner, $name = $Repository -split '/', 2
    $pages = (Invoke-PrTool gh "Read checks for PR #$Number" @(
        'api', 'graphql', '--paginate', '--slurp', '-f', "query=$query",
        '-f', "owner=$owner", '-f', "name=$name", '-F', "number=$Number"
    )) -join "`n" | ConvertFrom-Json -ErrorAction Stop
    foreach ($page in $pages) {
        $pr = $page.data.repository.pullRequest
        Assert-PrOpenOnMain $pr $Number
        $commit = $pr.commits.nodes.commit
        if ($pr.headRefOid -cne $HeadSha -or $commit.oid -cne $HeadSha) {
            throw "PR #$Number changed head while waiting; retry for the new head."
        }
        $commit.statusCheckRollup.contexts.nodes
    }
}

function Get-PrCheckState {
    param([object[]]$Contexts)

    # Keep the newest attempt per check provider and name. An old failed attempt
    # must not block a successful rerun, nor can an old success cover a rerun.
    $runs = @($Contexts | Where-Object { $_.__typename -eq 'CheckRun' } |
        Sort-Object databaseId -Descending | Group-Object -Property { $_.checkSuite.app.slug }, name |
        ForEach-Object { $_.Group[0] } | Where-Object isRequired)
    $statuses = @($Contexts | Where-Object { $_.__typename -eq 'StatusContext' } |
        Sort-Object createdAt -Descending | Group-Object context |
        ForEach-Object { $_.Group[0] } | Where-Object isRequired)
    # The required gate can be absent entirely while GitHub schedules the run.
    if (-not ($runs | Where-Object { $_.name -ceq 'ci-ok' -and $_.checkSuite.app.slug -eq 'github-actions' })) {
        return 'waiting for ci-ok to appear'
    }
    if (($runs | Where-Object { $_.status -ne 'COMPLETED' }) -or
        ($statuses | Where-Object { $_.state -notin @('SUCCESS', 'FAILURE', 'ERROR') })) {
        return 'waiting for required checks to finish'
    }
    if (($runs | Where-Object { $_.conclusion -ne 'SUCCESS' }) -or
        ($statuses | Where-Object { $_.state -ne 'SUCCESS' })) {
        return 'failed'
    }
    return 'passed'
}

function Wait-PrPoll {
    param([Diagnostics.Stopwatch]$Clock, [int]$TimeoutSeconds, [int]$PollSeconds)

    $remaining = $TimeoutSeconds - $Clock.Elapsed.TotalSeconds
    if ($remaining -le 0) { return $false }
    Start-Sleep -Milliseconds ([int][Math]::Ceiling(1000 * [Math]::Min($PollSeconds, $remaining)))
    return ($Clock.Elapsed.TotalSeconds -lt $TimeoutSeconds)
}

function Wait-PrHeadChecks {
    param([string]$Repository, [int]$Number, [string]$HeadSha, [int]$TimeoutSeconds, [int]$PollSeconds)

    $short = $HeadSha.Substring(0, 7)
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $previous = ''
    do {
        $contexts = @(Get-PrCheckContexts $Repository $Number $HeadSha)
        $state = Get-PrCheckState $contexts
        if ($state -eq 'failed') { throw "PR #$Number ($short): required checks did not pass." }
        if ($clock.Elapsed.TotalSeconds -ge $TimeoutSeconds) { break }
        if ($state -eq 'passed') {
            Write-Host "PR #$Number ($short): required checks passed."
            return $HeadSha
        }
        if ($state -ne $previous) { Write-Host "PR #$Number ($short): $state." }
        $previous = $state
    } while (Wait-PrPoll $clock $TimeoutSeconds $PollSeconds)
    throw "PR #$Number ($short): timed out waiting for required checks."
}

function New-PrWorktree {
    <#
    .SYNOPSIS
    Create a writer worktree from origin/main. Setup optionally installs local dependencies.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_-]*\z')][string]$Name,
        [Parameter(Mandatory)][string]$Branch,
        [switch]$Setup
    )

    if ($Name -match '^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])$') { throw 'Choose a non-reserved worktree name.' }
    $base = Join-Path $env:USERPROFILE 'worktrees/POLYPBASE'
    $path = Join-Path (Join-Path $base $Name) 'POLYPBASE'
    if (Test-Path -LiteralPath $path -ErrorAction Stop) { throw 'The worktree path already exists.' }
    $root = Get-PrMainCheckout
    if ($Branch.StartsWith('-')) { throw 'Choose a valid branch name.' }
    $ref = "refs/heads/$Branch"
    Invoke-PrTool git 'Validate branch name' @('check-ref-format', $ref) | Out-Null
    if ((Invoke-PrTool git 'Check local branch' @('-C', $root, 'show-ref', '--verify', '--quiet', $ref) -TestMatch) -or
        (Invoke-PrTool git 'Check remote branch' @('-C', $root, 'ls-remote', '--heads', 'origin', $ref))) {
        throw 'The branch already exists.'
    }
    Invoke-PrTool git 'Fetch origin' @('-C', $root, 'fetch', '--quiet', 'origin')
    Invoke-PrTool git 'Create worktree' @('-C', $root, 'worktree', 'add', '--no-track', '-b', $Branch, $path, 'refs/remotes/origin/main') -ShowOutput
    if ($Setup) {
        Push-Location -LiteralPath $path -ErrorAction Stop
        try {
            Write-Host 'Installing frontend and Python dependencies.'
            Invoke-PrTool npm.cmd 'Install frontend dependencies' @('--prefix', 'frontend', 'ci') | Out-Null
            Invoke-PrTool uv 'Sync Python dependencies' @('sync') | Out-Null
        } finally {
            Pop-Location
        }
    }
}

function Wait-PrChecks {
    <#
    .SYNOPSIS
    Return the checked SHA on success. A merged PR is reported and skipped.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, Position = 0)][ValidateRange(1, 2147483647)][int]$Number,
        [ValidateRange(1, 2147483647)][int]$TimeoutSeconds = 1800,
        [ValidateRange(1, 60)][int]$PollSeconds = 10
    )

    $repository = Get-PrRepository (Get-PrMainCheckout)
    $pr = Get-PrSnapshot $repository $Number
    if ($pr.state -eq 'MERGED') { Write-Host "PR #$Number is already merged; nothing to check."; return }
    Assert-PrOpenOnMain $pr $Number
    Wait-PrHeadChecks $repository $Number $pr.headRefOid $TimeoutSeconds $PollSeconds
}

function Remove-MergedWorktrees {
    <#
    .SYNOPSIS
    Remove clean writer worktrees whose exact tip was merged and is in origin/main.
    #>
    [CmdletBinding(SupportsShouldProcess)]
    param()

    $root = Get-PrMainCheckout
    $repository = Get-PrRepository $root
    if (-not $WhatIfPreference) {
        Invoke-PrTool git 'Fetch origin' @('-C', $root, 'fetch', '--quiet', 'origin')
    }
    $main = Invoke-PrTool git 'Read origin/main' @('-C', $root, 'rev-parse', '--verify', 'refs/remotes/origin/main')
    if ($WhatIfPreference) {
        $remoteMain = Invoke-PrTool git 'Check remote main' @('-C', $root, 'ls-remote', '--heads', 'origin', 'refs/heads/main')
        if (-not $remoteMain -or ($remoteMain -split "`t")[0] -cne $main) {
            throw 'origin/main is stale; fetch origin before previewing cleanup.'
        }
    }
    $base = Join-Path $env:USERPROFILE 'worktrees/POLYPBASE'
    $listing = Invoke-PrTool git 'List worktrees' @('-C', $root, '-c', 'core.quotePath=false', 'worktree', 'list', '--porcelain')
    foreach ($block in (($listing -join "`n") -split "`n`n")) {
        $lines = $block -split "`n"
        if ($lines -match '^(locked|prunable)( |$)') { continue }
        $path = $lines -replace '^worktree ', '' | Select-Object -First 1
        $branch = $lines -cmatch '^branch refs/heads/' -creplace '^branch refs/heads/', ''
        if (-not $branch -or $branch -ceq 'main' -or -not $path) { continue }
        $full = [IO.Path]::GetFullPath($path)
        $parent = Split-Path -LiteralPath $full
        if ($full -eq $root -or (Split-Path -Leaf $full) -ne 'POLYPBASE' -or
            (Split-Path -LiteralPath $parent) -ne $base.Replace('/', '\')) { continue }

        $tip = Invoke-PrTool git 'Read worktree HEAD' @('-C', $full, 'rev-parse', 'HEAD')
        if (Invoke-PrTool git 'Check worktree status' @('-C', $full, 'status', '--porcelain', '--untracked-files=all')) {
            Write-Host "Keep $branch (uncommitted changes)."
            continue
        }
        if (-not (Invoke-PrTool git 'Check worktree ancestry' @('-C', $root, 'merge-base', '--is-ancestor', $tip, $main) -TestMatch)) {
            Write-Host "Keep $branch (commits not in origin/main)."
            continue
        }
        $merged = Invoke-PrTool gh 'Look up merged PRs' @(
            'pr', 'list', '--repo', $repository, '--state', 'merged', '--base', 'main', '--head', $branch,
            '--limit', '100', '--json', 'headRefName,headRefOid,isCrossRepository'
        ) | ConvertFrom-Json -ErrorAction Stop
        if (-not ($merged | Where-Object { -not $_.isCrossRepository -and $_.headRefName -ceq $branch -and $_.headRefOid -ceq $tip })) {
            Write-Host "Keep $branch (no merged PR for this tip)."
            continue
        }
        if ($PSCmdlet.ShouldProcess($branch, 'Remove merged worktree and local branch')) {
            $currentTip = Invoke-PrTool git 'Recheck worktree HEAD' @('-C', $full, 'rev-parse', 'HEAD')
            $currentRef = Invoke-PrTool git 'Recheck worktree branch' @('-C', $full, 'symbolic-ref', '--short', 'HEAD')
            if ($currentTip -cne $tip -or $currentRef -cne $branch) { throw "Worktree $branch changed; nothing removed." }
            Invoke-PrTool git 'Remove worktree' @('-C', $root, 'worktree', 'remove', '--', $full) -ShowOutput
            Invoke-PrTool git 'Delete merged branch' @('-C', $root, 'branch', '-d', '--', $branch) -ShowOutput
        }
    }
}

function Invoke-PrCompletion {
    param([string]$Root, [string]$Repository, [int]$Number, [string]$HeadSha)

    if (Invoke-PrTool git 'Check main checkout status' @('-C', $Root, 'status', '--porcelain', '--untracked-files=all')) {
        throw 'The main checkout has uncommitted changes; nothing merged.'
    }
    $pr = Get-PrSnapshot $Repository $Number
    Assert-PrOpenOnMain $pr $Number
    if ($pr.headRefOid -cne $HeadSha) { throw "PR #$Number changed head; nothing merged." }
    if ($pr.isDraft) {
        Invoke-PrTool gh "Mark PR #$Number ready" @('pr', 'ready', "$Number", '--repo', $Repository) | Out-Null
    }
    Invoke-PrTool gh "Merge PR #$Number" @(
        'pr', 'merge', "$Number", '--repo', $Repository, '--merge', '--match-head-commit', $HeadSha
    ) | Out-Null
    if ((Get-PrSnapshot $Repository $Number).state -ne 'MERGED') { throw "PR #$Number did not report a merge; local checkout unchanged." }
    Write-Host "PR #$Number merged. Updating main."
    Invoke-PrTool git 'Switch to main' @('-C', $Root, 'switch', '--quiet', 'main')
    Invoke-PrTool git 'Fast-forward main' @('-C', $Root, 'pull', '--ff-only', '--quiet', 'origin', 'main')
    Remove-MergedWorktrees
}

function Complete-Pr {
    <#
    .SYNOPSIS
    For the product owner: check and merge one exact PR head, then update main and clean up.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, Position = 0)][ValidateRange(1, 2147483647)][int]$Number,
        [ValidateRange(1, 2147483647)][int]$TimeoutSeconds = 1800,
        [ValidateRange(1, 60)][int]$PollSeconds = 10
    )

    $headSha = Wait-PrChecks $Number -TimeoutSeconds $TimeoutSeconds -PollSeconds $PollSeconds
    if (-not $headSha) { throw "PR #$Number was not checked; nothing merged." }
    $root = Get-PrMainCheckout
    Invoke-PrCompletion $root (Get-PrRepository $root) $Number $headSha
}

function Complete-DependabotPrs {
    <#
    .SYNOPSIS
    For the product owner: request one Dependabot rebase at a time, then check and merge the new head.
    #>
    [CmdletBinding(SupportsShouldProcess)]
    param(
        [ValidateRange(1, 2147483647)][int]$RebaseTimeoutSeconds = 600,
        [ValidateRange(1, 2147483647)][int]$TimeoutSeconds = 1800,
        [ValidateRange(1, 60)][int]$PollSeconds = 10
    )

    $root = Get-PrMainCheckout
    $repository = Get-PrRepository $root
    $prs = Invoke-PrTool gh 'List Dependabot PRs' @(
        'pr', 'list', '--repo', $repository, '--state', 'open', '--base', 'main', '--app', 'dependabot',
        '--limit', '1000', '--json', 'number,createdAt'
    ) | ConvertFrom-Json -ErrorAction Stop
    if (-not $prs) { Write-Host 'No open Dependabot PRs on main.'; return }
    $skipped = 0
    foreach ($entry in ($prs | Sort-Object createdAt, number)) {
        $number = $entry.number
        if (-not $PSCmdlet.ShouldProcess("Dependabot PR #$number", 'Request rebase, wait for the new head and checks, then merge')) { continue }
        try {
            $pr = Get-PrSnapshot $repository $number
            Assert-PrOpenOnMain $pr $number
            $oldSha = $pr.headRefOid
            Invoke-PrTool gh "Request rebase for PR #$number" @(
                'pr', 'comment', "$number", '--repo', $repository, '--body', '@dependabot rebase'
            ) | Out-Null
            Write-Host "PR #${number}: waiting for a new head."
            $clock = [Diagnostics.Stopwatch]::StartNew()
            do {
                $pr = Get-PrSnapshot $repository $number
                Assert-PrOpenOnMain $pr $number
                if ($pr.headRefOid -cne $oldSha) { break }
            } while (Wait-PrPoll $clock $RebaseTimeoutSeconds $PollSeconds)
            if ($pr.headRefOid -ceq $oldSha -or $clock.Elapsed.TotalSeconds -ge $RebaseTimeoutSeconds) {
                throw 'Timed out waiting for a new head.'
            }
            $headSha = Wait-PrHeadChecks $repository $number $pr.headRefOid $TimeoutSeconds $PollSeconds
        } catch {
            Write-Warning "Skip PR #$number. $($_.Exception.Message)"
            $skipped++
            continue
        }
        # Stop the batch on merge or local cleanup failure: the state may have changed.
        Invoke-PrCompletion $root $repository $number $headSha
    }
    if ($skipped) { throw "Skipped $skipped Dependabot PR(s)." }
}
