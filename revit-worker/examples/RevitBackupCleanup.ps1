<#
.SYNOPSIS
  Remove Revit incremental backup .rvt files next to a model (e.g. Model.0001.rvt).
#>
function Remove-RevitIncrementalBackupRvt {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ModelPath,
        [Parameter(Mandatory = $false)][scriptblock]$LogMessage = $null
    )

    function Write-CleanupLog([string]$Message) {
        if ($LogMessage) { & $LogMessage $Message } else { Write-Host $Message }
    }

    $dir = [System.IO.Path]::GetDirectoryName($ModelPath)
    if ([string]::IsNullOrWhiteSpace($dir) -or -not (Test-Path -LiteralPath $dir)) { return }
    $mainName = [System.IO.Path]::GetFileName($ModelPath)

    Get-ChildItem -LiteralPath $dir -Filter *.rvt -File -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.Name -notmatch '\.\d{4}\.rvt$') { return }
        if ($_.Name -ieq $mainName) { return }
        try {
            Remove-Item -LiteralPath $_.FullName -Force
            Write-CleanupLog "Removed incremental backup $($_.Name)"
        } catch {
            Write-CleanupLog "WARN: could not remove $($_.Name): $_"
        }
    }
}
