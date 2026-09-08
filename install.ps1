# AlbionLimiter Installer - Run as Administrator
# Right-click -> Run with PowerShell as Administrator

$ErrorActionPreference = "Stop"

$InstallDir = "C:\Program Files\AlbionLimiter"
$DataDir = "C:\ProgramData\AlbionLimiter"
$ScriptName = "albion_limiter.py"
$TaskName = "AlbionLimiter"

Write-Host "=== Albion Online Parental Limiter - Installer ===" -ForegroundColor Cyan
Write-Host ""

# Check admin
if (-NOT ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Host "ERROR: Please run this installer as Administrator!" -ForegroundColor Red
    Write-Host "Right-click install.ps1 -> Run with PowerShell as Administrator" -ForegroundColor Yellow
    pause
    exit 1
}

# Check Python (optional - only needed if running the .py directly; the exe is standalone)
$HasPython = $false
try {
    $py = Get-Command py -ErrorAction Stop
    $pyVersion = & py --version 2>&1
    Write-Host "Found Python: $pyVersion" -ForegroundColor Green
    $HasPython = $true
} catch {
    try {
        $py = Get-Command python -ErrorAction Stop
        $pyVersion = & python --version 2>&1
        Write-Host "Found Python: $pyVersion" -ForegroundColor Green
        $HasPython = $true
    } catch {
        Write-Host "Note: no Python found - fine, the standalone exe needs none." -ForegroundColor Yellow
    }
}

# Determine python executable to use
$PythonExe = "py"
try { & py --version 2>&1 | Out-Null } catch { $PythonExe = "python" }
if ($PythonExe -eq "py") {
    # Use py launcher with pythonw for hidden window
    $PythonW = "pyw"
    try { & pyw --version 2>&1 | Out-Null } catch { $PythonW = "pythonw" }
} else {
    $PythonW = "pythonw"
}

Write-Host "Using launcher: $PythonExe / $PythonW" -ForegroundColor Gray

# Create directories
Write-Host "Creating directories..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

# Stop any running copy first (exe cannot be replaced while running)
Write-Host "Stopping any running PlayLimit..." -ForegroundColor Yellow
try { Get-Process PlayLimit* -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Seconds 1

# Copy script
$SourceScript = Join-Path $PSScriptRoot $ScriptName
if (-not (Test-Path $SourceScript)) {
    Write-Host "ERROR: $ScriptName not found next to install.ps1" -ForegroundColor Red
    pause
    exit 1
}
Copy-Item -Path $SourceScript -Destination (Join-Path $InstallDir $ScriptName) -Force
Write-Host "Copied $ScriptName -> $InstallDir" -ForegroundColor Green

# The app RUNS from $DataDir\PlayLimit.exe (writable by standard users) so it can
# replace itself with verified newer versions without admin. This is the live copy.
$ExeSource = Join-Path $PSScriptRoot "dist\PlayLimit.exe"
$LiveExe = Join-Path $DataDir "PlayLimit.exe"
$HasExe = Test-Path $ExeSource
if (-not $HasExe) {
    $AltExe = Join-Path $PSScriptRoot "PlayLimit.exe"
    if (Test-Path $AltExe) { $ExeSource = $AltExe; $HasExe = $true }
}
if (-not $HasExe) {
    Write-Host "ERROR: PlayLimit.exe not found (expected dist\PlayLimit.exe next to install.ps1)" -ForegroundColor Red
    pause
    exit 1
}
Copy-Item -Path $ExeSource -Destination $LiveExe -Force
Write-Host "Installed PlayLimit.exe -> $DataDir (self-updates from GitHub, no admin needed)" -ForegroundColor Green

# Remove legacy Program Files copy (old versions ran from here and could never self-update)
$LegacyExe = Join-Path $InstallDir "PlayLimit.exe"
if (Test-Path $LegacyExe) {
    try { Remove-Item -Force $LegacyExe -ErrorAction Stop; Write-Host "Removed legacy $LegacyExe (old code deleted)" -ForegroundColor Green }
    catch { Write-Host "Note: could not remove legacy exe (may be locked): $_" -ForegroundColor Yellow }
}
$LegacyVbs = Join-Path $InstallDir "launch_hidden.vbs"
if (Test-Path $LegacyVbs) {
    try { Remove-Item -Force $LegacyVbs -ErrorAction SilentlyContinue } catch {}
}

# Python is optional now (exe is standalone); only needed for running the .py directly
if (-not $HasExe) {
    Write-Host "Installing dependencies (psutil)..." -ForegroundColor Yellow
    try {
        & $PythonExe -m pip install --quiet psutil
        Write-Host "psutil installed" -ForegroundColor Green
    } catch {
        Write-Host "Warning: could not install psutil, fallback to tasklist will be used" -ForegroundColor Yellow
    }
} else {
    Write-Host "Exe found, skipping Python dependency install (exe is standalone)" -ForegroundColor Green
}

# Create Scheduled Task - runs at logon + at startup as the logged-on user (visible window, no auto-restart)
Write-Host "Creating Scheduled Task '$TaskName'..." -ForegroundColor Yellow

# Remove old task if exists
try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue } catch {}

$Action = New-ScheduledTaskAction -Execute "$LiveExe"
$Trigger1 = New-ScheduledTaskTrigger -AtLogOn
$Trigger2 = New-ScheduledTaskTrigger -AtStartup
# NOTE: no 5-minute watchdog trigger on purpose. Window visible = running, closed = fully gone.
# The task only starts the app at logon/startup. Closing the window exits the process and
# Task Scheduler does NOT restart it (RestartCount 0).

