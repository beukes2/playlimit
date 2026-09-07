# PlayLimit — Albion Online Parental Time Limiter

Limits Albion Online playtime for kids:
- **Currently: 10 minutes/day (test) — easy to change to 50/120**
- 5 minute warning popup before time runs out
- Automatically closes game when time is up
- Blocks reopening until next day (midnight)
- **Browser block: Chrome/Edge/Firefox/Brave/Opera etc. are auto-closed**
- **Not closable by kids:** close button disabled, Ctrl+C ignored, auto-restarts if killed, runs as SYSTEM when possible
- **Parent hotkeys: `Ctrl+Alt+T` +15 min, `Ctrl+Shift+D` DISABLE app** (resets tomorrow / creates disabled flag)

## How It Works

- Runs with visible console (shows time left every 60s) + background blocking
- Counts only time when Albion Online is actually running
- If game is closed early, remaining time is saved (can't cheat by restarting)
- Counter resets automatically at midnight
- **Browser block:** any browser (`chrome.exe`, `msedge.exe`, `firefox.exe`, `brave.exe`, `opera.exe`, `vivaldi.exe`, `iexplore.exe`...) is terminated within 5 seconds
- Uses Task Scheduler + Startup shortcut so it survives reboots
- **Not closable:** console X disabled, `Ctrl+C`/close ignored, task set `AllowHardTerminate=$false` + `RestartCount 10` (restarts in 1 min if killed), tries to run as `SYSTEM` (standard users get Access Denied), `Protect: anti-close enabled`
- **Disable:** `Ctrl+Shift+D` creates `%ProgramData%\AlbionLimiter\disabled.flag` and disables task — delete flag + reboot to re-enable

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

## Parent Hotkeys

**`Ctrl+Alt+T`** — add 15 minutes for **today only** (even while game is fullscreen):
- Each press adds another 15 min (press twice = +30 min)
- Bonus resets to 0 at midnight
- Popup confirms: *"Bonus Added! New total: X min"*
- If game was already blocked, unblocks it immediately

**`Ctrl+Shift+D`** — **DISABLE PlayLimit** (parent override):
- Instantly stops all blocking (Albion + browsers allowed)
- Creates `%ProgramData%\AlbionLimiter\disabled.flag` and disables scheduled task `AlbionLimiter`
- Console shows `*** DISABLED ***`
- To re-enable: `Remove-Item C:\ProgramData\AlbionLimiter\disabled.flag; Enable-ScheduledTask -TaskName AlbionLimiter` or delete flag and reboot

Config in `albion_limiter.py`:
```python
WEEKDAY_LIMIT_SEC = 10 * 60  # currently 10 min test (set to 50*60 / 120*60 for prod)
WEEKEND_LIMIT_SEC = 10 * 60
WARNING_BEFORE_SEC = 5 * 60  # popup at 5 min left
BONUS_STEP_SEC = 15 * 60     # per Ctrl+Alt+T
BROWSER_PROCESSES = ["chrome.exe","msedge.exe","firefox.exe","brave.exe",...]
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
**Browsers blocked:** `chrome.exe`, `msedge.exe`, `firefox.exe`, `brave.exe`, `opera.exe`, `vivaldi.exe`, `iexplore.exe`, `chromium.exe` etc. — killed within 5 seconds with `Browser Blocked` popup.

**Does it need internet?**  
No, runs fully offline.

**Can kids kill it in Task Manager?**  
No — close button is disabled, Ctrl+C/Close are ignored (`SetConsoleCtrlHandler` + `DeleteMenu`), task has `AllowHardTerminate=$false` and `RestartCount 10` (auto-restarts in 1 min if killed). If installed as `SYSTEM` (default when run as Admin), standard users get `Access Denied` when trying to End Task. They need the admin password or `Ctrl+Shift+D` (which also needs physical access).

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
