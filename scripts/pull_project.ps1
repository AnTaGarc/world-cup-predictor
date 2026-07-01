param(
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$arguments = @("$PSScriptRoot\project_sync.py", "pull", "--root", $root)
if ($WhatIf) {
    $arguments += "--what-if"
}

Write-Host "Cierra la aplicacion antes de sincronizar la base SQLite."
& python @arguments
exit $LASTEXITCODE
