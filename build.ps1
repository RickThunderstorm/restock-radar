# Baut RestockRadar.exe (eine einzige Datei, ohne Konsolenfenster) nach dist\RestockRadar.exe
# PyInstaller schreibt Fortschritt nach stderr – das darf PowerShell nicht als Fehler werten.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$py = ".\.venv\Scripts\python.exe"

New-Item -ItemType Directory -Force build | Out-Null
& $py -c "from restockradar.tray import make_icon; make_icon(256).save('build/icon.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"

& $py -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name RestockRadar `
    --icon build\icon.ico `
    --add-data "restockradar\static;restockradar\static" `
    --collect-submodules uvicorn `
    --collect-submodules h2 --collect-submodules hpack --collect-submodules hyperframe `
    --hidden-import pystray._win32 `
    run.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller ist fehlgeschlagen" }

Write-Host "Fertig: dist\RestockRadar.exe"
