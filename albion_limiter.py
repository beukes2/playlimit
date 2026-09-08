#!/usr/bin/env python3
"""
Albion Online Parental Time Limiter
- Mon-Thu: 45 minutes
- Fri-Sun: 120 minutes (2 hours, Friday counts as weekend)
- 5 minute warning, then closes game and blocks re-open until next day (midnight)

Runs silently in background. Install with install.ps1 (requires Admin).
State stored in %ProgramData%\\AlbionLimiter\\state.json
"""

import os
import sys
import json
import time
import datetime
import subprocess
import ctypes
import threading
from pathlib import Path

__version__ = "1.4.1"
APP_NAME = "PlayLimit"

# ---------- CONFIG ----------
WEEKDAY_LIMIT_SEC = 45 * 60          # Mon-Thu default
WEEKEND_LIMIT_SEC = 120 * 60         # Fri-Sun default (Friday counts as weekend)
WARNING_BEFORE_SEC = 5 * 60          # 5 minute warning
POLL_INTERVAL_SEC = 5                # check every 5 seconds
GRACEFUL_CLOSE_TIMEOUT = 15          # seconds to wait after WM_CLOSE before kill
BONUS_STEP_SEC = 15 * 60             # added per Ctrl+Alt+T press

# Albion Online process names to watch (covers launcher variants)
TARGET_PROCESSES = [
    "Albion-Online.exe",
    "AlbionOnline.exe",
    "Albion-Online Launcher.exe",
    "AlbionLauncher.exe",
    "Albion-Online.exe",  # duplicate for safety
]

# Browser processes to block (kids not allowed to open any browser)
BROWSER_PROCESSES = [
    "chrome.exe",
    "chrome_proxy.exe",
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
    "opera_gx.exe",
    "vivaldi.exe",
    "iexplore.exe",
    "chromium.exe",
    "whale.exe",
    "arc.exe",
    "browser.exe",
]

# Browser block exempt - private; set env var PLAYLIMIT_EXEMPT=1 or create file %ProgramData%\AlbionLimiter\no_browser_block on dev PC
def is_browser_block_exempt() -> bool:
    try:
        # Env var exempts this PC (set PLAYLIMIT_EXEMPT=1)
        if os.environ.get("PLAYLIMIT_EXEMPT", "").lower() in ("1", "true", "yes"):
            return True
        # Local flag file exempts (create empty file C:\ProgramData\AlbionLimiter\no_browser_block on your dev PC)
        try:
            if DATA_DIR and (DATA_DIR / "no_browser_block").exists():
                return True
            # Optional host file: contains hostname to exempt
            host_file = DATA_DIR / "exempt_host.txt"
            if host_file.exists():
                import socket
                exempt_host = host_file.read_text(encoding="utf-8").strip().upper()
                if exempt_host and (socket.gethostname().upper() == exempt_host or os.environ.get("COMPUTERNAME", "").upper() == exempt_host):
                    return True
        except Exception:
            pass
    except Exception:
        pass
    return False

# App control
APP_DISABLED = False  # set True by Ctrl+Shift+D hotkey
DISABLE_FLAG_FILE = None  # set after DATA_DIR known

# State / log locations
def get_data_dir():
    # Prefer ProgramData (persists across users, survives if kids delete AppData)
    for p in [Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "AlbionLimiter",
              Path(os.environ.get("APPDATA", str(Path.home()))) / "AlbionLimiter"]:
        try:
            p.mkdir(parents=True, exist_ok=True)
            test = p / ".writetest"
            test.write_text("ok", encoding="utf-8")
            test.unlink(missing_ok=True)
            return p
        except Exception:
            continue
    return Path.cwd() / "AlbionLimiter_data"

DATA_DIR = get_data_dir()
STATE_FILE = DATA_DIR / "state.json"
LOG_FILE = DATA_DIR / "limiter.log"
DISABLE_FLAG_FILE = DATA_DIR / "disabled.flag"

# ---------- AUTO-UPDATE CONFIG ----------
# The app RUNS from LIVE_EXE (inside DATA_DIR, writable by standard users), so it can
# replace itself without admin. Flow: check version.txt -> download exe+sha256 ->
# verify -> stage -> one clean hop (helper swaps files after we exit, relaunches).
# Strict version increase + verified hash = converges, no respawn storm possible.
AUTO_UPDATE_ENABLED = True
REPO_RAW_BASE = "https://raw.githubusercontent.com/beukes2/playlimit/master"
LIVE_EXE = DATA_DIR / "PlayLimit.exe"
STAGED_EXE = DATA_DIR / "PlayLimit.staged.exe"
STAGED_VER = DATA_DIR / "PlayLimit.staged.ver"
UPDATE_TIMEOUT_SEC = 20

# Try to import psutil optionally
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

def log(msg: str):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass  # stdout broken during teardown (frozen exe) - file log below still works
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

_shutdown = threading.Event()
_tk_root = None

def request_shutdown(reason="closed"):
    """Signal full app exit. Window closed = process exits, no traces left."""
    try:
        import traceback
        log(f"Shutdown requested ({reason}) - closing window, tray, hotkeys, enforcement")
        log("request_shutdown stack: " + "".join(traceback.format_stack()[-8:-1]))
    except Exception:
        log(f"Shutdown requested ({reason}) - closing window, tray, hotkeys, enforcement")
    _shutdown.set()
    # Stop tray icon if running
    try:
        global _tray_icon
        if '_tray_icon' in globals() and _tray_icon is not None:
            try:
                _tray_icon.stop()
            except Exception:
                pass
    except Exception:
        pass
    # Close Tk window if open (schedule on Tk thread if needed)
    try:
        global _tk_root
        if _tk_root is not None:
            try:
                _tk_root.after(0, _tk_root.destroy)
            except Exception:
                try:
                    _tk_root.destroy()
                except Exception:
                    pass
    except Exception:
        pass

def is_disabled() -> bool:
    # No persistent flag - disabled is in-memory only for this session, always starts enabled
    return APP_DISABLED

