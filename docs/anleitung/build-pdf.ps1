# Druckt anleitung.html mit Microsoft Edge zu dist\Anleitung-Restock-Radar.pdf
$here = $PSScriptRoot
$root = Resolve-Path "$here\..\.."
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$out = Join-Path $root "dist\Anleitung-Restock-Radar.pdf"
$page = "file:///" + ((Join-Path $here "anleitung.html") -replace "\\", "/")
$profile = Join-Path $root "build\edge-profile"

New-Item -ItemType Directory -Force (Join-Path $root "dist") | Out-Null
& $edge --headless=new --disable-gpu --no-pdf-header-footer --user-data-dir="$profile" `
    --print-to-pdf="$out" $page | Out-Null
Start-Sleep -Seconds 2
if (Test-Path $out) { Write-Host "Fertig: $out" } else { throw "PDF wurde nicht erzeugt" }