# Settings: allow run on battery, don't stop. RestartCount 0 so a user close stays closed.
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Days 365) -RestartCount 0 -MultipleInstances IgnoreNew
$Settings.Hidden = $false
$Settings.DisallowStartIfOnBatteries = $false
$Settings.AllowHardTerminate = $true  # Parent can End Task via Task Manager; closing window exits fully

# Users principal only (visible window on the user's desktop). No SYSTEM fallback - SYSTEM
# runs in session 0 where the window is invisible, which violates "window visible = running".
$Created = $false
try {
    $Principal = New-ScheduledTaskPrincipal -GroupId "Users" -RunLevel Highest
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger1,$Trigger2 -Settings $Settings -Principal $Principal -Description "PlayLimit GUI - visible window, self-updating - Ctrl+Shift+D disables and exits" | Out-Null
    Write-Host "Scheduled Task created as Users (visible window, no auto-restart on close)!" -ForegroundColor Green
    $Created = $true
} catch {
    Write-Host "Users task failed ($_), trying current user..." -ForegroundColor Yellow
    $CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $Principal2 = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Highest
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger1,$Trigger2 -Settings $Settings -Principal $Principal2 -Description "PlayLimit GUI - visible window, self-updating" | Out-Null
    Write-Host "Scheduled Task created (user-specific, visible, no auto-restart)!" -ForegroundColor Green
    $Created = $true
}

# Also add to Startup folder as fallback (for non-admin installs)
$StartupDir = [Environment]::GetFolderPath("CommonStartup")
if (-not (Test-Path $StartupDir)) { $StartupDir = [Environment]::GetFolderPath("Startup") }
$ShortcutPath = Join-Path $StartupDir "AlbionLimiter.lnk"
try {
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = "$LiveExe"
    $Shortcut.WorkingDirectory = $DataDir
    $Shortcut.Description = "AlbionLimiter - Parental Control"
    $Shortcut.Save()
    Write-Host "Startup shortcut created: $ShortcutPath" -ForegroundColor Green
} catch {
    Write-Host "Could not create startup shortcut: $_" -ForegroundColor Yellow
}

# Create desktop icon to open little time-remaining screen
$DesktopDir = [Environment]::GetFolderPath("CommonDesktopDirectory")
if (-not (Test-Path $DesktopDir)) { $DesktopDir = [Environment]::GetFolderPath("Desktop") }
$DesktopPath = Join-Path $DesktopDir "PlayLimit Time.lnk"
try {
    $WshShell2 = New-Object -ComObject WScript.Shell
    $Shortcut2 = $WshShell2.CreateShortcut($DesktopPath)
    if ($HasExe) {
        $Shortcut2.TargetPath = "$LiveExe"
        $Shortcut2.Arguments = "--show-time"
        $Shortcut2.IconLocation = "$LiveExe"
    } else {
        $Shortcut2.TargetPath = $PythonW
        $Shortcut2.Arguments = "`"$InstallDir\$ScriptName`" --show-time"
    }
    $Shortcut2.WorkingDirectory = $DataDir
    $Shortcut2.Description = "PlayLimit - Show remaining time"
    $Shortcut2.Save()
    Write-Host "Desktop icon created: $DesktopPath (double-click to see time left)" -ForegroundColor Green
} catch {
    Write-Host "Could not create desktop icon: $_" -ForegroundColor Yellow
}

# Set permissions: make files not easily deletable by standard users (optional)
try {
    icacls "$InstallDir" /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" /grant:r "Administrators:(OI)(CI)F" /grant:r "Users:(OI)(CI)RX" | Out-Null
    icacls "$DataDir" /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" /grant:r "Administrators:(OI)(CI)F" /grant:r "Users:(OI)(CI)M" | Out-Null
    Write-Host "Permissions hardened (kids need admin to uninstall)" -ForegroundColor Green
} catch {
    Write-Host "Note: could not harden permissions" -ForegroundColor Yellow
}

# Start task now
Write-Host "Starting limiter now..." -ForegroundColor Yellow
try {
    Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    # Also run immediately for instant effect
    Start-Process "$LiveExe" -WindowStyle Hidden
    Write-Host "Limiter started!" -ForegroundColor Green
} catch {
    Write-Host "Task start warning: $_" -ForegroundColor Yellow
    Start-Process "$LiveExe" -WindowStyle Hidden
}

Write-Host ""
Write-Host "=== INSTALL COMPLETE ===" -ForegroundColor Cyan
Write-Host "Limits: Mon-Thu 45 minutes, Fri-Sun 120 minutes" -ForegroundColor White
Write-Host "Runs from: $LiveExe (self-updates from GitHub, no admin needed)" -ForegroundColor White
Write-Host "Behavior: closes Albion + blocks reopen until midnight, browser block ON (chrome/edge/firefox/brave/opera)" -ForegroundColor White
Write-Host "Hotkeys: Ctrl+Alt+T = +15 min today, Ctrl+Alt+D = close, Ctrl+Shift+D = disable + exit" -ForegroundColor White
Write-Host "Window: shows time left (X/taskbar close blocked for kids; no popups of any kind)" -ForegroundColor White
Write-Host ""
Write-Host "Data file: $DataDir\state.json" -ForegroundColor Gray
Write-Host "Log file:  $DataDir\limiter.log" -ForegroundColor Gray
Write-Host "To check status: Get-Content `"$DataDir\state.json`"" -ForegroundColor Gray
Write-Host "To test browser block: try opening Chrome - it will be closed" -ForegroundColor Gray
Write-Host "To disable: press Ctrl+Shift+D, or run: schtasks /Change /TN AlbionLimiter /Disable" -ForegroundColor Gray
Write-Host ""
Write-Host "To uninstall, run uninstall.ps1 as Administrator" -ForegroundColor Yellow
Write-Host ""
pause