def disable_app():
    global APP_DISABLED
    APP_DISABLED = True
    log("=== PlayLimit DISABLED by Ctrl+Shift+D - shutting down fully (no traces) ===")
    # No popup, no flag file. Disable scheduled task so it does not restart hidden,
    # then exit the whole process so Task Manager shows nothing.
    try:
        subprocess.run(["schtasks", "/Change", "/TN", "AlbionLimiter", "/DISABLE"], capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        log("Scheduled task AlbionLimiter disabled (disable hotkey)")
    except Exception as e:
        log(f"Disable task failed: {e}")
    request_shutdown("disabled via Ctrl+Shift+D")
    # No os._exit: hotkey loop below sees _shutdown and breaks, enforcement loop
    # exits, Tk mainloop already destroyed -> clean interpreter teardown so the
    # PyInstaller temp dir removes cleanly (no _MEI warning popup).

def enable_app():
    global APP_DISABLED
    APP_DISABLED = False
    try:
        if DISABLE_FLAG_FILE.exists():
            DISABLE_FLAG_FILE.unlink()
    except Exception:
        pass
    try:
        subprocess.run(["schtasks", "/Change", "/TN", "AlbionLimiter", "/ENABLE"], capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        pass
    log("PlayLimit re-enabled")

def protect_process():
    """No-op now: app must be closable. Window visible = running, closed = fully gone."""
    return

def is_weekend(d: datetime.date = None) -> bool:
    if d is None:
        d = datetime.date.today()
    return d.weekday() >= 5  # 5=Sat, 6=Sun

def get_daily_limit_sec(d: datetime.date = None) -> int:
    # Mon-Thu 45 min, Fri-Sun 120 min (Friday counts as weekend)
    if d is None:
        d = datetime.date.today()
    return WEEKEND_LIMIT_SEC if d.weekday() >= 4 else WEEKDAY_LIMIT_SEC

def get_effective_limit_sec(d: datetime.date = None, state: dict = None) -> int:
    """Base limit + bonus for today (bonus resets daily via state date)."""
    base = get_daily_limit_sec(d)
    bonus = 0
    if state is not None:
        bonus = int(state.get("bonus_seconds", 0))
    else:
        # try to read from file if no state passed
        try:
            s = load_state()
            bonus = int(s.get("bonus_seconds", 0))
        except Exception:
            pass
    return base + bonus

_state_lock = threading.Lock()

def load_state() -> dict:
    default = {
        "date": datetime.date.today().isoformat(),
        "used_seconds": 0,
        "warned": False,
        "last_seen_running": False,
        "bonus_seconds": 0,
        "blocked_notified": False
    }
    if not STATE_FILE.exists():
        return default
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        # Reset if new day
        if data.get("date") != datetime.date.today().isoformat():
            log(f"New day detected ({data.get('date')} -> {datetime.date.today().isoformat()}), resetting counter")
            return default
        return {**default, **data}
    except Exception as e:
        log(f"State load error: {e}, resetting")
        return default

def save_state(state: dict):
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        log(f"State save error: {e}")

# ---------- AUTO-UPDATE HELPERS ----------
def _sha256_file(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def _files_equal(a: Path, b: Path) -> bool:
    try:
        if not a.exists() or not b.exists():
            return False
        if a.stat().st_size != b.stat().st_size:
            return False
        return _sha256_file(a) == _sha256_file(b)
    except Exception:
        return False

def _parse_ver(v: str):
    try:
        return tuple(int(x) for x in v.strip().split('.'))
    except Exception:
        return (0,)

def _download_bytes(url: str, timeout: int):
    """Download raw bytes or return None. No popups, log only."""
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status != 200:
                log(f"Updater: HTTP {resp.status} for {url}")
                return None
            return resp.read()
    except Exception as e:
        log(f"Updater: download failed {url}: {e}")
        return None

def _cleanup_old_files():
    """Delete old/stale updater leftovers so old code never accumulates on disk."""
    # Legacy git cache dir (no longer used) - remove whole tree
    try:
        legacy_repo = DATA_DIR / "repo"
        if legacy_repo.exists():
            import shutil
            shutil.rmtree(legacy_repo, ignore_errors=True)
            log("Updater: removed legacy repo cache")
    except Exception:
        pass
    # Timestamped temp exes from the old respawn-storm era + old http-simple temps
    for pattern, older_than_sec in (("PlayLimit_*.exe", 0), ("PlayLimit_staged*.exe", 0)):
        try:
            for p in DATA_DIR.glob(pattern):
                try:
                    # Never delete the live exe or a fresh staged file
                    if p.resolve() == LIVE_EXE.resolve():
                        continue
                    if p == STAGED_EXE and p.exists():
                        continue
                    p.unlink(missing_ok=True)
                    log(f"Updater: cleaned old file {p.name}")
                except Exception:
                    pass
        except Exception:
            pass
    # Stale _MEI temp dirs from killed onefile runs (older than 1 day only; active one is locked anyway)
    try:
        import tempfile
        tmpd = Path(tempfile.gettempdir())
        now = time.time()
        for p in tmpd.glob("_MEI*"):
            try:
                if p.is_dir() and now - p.stat().st_mtime > 86400:
                    import shutil
                    shutil.rmtree(p, ignore_errors=True)
                    log(f"Updater: cleaned stale temp {p.name}")
            except Exception:
                pass
    except Exception:
        pass

def _remote_version():
    """Fetch version.txt from GitHub. Returns version string or None."""
    data = _download_bytes(f"{REPO_RAW_BASE}/version.txt", 10)
    if not data:
        return None
    try:
        return data.decode('utf-8', errors='ignore').strip()
    except Exception:
        return None

def _check_and_stage_update():
    """Download + verify a newer exe into STAGED_EXE/STAGED_VER. Returns staged version or None.

    Verification (all must pass): size > 100KB, MZ header, sha256 matches
    dist/PlayLimit.exe.sha256 from GitHub. Old code is only ever replaced by
    verified-newer code, never the reverse.
    """
    try:
        remote_ver = _remote_version()
        if not remote_ver:
            return None
        log(f"Updater: local {__version__} vs remote {remote_ver}")
        if _parse_ver(remote_ver) <= _parse_ver(__version__):
            log("Updater: already latest")
            # Drop any stale staged files for versions we already passed
            try:
                if STAGED_VER.exists():
                    sv = STAGED_VER.read_text(encoding="utf-8", errors="ignore").strip()
                    if _parse_ver(sv) <= _parse_ver(__version__):
                        STAGED_EXE.unlink(missing_ok=True)
                        STAGED_VER.unlink(missing_ok=True)
                        log("Updater: removed stale staged update")
            except Exception:
                pass
            return None

        log(f"Updater: new version {remote_ver} available, downloading...")
        exe_data = _download_bytes(f"{REPO_RAW_BASE}/dist/PlayLimit.exe", 60)
        if not exe_data or len(exe_data) < 1024 * 100:
            log("Updater: exe download failed or too small, abort")
            return None
        if exe_data[:2] != b'MZ':
            log("Updater: download is not a Windows exe (bad MZ), abort")
            return None
        sha_data = _download_bytes(f"{REPO_RAW_BASE}/dist/PlayLimit.exe.sha256", 15)
        if sha_data:
            try:
                import hashlib
                expected = sha_data.decode('utf-8', errors='ignore').split()[0].strip().lower()
                actual = hashlib.sha256(exe_data).hexdigest()
                if actual != expected:
                    log(f"Updater: SHA256 MISMATCH (got {actual[:12]}..., want {expected[:12]}...), abort - old code kept")
                    return None
                log("Updater: SHA256 verified")
            except Exception as e:
                log(f"Updater: sha check error {e}, abort")
                return None
        else:
            log("Updater: no sha file on remote, abort (refusing unverified exe)")
            return None

        STAGED_EXE.parent.mkdir(parents=True, exist_ok=True)
        STAGED_EXE.write_bytes(exe_data)
        STAGED_VER.write_text(remote_ver, encoding="utf-8")
        log(f"Updater: v{remote_ver} downloaded+verified, staged ({len(exe_data)} bytes)")
        return remote_ver
    except Exception as e:
        log(f"Updater: stage error: {e}")
        return None

def _do_update_hop(staged_ver: str):
    """Swap the verified staged exe over the live exe and restart once.

    A running Windows exe cannot replace its own file, so a tiny hidden helper
    waits for our PID to exit, moves staged->live (old bytes gone), deletes the
    staged marker, and starts the live exe again. Exactly one hop per newer
    version (strict version increase + verified hash), so no storm is possible.
    No popups - progress goes to limiter.log and the new window title.
    """
    try:
        _spawn_hop_helper_only(staged_ver)
        log(f"Updater: exiting to apply v{staged_ver} (old code replaced)")
        request_shutdown(f"applying update v{staged_ver}")
    except SystemExit:
        raise
    except Exception as e:
        log(f"Updater: hop failed: {e}")
        import traceback
        log(traceback.format_exc())

HOP_HELPER_PS1 = DATA_DIR / "hop_helper.ps1"
HOP_LOG = DATA_DIR / "hop.log"

def _spawn_hop_helper_only(staged_ver: str):
    """Spawn the swap helper without touching app state (for pre-main-loop use).

    The helper is a plain .ps1 file (not an encoded blob) so it is inspectable
    and writes every step to hop.log - if a hop ever fails, hop.log shows why.
    """
    me = os.getpid()
    live = str(LIVE_EXE)
    staged = str(STAGED_EXE)
    sver = str(STAGED_VER)
    hoplog = str(HOP_LOG)
    script = (
        "param([int]$OldPid, [string]$Live, [string]$Staged, [string]$Sver, [string]$HopLog)\n"
        "\"hop-start old=$OldPid at $(Get-Date -Format 'HH:mm:ss')\" | Out-File $HopLog -Append\n"
        "while (Get-Process -Id $OldPid -ErrorAction SilentlyContinue) { Start-Sleep -Milliseconds 200 }\n"
        "\"old-gone, moving staged->live\" | Out-File $HopLog -Append\n"
        "try {\n"
        "  Move-Item -LiteralPath $Staged -Destination $Live -Force -ErrorAction Stop\n"
        "  \"moved ok\" | Out-File $HopLog -Append\n"
        "} catch { \"MOVE-FAILED: $($_.Exception.Message)\" | Out-File $HopLog -Append; exit 1 }\n"
        "Remove-Item -LiteralPath $Sver -Force -ErrorAction SilentlyContinue\n"
        "\"marker deleted, relaunching\" | Out-File $HopLog -Append\n"
        "Start-Process -FilePath $Live\n"
        "\"relaunched\" | Out-File $HopLog -Append\n"
    )
    HOP_HELPER_PS1.parent.mkdir(parents=True, exist_ok=True)
    HOP_HELPER_PS1.write_text(script, encoding="utf-8")
    # NOTE: do NOT use DETACHED_PROCESS here - empirically a detached powershell
    # running a -File script exits 0 without executing anything. NEW_GROUP alone
    # keeps the helper alive after we exit, -WindowStyle Hidden keeps it invisible.
    NEW_GROUP = 0x00000200
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-WindowStyle", "Hidden", "-File", str(HOP_HELPER_PS1),
         str(me), live, staged, sver, hoplog],
        creationflags=NEW_GROUP,
    )
    log(f"Updater: hop helper started for v{staged_ver} (see hop.log)")

def _pending_staged_version():
    """Return staged version string if a valid pending update exists, else None."""
    try:
        if not STAGED_EXE.exists() or not STAGED_VER.exists():
            return None
        sv = STAGED_VER.read_text(encoding="utf-8", errors="ignore").strip()
        if not sv or _parse_ver(sv) <= _parse_ver(__version__):
            return None
        if STAGED_EXE.stat().st_size < 1024 * 100:
            return None
        return sv
    except Exception:
        return None

def _http_simple_update(show_ui=False):
    """Deprecated stub - superseded by _check_and_stage_update(). Kept so no caller breaks. Never spawns processes."""
    return False

def self_update(show_ui=False):
    """Self-update entry point (runs in background thread, also on tray click).

    - show_ui is accepted but IGNORED: no popups allowed, ever. Status goes to
      limiter.log and is visible in the app window title (new version after hop).
    - Cleans stale files, applies any pending staged update via one hop, then
      checks GitHub for a newer version and hops if one is verified.
    """
    if not AUTO_UPDATE_ENABLED:
        log("Updater: disabled")
        return
    try:
        log("=== Auto-update check ===")
        _cleanup_old_files()

        # 1) Pending staged update from a previous run? Apply with one hop.
        try:
            sv = _pending_staged_version()
            if sv:
                log(f"Updater: pending staged v{sv} found at startup - applying with one hop")
                _do_update_hop(sv)
                return
        except SystemExit:
            raise
        except Exception as e:
            log(f"Updater: pending-stage error: {e}")

        # 2) Check GitHub for newer, download+verify+stage, then hop once.
        try:
            sv = _check_and_stage_update()
            if sv:
                _do_update_hop(sv)
                return
        except SystemExit:
            raise
        except Exception as e:
            log(f"Updater: stage error: {e}")

        log("=== Auto-update done (already latest) ===")
    except SystemExit:
        raise
    except Exception as e:
        log(f"Updater: unexpected error: {e}")
        import traceback
        log(traceback.format_exc())

# ---------- GITHUB LOG SHIPPING ----------
# Every running copy periodically uploads its log so the parent PC can read all
# machines' logs from one place: https://github.com/beukes2/playlimit-logs/tree/master/logs
# Auth: a fine-grained PAT (contents:write on playlimit-logs ONLY) lives in a local
# file created by the parent on each machine - NEVER baked into the exe/repo.
LOG_REPO = "beukes2/playlimit-logs"
TOKEN_FILE = DATA_DIR / "github_token.txt"
LOG_UPLOAD_EVERY_SEC = 600
LOG_UPLOAD_MAX_BYTES = 200 * 1024

def _read_token():
    try:
        if TOKEN_FILE.exists():
            t = TOKEN_FILE.read_text(encoding="utf-8", errors="ignore").strip()
            return t or None
    except Exception:
        pass
    return None

def _upload_log_once():
    """Upload last LOG_UPLOAD_MAX_BYTES of limiter.log to logs/<HOST>-<date>.log. Returns True on success."""
    token = _read_token()
    if not token:
        return False
    try:
        import socket
        import base64 as _b64
        import urllib.request as _urlreq
        import urllib.error as _urlerr
        try:
            host = socket.gethostname()
        except Exception:
            host = os.environ.get("COMPUTERNAME", "unknown")
        safe_host = "".join(c if (c.isalnum() or c in "-_") else "_" for c in host) or "unknown"
        day = datetime.date.today().isoformat()
        try:
            raw = LOG_FILE.read_bytes()
        except Exception:
            return False
        if len(raw) > LOG_UPLOAD_MAX_BYTES:
            raw = raw[-LOG_UPLOAD_MAX_BYTES:]
        header = f"# PlayLimit log | host={host} | version={__version__} | uploaded={datetime.datetime.now().isoformat()} | tail\n".encode("utf-8")
        body = _b64.b64encode(header + raw).decode("ascii")
        path = f"logs/{safe_host}-{day}.log"
        api = f"https://api.github.com/repos/{LOG_REPO}/contents/{path}"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                   "Content-Type": "application/json", "User-Agent": "PlayLimit"}
        # Need sha when overwriting an existing file
        sha = None
        try:
            req = _urlreq.Request(api, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "PlayLimit"})
            with _urlreq.urlopen(req, timeout=15) as resp:
                sha = json.loads(resp.read().decode("utf-8")).get("sha")
        except _urlerr.HTTPError as e:
            if e.code != 404:
                log(f"LogShip: check failed HTTP {e.code}")
                return False
        except Exception as e:
            log(f"LogShip: check failed: {e}")
            return False
        payload = json.dumps({"message": f"log {safe_host} {day} {__version__}", "content": body, **({"sha": sha} if sha else {})}).encode("utf-8")
        req = _urlreq.Request(api, data=payload, method="PUT", headers=headers)
        with _urlreq.urlopen(req, timeout=30) as resp:
            if resp.status in (200, 201):
                log(f"LogShip: uploaded {len(raw)} bytes -> {path}")
                return True
            log(f"LogShip: unexpected HTTP {resp.status}")
            return False
    except _urlerr.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="ignore")[:200]
        except Exception:
            detail = ""
        log(f"LogShip: upload HTTP {e.code} {detail}")
        return False
    except Exception as e:
        log(f"LogShip: upload failed: {e}")
        return False

