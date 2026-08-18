<#
.SYNOPSIS
    RBP launcher: detach a workshared model, run model audit, optionally SaveAs.

.DESCRIPTION
    Runs Revit Batch Processor with detach_repath_rbp.py. BatchRvt opens the model
    (--detach). Python does not call OpenDocumentFile.

.PARAMETER ModelPath
    Path to the .rvt file (local or UNC).

.PARAMETER RevitYear
    Revit version year for RBP (default 2025). Alias: RevitVersion.

.PARAMETER JobId
    Optional job id for logs and sidecar names.

.PARAMETER AnalyticsOnly
    Collect model audit only; skip SaveAs.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$ModelPath,

    [Parameter(Mandatory = $false)]
    [Alias("RevitVersion")]
    [int]$RevitYear = 2025,

    [Parameter(Mandatory = $false)]
    [string]$JobId = "",

    [Parameter(Mandatory = $false)]
    [switch]$AnalyticsOnly
)

$ErrorActionPreference = "Stop"

if (-not $JobId) { $JobId = [guid]::NewGuid().ToString('N').Substring(0, 8) }

$env:IFC_PIPELINE_JOB_ID = $JobId

$ScriptDir = $PSScriptRoot
$TempDir = Join-Path $env:TEMP "rbp_detach_repath"
if (-not (Test-Path $TempDir)) { New-Item -ItemType Directory -Path $TempDir -Force | Out-Null }

$DiagLog = Join-Path $TempDir "rbp_diag_$JobId.log"

function Diag($msg) {
    $ts = Get-Date -Format "HH:mm:ss.fff"
    $line = "[$ts] $msg"
    $line | Out-File -Append -FilePath $DiagLog -Encoding UTF8
}

Diag "=== RBP DetachRepath Start ==="
Diag "Model: $ModelPath JobId: $JobId RevitYear: $RevitYear AnalyticsOnly: $AnalyticsOnly"

$BatchRvtExe = Join-Path $env:LOCALAPPDATA "RevitBatchProcessor\BatchRvt.exe"
if (-not (Test-Path $BatchRvtExe)) {
    Diag "ERROR: BatchRvt.exe not found at $BatchRvtExe"
    $fallback = @{ error = "BatchRvt.exe not found at $BatchRvtExe"; host = "rbp" } | ConvertTo-Json -Compress
    Write-Output "RW_RESULT:$fallback"
    exit 1
}

$TaskScript = Join-Path $ScriptDir "detach_repath_rbp.py"
if (-not (Test-Path -LiteralPath $TaskScript)) {
    Diag "ERROR: detach_repath_rbp.py missing under $ScriptDir"
    $fallback = @{ error = "detach_repath_rbp.py not found next to the launcher"; host = "rbp" } | ConvertTo-Json -Compress
    Write-Output "RW_RESULT:$fallback"
    exit 1
}

$FileListPath = Join-Path $TempDir "filelist_$JobId.txt"
[System.IO.File]::WriteAllText($FileListPath, $ModelPath, (New-Object System.Text.UTF8Encoding $false))

$LogFolder = Join-Path $TempDir "logs_$JobId"
if (-not (Test-Path $LogFolder)) { New-Item -ItemType Directory -Path $LogFolder -Force | Out-Null }

if ($AnalyticsOnly) { $env:ANALYTICS_ONLY = "1" }
else { Remove-Item Env:\ANALYTICS_ONLY -ErrorAction SilentlyContinue }

$modelBaseName = [System.IO.Path]::GetFileNameWithoutExtension($ModelPath)
$preflightSidecar = Join-Path $TempDir "$modelBaseName.rbp_rw_result.json"
$env:RBP_SIDECAR_PATH = $preflightSidecar
$preflightJson = @{
    error = "RBP task did not complete"
    host = "rbp"
    preflight = $true
    model_path = $ModelPath
    job_id = $JobId
} | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText($preflightSidecar, $preflightJson, (New-Object System.Text.UTF8Encoding $false))

$rbpOutputLog = Join-Path $TempDir "batchrvt_stdout_$JobId.log"
$rbpArgs = @(
    "--task_script", $TaskScript,
    "--file_list", $FileListPath,
    "--detach",
    "--worksets", "open_all",
    "--revit_version", $RevitYear,
    "--log_folder", $LogFolder
)

$rbpExitCode = 0
try {
    Diag "BatchRvt args: $($rbpArgs -join ' ')"
    & $BatchRvtExe @rbpArgs > $rbpOutputLog 2>&1
    $rbpExitCode = $LASTEXITCODE
} catch {
    Diag "ERROR running BatchRvt: $_"
    $rbpExitCode = 99
}

Diag "BatchRvt exit: $rbpExitCode"

$revitCleanup = Join-Path $ScriptDir "RevitBackupCleanup.ps1"
if (Test-Path -LiteralPath $revitCleanup) {
    . $revitCleanup
    Remove-RevitIncrementalBackupRvt -ModelPath $ModelPath -LogMessage { param($Message) Diag $Message }
}

Write-Output "Logs: $LogFolder\"
Write-Output """$DiagLog"""
if (Test-Path $rbpOutputLog) { Write-Output """$rbpOutputLog""" }

$modelDir = [System.IO.Path]::GetDirectoryName($ModelPath)
$sidecarName = "$modelBaseName.rbp_rw_result.json"
$detachedBaseName = ($modelBaseName -replace '_detached$', '') + "_detached"
$candidatePaths = @(
    (Join-Path $modelDir $sidecarName),
    (Join-Path $modelDir "$detachedBaseName.rbp_rw_result.json"),
    $preflightSidecar
)

$rwFile = $null
foreach ($p in $candidatePaths) {
    if (Test-Path $p) { $rwFile = $p; Diag "Found sidecar: $p"; break }
}

if ($rwFile) {
    $rwJson = Get-Content -LiteralPath $rwFile -Raw -Encoding utf8 -ErrorAction SilentlyContinue
    if ($rwJson) { Write-Output "RW_RESULT:$rwJson" }
} else {
    $fallback = @{
        error = "No RBP RW_RESULT sidecar found"
        host = "rbp"
        rbp_exit_code = $rbpExitCode
    } | ConvertTo-Json -Compress
    Write-Output "RW_RESULT:$fallback"
}

Remove-Item Env:\RBP_SIDECAR_PATH -ErrorAction SilentlyContinue
Remove-Item Env:\ANALYTICS_ONLY -ErrorAction SilentlyContinue
Remove-Item $FileListPath -Force -ErrorAction SilentlyContinue

Diag "=== RBP DetachRepath End ==="
exit $rbpExitCode
