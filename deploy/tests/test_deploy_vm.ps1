# Parse and exercise selected blocks only. Never invoke the deployment script.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$deployPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'deploy_vm.ps1'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($deployPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -ne 0) {
    throw ($parseErrors | Out-String)
}
$source = [System.IO.File]::ReadAllText($deployPath)

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

foreach ($name in @('Write-Step', 'Invoke-NativeCommand', 'Invoke-RemoteCommand', 'Show-RemoteDiagnostics')) {
    $definition = $ast.Find({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    }, $true)
    Assert-True ($null -ne $definition) "Function not found: $name"
    . ([scriptblock]::Create($definition.Extent.Text))
}

# Exercise the actual parameter attributes without any deployment body.
$bindParameters = [scriptblock]::Create($ast.ParamBlock.Extent.Text + "`nreturn `$ReviewedMigrationApproval")
$targetCommit = '23a9f3edd37da19d2d11d4378009f339e6d14db8'
$approval = $targetCommit + ':' + ('a' * 64)
$bound = & $bindParameters -ReviewedMigrationApproval $approval
Assert-True ($bound -ceq $approval) 'Valid approval parameter was not preserved.'
$default = & $bindParameters
Assert-True ([string]::IsNullOrEmpty($default)) 'Approval must be absent by default.'
foreach ($invalid in @('true', $targetCommit, $approval.ToUpperInvariant(), ($approval + "`n"), ($approval + '; echo unsafe'))) {
    $rejected = $false
    try { & $bindParameters -ReviewedMigrationApproval $invalid | Out-Null }
    catch { $rejected = $true }
    Assert-True $rejected 'Malformed approval parameter was accepted.'
}

# A local PowerShell child is a native-output fixture, not plink or SSH.
$nativeShell = (Get-Process -Id $PID).Path
Invoke-NativeCommand -Label 'Test native success' -FilePath $nativeShell -Arguments @(
    '-NoProfile', '-NonInteractive', '-Command', '[Console]::WriteLine(''NATIVE_SUCCESS_VISIBLE''); exit 0'
) | Out-Null
$failureCaught = $false
try {
    Invoke-NativeCommand -Label 'Test native failure' -FilePath $nativeShell -Arguments @(
        '-NoProfile', '-NonInteractive', '-Command', '[Console]::WriteLine(''NATIVE_FAILURE_VISIBLE''); exit 7'
    ) | Out-Null
}
catch {
    $failureCaught = $_.Exception.Message -match 'failed with exit code 7'
}
Assert-True $failureCaught 'Native exit status was not propagated.'
Invoke-NativeCommand -Label 'Test native stderr' -FilePath $nativeShell -Arguments @(
    '-NoProfile', '-NonInteractive', '-Command', '[Console]::Error.WriteLine(''NATIVE_STDERR_VISIBLE''); exit 0'
) | Out-Null
$captured = Invoke-NativeCommand -Label 'Test capture contract' -FilePath $nativeShell -Arguments @(
    '-NoProfile', '-NonInteractive', '-Command', '[Console]::WriteLine(''CAPTURE_VALUE''); exit 0'
) -Capture
Assert-True ($captured -ceq 'CAPTURE_VALUE') 'Captured output contract changed.'

# Replace only the native boundary. The real remote wrapper and diagnostics run
# with a fake transport so no VM, network, key file or production tool is used.
function Invoke-NativeCommand {
    param([string]$Label, [string]$FilePath, [string[]]$Arguments, [switch]$Capture)
    $script:ObservedArguments = $Arguments
    if ($Label -eq 'Service status') {
        Write-Host 'SERVICE_DIAGNOSTIC_VISIBLE'
        throw 'Fixture inactive service'
    }
    if ($Label -eq 'Recent application logs') {
        Write-Host 'JOURNAL_DIAGNOSTIC_VISIBLE'
    }
}
$script:PlinkPath = 'fake-plink-never-executed'
$script:ResolvedKeyPath = 'fake-key-never-read'
$VmUser = 'fixture-user'
$VmHost = 'fixture-host.invalid'
$HostKeyFingerprint = 'fixture-host-key'
$remoteScript = '/tmp/fixture-executor.sh'

$guardStart = $source.IndexOf('    if (-not [string]::IsNullOrEmpty($ReviewedMigrationApproval)) {')
$guardEnd = $source.IndexOf('    Write-Host "Target commit: $targetCommit"', $guardStart)
$targetGuard = [scriptblock]::Create($source.Substring($guardStart, $guardEnd - $guardStart))
$ReviewedMigrationApproval = $approval
& $targetGuard
$ReviewedMigrationApproval = ('b' * 40) + ':' + ('a' * 64)
$mismatchCaught = $false
try { & $targetGuard }
catch { $mismatchCaught = $_.Exception.Message -match 'does not match the target release commit' }
Assert-True $mismatchCaught 'Mismatched release target was accepted.'

$prepareStart = $source.IndexOf('    $prepareCommand = "sudo -n -u polypbase')
$prepareEnd = $source.IndexOf("    Invoke-RemoteCommand -Label 'Restart Polypbase'", $prepareStart)
$prepare = [scriptblock]::Create($source.Substring($prepareStart, $prepareEnd - $prepareStart))
foreach ($ReviewedMigrationApproval in @('', $approval)) {
    & $prepare
    $expected = "sudo -n -u polypbase /bin/bash $remoteScript $targetCommit"
    if ($ReviewedMigrationApproval) { $expected += " $ReviewedMigrationApproval" }
    Assert-True ($script:ObservedArguments[-1] -ceq $expected) 'Approval was not passed as the exact sudo/Bash argument.'
    Assert-True ($script:ObservedArguments[0] -ceq '-batch') 'Plink batch safeguard changed.'
    Assert-True ($script:ObservedArguments[1] -ceq '-hostkey') 'Plink host-key safeguard changed.'
}
Show-RemoteDiagnostics
Write-Host 'POWERSHELL_TESTS_OK'
