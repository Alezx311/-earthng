# EarthNG planet for BeamNG.drive: builds and installs the mod, starts the companion and the game.
#   powershell -ExecutionPolicy Bypass -File C:\js\EarthNG\run_world.ps1
# In the game: Freeroam -> the "Earth2Road" planet level. Keep this window open while playing.
# Default: generator fast over the whole of Ukraine (data/osm_tiles/ukraine-260918); tiles stream around the car.
# -Generator v2 needs a prepared region (earthng/region.py, e.g. out/region/kyiv-ns).
# Python is this checkout's .venv (requirements-fast.txt). -Generator v2 still needs Earth2Road.
# ASCII only: Windows PowerShell 5.1 reads a UTF-8 file without BOM as ANSI.
param(
    [string]$Earth2Road = $(if ($env:EARTH2ROAD) { $env:EARTH2ROAD } else { 'C:\js\Earth2Road' }),
    [string]$Tiles = '',
    [string]$UserFolder = "$env:LOCALAPPDATA\BeamNG\BeamNG.drive\current",
    [int]$Workers = 8,
    [ValidateSet('v2', 'fast')][string]$Generator = 'fast',
    [switch]$NoGame
)
# fast: the Ukraine package when it is there (tiles are indexed as a window round the landing)
if (-not $Tiles) {
    $Tiles = "$PSScriptRoot\data\osm_tiles\kyiv-260918"
    if ($Generator -eq 'fast') {
        foreach ($name in @('ukraine-260918')) {
            $dir = "$PSScriptRoot\data\osm_tiles\$name"
            if (Test-Path "$dir\index.json") { $Tiles = $dir; break }
        }
    }
}
$own = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $own)) { $own = Join-Path $PSScriptRoot 'python\python.exe' }
if ($Generator -eq 'v2') {
    $env:EARTH2ROAD = $Earth2Road
    $py = Join-Path $Earth2Road '.venv\Scripts\python.exe'
} else {
    $env:EARTH2ROAD = Join-Path $PSScriptRoot 'none'
    $env:PYTHONNOUSERSITE = '1'
    if (Test-Path Env:PYTHONPATH) { Remove-Item Env:PYTHONPATH }
    $py = $own
}
if (-not (Test-Path $py)) {
    Write-Error "Python not found: $py. From the repo: py -3.13 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements-fast.txt"
    exit 1
}
& $py (Join-Path $PSScriptRoot 'earthng\build_mod.py') --legacy --out (Join-Path $PSScriptRoot 'out\mod') --install $UserFolder
if (-not $NoGame) { Start-Process 'steam://rungameid/284160' }
Write-Host 'Companion is running. In BeamNG: Freeroam -> Earth2Road planet level. Ctrl+C here to stop.'
& $py (Join-Path $PSScriptRoot 'earthng\world_server.py') --tiles $Tiles --userfolder $UserFolder --workers $Workers --generator $Generator