def logship_thread():
    """Upload logs every LOG_UPLOAD_EVERY_SEC (first try 60s after start). Silent when no token file."""
    try:
        if not _read_token():
            log("LogShip: no token file (github_token.txt) - log shipping off (see README)")
            return
        log("LogShip: token found - shipping logs to GitHub")
    except Exception:
        pass
    try:
        time.sleep(60)
        while not _shutdown.is_set():
            try:
                _upload_log_once()
            except Exception as e:
                try:
                    log(f"LogShip: cycle error: {e}")
                except Exception:
                    pass
            _shutdown.wait(LOG_UPLOAD_EVERY_SEC)
    except Exception:
        pass

def add_bonus_time(seconds: int = BONUS_STEP_SEC):
    """Add bonus time for today. Called by hotkey. Thread-safe via _state_lock."""
    with _state_lock:
        state = load_state()
        old_bonus = int(state.get("bonus_seconds", 0))
        new_bonus = old_bonus + seconds
        state["bonus_seconds"] = new_bonus

        # If we were over limit, adding bonus may unblock - reset flags so warning can fire again
        effective_limit = get_daily_limit_sec() + new_bonus
        used = int(state.get("used_seconds", 0))
        remaining = effective_limit - used

        # Reset warned/blocked if now we have >5 min left again
        if remaining > WARNING_BEFORE_SEC:
            state["warned"] = False
            state["blocked_notified"] = False
        elif remaining > 0:
            # still within warning window - ensure blocked is cleared so they can play remaining time
            state["blocked_notified"] = False
            # keep warned as True to avoid re-warning immediately if they were already warned
            # but if they had not been warned yet, keep as is

        save_state(state)

        base = get_daily_limit_sec()
        log(f"HOTKEY: Added {seconds//60} min bonus. Base {base//60} min + bonus {new_bonus//60} min = {effective_limit//60} min total. Used {used//60} min, remaining {max(0, remaining)//60} min")

        # Show confirmation popup
        try:
            kind = "Weekend" if is_weekend() else "Weekday"
            show_warning_async(
                "AlbionLimiter - Bonus Added!",
                f"+15 minutes added for today!\n\n"
                f"Base limit ({kind}): {base//60} min\n"
                f"Bonus today: +{new_bonus//60} min\n"
                f"New total: {effective_limit//60} min\n"
                f"Used: {format_minutes(used)}\n"
                f"Remaining: {format_minutes(max(0, remaining))}",
                style=0x40  # MB_ICONINFORMATION
            )
        except Exception as e:
            log(f"Bonus popup failed: {e}")

        return state

