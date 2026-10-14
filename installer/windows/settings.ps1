# Alien installer helper: merge the script folder and (optionally) the OpenRouter key into
# %APPDATA%\Alien\settings.json, keeping everything already there. Run by Alien-<version>-Setup.exe.
# The key comes from a file the installer writes and deletes, so it never appears on a command line.
param([string]$KeyFile, [string]$Scripts)
$ErrorActionPreference = 'Stop'

$dir = Join-Path $env:APPDATA 'Alien'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$file = Join-Path $dir 'settings.json'

$s = $null
if (Test-Path $file) {
    try { $raw = Get-Content -Raw -Path $file; if ($raw) { $s = $raw | ConvertFrom-Json } }
    catch { Copy-Item $file "$file.bak" -Force }  # unreadable: keep a copy, start again
}
if ($null -eq $s) { $s = New-Object psobject }

function Set-Setting($name, $value) {
    if ($s.PSObject.Properties[$name]) { $s.$name = $value }
    else { $s | Add-Member -NotePropertyName $name -NotePropertyValue $value }
}

$key = ''
if ($KeyFile -and (Test-Path $KeyFile)) {
    $raw = Get-Content -Raw -Path $KeyFile  # $null when the box was left empty
    if ($raw) { $key = $raw.Trim() }
}
if ($key) { Set-Setting 'openrouterKey' $key }
elseif (-not $s.PSObject.Properties['openrouterKey']) { Set-Setting 'openrouterKey' '' }

if (-not $s.repository) {  # a chosen folder that's missing (a drive unplugged) is left for the user
    New-Item -ItemType Directory -Force -Path $Scripts | Out-Null
    Set-Setting 'repository' $Scripts
}
if (-not $s.model) { Set-Setting 'model' 'anthropic/claude-haiku-5.5' }

# UTF-8 without a byte-order mark: Alien and Python read the file as plain UTF-8.
$json = $s | ConvertTo-Json -Depth 20
[IO.File]::WriteAllText($file, $json, (New-Object Text.UTF8Encoding $false))
Write-Output "settings: $file"
