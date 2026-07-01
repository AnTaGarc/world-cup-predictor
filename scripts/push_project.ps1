param(
    [Parameter(Mandatory = $true)]
    [string]$Message,
    [switch]$SkipTests,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$arguments = @(
    "$PSScriptRoot\project_sync.py", "push", "--root", $root,
    "--message", $Message
)
if ($SkipTests) {
    $arguments += "--skip-tests"
}
if ($WhatIf) {
    $arguments += "--what-if"
}

Write-Host "Cierra la aplicación antes de sincronizar la base SQLite."
& python @arguments
exit $LASTEXITCODE
