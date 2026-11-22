# PlayLimit — Albion Online Parental Time Limiter

Limits Albion Online playtime for kids:
- **Mon–Thu: 1 hour/day TOTAL across all games, Fri–Sun: 120 minutes/day**
- Watched: Albion Online, Minecraft (Java + Bedrock + launcher), Roblox, Fortnite, Riot games, **any game launched through Steam** (detected via parent process) — Java only counts when it's actually Minecraft, Steam client/service/helpers never count
- 2 minute warning popup before time runs out
- Automatically closes game when time is up
- Blocks reopening until next day (midnight)
- **Browser block: Chrome/Edge/Firefox/Brave/Opera etc. are auto-closed**
- **Scheduled browser exception: browsers are allowed Tuesdays 08:00–13:30 (school IT class)**
- **Not closable by kids:** close button disabled, Ctrl+C ignored, auto-restarts if killed, runs as SYSTEM when possible
- **Parent hotkeys: `Ctrl+Shift+I` +15 min, `Ctrl+Shift+E` toggle disable/re-enable, `Ctrl+Shift+D` disable + exit**

## How It Works

- Runs with visible console (shows time left every 60s) + background blocking
- Counts only time when Albion Online is actually running
- If game is closed early, remaining time is saved (can't cheat by restarting)
- Counter resets automatically at midnight
- **Browser block:** any browser (`chrome.exe`, `msedge.exe`, `firefox.exe`, `brave.exe`, `opera.exe`, `vivaldi.exe`, `iexplore.exe`...) is terminated within 5 seconds
- Uses Task Scheduler + Startup shortcut so it survives reboots
- **Not closable:** console X disabled, `Ctrl+C`/close ignored, task set `AllowHardTerminate=$false` + `RestartCount 10` (restarts in 1 min if killed), tries to run as `SYSTEM` (standard users get Access Denied), `Protect: anti-close enabled`
- **Disable:** `Ctrl+Shift+E` toggles disable/re-enable for today while the app keeps running; `Ctrl+Shift+D` disables + exits. Both clear automatically at midnight — no `disabled.flag` and no scheduled-task changes

## Files

| File | Description |
|------|-------------|
| `albion_limiter.py` | Main service — polling + hotkey + auto-update + log/status shipping |
| `dist/PlayLimit.exe` | Native launcher wrapping the real app (auto-updated on launch) |
| `build.ps1` | Builds the real app + native launcher + SHA in one step |
| `bootstrap/` | C# source for the native launcher/apply helper |
| `install.ps1` | Installer (run as Administrator) |
| `install.bat` | Batch wrapper for installer |
| `uninstall.ps1` | Uninstaller (run as Administrator) |
| `repair.bat` / `repair.ps1` | One-click repair/forced update for a machine stuck on an old version |
| `monitor/dashboard.html` | Remote monitoring dashboard - bundled into the exe and opened by the **Logs** button |
| `test_matcher.py` | Game-detection matcher tests |
| `test_basic.py` | Unit tests: limits, state, sessions, status, logging |
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

Build + publish a new version (example v1.7.5):
```powershell
.\build.ps1
git add albion_limiter.py version.txt version_info.txt dist/PlayLimit.exe dist/PlayLimit.exe.sha256
git commit -m "v1.7.5 ..."
git push
```
`build.ps1` builds the real PyInstaller app, wraps it in the native
`dist\PlayLimit.exe` **launcher**, regenerates the SHA, and verifies both versions.

**Why a launcher?** The old updater launches the downloaded exe with PyInstaller's
private `_PYI_*` variables in its environment, which can make a normal PyInstaller
exe fail with `Failed to load Python DLL`. The launcher is native: it ignores those
variables, installs the real app over itself, and starts the real app with a clean
environment. Old and new clients can both update normally.

`version.txt` must contain the new version (e.g. `1.7.5`) or clients will not pick
it up. Never commit a `version.txt` bump without rebuilding `dist/PlayLimit.exe`.

> **Versioning policy (see `AGENTS.md`):** bump the version on *every* change —
> `__version__`, `version_info.txt`, and `version.txt` in lockstep, then run
> `build.ps1` and commit the rebuilt launcher + SHA in the same change.

### If a machine was already left with a broken exe

`repair.bat` remains as a last-resort one-click fix for a machine whose live exe
will not start at all. 1.7.5+ does **not** need it: the launcher handles the old
updater's environment automatically.

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

**`Ctrl+Shift+I`** — add 15 minutes for **today only** (even while game is fullscreen):
- Each press adds another 15 min (press twice = +30 min)
- Bonus resets to 0 at midnight
- Popup confirms: *"Bonus Added! New total: X min"*
- If game was already blocked, unblocks it immediately

**`Ctrl+Shift+E`** — **toggle disable / re-enable for today** (app keeps running):
- First press: instantly stops all blocking (Albion + browsers allowed), window/console shows `*** DISABLED ***`
- Press the **same combo again**: re-enables all limits immediately, no restart needed
- While disabled, `Ctrl+Shift+I` is ignored
- Clears automatically at midnight

**`Ctrl+Shift+D`** — **disable + exit** (parent override):
- If enabled: disables for today and exits the app
- If already disabled (e.g. after `Ctrl+Shift+E`): re-enables and keeps running instead of exiting
- If the app exited, relaunch **PlayLimit Time**; it stays disabled for that day until you press `Ctrl+Shift+E`

There is no `disabled.flag` and the scheduled task is never disabled by these hotkeys.

Config in `albion_limiter.py`:
```python
WEEKDAY_LIMIT_SEC = 60 * 60   # Mon-Thu (shared budget, all games combined)
WEEKEND_LIMIT_SEC = 120 * 60  # Fri-Sun (shared budget, Friday counts as weekend)
WARNING_BEFORE_SEC = 2 * 60  # popup at 2 min left
BONUS_STEP_SEC = 15 * 60     # per Ctrl+Shift+I
BROWSER_PROCESSES = ["chrome.exe","msedge.exe","firefox.exe","brave.exe",...]

# Recurring windows where browser blocking is PAUSED (browsers may stay open).
# Games are still limited/counted as normal. (weekday, start, end, reason)
# weekday: Monday=0 ... Sunday=6
BROWSER_ALLOW_WINDOWS = [
    # Tuesday IT class at school - the child needs a browser for schoolwork
    # between 08:00 and 13:30, so browser blocking is paused then.
    (1, "08:00", "13:30", "School IT class (Tuesday 08:00-13:30)"),
]
```

**Scheduled browser exceptions** (`BROWSER_ALLOW_WINDOWS`): during a listed window
`is_browser_running()`/`kill_browsers()` return early so browsers are left open.
The active window's reason is shown on the machine's card in the dashboard
(“browser allowed now”) and in `status.json` (`browser_allow_reason`). Add more
tuples to allow other days/times; the window is `[start, end)` (end-exclusive).

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
Warning is a Windows MessageBox - if game is fullscreen it may be behind the game. Game will still close after 2 minutes.

## Log & State

- State: `C:\ProgramData\AlbionLimiter\state.json`
- Log: `C:\ProgramData\AlbionLimiter\limiter.log`
- Status heartbeat: `C:\ProgramData\AlbionLimiter\status.json`

### Logging (v1.7.0+)

Every log line now carries a severity level:

```
[2026-10-24 14:03:24] [INFO] Playing... 20m / 120m used, 1h 40m remaining
[2026-10-24 14:05:01] [WARN] LogShip: PUT status/PC-01.json HTTP 409 ...
[2026-10-24 14:05:02] [ERROR] UNCAUGHT exception in thread 'HotkeyListener'
Traceback (most recent call last): ...
```

- `INFO` — normal activity (the historic log lines).
- `WARN` — recoverable problems (failed upload, missing psutil, etc.).
- `ERROR` — failures, **including any uncaught exception** in the main loop or a
  worker thread (a global `sys.excepthook` + `threading.excepthook` route crashes
  to the log with a full traceback, so nothing dies silently).

The last 50 WARN/ERROR lines are also kept in memory and surfaced in `status.json`
(`recent_issues`) so the dashboard can show recent problems without the full log.

### `status.json` (machine-readable heartbeat)

Written atomically every poll (and on startup/shutdown) for the monitor:

```json
{
  "schema": 1,
  "host": "PC-01", "user": "ethan", "version": "1.7.0",
  "heartbeat": "2026-10-24T14:03:24", "heartbeat_epoch": 1792161804, "uptime_sec": 5400,
  "date": "2026-10-24", "weekend": true,
  "base_limit_sec": 7200, "bonus_sec": 900, "limit_sec": 8100,
  "used_sec": 1234, "remaining_sec": 6866,
  "disabled": false, "blocked": false, "warned": false,
  "playing": true, "games_running": ["albion-online.exe"],
  "sessions": [{ "game": "albion-online.exe", "start": "14:00", "end": null, "user": "ethan" }],
  "psutil": true, "browser_block": true, "browser_allow_reason": null,
  "recent_issues": [{ "ts": "...", "level": "WARN", "msg": "..." }],
  "error_count": 0, "warn_count": 1,
  "today": { "total_sec": 1234, "per_game": { "albion-online.exe": 1234 }, "per_user": { "ethan": 1234 } },
  "week": {
    "total_sec": 18000,
    "per_game": { "albion-online.exe": 12000, "(browser)": 4200, "roblox": 1800 },
    "per_user": { "ethan": 15000, "mia": 3000 },
    "days": [{ "date": "2026-10-18", "total_sec": 3600, "per_game": { "roblox": 3600 } }]
  }
}
```

### `history.json` (rolling per-day usage)

A separate file (not reset at midnight) accumulates the last ~35 days of usage so
the monitor can draw daily/weekly summaries:

```json
{ "days": { "2026-10-24": { "total_sec": 5400,
    "per_game": { "albion-online.exe": 3600, "roblox": 1800, "(browser)": 900 },
    "per_user": { "ethan": 5400 } } } }
```

- **Game time** is credited while actually playing (under the limit).
- **Browser time** (`"(browser)"`) is credited whenever a browser is open during an
  allow-window / on an exempt PC — so school-IT browsing shows up in the charts.
- **`per_user`** is the OS username at the time (per-machine state is shared, but
  the active user is attributed to the time credited).

`status.json` embeds today's buckets (`today`) and a 7-day rollup (`week`) so the
dashboard needs only the per-machine status file for its summaries.

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
`bonus_seconds` = extra time added today via `Ctrl+Shift+I`.

## Remote Monitoring Dashboard (v1.7.0+)

`monitor/dashboard.html` is a **single self-contained page** (no server, no build
step) that shows every machine's live status by reading the private logs repo
directly in your browser.

**Open it:** click the **Logs** button in the PlayLimit window (v1.7.4+), or
double-click `monitor/dashboard.html` (or host it anywhere static). The Logs button
opens a stable local copy at `%ProgramData%\AlbionLimiter\dashboard.html`, so the
browser remembers your repo/token between runs.
On first run it asks for:
- the logs repo (`beukes2/playlimit-logs`), and
- a GitHub **fine-grained, read-only** token (Contents → **Read-only**, scoped to
  **only** the logs repo). This is different from the machines' write token — the
  dashboard never needs write access. The token is stored only in that browser's
  `localStorage` and is sent only to `api.github.com`.

**Overview (top of the page):**
- **Today — per host & user:** a table of time used today per machine and per OS
  user, with an all-machines total.
- **This week — by game / browser / app:** horizontal bars of time per game (and a
  `🌐 Browser` bucket), summed across all machines over the last 7 days.
- **This week — total per day:** a 7-column bar chart of total time per day (today
  highlighted).

**Per machine:**
- Live badge: **Playing / Idle / Blocked / Disabled / Offline** (offline = no
  heartbeat for ~6 min).
- Used-vs-limit progress bar, remaining time, bonus, weekday/weekend.
- Heartbeat age + uptime, current user, running games, and flags
  (`browser block on/off`, a `browser allowed now` pill during a scheduled
  exception, `psutil`, error/warn counts).
- Today's session timeline and the most recent WARN/ERROR lines.
- A **View full log** button that pulls that day's `logs/<host>-<date>.log`.

Auto-refreshes (default 30 s). Because machines ship `status.json` every ~2 min,
the dashboard is near-live; the full log tail still ships every 10 min.

**Try it with no setup:** open `monitor/dashboard.html?demo=1` to see the layout
rendered with three sample machines (playing / blocked / offline) — no repo or
token needed. Handy for a quick look or for testing UI changes.

## Remote Logs (read all machines from this PC)

Every running copy (v1.4.1+) uploads its log to the **private** repo
`beukes2/playlimit-logs` under `logs/<HOSTNAME>-<date>.log` — first upload ~60s
after start, then every 10 min. Since **v1.7.0** each copy also ships a
machine-readable heartbeat to `status/<HOSTNAME>.json` every ~2 min (this is what
the dashboard reads). Read the logs from this PC with:
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

## Tests

Pure-Python, run on any OS (they redirect state to a temp dir, never touching
`ProgramData`):

```bash
python3 -m unittest test_basic -v   # limits, state + sanitization, sessions, status, logging
python3 test_matcher.py             # game-detection matcher contract
```

The dashboard has a demo mode for a quick visual check — open
`monitor/dashboard.html?demo=1` in any browser.

## Troubleshooting

- `Task Scheduler → AlbionLimiter → History / Last Run Result`
- Run manually for debugging:
  ```powershell
  py "C:\Program Files\AlbionLimiter\albion_limiter.py"
  ```
