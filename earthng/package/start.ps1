# EarthNG: генерація Київської області в BeamNG.drive (тестова збірка).
# Встановлює мод, запускає компаньйон-генератор і гру. Вікно не закривати, поки граєте.
param(
    [string]$UserFolder = '',      # тека користувача BeamNG (де лежить mods\)
    [int]$Workers = 0,             # процеси генерації тайлів (типово: половина ядер, 2..8)
    [switch]$NoGame                # не запускати гру (лише компаньйон)
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

Write-Host "Тека BeamNG: $UserFolder"
New-Item -ItemType Directory -Force (Join-Path $UserFolder 'mods') | Out-Null
Copy-Item (Join-Path $root 'mod\earth2road_world.zip') (Join-Path $UserFolder 'mods\earth2road_world.zip') -Force
Write-Host 'Мод встановлено: mods\earth2road_world.zip'

$env:EARTH2ROAD = Join-Path $root 'none'
$env:PYTHONNOUSERSITE = '1'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
if (-not $NoGame) {
    Start-Process 'steam://rungameid/284160'
    Write-Host 'Запускаю BeamNG.drive через Steam...'
}
Write-Host ''
Write-Host "Компаньйон працює ($Workers процесів генерації). У грі: Freeroam -> 'Earth2Road - Планета'."
Write-Host 'Не закривайте це вікно, поки граєте. Ctrl+C - зупинити.'
& $py (Join-Path $root 'earthng\world_server.py') `
    --tiles (Join-Path $root 'data\osm_tiles\kyiv-oblast-260918') `
    --userfolder $UserFolder --work (Join-Path $root 'work') --cache (Join-Path $root 'data\dem') `
    --regions (Join-Path $root 'work\regions') --workers $Workers --generator fast
