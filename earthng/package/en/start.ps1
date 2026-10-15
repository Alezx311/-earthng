# EarthNG: real places in BeamNG.drive (test build).
# Installs the mod, starts the generator companion and the game. Keep this window open while you play.
param(
    [string]$UserFolder = '',      # BeamNG user folder (the one with mods\)
    [int]$Workers = 0,             # tile generation processes (default: half the cores, 2..8)
    [switch]$NoGame                # do not start the game (the companion only)
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$py = Join-Path $root 'python\python.exe'

if (-not $UserFolder) {
    $candidates = @("$env:LOCALAPPDATA\BeamNG\BeamNG.drive\current", "$env:LOCALAPPDATA\BeamNG.drive\current")
    $UserFolder = ($candidates | Where-Object { Test-Path $_ } | Select-Object -First 1)
    if (-not $UserFolder) { $UserFolder = $candidates[0] }
}
if ($Workers -le 0) { $Workers = [Math]::Max(2, [Math]::Min(8, [int]([Environment]::ProcessorCount / 2))) }

Write-Host "BeamNG folder: $UserFolder"
New-Item -ItemType Directory -Force (Join-Path $UserFolder 'mods') | Out-Null
Copy-Item (Join-Path $root 'mod\earth2road_world.zip') (Join-Path $UserFolder 'mods\earth2road_world.zip') -Force
Write-Host 'Mod installed: mods\earth2road_world.zip'

# the OSM tiles shipped in data\osm_tiles (one package)
$tiles = (Get-ChildItem (Join-Path $root 'data\osm_tiles') -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'index.json') } | Select-Object -First 1).FullName
Write-Host "OSM data: $tiles"
$env:EARTH2ROAD = Join-Path $root 'none'
$env:PYTHONNOUSERSITE = '1'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
if (-not $NoGame) {
    Start-Process 'steam://rungameid/284160'
    Write-Host 'Starting BeamNG.drive via Steam...'
}
Write-Host ''
Write-Host "The companion is running ($Workers generation processes). In the game: Freeroam -> 'Earth2Road - Planet'."
Write-Host 'Keep this window open while you play. Ctrl+C - stop.'
& $py (Join-Path $root 'earthng\world_server.py') `
    --tiles $tiles `
    --userfolder $UserFolder --work (Join-Path $root 'work') --cache (Join-Path $root 'data\dem') `
    --regions (Join-Path $root 'work\regions') --workers $Workers --generator fast