def hotkey_listener_thread():
    """Global hotkeys: Ctrl+Alt+T -> +15 min, Ctrl+Shift+D -> disable, Ctrl+Alt+D -> close app."""
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
    except Exception as e:
        log(f"Hotkey: ctypes not available: {e}")
        return

    HOTKEY_ID_BONUS = 1
    HOTKEY_ID_DISABLE = 2
    HOTKEY_ID_CLOSE = 3
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    MOD_SHIFT = 0x0004
    VK_T = 0x54  # 'T'
    VK_D = 0x44  # 'D'
    WM_HOTKEY = 0x0312

    ok1 = user32.RegisterHotKey(None, HOTKEY_ID_BONUS, MOD_CONTROL | MOD_ALT, VK_T)
    if not ok1:
        log(f"Hotkey: RegisterHotKey Ctrl+Alt+T failed, error {kernel32.GetLastError()}")
    else:
        log("Hotkey registered: Ctrl+Alt+T = +15 minutes for today (resets tomorrow)")

    ok2 = user32.RegisterHotKey(None, HOTKEY_ID_DISABLE, MOD_CONTROL | MOD_SHIFT, VK_D)
    if not ok2:
        log(f"Hotkey: RegisterHotKey Ctrl+Shift+D failed, error {kernel32.GetLastError()}")
    else:
        log("Hotkey registered: Ctrl+Shift+D = DISABLE PlayLimit")

    ok3 = user32.RegisterHotKey(None, HOTKEY_ID_CLOSE, MOD_CONTROL | MOD_ALT, VK_D)
    if not ok3:
        log(f"Hotkey: RegisterHotKey Ctrl+Alt+D failed, error {kernel32.GetLastError()}")
    else:
        log("Hotkey registered: Ctrl+Alt+D = CLOSE PlayLimit")

    if not ok1 and not ok2 and not ok3:
        return

    try:
        msg = ctypes.wintypes.MSG()
        while not _shutdown.is_set():
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0:  # WM_QUIT
                break
            if ret == -1:
                log(f"Hotkey: GetMessage error {kernel32.GetLastError()}")
                time.sleep(0.5)
                continue
            if msg.message == WM_HOTKEY:
                if msg.wParam == HOTKEY_ID_BONUS:
                    log("Hotkey pressed: Ctrl+Alt+T")
                    try:
                        if is_disabled():
                            log("Bonus ignored - app is DISABLED")
                        else:
                            add_bonus_time(BONUS_STEP_SEC)
                    except Exception as e:
                        log(f"Hotkey handler error: {e}")
                        import traceback
                        log(traceback.format_exc())
                elif msg.wParam == HOTKEY_ID_DISABLE:
                    log("Hotkey pressed: Ctrl+Shift+D - DISABLING")
                    try:
                        disable_app()
                    except Exception as e:
                        log(f"Disable hotkey error: {e}")
                elif msg.wParam == HOTKEY_ID_CLOSE:
                    log("Hotkey pressed: Ctrl+Alt+D - CLOSING PlayLimit (no popup, full exit)")
                    request_shutdown("closed via Ctrl+Alt+D")
                    # No os._exit (see disable_app): loops observe _shutdown and unwind cleanly.
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        if ok1:
            user32.UnregisterHotKey(None, HOTKEY_ID_BONUS)
        if ok2:
            user32.UnregisterHotKey(None, HOTKEY_ID_DISABLE)
        if 'ok3' in locals() and ok3:
            user32.UnregisterHotKey(None, HOTKEY_ID_CLOSE)
        log("Hotkeys unregistered")

