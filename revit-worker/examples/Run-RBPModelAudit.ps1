<#
.SYNOPSIS
    RBP model audit only (no SaveAs).

.DESCRIPTION
    Same BatchRvt detach open as Run-RBPDetachRepath.ps1, with -AnalyticsOnly.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$ModelPath,

    [Parameter(Mandatory = $false)]
    [Alias("RevitVersion")]
    [int]$RevitYear = 2025,

    [Parameter(Mandatory = $false)]
    [string]$JobId = ""
)

& (Join-Path $PSScriptRoot "Run-RBPDetachRepath.ps1") `
    -ModelPath $ModelPath `
    -RevitYear $RevitYear `
    -JobId $JobId `
    -AnalyticsOnly
exit $LASTEXITCODE
