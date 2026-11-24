ALBION ONLINE PARENTAL TIME LIMITER
==================================
Limits Albion Online playtime for kids:
- Weekdays (Mon-Fri): 50 minutes per day
- Weekends (Sat-Sun): 2 hours per day
- 2 minute warning popup before time runs out
- Automatically closes game when time is up
- Blocks reopening until next day (midnight)

HOW IT WORKS
------------
- Runs silently in background (hidden, no console window)
- Counts only time when Albion Online is actually running
- If game is closed early, remaining time is NOT lost but saved (can't cheat by restarting)
- Counter resets automatically at midnight
- Uses Task Scheduler + Startup shortcut so it survives reboots
- Standard (non-admin) users cannot kill or uninstall it easily

FILES
-----
albion_limiter.py  - main program
install.ps1        - installer (run as Administrator)
uninstall.ps1      - uninstaller (run as Administrator)
README.txt         - this file

INSTALLATION
------------
1. Make sure Python 3.10+ is installed (check https://www.python.org)
   During Python install, tick "Add python.exe to PATH"

2. Right-click install.ps1 -> "Run with PowerShell" -> Choose "Run as Administrator"
   (Or open PowerShell as Admin and run: powershell -ExecutionPolicy Bypass -File install.ps1)

3. The installer will:
   - Copy files to C:\Program Files\AlbionLimiter
   - Install psutil (if needed)
   - Create a Scheduled Task that runs at login
   - Start the limiter immediately

4. Test: Open Albion Online, then check log at C:\ProgramData\AlbionLimiter\limiter.log
   Check usage: type in PowerShell: Get-Content C:\ProgramData\AlbionLimiter\state.json

UNINSTALL
---------
Right-click uninstall.ps1 -> Run as Administrator

PARENT CONTROL - PLAYLIMIT SERVER
---------------------------------
There are NO local hotkeys.
Run dist\PlayLimitServer.exe on the parent PC and open:
  http://localhost:8767
From the dashboard you can enable/disable any PC, add/remove today's minutes,
set a daily limit, view logs/history, or ask a PC to exit.

Add a client:
  1. Dashboard -> Download client config (server.json)
  2. Copy it to %ProgramData%\AlbionLimiter\server.json on that PC
  3. Restart PlayLimit
Clients heartbeat every 30 seconds over the LAN.

Other overrides:
Option A - Reset today's counter (PowerShell as Admin):
  Remove-Item C:\ProgramData\AlbionLimiter\state.json
  Restart-ScheduledTask -TaskName AlbionLimiter

Option B - Edit default limits (albion_limiter.py):
  Change WEEKDAY_LIMIT_SEC and WEEKEND_LIMIT_SEC, then rebuild with build.ps1.
  Per-machine overrides are done from the PlayLimit Server dashboard.

FAQ
---
Q: What process names are blocked?
A: Albion-Online.exe, AlbionOnline.exe, AlbionLauncher.exe and any process with "albion" in name.

Q: Does it need internet?
A: No, runs fully offline.

Q: Can kids just kill it in Task Manager?
A: If you hardened permissions (default installer does), they need admin password to stop the Scheduled Task. 
   They can kill the pythonw process, but it restarts within 5 minutes via scheduler.

Q: What if PC is off at midnight?
A: Counter resets on next boot when date changes.

Q: Warning not showing?
A: Warning is a Windows popup (MessageBox). If game is fullscreen, it may be behind game. 
   The game will still close after 2 minutes.

LOG & STATE
-----------
State: C:\ProgramData\AlbionLimiter\state.json  (contains used_seconds today)
Log:   C:\ProgramData\AlbionLimiter\limiter.log

Example state.json:
{
  "date": "2026-09-03",
  "used_seconds": 1800,
  "warned": false,
  "bonus_seconds": 900,
  "blocked_notified": false
}
(bonus_seconds = extra time added today from PlayLimit Server)

SUPPORT
-------
If limiter not starting, check:
- Task Scheduler -> AlbionLimiter -> History/Last Run Result
- Run manually for debugging: py "C:\Program Files\AlbionLimiter\albion_limiter.py"
