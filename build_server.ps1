# Build the LAN server + dashboard into dist\PlayLimitServer.exe.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

New-Item -ItemType Directory -Force -Path dist | Out-Null
py -m PyInstaller --noconfirm --clean --onefile --console --name PlayLimitServer `
    --icon playlimit.ico --add-data "server/dashboard.html;server" `
    --distpath dist --workpath build/server --specpath . server.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed building PlayLimitServer" }

Write-Host "Built dist\PlayLimitServer.exe ($((Get-Item dist\PlayLimitServer.exe).Length) bytes)" -ForegroundColor Green
exit 0
