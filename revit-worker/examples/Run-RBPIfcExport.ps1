<#
.SYNOPSIS
    RBP IFC exporter: open detached, ensure a 3D view, export IFC4 Reference View.

.PARAMETER ModelPath
    Path to the .rvt model.

.PARAMETER ExportDir
    Directory for the .ifc file.

.PARAMETER Filename
    Output stem without .ifc (default: model name, minus _detached).

.PARAMETER RevitYear
    Revit version year (default 2025). Alias: RevitVersion.

.PARAMETER JobId
    Job id for logs and sidecar names.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$ModelPath,

    [Parameter(Mandatory = $true)]
    [string]$ExportDir,

    [Parameter(Mandatory = $false)]
    [string]$Filename = "",

    [Parameter(Mandatory = $false)]
    [Alias("RevitVersion")]
    [int]$RevitYear = 2025,

    [Parameter(Mandatory = $false)]
    [string]$JobId = ""
)

$ErrorActionPreference = "Stop"

if (-not $JobId) { $JobId = [guid]::NewGuid().ToString('N').Substring(0, 8) }

if (-not $Filename) {
    $Filename = [System.IO.Path]::GetFileNameWithoutExtension($ModelPath)
    $Filename = $Filename -replace '_detached$', ''
}
$Filename = [System.IO.Path]::GetFileName($Filename)
if ($Filename -match '\.') { $Filename = [System.IO.Path]::GetFileNameWithoutExtension($Filename) }

$ScriptDir = $PSScriptRoot
$TempDir = Join-Path $env:TEMP "rbp_ifc_export"
if (-not (Test-Path $TempDir)) { New-Item -ItemType Directory -Path $TempDir -Force | Out-Null }

$DiagLog = Join-Path $TempDir "rbp_diag_$JobId.log"

function Diag($msg) {
    $ts = Get-Date -Format "HH:mm:ss.fff"
    $line = "[$ts] $msg"
    $line | Out-File -Append -FilePath $DiagLog -Encoding UTF8
}

Diag "=== RBP IFC export Start ==="
Diag "Model: $ModelPath ExportDir: $ExportDir Filename: $Filename JobId: $JobId"

$BatchRvtExe = Join-Path $env:LOCALAPPDATA "RevitBatchProcessor\BatchRvt.exe"
if (-not (Test-Path $BatchRvtExe)) {
    Diag "ERROR: BatchRvt.exe not found at $BatchRvtExe"
    $fallback = @{ ifc_exported = $false; view_created = $false; error = "BatchRvt.exe not found at $BatchRvtExe" } | ConvertTo-Json -Compress
    Write-Output "RW_RESULT:$fallback"
    exit 1
}

$TaskScript = Join-Path $ScriptDir "ifc_export_rbp.py"
if (-not (Test-Path -LiteralPath $TaskScript)) {
    Diag "ERROR: ifc_export_rbp.py missing under $ScriptDir"
    $fallback = @{ ifc_exported = $false; view_created = $false; error = "ifc_export_rbp.py not found next to the launcher" } | ConvertTo-Json -Compress
    Write-Output "RW_RESULT:$fallback"
    exit 1
}

$FileListPath = Join-Path $TempDir "filelist_$JobId.txt"
[System.IO.File]::WriteAllText($FileListPath, $ModelPath, (New-Object System.Text.UTF8Encoding $false))

$LogFolder = Join-Path $TempDir "logs_$JobId"
if (-not (Test-Path $LogFolder)) { New-Item -ItemType Directory -Path $LogFolder -Force | Out-Null }

$env:RBP_EXPORT_DIR = $ExportDir
$env:RBP_IFC_FILENAME = $Filename
$env:RBP_JOB_ID = $JobId

$modelBaseName = [System.IO.Path]::GetFileNameWithoutExtension($ModelPath)
$preflightSidecar = Join-Path $TempDir "$modelBaseName.rbp_rw_result.json"
$env:RBP_SIDECAR_PATH = $preflightSidecar
$preflightJson = @{
    ifc_exported = $false
    view_created = $false
    error = "RBP task script did not execute"
    model_path = $ModelPath
    job_id = $JobId
    preflight = $true
} | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText($preflightSidecar, $preflightJson, (New-Object System.Text.UTF8Encoding $false))

$rbpOutputLog = Join-Path $TempDir "batchrvt_stdout_$JobId.log"
$rbpExitCode = 0
try {
    & $BatchRvtExe --task_script $TaskScript --file_list $FileListPath --detach --worksets open_all --revit_version $RevitYear --log_folder $LogFolder > $rbpOutputLog 2>&1
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
    (Join-Path $ExportDir $sidecarName),
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
        ifc_exported = $false
        view_created = $false
        error = "No RW_RESULT sidecar file produced"
        rbp_exit_code = $rbpExitCode
    } | ConvertTo-Json -Compress
    Write-Output "RW_RESULT:$fallback"
}

Remove-Item Env:\RBP_EXPORT_DIR -ErrorAction SilentlyContinue
Remove-Item Env:\RBP_IFC_FILENAME -ErrorAction SilentlyContinue
Remove-Item Env:\RBP_JOB_ID -ErrorAction SilentlyContinue
Remove-Item Env:\RBP_SIDECAR_PATH -ErrorAction SilentlyContinue
Remove-Item $FileListPath -Force -ErrorAction SilentlyContinue

Diag "=== RBP IFC export End ==="
exit $rbpExitCode
