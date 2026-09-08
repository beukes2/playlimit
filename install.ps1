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

# Check Python
try {
    $py = Get-Command py -ErrorAction Stop
    $pyVersion = & py --version 2>&1
    Write-Host "Found Python: $pyVersion" -ForegroundColor Green
} catch {
    try {
        $py = Get-Command python -ErrorAction Stop
        $pyVersion = & python --version 2>&1
        Write-Host "Found Python: $pyVersion" -ForegroundColor Green
    } catch {
        Write-Host "ERROR: Python not found. Install Python 3.10+ from https://www.python.org and add to PATH" -ForegroundColor Red
        pause
        exit 1
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

# Copy script
$SourceScript = Join-Path $PSScriptRoot $ScriptName
if (-not (Test-Path $SourceScript)) {
    Write-Host "ERROR: $ScriptName not found next to install.ps1" -ForegroundColor Red
    pause
    exit 1
}
Copy-Item -Path $SourceScript -Destination (Join-Path $InstallDir $ScriptName) -Force
Write-Host "Copied $ScriptName -> $InstallDir" -ForegroundColor Green

# Copy exe if present (dist/PlayLimit.exe) - preferred launch method, no Python needed
$ExeSource = Join-Path $PSScriptRoot "dist\PlayLimit.exe"
$ExeDest = Join-Path $InstallDir "PlayLimit.exe"
$HasExe = Test-Path $ExeSource
if ($HasExe) {
    Copy-Item -Path $ExeSource -Destination $ExeDest -Force
    Write-Host "Copied PlayLimit.exe -> $InstallDir (will be auto-updated from GitHub on each start)" -ForegroundColor Green
} else {
    # Also check root PlayLimit.exe (if user downloaded exe directly)
    $AltExe = Join-Path $PSScriptRoot "PlayLimit.exe"
    if (Test-Path $AltExe) {
        Copy-Item -Path $AltExe -Destination $ExeDest -Force
        $HasExe = $true
        Write-Host "Copied PlayLimit.exe -> $InstallDir" -ForegroundColor Green
    }
}

# Install psutil (optional but recommended) - not needed if using exe
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

# Create a VBS launcher to run hidden (no console window)
$VbsPath = Join-Path $InstallDir "launch_hidden.vbs"
if ($HasExe) {
    $VbsContent = @"
Set WshShell = CreateObject("WScript.Shell")
' Run exe hidden, no window
WshShell.Run """$ExeDest""", 0, False
Set WshShell = Nothing
"@
} else {
    $VbsContent = @"
Set WshShell = CreateObject("WScript.Shell")
' Run with pythonw hidden, no window
WshShell.Run """$PythonW"" ""$InstallDir\$ScriptName""", 0, False
Set WshShell = Nothing
"@
}
Set-Content -Path $VbsPath -Value $VbsContent -Encoding ASCII
Write-Host "Created hidden launcher: $VbsPath" -ForegroundColor Green

# Create Scheduled Task - runs at logon for all users + at startup, hidden + NOT CLOSABLE
Write-Host "Creating Scheduled Task '$TaskName'..." -ForegroundColor Yellow

# Remove old task if exists
try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue } catch {}

$Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$VbsPath`""
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
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger1,$Trigger2 -Settings $Settings -Principal $Principal -Description "PlayLimit GUI - 10min limit, browser block, visible window - Ctrl+Shift+D disables and exits" | Out-Null
    Write-Host "Scheduled Task created as Users (visible window, no auto-restart on close)!" -ForegroundColor Green
    $Created = $true
} catch {
    Write-Host "Users task failed ($_), trying current user..." -ForegroundColor Yellow
    $CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $Principal2 = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Highest
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger1,$Trigger2 -Settings $Settings -Principal $Principal2 -Description "PlayLimit GUI - 10min limit, browser block, visible window" | Out-Null
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
    $Shortcut.TargetPath = "wscript.exe"
    $Shortcut.Arguments = "`"$VbsPath`""
    $Shortcut.WorkingDirectory = $InstallDir
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
        $Shortcut2.TargetPath = $ExeDest
        $Shortcut2.Arguments = "--show-time"
        $Shortcut2.IconLocation = $ExeDest
    } else {
        $Shortcut2.TargetPath = $PythonW
        $Shortcut2.Arguments = "`"$InstallDir\$ScriptName`" --show-time"
    }
    $Shortcut2.WorkingDirectory = $InstallDir
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
    # Also run immediately via wscript for instant effect
    Start-Process "wscript.exe" -ArgumentList "`"$VbsPath`"" -WindowStyle Hidden
    Write-Host "Limiter started!" -ForegroundColor Green
} catch {
    Write-Host "Task start warning: $_" -ForegroundColor Yellow
    Start-Process "wscript.exe" -ArgumentList "`"$VbsPath`"" -WindowStyle Hidden
}

Write-Host ""
Write-Host "=== INSTALL COMPLETE ===" -ForegroundColor Cyan
Write-Host "Limits: 10 minutes (currently test, edit albion_limiter.py for 50/120)" -ForegroundColor White
Write-Host "Warning: 5 minutes before limit (popup)" -ForegroundColor White
Write-Host "Behavior: closes Albion + blocks reopen until midnight, browser block ON (chrome/edge/firefox/brave/opera)" -ForegroundColor White
Write-Host "Hotkeys: Ctrl+Alt+T = +15 min today, Ctrl+Shift+D = DISABLE PlayLimit" -ForegroundColor White
Write-Host "Console: shows time left every 60s (cannot be closed by kids - close button disabled)" -ForegroundColor White
Write-Host "Protection: Task restarts if killed (1 min), runs as SYSTEM if possible - kids need admin to stop" -ForegroundColor White
Write-Host ""
Write-Host "Data file: $DataDir\state.json" -ForegroundColor Gray
Write-Host "Log file:  $DataDir\limiter.log" -ForegroundColor Gray
Write-Host "To check status: Get-Content `"$DataDir\state.json`"" -ForegroundColor Gray
Write-Host "To test browser block: try opening Chrome - it will be closed" -ForegroundColor Gray
Write-Host "To disable: Press Ctrl+Shift+D (creates $DataDir\disabled.flag) or Run: schtasks /Change /TN AlbionLimiter /Disable" -ForegroundColor Gray
Write-Host ""
Write-Host "To uninstall, run uninstall.ps1 as Administrator" -ForegroundColor Yellow
Write-Host ""
pause