def console_thread():
    """GUI app mode: no console window - just log time left every 60s (tray/window shows UI)."""
    # App mode: do NOT allocate console - this is now a GUI app, not console
    # Header is logged, not printed to console
    try:
        log("=" * 60)
        log(f" {APP_NAME} v{__version__} - Albion Online Time Limiter (GUI app)")
        log("=" * 60)
        log(f" Weekday limit: {WEEKDAY_LIMIT_SEC//60} min | Weekend: {WEEKEND_LIMIT_SEC//60} min | Warning: {WARNING_BEFORE_SEC//60} min before")
        log(f" Hotkey: Ctrl+Alt+T = +15 min for today (resets tomorrow)")
        log(f" Hotkey: Ctrl+Shift+D = DISABLE PlayLimit (allow browsers/game)")
        log(f" Hotkey: Ctrl+Alt+D = CLOSE PlayLimit (exit app)")
        if is_browser_block_exempt():
            log(f" Browser block: OFF on this PC (exempt) - your browsers will NOT be closed")
        else:
            log(f" Browser block: {', '.join(BROWSER_PROCESSES)}")
        log(f" State: {STATE_FILE}")
        log(f" Log:   {LOG_FILE}")
        log(f" App: GUI (no console) - use tray icon or desktop icon to show time window")
        log("-" * 60)
    except Exception:
        pass

    while True:
        try:
            with _state_lock:
                s = load_state()
                today = datetime.date.today()
                limit = get_effective_limit_sec(today, s)
                used = int(s.get("used_seconds", 0))
                bonus = int(s.get("bonus_seconds", 0))
                remaining = max(0, limit - used)
                warned = s.get("warned", False)
            running = is_albion_running()
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            day_type = "Weekend" if is_weekend(today) else "Weekday"
            base = get_daily_limit_sec(today)
            # Build status line
            status = "RUNNING" if running else "not running"
            # Color not needed, plain text
            if is_disabled():
                line = f"[{ts}] *** DISABLED *** | PlayLimit is OFF | Used {format_minutes(used)} | Left {format_minutes(remaining)} | Albion {status} | Browser block OFF | Press reboot or delete {DISABLE_FLAG_FILE} to re-enable"
            elif is_browser_block_exempt():
                line = f"[{ts}] {day_type} | Base {base//60}min + Bonus {bonus//60}min = {limit//60}min | Used {format_minutes(used)} | Left {format_minutes(remaining)} | Albion {status} {'(WARNED)' if warned else ''} | Browser OFF (exempt)"
            else:
                line = f"[{ts}] {day_type} | Base {base//60}min + Bonus {bonus//60}min = {limit//60}min | Used {format_minutes(used)} | Left {format_minutes(remaining)} | Albion {status} {'(WARNED)' if warned else ''} | Browser BLOCKED"
            try:
                print(line)
                sys.stdout.flush()
            except Exception:
                pass
            # Also log so headless runs still show updates in limiter.log
            try:
                log(f"Console: {line}")
            except Exception:
                pass
        except Exception as e:
            try:
                log(f"Console thread error: {e}")
            except Exception:
                pass
        time.sleep(60)

# ---------- TRAY ICON + LITTLE TIME WINDOW ----------
_tray_icon = None

