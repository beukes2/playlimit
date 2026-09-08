# AlbionLimiter Uninstaller - Run as Administrator
$ErrorActionPreference = "SilentlyContinue"
$TaskName = "AlbionLimiter"
$InstallDir = "C:\Program Files\AlbionLimiter"
$Startup1 = Join-Path ([Environment]::GetFolderPath("CommonStartup")) "AlbionLimiter.lnk"
$Startup2 = Join-Path ([Environment]::GetFolderPath("Startup")) "AlbionLimiter.lnk"

Write-Host "=== AlbionLimiter Uninstaller ===" -ForegroundColor Cyan

# Check admin
if (-NOT ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Host "WARNING: Not running as Administrator, some cleanup may fail" -ForegroundColor Yellow
}

# Stop task
try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null; Write-Host "Stopped task" } catch {}

# Kill running limiter (exe + any python fallback)
try { Get-Process PlayLimit* -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue; Write-Host "Stopped PlayLimit" } catch {}
try {
    Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "*AlbionLimiter*" -or $_.CommandLine -like "*albion_limiter*" } | Stop-Process -Force -ErrorAction SilentlyContinue
    # Brute force: kill any pythonw running albion_limiter.py
    $procs = Get-WmiObject Win32_Process -Filter "name='pythonw.exe' or name='python.exe'" -ErrorAction SilentlyContinue
    foreach ($p in $procs) {
        if ($p.CommandLine -and $p.CommandLine.Contains("albion_limiter")) {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "Killed limiter process $($p.ProcessId)"
        }
    }
} catch {}

# Unregister task
try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue | Out-Null; Write-Host "Removed Scheduled Task" } catch {}

# Remove shortcuts (startup + desktop time window icon)
$Desktop1 = Join-Path ([Environment]::GetFolderPath("CommonDesktopDirectory")) "PlayLimit Time.lnk"
$Desktop2 = Join-Path ([Environment]::GetFolderPath("Desktop")) "PlayLimit Time.lnk"
foreach ($s in @($Startup1, $Startup2, $Desktop1, $Desktop2)) {
    if ($s -and (Test-Path $s)) { Remove-Item $s -Force; Write-Host "Removed $s" }
}

# Optionally keep data dir for history, or delete
$choice = Read-Host "Delete saved usage data (C:\ProgramData\AlbionLimiter)? (y/N)"
if ($choice -eq "y" -or $choice -eq "Y") {
    Remove-Item -Recurse -Force "C:\ProgramData\AlbionLimiter" -ErrorAction SilentlyContinue
    Write-Host "Data deleted"
} else {
    Write-Host "Data kept at C:\ProgramData\AlbionLimiter"
}

# Remove install dir
if (Test-Path $InstallDir) {
    # Reset permissions first
    try { icacls "$InstallDir" /reset /T /Q 2>$null | Out-Null } catch {}
    Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue
    Write-Host "Removed $InstallDir"
}

Write-Host "Uninstall complete!" -ForegroundColor Green
pause
