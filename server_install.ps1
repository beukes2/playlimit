# One-time PlayLimit Server setup: copy to ProgramData, allow LAN ports, start at logon.
# Run as Administrator (server_install.bat does this).
$ErrorActionPreference = "Stop"
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")
if (-not $isAdmin) { throw "Run as Administrator (server_install.bat does this)." }

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $root "dist\PlayLimitServer.exe"
if (-not (Test-Path $src)) { throw "Missing $src - run build_server.ps1 first." }

$dstDir = Join-Path $env:ProgramData "PlayLimitServer"
New-Item -ItemType Directory -Force -Path $dstDir | Out-Null
$dst = Join-Path $dstDir "PlayLimitServer.exe"

Write-Host "Stopping any running PlayLimit Server..."
Get-Process PlayLimitServer -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
Copy-Item -LiteralPath $src -Destination $dst -Force

Write-Host "Allowing LAN ports on the Private profile..."
foreach ($rule in @(
    @{Name="PlayLimit Server (TCP 8765)"; Proto="TCP"; Port=8765},
    @{Name="PlayLimit Server (UDP 8766)"; Proto="UDP"; Port=8766}
)) {
    Remove-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue
    # Home LANs are sometimes classified "Public" by Windows; open only these
    # two server ports on both profiles so clients can reach the heartbeat API.
    New-NetFirewallRule -DisplayName $rule.Name -Direction Inbound -Action Allow `
        -Protocol $rule.Proto -LocalPort $rule.Port -Profile Private,Public | Out-Null
}

Write-Host "Creating Startup shortcut..."
$ws = New-Object -ComObject WScript.Shell
$lnk = Join-Path ([Environment]::GetFolderPath("Startup")) "PlayLimitServer.lnk"
$s = $ws.CreateShortcut($lnk)
$s.TargetPath = $dst
$s.WorkingDirectory = $dstDir
$s.Description = "PlayLimit LAN server and dashboard"
$s.Save()

Write-Host "Starting PlayLimit Server..."
Start-Process -FilePath $dst
Start-Sleep -Seconds 2
Write-Host ""
Write-Host "Done. Dashboard: http://localhost:8765" -ForegroundColor Green
Write-Host "Client config: http://localhost:8765/server.json"
Write-Host "Data/secret/logs: $dstDir"
exit 0
