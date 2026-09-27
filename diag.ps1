# PlayLimit diagnostics - prints everything needed to debug a machine.
# Run via diag.bat (double-click). Read-only: changes nothing.
$ErrorActionPreference = "SilentlyContinue"

function Line($t) { Write-Host ""; Write-Host "=== $t ===" -ForegroundColor Cyan }

Write-Host "PlayLimit DIAGNOSTICS" -ForegroundColor Green
Write-Host ("Time: " + (Get-Date))

Line "Admin?"
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")
Write-Host "Running as Administrator: $isAdmin"
Write-Host ("User: " + [System.Security.Principal.WindowsIdentity]::GetCurrent().Name)

Line "Host"
Write-Host ("Computer: " + $env:COMPUTERNAME)

Line "Running PlayLimit processes"
$procs = Get-CimInstance Win32_Process -Filter "name like 'PlayLimit%'" |
    Select-Object ProcessId, Name, ExecutablePath, CreationDate
if ($procs) { $procs | Format-List } else { Write-Host "none running" }

Line "Exe versions on disk"
foreach ($p in @(
    "C:\ProgramData\AlbionLimiter\PlayLimit.exe",
    "C:\Program Files\AlbionLimiter\PlayLimit.exe",
    "$env:USERPROFILE\Downloads\PlayLimit.exe",
    "$env:USERPROFILE\Desktop\PlayLimit.exe")) {
    if (Test-Path $p) {
        $v = (Get-Item $p).VersionInfo.FileVersion
        $len = (Get-Item $p).Length
        Write-Host "$p -> v$v ($len bytes)"
    } else {
        Write-Host "$p -> MISSING"
    }
}

Line "Data dir contents (updater files)"
Get-ChildItem "C:\ProgramData\AlbionLimiter" -File |
    Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize
Write-Host "staged update present:"
Write-Host ("  PlayLimit.staged.exe: " + (Test-Path "C:\ProgramData\AlbionLimiter\PlayLimit.staged.exe"))
Write-Host ("  PlayLimit.staged.ver: " + (Test-Path "C:\ProgramData\AlbionLimiter\PlayLimit.staged.ver"))
Write-Host ("  github_token.txt:     " + (Test-Path "C:\ProgramData\AlbionLimiter\github_token.txt"))
Write-Host ("  no_browser_block:     " + (Test-Path "C:\ProgramData\AlbionLimiter\no_browser_block"))

Line "Scheduled task"
$taskOut = & schtasks /Query /TN AlbionLimiter /V /FO LIST 2>&1 | Out-String
if ($taskOut -and $taskOut.Trim()) {
    foreach ($l in ($taskOut -split "`r?`n")) {
        if ($l -match "TaskName|Status|Task To Run|Run As User|Last Run Time|Last Result|Logon Mode") {
            Write-Host $l.Trim()
        }
    }
} else {
    Write-Host "schtasks returned nothing (task missing?)"
}

Line "Shortcuts -> targets"
$sh = New-Object -ComObject WScript.Shell
$links = @(
    (Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\PlayLimit.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "PlayLimit Time.lnk"),
    ("C:\Users\Public\Desktop\PlayLimit Time.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Startup")) "AlbionLimiter.lnk"),
    ("C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Startup\AlbionLimiter.lnk")
)
foreach ($l in $links) {
    if (Test-Path $l) {
        $s = $sh.CreateShortcut($l)
        Write-Host "$l"
        Write-Host "    -> $($s.TargetPath) $($s.Arguments)"
    } else {
        Write-Host "$l -> MISSING"
    }
}

Line "GitHub reachability / remote version"
# Use whatever exe is present to ask GitHub for the published version
try {
    $req = [System.Net.WebRequest]::Create("https://raw.githubusercontent.com/beukes2/playlimit/master/version.txt")
    $req.Timeout = 15000
    $resp = $req.GetResponse()
    $sr = New-Object System.IO.StreamReader($resp.GetResponseStream())
    $rv = $sr.ReadToEnd().Trim()
    Write-Host "remote version: $rv"
} catch {
    Write-Host "FAILED to reach GitHub: $($_.Exception.Message)" -ForegroundColor Red
}

Line "state.json"
if (Test-Path "C:\ProgramData\AlbionLimiter\state.json") {
    Get-Content "C:\ProgramData\AlbionLimiter\state.json"
} else { Write-Host "MISSING" }

Line "install.log (last 15)"
if (Test-Path "C:\ProgramData\AlbionLimiter\install.log") {
    Get-Content "C:\ProgramData\AlbionLimiter\install.log" -Tail 15
} else { Write-Host "no install.log" }

Line "hop.log (last 15)"
if (Test-Path "C:\ProgramData\AlbionLimiter\hop.log") {
    Get-Content "C:\ProgramData\AlbionLimiter\hop.log" -Tail 15
} else { Write-Host "no hop.log" }

Line "limiter.log - updater lines (last 20)"
Get-Content "C:\ProgramData\AlbionLimiter\limiter.log" |
    Select-String -Pattern "Updater:|Install:|hop|Update button" |
    Select-Object -Last 20

Line "limiter.log - tail 30"
Get-Content "C:\ProgramData\AlbionLimiter\limiter.log" -Tail 30

Line "END OF DIAGNOSTICS"
Write-Host "Copy everything above and send it back." -ForegroundColor Yellow
exit 0
