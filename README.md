# PlayLimit — Albion Online Parental Time Limiter

Limits Albion Online playtime for kids:
- **Mon–Thu: 45 minutes/day, Fri–Sun: 120 minutes/day**
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

## Auto-Update (self-replacing exe, no admin needed)

The app **runs from** `%ProgramData%\AlbionLimiter\PlayLimit.exe` (writable by standard
users), so it can switch to the latest version by itself:

1. On start (background thread, window opens first) it fetches `version.txt` from GitHub
2. If newer: downloads `dist/PlayLimit.exe` + `dist/PlayLimit.exe.sha256`, checks size,
   `MZ` header and SHA256 — anything failing keeps the old code, nothing is replaced
3. The verified file is staged, then the app does **one clean hop**: a hidden helper waits
   for our PID to exit, **moves staged over live (old bytes deleted)**, removes the
   staged marker, and starts the new exe. Strict version increase = converges, no storm.
4. Old temp exes (`PlayLimit_*.exe`), the legacy git cache and stale `_MEI*` dirs are
   deleted on startup. No popups anywhere — progress goes to `limiter.log` and the new
   window title shows the new version.

Why the old machine kept old code: previous versions ran from `C:\Program Files\...`
(which standard users cannot overwrite) and only *staged* downloads without ever
switching. v1.4.0 runs from `%ProgramData%` and hops automatically.

Build + publish a new version (example v1.4.0):
```powershell
py -m PyInstaller --onefile --noconsole --name PlayLimit --icon playlimit.ico --version-file version_info.txt --distpath dist --workpath build --specpath . --hidden-import pystray --hidden-import PIL --hidden-import psutil albion_limiter.py
$sha = (Get-FileHash dist\PlayLimit.exe -Algorithm SHA256).Hash
"$sha  PlayLimit.exe" | Set-Content dist\PlayLimit.exe.sha256 -NoNewline
git add albion_limiter.py version.txt version_info.txt dist/PlayLimit.exe dist/PlayLimit.exe.sha256
git commit -m "v1.4.0 ..."
git push
```
`version.txt` must contain the new version (e.g. `1.4.0`) or clients will not pick it up.

## Installation (none needed — just open the exe)

1. Download `PlayLimit.exe` (from `dist/` in this repo) and **double-click it** — that's the whole install.
   - First run asks **one UAC Yes/No** (to create the logon task + icons), then the window opens.
   - The exe copies itself to `%ProgramData%\AlbionLimiter\PlayLimit.exe` (the canonical copy
     everything runs from), creates the `AlbionLimiter` logon/startup task and the
     Startup + Desktop shortcuts, and deletes the legacy `C:\Program Files\...` copy.
   - No Python needed (standalone exe). `install.ps1` still exists as an Admin alternative
     that does the same steps.
   - Pin the **Desktop icon** (`PlayLimit Time`) to the taskbar, not a Downloads copy —
     any stale copy auto-hops to the canonical one on start.

2. Test:
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
WEEKDAY_LIMIT_SEC = 45 * 60   # Mon-Thu
WEEKEND_LIMIT_SEC = 120 * 60  # Fri-Sun (Friday counts as weekend)
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

## Remote Logs (read all machines from this PC)

Every running copy (v1.4.1+) uploads its log to the **private** repo
`beukes2/playlimit-logs` under `logs/<HOSTNAME>-<date>.log` — first upload ~60s
after start, then every 10 min. Read them from this PC with:
```powershell
gh repo clone beukes2/playlimit-logs "$env:TEMP\playlimit-logs" 2>$null
Get-ChildItem "$env:TEMP\playlimit-logs\logs" | Sort-Object LastWriteTime -Descending
Get-Content "$env:TEMP\playlimit-logs\logs\<HOST>-<date>.log" -Tail 30
```
Or browse: `https://github.com/beukes2/playlimit-logs/tree/master/logs`

Setup (parent does this once per machine — the token is NEVER in the repo/exe):
1. GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token
2. Resource owner: beukes2, repository access: **Only select repositories → playlimit-logs**, permissions: **Contents → Read and write**, expiry: 1 year (note the date to rotate)
3. As Admin on the machine, save it (no extra spaces/newlines) to `C:\ProgramData\AlbionLimiter\github_token.txt`
4. Restart PlayLimit — log shows `LogShip: token found`, next upload within ~60s
5. No token file = shipping silently off. The token can only touch the logs repo, nothing else.

## Troubleshooting

- `Task Scheduler → AlbionLimiter → History / Last Run Result`
- Run manually for debugging:
  ```powershell
  py "C:\Program Files\AlbionLimiter\albion_limiter.py"
  ```
