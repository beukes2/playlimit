# PlayLimit — Albion Online Parental Time Limiter

Limits Albion Online playtime for kids:
- **Weekdays (Mon-Fri): 50 minutes/day**
- **Weekends (Sat-Sun): 2 hours/day**
- 5 minute warning popup before time runs out
- Automatically closes game when time is up
- Blocks reopening until next day (midnight)
- **Parent hotkey: `Ctrl+Alt+T` adds 15 minutes for today** (resets tomorrow)

## How It Works

- Runs silently in background (hidden, no console window)
- Counts only time when Albion Online is actually running
- If game is closed early, remaining time is saved (can't cheat by restarting)
- Counter resets automatically at midnight
- Uses Task Scheduler + Startup shortcut so it survives reboots
- Standard (non-admin) users cannot kill or uninstall it easily

## Files

| File | Description |
|------|-------------|
| `albion_limiter.py` | Main service — polling + hotkey + auto-update |
| `dist/PlayLimit.exe` | Latest compiled executable (auto-updated on launch) |
| `install.ps1` | Installer (run as Administrator) |
| `install.bat` | Batch wrapper for installer |
| `uninstall.ps1` | Uninstaller (run as Administrator) |
| `README.txt` | Plain-text docs |

## Auto-Update

On **every start**, the program checks GitHub and self-updates:

1. Tries `git clone/pull` of `https://github.com/beukes2/playlimit` into `%ProgramData%\AlbionLimiter\repo` (writable cache)
2. Fallback to HTTP `raw.githubusercontent.com` if `git` not available
3. If `dist/PlayLimit.exe` on GitHub is newer, copies to `%ProgramData%\AlbionLimiter\PlayLimit.exe` and **re-launches the new exe** (Python exits)
4. Next startups run the exe directly — `install.ps1`'s scheduled task is auto-patched to launch the exe

Result: just `git push` a new `albion_limiter.py` + rebuilt `dist/PlayLimit.exe`, and all installs update on their next launch (no manual reinstall). If offline, it skips update and runs the cached version.

Build a new exe after changes:
```powershell
py -m pip install pyinstaller psutil
py -m PyInstaller --onefile --noconsole --name PlayLimit albion_limiter.py --distpath dist
git add albion_limiter.py dist/PlayLimit.exe
git commit -m "update + rebuild exe"
git push
```

## Installation

1. Make sure **Python 3.10+** is installed from https://www.python.org — tick **"Add python.exe to PATH"**

2. Right-click `install.ps1` → **Run with PowerShell as Administrator**  
   Or from Admin PowerShell:
   ```powershell
   powershell -ExecutionPolicy Bypass -File install.ps1
   ```

3. The installer will:
   - Copy files to `C:\Program Files\AlbionLimiter`
   - Install `psutil` (if needed)
   - Create a Scheduled Task `AlbionLimiter` that runs at logon/startup (+ 5-min watchdog)
   - Start the limiter immediately

4. Test:
   ```powershell
   Get-Content C:\ProgramData\AlbionLimiter\state.json
   Get-Content C:\ProgramData\AlbionLimiter\limiter.log -Tail 20
   ```

## Parent Hotkey

Press **`Ctrl+Alt+T`** at any time (even while game is fullscreen) to add 15 minutes for **today only**.

- Each press adds another 15 min (press twice = +30 min)
- Bonus resets to 0 at midnight — next day uses default 50min/2h again
- Popup confirms: *"Bonus Added! New total: X min"*
- If game was already blocked (time's up), `Ctrl+Alt+T` unblocks it immediately

Config in `albion_limiter.py`:
```python
WEEKDAY_LIMIT_SEC = 50 * 60
WEEKEND_LIMIT_SEC = 120 * 60
WARNING_BEFORE_SEC = 5 * 60
BONUS_STEP_SEC = 15 * 60   # per Ctrl+Alt+T
```

## Other Overrides

**A. Reset today's counter (Admin):**
```powershell
Remove-Item C:\ProgramData\AlbionLimiter\state.json
Restart-ScheduledTask -TaskName AlbionLimiter
```

**B. Edit limits:**
Edit `C:\Program Files\AlbionLimiter\albion_limiter.py`, change the constants above, then:
```powershell
Start-ScheduledTask AlbionLimiter
```

**C. Temporarily disable:**
`Task Scheduler → Task Scheduler Library → AlbionLimiter → Disable` (needs admin)

## Uninstall

Right-click `uninstall.ps1` → **Run as Administrator**

## FAQ

**What process names are blocked?**  
`Albion-Online.exe`, `AlbionOnline.exe`, `AlbionLauncher.exe` and any process with `albion` in the name.

**Does it need internet?**  
No, runs fully offline.

**Can kids kill it in Task Manager?**  
If installed with default hardened permissions, they need admin to stop the Scheduled Task. They can kill the `pythonw` process, but it restarts within 5 minutes via scheduler.

**What if PC is off at midnight?**  
Counter resets on next boot when date changes.

**Warning not showing?**  
Warning is a Windows MessageBox — if game is fullscreen it may be behind the game. Game will still close after 5 minutes.

## Log & State

- State: `C:\ProgramData\AlbionLimiter\state.json`
- Log: `C:\ProgramData\AlbionLimiter\limiter.log`

Example `state.json`:
```json
{
  "date": "2026-09-03",
  "used_seconds": 1800,
  "warned": false,
  "bonus_seconds": 900,
  "blocked_notified": false
}
```
`bonus_seconds` = extra time added today via `Ctrl+Alt+T`.

## Troubleshooting

- `Task Scheduler → AlbionLimiter → History / Last Run Result`
- Run manually for debugging:
  ```powershell
  py "C:\Program Files\AlbionLimiter\albion_limiter.py"
  ```