def _create_tray_image():
    """Create 64x64 icon for tray (blue with PL)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new('RGBA', (64, 64), (30, 144, 255, 255))
        draw = ImageDraw.Draw(img)
        # white circle
        draw.ellipse([4, 4, 60, 60], fill=(255, 255, 255, 255), outline=(0, 0, 0, 255), width=2)
        draw.ellipse([8, 8, 56, 56], fill=(30, 144, 255, 255))
        try:
            font = ImageFont.truetype("segoeui.ttf", 28)
        except Exception:
            font = ImageFont.load_default()
        # Draw PL
        draw.text((12, 14), "PL", fill=(255, 255, 255, 255), font=font)
        return img
    except Exception:
        try:
            from PIL import Image
            return Image.new('RGBA', (64, 64), (30, 144, 255, 255))
        except Exception:
            return None

def show_time_window():
    """Little screen showing remaining time - Tkinter popup, updates every second."""
    try:
        import tkinter as tk
        # If window already exists, bring to front
        global _time_window
        try:
            if '_time_window' in globals() and _time_window and _time_window.winfo_exists():
                _time_window.lift()
                _time_window.attributes('-topmost', True)
                return
        except Exception:
            pass

        t_w0 = time.time()
        root = tk.Tk()
        log(f"Window: Tk() created in {time.time()-t_w0:.1f}s")
        # Keep reference
        globals()['_time_window'] = root
        root.title(f"{APP_NAME} v{__version__} - Time Left")
        root.geometry("360x240")
        root.resizable(False, False)
        try:
            root.attributes('-topmost', True)
        except Exception:
            pass
        # Icon if available
        try:
            icon = _create_tray_image()
            if icon:
                # Convert PIL to Tk PhotoImage via temp file
                import tempfile
                tmp = tempfile.NamedTemporaryFile(suffix=".ico", delete=False)
                icon.save(tmp.name, format="ICO", sizes=[(64, 64)])
                tmp.close()
                root.iconbitmap(tmp.name)
        except Exception:
            pass

        # Style
        try:
            root.configure(bg="#1e1e2e")
        except Exception:
            pass

        title = tk.Label(root, text=f"{APP_NAME} v{__version__}", font=("Segoe UI", 16, "bold"), bg="#1e1e2e", fg="white")
        title.pack(pady=(12, 4))

        time_label = tk.Label(root, text="--:--", font=("Segoe UI", 32, "bold"), bg="#1e1e2e", fg="#00ff88")
        time_label.pack()

        detail_label = tk.Label(root, text="", font=("Segoe UI", 9), bg="#1e1e2e", fg="#cccccc", justify="center")
        detail_label.pack(pady=4)

        progress = tk.Canvas(root, width=320, height=14, bg="#2d2d44", highlightthickness=0)
        progress.pack(pady=6)
        bar = progress.create_rectangle(0, 0, 0, 14, fill="#00ff88", outline="")

        btn_frame = tk.Frame(root, bg="#1e1e2e")
        btn_frame.pack(pady=6)

        def refresh():
            try:
                if is_disabled():
                    time_label.config(text="DISABLED", fg="#ff5555")
                    detail_label.config(text="PlayLimit is OFF\nBrowsers and Albion allowed")
                    progress.coords(bar, 0, 0, 0, 14)
                    progress.itemconfig(bar, fill="#ff5555")
                elif is_browser_block_exempt():
                    with _state_lock:
                        s = load_state()
                        today = datetime.date.today()
                        limit = get_effective_limit_sec(today, s)
                        used = int(s.get("used_seconds", 0))
                        bonus = int(s.get("bonus_seconds", 0))
                        remaining = max(0, limit - used)
                    # Show but browser exempt
                    mins, secs = divmod(remaining, 60)
                    time_label.config(text=f"{mins:02d}:{secs:02d}", fg="#ffcc00")
                    detail_label.config(text=f"Browser block OFF (exempt)\nUsed {format_minutes(used)} / {format_minutes(limit)} (+{bonus//60} bonus)")
                    pct = (used / limit) if limit else 0
                    w = int(320 * min(pct, 1.0))
                    progress.coords(bar, 0, 0, w, 14)
                    progress.itemconfig(bar, fill="#ffcc00")
                else:
                    with _state_lock:
                        s = load_state()
                        today = datetime.date.today()
                        limit = get_effective_limit_sec(today, s)
                        used = int(s.get("used_seconds", 0))
                        bonus = int(s.get("bonus_seconds", 0))
                        remaining = max(0, limit - used)
                    mins, secs = divmod(remaining, 60)
                    time_label.config(text=f"{mins:02d}:{secs:02d}", fg="#00ff88" if remaining > 300 else "#ff5555")
                    day_type = "Weekend" if is_weekend() else "Weekday"
                    detail_label.config(text=f"{day_type} {limit//60} min (base {get_daily_limit_sec()//60}+{bonus//60} bonus)\nUsed {format_minutes(used)} | Left {format_minutes(remaining)}")
                    pct = (used / limit) if limit else 0
                    w = int(320 * min(pct, 1.0))
                    progress.coords(bar, 0, 0, w, 14)
                    progress.itemconfig(bar, fill="#ff5555" if remaining <= 300 else "#00ff88")
                # schedule next
                if root.winfo_exists():
                    root.after(1000, refresh)
            except Exception as e:
                try:
                    log(f"Time window refresh error: {e}")
                except Exception:
                    pass
                try:
                    if root.winfo_exists():
                        root.after(1000, refresh)
                except Exception:
                    pass

        # Kids must NOT be able to close the app via X, taskbar Close or Alt+F4.
        # Those all arrive as WM_CLOSE -> ignored here (logged only).
        # Parent closes via Ctrl+Alt+D hotkey or Task Manager as Admin (TerminateProcess
        # cannot be blocked); both do a full shutdown so no traces are left behind.
        # Register this root so request_shutdown() can destroy it from hotkeys.
        global _tk_root
        _tk_root = root

        def on_close(*args):
            log("Close attempt blocked (X/taskbar/Alt+F4) - kids cannot close; parent: Ctrl+Alt+D or Task Manager as Admin")

        try:
            root.protocol("WM_DELETE_WINDOW", on_close)
        except Exception:
            pass
        # Grey out / remove Close from the window system menu (X button + taskbar menu).
        # NOTE: root.winfo_id() already IS the HWND for a Tk toplevel on Windows -
        # GetParent() on it returns the desktop (wrong menu), so use it directly.
        # Re-applied on timers: Tk can recreate the system menu after mapping.
        def _strip_close(tag="now"):
            try:
                hwnd = root.winfo_id()
                if not hwnd:
                    return
                hMenu = ctypes.windll.user32.GetSystemMenu(hwnd, 0)
                if hMenu:
                    ctypes.windll.user32.DeleteMenu(hMenu, 0xF060, 0x0)  # SC_CLOSE
                    ctypes.windll.user32.EnableMenuItem(hMenu, 0xF060, 0x00000001)  # MF_GRAYED
                    ctypes.windll.user32.DrawMenuBar(hwnd)
                    if tag == "now":
                        log("Window: system-menu Close disabled")
            except Exception as e:
                if tag == "now":
                    log(f"Window: could not disable system-menu Close: {e}")
        try:
            root.update_idletasks()
            root.update()
            _strip_close("now")
            root.after(500, lambda: _strip_close("t500"))
            root.after(2000, lambda: _strip_close("t2000"))
        except Exception as e:
            log(f"Window: could not disable system-menu Close: {e}")

        refresh()
        # Center on screen
        try:
            root.update_idletasks()
            x = (root.winfo_screenwidth() // 2) - (360 // 2)
            y = (root.winfo_screenheight() // 2) - (220 // 2)
            root.geometry(f"360x240+{x}+{y}")
        except Exception:
            pass
        try:
            root.update()
            log(f"Window: mapped={root.winfo_ismapped()} viewable={root.winfo_viewable()} geom={root.winfo_geometry()}")
        except Exception as e:
            log(f"Window: pre-mainloop check failed: {e}")
        # Close PyInstaller splash (shows instantly at launch during exe extraction)
        try:
            import pyi_splash  # type: ignore
            pyi_splash.close()
            log("Window: splash closed")
        except Exception:
            pass
        root.mainloop()
    except SystemExit:
        raise
    except Exception as e:
        try:
            log(f"show_time_window error: {e}")
        except Exception:
            pass

def tray_thread():
    """System tray icon - click to open time window. Runs in own thread."""
    # Try pystray first
    try:
        import pystray
        from pystray import MenuItem as item
        img = _create_tray_image()
        if img is None:
            raise ImportError("No PIL")

        def on_show(icon, item):
            # Tray click only opens the window (update check is silent on startup, no popup).
            def do_show():
                try:
                    show_time_window()
                except Exception as e:
                    log(f"Show window error: {e}")
            threading.Thread(target=do_show, daemon=True).start()

        # NOTE: no Exit/Disable menu items on purpose - kids would click them.
        # Parent closes via Ctrl+Alt+D hotkey or Task Manager as Admin.
        menu = pystray.Menu(
            item('Show Time Left', on_show, default=True)
        )
        global _tray_icon
        _tray_icon = pystray.Icon("PlayLimit", img, f"{APP_NAME} v{__version__} - Double-click to show time (checks for updates)", menu)
        log(f"Tray icon started (pystray) v{__version__} - double-click to show time + auto-update on click")
        _tray_icon.run()
        return
    except Exception as e:
        log(f"Tray pystray failed ({e}), trying Tk fallback")

    # Fallback: simple Tk hidden root with icon in taskbar (no real tray)
    # We create a tiny Tk window that stays hidden and shows in taskbar; clicking it shows time
    try:
        import tkinter as tk
        # This fallback just ensures show_time_window is available via hotkey; no tray
        log("Tray fallback: use Ctrl+Alt+T / show_time_window() via console")
        # Keep thread alive
        while True:
            time.sleep(60)
    except Exception as e:
        log(f"Tray fallback failed: {e}")

def show_message(title: str, text: str, style: int = 0x40):
    """No popups allowed - log only. Window visible = running, no MessageBox of any kind."""
    log(f"UI message suppressed [{title}]: {text[:200]}")

def show_warning_async(title, text, style=0x30):
    """No popups allowed - log only (previously spawned MessageBox subprocess)."""
    log(f"UI message suppressed [{title}]: {text[:200]}")

def is_albion_running() -> bool:
    names_lower = [n.lower() for n in TARGET_PROCESSES]
    if HAS_PSUTIL:
        try:
            for p in psutil.process_iter(['name']):
                try:
                    n = (p.info.get('name') or "").lower()
                    if n in names_lower:
                        return True
                    # also partial match: albion
                    if "albion" in n:
                        return True
                except Exception:
                    continue
            return False
        except Exception as e:
            log(f"psutil error: {e}")

    # Fallback: tasklist CSV
    try:
        out = subprocess.check_output('tasklist /FO CSV /NH', shell=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        low = out.lower()
        for n in names_lower:
            if n.lower() in low:
                return True
        if "albion" in low:
            # double check to avoid false positives but log it
            return True
        return False
    except Exception as e:
        log(f"tasklist check failed: {e}")
        return False

def is_browser_running() -> bool:
    """Check if any browser process is running."""
    if is_disabled() or is_browser_block_exempt():
        return False
    names_lower = [n.lower() for n in BROWSER_PROCESSES]
    if HAS_PSUTIL:
        try:
            for p in psutil.process_iter(['name']):
                try:
                    n = (p.info.get('name') or "").lower()
                    if n in names_lower:
                        return True
                except Exception:
                    continue
            return False
        except Exception as e:
            log(f"browser psutil error: {e}")
    try:
        out = subprocess.check_output('tasklist /FO CSV /NH', shell=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        low = out.lower()
        for n in names_lower:
            if n.lower() in low:
                return True
        return False
    except Exception:
        return False

def kill_browsers():
    """Close any browser process immediately."""
    if is_disabled() or is_browser_block_exempt():
        return False
    killed = False
    names_lower = [n.lower() for n in BROWSER_PROCESSES]
    if HAS_PSUTIL:
        targets = []
        for p in psutil.process_iter(['name', 'pid']):
            try:
                n = (p.info.get('name') or "").lower()
                if n in names_lower:
                    targets.append(p)
            except Exception:
                continue
        for p in targets:
            try:
                p.terminate()
                killed = True
                log(f"Browser block: Terminate {p.info.get('name')} PID {p.pid}")
            except Exception as e:
                log(f"Browser terminate failed PID {p.pid}: {e}")
        if targets:
            gone, alive = psutil.wait_procs(targets, timeout=5)
            for p in alive:
                try:
                    p.kill()
                    log(f"Browser block: Kill PID {p.pid}")
                except Exception:
                    pass
                killed = True
        if killed:
            try:
                show_warning_async("Browser Blocked", "Browsing is blocked by PlayLimit.\n\nAsk a parent to press Ctrl+Shift+D to disable.", style=0x10)
            except Exception:
                pass
        return killed
    else:
        try:
            for name in BROWSER_PROCESSES:
                result = subprocess.run(f'taskkill /F /IM "{name}" /T', shell=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                if result.returncode == 0:
                    killed = True
                    log(f"Browser block: taskkill {name}")
            if killed:
                show_warning_async("Browser Blocked", "Browsing is blocked by PlayLimit.", style=0x10)
            return killed
        except Exception as e:
            log(f"browser taskkill failed: {e}")
            return False

def kill_albion():
    """Try graceful close via WM_CLOSE, then terminate."""
    if is_disabled():
        return False
    killed = False
    names_lower = [n.lower() for n in TARGET_PROCESSES]

    if HAS_PSUTIL:
        targets = []
        for p in psutil.process_iter(['name', 'pid']):
            try:
                n = (p.info.get('name') or "").lower()
                if n in names_lower or "albion" in n:
                    targets.append(p)
            except Exception:
                continue
        for p in targets:
            try:
                # Try to close window gracefully first: send WM_CLOSE to main window? psutil can't, so terminate
                # Attempt terminate (graceful)
                p.terminate()
                killed = True
                log(f"Terminate sent to {p.info.get('name')} PID {p.pid}")
            except Exception as e:
                log(f"Terminate failed PID {p.pid}: {e}")
        if targets:
            gone, alive = psutil.wait_procs(targets, timeout=GRACEFUL_CLOSE_TIMEOUT)
            for p in alive:
                try:
                    p.kill()
                    log(f"Kill sent to PID {p.pid}")
                except Exception as e:
                    log(f"Kill failed PID {p.pid}: {e}")
                killed = True
        return killed
    else:
        # Fallback: taskkill
        try:
            # /T kills child processes too
            for name in TARGET_PROCESSES:
                subprocess.run(f'taskkill /F /IM "{name}" /T', shell=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            # generic albion kill via wmic if any remaining
            subprocess.run('taskkill /F /FI "IMAGENAME eq Albion*" /T', shell=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            log("taskkill executed")
            return True
        except Exception as e:
            log(f"taskkill failed: {e}")
            return False

def format_minutes(sec: int) -> str:
    m = sec // 60
    s = sec % 60
    if s == 0:
        return f"{m} min"
    return f"{m} min {s} sec"

def main_loop():
    # No persistent disabled flag - always start enabled, clean any stale flag left from older versions
    global APP_DISABLED
    APP_DISABLED = False
    try:
        if DISABLE_FLAG_FILE and DISABLE_FLAG_FILE.exists():
            try:
                DISABLE_FLAG_FILE.unlink()
                log(f"Startup: removed stale disabled.flag at {DISABLE_FLAG_FILE} - starting ENABLED (no persistent disable)")
            except Exception as e:
                log(f"Startup: could not remove stale disabled.flag: {e} - starting ENABLED anyway")
    except Exception:
        pass

    # Make not closable
    try:
        protect_process()
        log("Protect: anti-close enabled (console close blocked, close button disabled)")
    except Exception as e:
        log(f"Protect failed: {e}")

    try:
        import socket
        _host = socket.gethostname()
    except Exception:
        _host = os.environ.get("COMPUTERNAME", "unknown")
    log(f"=== AlbionLimiter started ===")
    log(f"Host: {_host} | Version: {__version__} | Exe: {sys.executable if getattr(sys, 'frozen', False) else __file__}")
    log(f"Data dir: {DATA_DIR}")
    log(f"State file: {STATE_FILE}")
    if is_disabled():
        log("STATUS: DISABLED - all limits and browser block are OFF")
    elif is_browser_block_exempt():
        log(f"STATUS: ACTIVE - Browser block OFF on this PC (exempt) | Albion limit ON")
    else:
        log(f"STATUS: ACTIVE - Browser block ON ({', '.join(BROWSER_PROCESSES)})")
    log(f"Weekday limit: {WEEKDAY_LIMIT_SEC//60} min, Weekend: {WEEKEND_LIMIT_SEC//60} min, Warning: {WARNING_BEFORE_SEC//60} min before, Bonus step: {BONUS_STEP_SEC//60} min via Ctrl+Alt+T, Disable: Ctrl+Shift+D")
    log(f"Watching: {', '.join(TARGET_PROCESSES)}")
    log(f"psutil available: {HAS_PSUTIL}")

    # Show little time window FIRST so you SEE the app instantly on click.
    # Everything heavy (pystray import, hotkeys, update check) starts AFTER,
    # otherwise they contend startup and the window takes ~10s to appear.
    try:
        def _auto_show():
            t0 = time.time()
            log("Window thread started")
            try:
                show_time_window()
                log(f"Window mainloop exited after {time.time()-t0:.1f}s")
            except Exception as e:
                log(f"Window thread failed after {time.time()-t0:.1f}s: {e}")
        threading.Thread(target=_auto_show, daemon=True).start()
        log("Auto-show time window (immediate, before tray/update)")
    except Exception:
        pass

    # Start hotkey listener (daemon)
    try:
        t = threading.Thread(target=hotkey_listener_thread, daemon=True, name="HotkeyListener")
        t.start()
        log("Hotkey thread started")
    except Exception as e:
        log(f"Failed to start hotkey thread: {e}")

    # Start console display (updates every 60s with time left)
    try:
        ct = threading.Thread(target=console_thread, daemon=True, name="ConsoleDisplay")
        ct.start()
        log("Console thread started (updates every 60s)")
    except Exception as e:
        log(f"Failed to start console thread: {e}")

    # Start tray icon (click to show time window) - LAST, pystray import is heavy
    try:
        tr = threading.Thread(target=tray_thread, daemon=True, name="TrayIcon")
        tr.start()
        log("Tray thread started - click icon to show time")
    except Exception as e:
        log(f"Failed to start tray thread: {e}")

    # Update check runs in BACKGROUND so it never blocks the window.
    try:
        threading.Thread(target=self_update, kwargs={"show_ui": False}, daemon=True, name="AutoUpdate").start()
        log("Auto-update started in background (window opens first)")
    except Exception as e:
        log(f"Failed to start background update: {e}")

    # Log shipping to GitHub (silent unless parent placed github_token.txt)
    try:
        lt = threading.Thread(target=logship_thread, daemon=True, name="LogShip")
        lt.start()
        log("LogShip thread started")
    except Exception as e:
        log(f"Failed to start logship thread: {e}")

    with _state_lock:
        state = load_state()
        save_state(state)

    while not _shutdown.is_set():
        try:
            today = datetime.date.today()
            # Use effective limit (base + bonus) - must read state under lock to be consistent
            with _state_lock:
                # Reload state each loop to handle date rollover or hotkey bonus
                fresh = load_state()
                if fresh["date"] != state["date"]:
                    state = fresh
                    log(f"New day {fresh['date']}, new effective limit {get_effective_limit_sec(today, state)//60} min")
                else:
                    # Sync bonus/warned/blocked changes from hotkey (file was updated outside main loop)
                    # and keep used_seconds from memory (most up-to-date)
                    if fresh.get("bonus_seconds", 0) != state.get("bonus_seconds", 0) or \
                       fresh.get("warned", False) != state.get("warned", False) or \
                       fresh.get("blocked_notified", False) != state.get("blocked_notified", False):
                        state["bonus_seconds"] = fresh.get("bonus_seconds", 0)
                        state["warned"] = fresh.get("warned", False)
                        state["blocked_notified"] = fresh.get("blocked_notified", False)

                limit = get_effective_limit_sec(today, state)
            warning_at = limit - WARNING_BEFORE_SEC

            # If disabled, skip all blocking
            if is_disabled():
                # Still sleep and continue; console will show DISABLED
                if int(time.time()) % 60 == 0:
                    log("PlayLimit DISABLED - skipping all checks (browsers/Alibion allowed)")
                time.sleep(POLL_INTERVAL_SEC)
                continue

            # Browser block: close any browser immediately (always, when not disabled)
            # Do this every poll so browsers cannot stay open
            try:
                if is_browser_running():
                    log("Browser detected - killing")
                    kill_browsers()
            except Exception as e:
                log(f"Browser check error: {e}")

            running = is_albion_running()
            # Edge-triggered transition log (full picture without spam)
            try:
                prev = state.get("last_seen_running", False)
                if running and not prev:
                    log(f"GAME START detected - counting time (used {format_minutes(state['used_seconds'])}/{format_minutes(limit)})")
                elif prev and not running:
                    log(f"GAME STOP detected - timer paused (used {format_minutes(state['used_seconds'])}/{format_minutes(limit)})")
                if prev != running:
                    with _state_lock:
                        state["last_seen_running"] = running
                        save_state(state)
            except Exception:
                pass

            if running:
                # If already over limit -> block immediately
                with _state_lock:
                    over_limit = state["used_seconds"] >= limit
                if over_limit:
                    with _state_lock:
                        used = state["used_seconds"]
                    log(f"LIMIT REACHED ({format_minutes(used)}/{format_minutes(limit)}). Blocking Albion Online.")
                    kill_albion()
                    # Show block message only once per detection burst (avoid spam every 5 sec)
                    with _state_lock:
                        if not state.get("blocked_notified", False):
                            state["blocked_notified"] = True
                            save_state(state)
                            need_popup = True
                        else:
                            need_popup = False
                    if need_popup:
                        show_warning_async(
                            "Albion Online - Time's up!",
                            f"Daily limit reached ({limit//60} minutes).\n\n"
                            f"You've played {format_minutes(used)} today.\n"
                            f"Come back tomorrow!\n\n"
                            f"({today.isoformat()} - {'Weekend' if is_weekend(today) else 'Weekday'} limit: {limit//60} min)",
                            style=0x10  # MB_ICONERROR
                        )
                else:
                    # Count this interval
                    with _state_lock:
                        state["used_seconds"] += POLL_INTERVAL_SEC
                        # Cap at limit
                        if state["used_seconds"] > limit:
                            state["used_seconds"] = limit
                        save_state(state)
                        used = state["used_seconds"]
                        warned_flag = state.get("warned", False)

                    remaining = limit - used
                    # Log every minute to avoid spam
                    if used % 60 == 0:
                        log(f"Playing... {format_minutes(used)}/{format_minutes(limit)} used, {format_minutes(remaining)} remaining")

                    # Warning check
                    if not warned_flag and used >= warning_at:
                        with _state_lock:
                            state["warned"] = True
                            save_state(state)
                        log(f"WARNING: 5 minutes remaining! ({format_minutes(remaining)} left)")
                        show_warning_async(
                            "Albion Online - 5 Minutes Left!",
                            f"Only 5 minutes remaining today!\n\n"
                            f"Used: {format_minutes(used)} / {format_minutes(limit)}\n"
                            f"Remaining: {format_minutes(remaining)}\n\n"
                            f"Game will close automatically when time is up.\n"
                            f"Please finish up and save!",
                            style=0x30  # MB_ICONWARNING
                        )

                    # If we just hit the limit during this tick, close now (no popup - window shows countdown)
                    if used >= limit:
                        log(f"Time up! Closing Albion Online (used {format_minutes(used)})")
                        kill_albion()
                        with _state_lock:
                            state["blocked_notified"] = True
                            save_state(state)
            else:
                # Not running - reset blocked_notified so next block will notify again if they try to reopen
                # But keep warned flag if already warned
                if state.get("blocked_notified"):
                    # keep it true to avoid spam, but if they keep trying every 5 sec we already notify once
                    # reset after 60 sec of not running to allow re-notify on next launch attempt
                    pass
                # Small optimization: if not running, don't save every loop
                pass

            # Handle midnight reset: if date changed while sleeping, next iteration will reset via load_state
            _shutdown.wait(POLL_INTERVAL_SEC)

        except KeyboardInterrupt:
            log("Interrupted by user, exiting")
            break
        except Exception as e:
            log(f"Loop error: {e}")
            import traceback
            log(traceback.format_exc())
            time.sleep(POLL_INTERVAL_SEC)

if __name__ == "__main__":
    # Desktop icon (--show-time) just starts the normal visible app. Window visible = running.
    # Silent update check only (no popup); result goes to limiter.log and window version label.
    if "--show-time" in sys.argv or "--time" in sys.argv:
        sys.argv = [a for a in sys.argv if a not in ("--show-time", "--time")]

    # Ensure single instance FIRST (fast, no network) so a second click exits instantly
    # instead of waiting ~10s on an update check. The update itself runs in background
    # inside main_loop() after the window is already visible.
    # NOTE: Local\ (not Global\) - creating a Global\ mutex needs SeCreateGlobalPrivilege,
    # so as a standard user the check silently failed and every click spawned a duplicate.
    try:
        import ctypes.wintypes
        mutex_name = "Local\\AlbionLimiterMutex"
        kernel32 = ctypes.windll.kernel32
        mutex = kernel32.CreateMutexW(None, False, mutex_name)
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            log("Another instance is already running, exiting")
            # os._exit (not sys.exit): unconditional instant death so no ghost
            # process lingers invisibly - window visible = running, nothing else may run.
            os._exit(0)
    except Exception:
        pass

    # Pending staged update from a previous run? Apply NOW with one hop before
    # opening anything, so this process never runs stale code. No threads started
    # yet, so a plain sys.exit here is clean.
    try:
        sv = _pending_staged_version()
        if sv:
            log(f"Updater: pending staged v{sv} at startup - hopping to latest before opening")
            _spawn_hop_helper_only(sv)
            sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        log(f"Updater: startup pending-stage error (non-fatal): {e}")

    main_loop()
