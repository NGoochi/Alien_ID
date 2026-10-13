# Alien installer for Rhino 8 on Windows: copies Alien.gha (next to this script) into Grasshopper's Libraries folder
# and unblocks it. ALIEN_GH_LIBRARIES overrides the destination (for testing).
$ErrorActionPreference = 'Stop'
Write-Host 'Alien installer (Rhino 8, Windows)'

$src = Join-Path $PSScriptRoot 'Alien.gha'
if (-not (Test-Path -LiteralPath $src)) {
    Write-Host 'Alien.gha is not next to this script. Extract the whole zip first.'
    exit 1
}
if (Get-Process -Name 'Rhino' -ErrorAction SilentlyContinue) {
    Write-Host 'Rhino is running. Close it and run this again.'
    exit 1
}

if ($env:ALIEN_GH_LIBRARIES) { $dest = $env:ALIEN_GH_LIBRARIES } else { $dest = Join-Path $env:APPDATA 'Grasshopper\Libraries' }
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$target = Join-Path $dest 'Alien.gha'
if (Test-Path -LiteralPath $target) { Write-Host 'Replacing the installed Alien.gha.' }
Copy-Item -LiteralPath $src -Destination $target -Force
Unblock-File -LiteralPath $target   # downloaded files are blocked, and Grasshopper won't load a blocked plugin

Write-Host "Installed: $target"
Write-Host 'Next: start Rhino, type Grasshopper, and place an Alien node (Alien tab). See INSTALL.md.'
