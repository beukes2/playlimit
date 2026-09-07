#!/usr/bin/env python3
"""
Albion Online Parental Time Limiter
- Weekdays: 50 minutes
- Weekends: 120 minutes (2 hours)
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

__version__ = "1.2.6"
APP_NAME = "PlayLimit"

# ---------- CONFIG ----------
WEEKDAY_LIMIT_SEC = 10 * 60          # 50 minutes
WEEKEND_LIMIT_SEC = 10 * 60         # 120 minutes
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
AUTO_UPDATE_ENABLED = True
REPO_URL = "https://github.com/beukes2/playlimit.git"
REPO_BRANCH = "master"
REPO_RAW_BASE = "https://raw.githubusercontent.com/beukes2/playlimit/master"
GITHUB_API_COMMIT = "https://api.github.com/repos/beukes2/playlimit/commits/master"
# Local cache for git clone (writable by standard users)
UPDATE_CACHE_DIR = DATA_DIR / "repo"
EXE_REPO_REL = Path("dist") / "PlayLimit.exe"
EXE_LOCAL = DATA_DIR / "PlayLimit.exe"
PY_REPO_NAME = "albion_limiter.py"
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
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def is_disabled() -> bool:
    global APP_DISABLED
    if APP_DISABLED:
        return True
    try:
        if DISABLE_FLAG_FILE and DISABLE_FLAG_FILE.exists():
            return True
    except Exception:
        pass
    return False

def disable_app():
    global APP_DISABLED
    APP_DISABLED = True
    try:
        DISABLE_FLAG_FILE.parent.mkdir(parents=True, exist_ok=True)
        DISABLE_FLAG_FILE.write_text(datetime.datetime.now().isoformat(), encoding="utf-8")
    except Exception:
        pass
    log("=== PlayLimit DISABLED by Ctrl+Shift+D ===")
    # Try to disable scheduled task so it doesn't restart
    try:
        subprocess.run(["schtasks", "/Change", "/TN", "AlbionLimiter", "/DISABLE"], capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        log("Scheduled task AlbionLimiter disabled")
    except Exception as e:
        log(f"Disable task failed: {e}")
    try:
        show_warning_async("PlayLimit - Disabled", "PlayLimit has been DISABLED.\n\nBrowsers and Albion are now allowed.\n\nTo re-enable, delete:\n" + str(DISABLE_FLAG_FILE) + "\nand restart the app or reboot.", style=0x40)
    except Exception:
        pass
    # Don't exit immediately - let main loop see disabled flag and skip blocking; console will show DISABLED

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
    """Make console/app not closable by kids: disable close button, ignore Ctrl+C/close, and try to deny terminate."""
    # Ignore console close / logoff / shutdown
    try:
        # Handler that returns 1 = ignore
        handler_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
        def handler(ctrl):
            if ctrl in (0, 2, 5, 6):  # CTRL_C=0, CTRL_BREAK=1, CLOSE=2, LOGOFF=5, SHUTDOWN=6
                log(f"Blocked close attempt ctrl={ctrl}")
                return 1
            return 0
        # Keep reference alive
        global _console_handler_ref
        _console_handler_ref = handler_type(handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_handler_ref, 1)
    except Exception as e:
        log(f"protect: SetConsoleCtrlHandler failed: {e}")
    # Disable close button
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            hMenu = ctypes.windll.user32.GetSystemMenu(hwnd, 0)
            if hMenu:
                # SC_CLOSE = 0xF060, MF_BYCOMMAND = 0
                ctypes.windll.user32.DeleteMenu(hMenu, 0xF060, 0x0)
                ctypes.windll.user32.EnableMenuItem(hMenu, 0xF060, 0x1)  # MF_GRAYED=1
                ctypes.windll.user32.DrawMenuBar(hwnd)
    except Exception as e:
        log(f"protect: DeleteMenu failed: {e}")
    # Try to set file ACLs so kids can't delete exe (best-effort, may need admin)
    try:
        # Deny terminate for Users on current process via ACL is complex; fallback to making task SYSTEM
        pass
    except Exception:
        pass

def is_weekend(d: datetime.date = None) -> bool:
    if d is None:
        d = datetime.date.today()
    return d.weekday() >= 5  # 5=Sat, 6=Sun

def get_daily_limit_sec(d: datetime.date = None) -> int:
    return WEEKEND_LIMIT_SEC if is_weekend(d) else WEEKDAY_LIMIT_SEC

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

def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except Exception:
        return False

def _try_git_update() -> bool:
    """Clone or pull repo into UPDATE_CACHE_DIR. Returns True if updated/ok."""
    if not _git_available():
        return False
    try:
        UPDATE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        git_dir = UPDATE_CACHE_DIR / ".git"
        if not git_dir.exists():
            # clone fresh
            log(f"Updater: cloning {REPO_URL} -> {UPDATE_CACHE_DIR}")
            if any(UPDATE_CACHE_DIR.iterdir()):
                # clean non-git files
                for child in UPDATE_CACHE_DIR.iterdir():
                    if child.name == ".git":
                        continue
                    try:
                        if child.is_dir():
                            import shutil
                            shutil.rmtree(child)
                        else:
                            child.unlink()
                    except Exception:
                        pass
            result = subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", REPO_BRANCH, REPO_URL, str(UPDATE_CACHE_DIR)],
                capture_output=True, text=True, timeout=UPDATE_TIMEOUT_SEC,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if result.returncode != 0:
                log(f"Updater: git clone failed: {result.stderr[:300]}")
                return False
            log("Updater: git clone ok")
            return True
        else:
            log("Updater: git fetch + reset")
            result = subprocess.run(
                ["git", "-C", str(UPDATE_CACHE_DIR), "fetch", "origin", REPO_BRANCH],
                capture_output=True, text=True, timeout=UPDATE_TIMEOUT_SEC,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if result.returncode != 0:
                log(f"Updater: git fetch failed: {result.stderr[:300]}")
                return False
            result2 = subprocess.run(
                ["git", "-C", str(UPDATE_CACHE_DIR), "reset", "--hard", f"origin/{REPO_BRANCH}"],
                capture_output=True, text=True, timeout=UPDATE_TIMEOUT_SEC,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if result2.returncode != 0:
                log(f"Updater: git reset failed: {result2.stderr[:300]}")
                return False
            log("Updater: git pull ok")
            return True
    except subprocess.TimeoutExpired:
        log("Updater: git timeout")
        return False
    except Exception as e:
        log(f"Updater: git error: {e}")
        return False

def _try_http_update() -> bool:
    """Fallback: download latest py + exe via raw.githubusercontent. Returns True if any file updated."""
    import urllib.request
    import urllib.error
    updated = False
    try:
        UPDATE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        py_url = f"{REPO_RAW_BASE}/{PY_REPO_NAME}"
        py_dest = UPDATE_CACHE_DIR / PY_REPO_NAME
        try:
            log(f"Updater: HTTP downloading {py_url}")
            with urllib.request.urlopen(py_url, timeout=UPDATE_TIMEOUT_SEC) as resp:
                data = resp.read()
            if not py_dest.exists() or py_dest.read_bytes() != data:
                py_dest.write_bytes(data)
                log(f"Updater: updated {PY_REPO_NAME} via HTTP ({len(data)} bytes)")
                updated = True
            else:
                log("Updater: py already latest via HTTP")
        except Exception as e:
            log(f"Updater: HTTP py failed: {e}")

        exe_url = f"{REPO_RAW_BASE}/{EXE_REPO_REL.as_posix()}"
        exe_dest = UPDATE_CACHE_DIR / EXE_REPO_REL
        exe_dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            log(f"Updater: HTTP checking exe {exe_url}")
            with urllib.request.urlopen(exe_url, timeout=UPDATE_TIMEOUT_SEC) as resp:
                if resp.status == 200:
                    data = resp.read()
                    if len(data) < 1000:
                        log("Updater: exe not found on remote (small file)")
                    else:
                        if not exe_dest.exists() or exe_dest.read_bytes() != data:
                            exe_dest.write_bytes(data)
                            log(f"Updater: updated exe via HTTP ({len(data)} bytes)")
                            updated = True
                        else:
                            log("Updater: exe already latest via HTTP")
                else:
                    log(f"Updater: exe HTTP status {resp.status}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                log("Updater: no exe on remote yet (404)")
            else:
                log(f"Updater: HTTP exe error {e.code}: {e.reason}")
        except Exception as e:
            log(f"Updater: HTTP exe failed: {e}")
    except Exception as e:
        log(f"Updater: HTTP fallback error: {e}")
    return updated

def _launch_latest_exe_and_exit():
    """If exe exists in cache/local, launch it and exit current process. Returns True if launched."""
    try:
        # Cleanup old timestamped temp exes from previous buggy versions (keep at most 1 latest)
        try:
            for p in DATA_DIR.glob("PlayLimit_*.exe"):
                try:
                    # Keep only the newest one from last 5 minutes, delete older
                    if time.time() - p.stat().st_mtime > 300:
                        p.unlink(missing_ok=True)
                        log(f"Updater: cleaned old temp exe {p.name}")
                except Exception:
                    pass
            # If too many temp exes, clean all
            temps = list(DATA_DIR.glob("PlayLimit_*.exe"))
            if len(temps) > 5:
                for p in temps:
                    try:
                        p.unlink(missing_ok=True)
                    except Exception:
                        pass
                log(f"Updater: cleaned {len(temps)} temp exes (storm)")
        except Exception:
            pass

        exe_in_cache = UPDATE_CACHE_DIR / EXE_REPO_REL
        exe_local = EXE_LOCAL
        candidate = None

        # If we are already running the cached exe (frozen), check if we are latest - if so, no need to update local
        current = Path(sys.executable).resolve() if getattr(sys, 'frozen', False) else None
        if current and exe_in_cache.exists():
            try:
                if _files_equal(current, exe_in_cache):
                    log("Updater: current exe is already latest (matches cache) - no update needed")
                    return False
            except Exception:
                pass

        if exe_in_cache.exists() and exe_in_cache.stat().st_size > 1024 * 100:
            # Check if local needs update
            needs_update = False
            try:
                if not exe_local.exists():
                    needs_update = True
                elif not _files_equal(exe_in_cache, exe_local):
                    # Size mismatch of 7 bytes is likely just rebuild timestamp, check version instead?
                    # Consider equal if size diff < 1KB and version same? For now check hash
                    needs_update = True
                else:
                    needs_update = False
            except Exception:
                needs_update = True

            if needs_update:
                try:
                    exe_local.parent.mkdir(parents=True, exist_ok=True)
                    import shutil
                    # Try direct copy to local - if locked, don't create timestamped storm, just use cache
                    if exe_local.exists():
                        try:
                            # Try to unlink, if locked, fallback to launching cache directly
                            exe_local.unlink()
                            shutil.copy2(exe_in_cache, exe_local)
                            candidate = exe_local
                            log(f"Updater: updated local exe {candidate} (was locked before, now ok)")
                        except Exception as e:
                            # Locked - don't create timestamped exe, just launch cache directly
                            if "WinError 32" in str(e) or "being used" in str(e).lower():
                                log(f"Updater: local exe locked ({e}), launching cache directly without temp copy")
                                candidate = exe_in_cache
                            else:
                                log(f"Updater: copy failed ({e}), launching cache")
                                candidate = exe_in_cache
                    else:
                        shutil.copy2(exe_in_cache, exe_local)
                        candidate = exe_local
                        log(f"Updater: copied latest exe to {candidate}")
                except Exception as e:
                    log(f"Updater: copy exe failed: {e}")
                    candidate = exe_in_cache
            else:
                candidate = exe_local

        if candidate is None and exe_in_cache.exists():
            candidate = exe_in_cache

        if candidate is None or not candidate.exists():
            log("Updater: no exe found, staying on Python")
            return False

        if current and candidate.resolve() == current.resolve():
            log("Updater: already running latest exe")
            return False

        if not getattr(sys, 'frozen', False):
            log(f"Updater: launching exe {candidate} and exiting Python")
            subprocess.Popen([str(candidate)], creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
            try:
                _update_scheduled_task_to_exe(candidate)
            except Exception as e:
                log(f"Updater: task update failed: {e}")
            sys.exit(0)
        else:
            if candidate.resolve() != current.resolve():
                log(f"Updater: newer exe available {candidate} vs {current}, launching new and exiting")
                subprocess.Popen([str(candidate)], creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
                sys.exit(0)
        return False
    except SystemExit:
        raise
    except Exception as e:
        log(f"Updater: launch failed: {e}")
        import traceback
        log(traceback.format_exc())
        return False

def _update_scheduled_task_to_exe(exe_path: Path):
    """Try to update the scheduled task to launch exe directly (best-effort, needs admin)."""
    try:
        import subprocess
        task_name = "AlbionLimiter"
        result = subprocess.run(["schtasks", "/Query", "/TN", task_name], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode != 0:
            return
        ps_cmd = f"""
$Action = New-ScheduledTaskAction -Execute '{exe_path}'
$Task = Get-ScheduledTask -TaskName '{task_name}' -ErrorAction Stop
$Task.Actions = @($Action)
Set-ScheduledTask -TaskName '{task_name}' -Action $Action | Out-Null
"""
        subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        log(f"Updater: scheduled task updated to {exe_path}")
    except Exception as e:
        log(f"Updater: task update error: {e}")

def _http_simple_update(show_ui=False):
    """Simple HTTP version check: fetch version.txt, compare, download exe if newer. Most reliable, no git needed."""
    try:
        import urllib.request
        # Fetch remote version
        ver_url = f"{REPO_RAW_BASE}/version.txt"
        log(f"Updater: HTTP simple check {ver_url}")
        try:
            with urllib.request.urlopen(ver_url, timeout=10) as resp:
                remote_ver = resp.read().decode('utf-8', errors='ignore').strip()
        except Exception as e:
            log(f"Updater: version fetch failed: {e}")
            return False

        # Compare versions (simple tuple)
        def parse(v):
            try:
                return tuple(int(x) for x in v.strip().split('.'))
            except Exception:
                return (0,)
        local = parse(__version__)
        remote = parse(remote_ver)
        log(f"Updater: local {__version__} vs remote {remote_ver}")
        if remote <= local:
            log("Updater: already latest (HTTP simple)")
            return False

        log(f"Updater: new version {remote_ver} available, downloading exe...")
        exe_url = f"{REPO_RAW_BASE}/dist/PlayLimit.exe"
        # Download to temp
        import tempfile
        tmp_path = Path(tempfile.gettempdir()) / f"PlayLimit_{remote_ver.replace('.','_')}.exe"
        try:
            with urllib.request.urlopen(exe_url, timeout=30) as resp:
                data = resp.read()
            if len(data) < 1024*100:
                log("Updater: downloaded exe too small, abort")
                return False
            tmp_path.write_bytes(data)
            log(f"Updater: downloaded {len(data)} bytes to {tmp_path}")
        except Exception as e:
            log(f"Updater: exe download failed: {e}")
            return False

        # Try to replace local exe
        try:
            # If running as exe, we can't overwrite ourselves, just launch temp and exit
            if getattr(sys, 'frozen', False):
                current = Path(sys.executable)
                # Launch temp exe directly
                log(f"Updater: launching new version {tmp_path}")
                subprocess.Popen([str(tmp_path)], creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
                # Also try to overwrite ProgramData local for next time
                try:
                    import shutil
                    # Try to copy to EXE_LOCAL for next boot, ignore lock
                    try:
                        EXE_LOCAL.unlink(missing_ok=True)
                    except Exception:
                        pass
                    shutil.copy2(tmp_path, EXE_LOCAL)
                    log(f"Updater: updated {EXE_LOCAL}")
                except Exception as e:
                    log(f"Updater: copy to local failed (will use temp): {e}")
                # Also try to update Program Files if writable
                try:
                    pf_exe = Path("C:/Program Files/AlbionLimiter/PlayLimit.exe")
                    if pf_exe.exists():
                        try:
                            pf_exe.unlink(missing_ok=True)
                        except Exception:
                            pass
                        import shutil
                        shutil.copy2(tmp_path, pf_exe)
                        log(f"Updater: updated {pf_exe}")
                except Exception:
                    pass
                if show_ui:
                    show_message("PlayLimit Update", f"Updated to v{remote_ver}!\nRestarting...", 0x40)
                # Exit current
                time.sleep(0.5)
                os._exit(0)
            else:
                # Running as python - just update local exe files
                try:
                    EXE_LOCAL.parent.mkdir(parents=True, exist_ok=True)
                    import shutil
                    try:
                        EXE_LOCAL.unlink(missing_ok=True)
                    except Exception:
                        pass
                    shutil.copy2(tmp_path, EXE_LOCAL)
                    log(f"Updater: updated {EXE_LOCAL} to v{remote_ver}")
                    pf_exe = Path("C:/Program Files/AlbionLimiter/PlayLimit.exe")
                    try:
                        pf_exe.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            pf_exe.unlink(missing_ok=True)
                        except Exception:
                            pass
                        shutil.copy2(tmp_path, pf_exe)
                        log(f"Updater: updated {pf_exe}")
                    except Exception:
                        pass
                    # Update cache too
                    try:
                        cache_exe = UPDATE_CACHE_DIR / EXE_REPO_REL
                        cache_exe.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(tmp_path, cache_exe)
                    except Exception:
                        pass
                    if show_ui:
                        show_message("PlayLimit Update", f"Updated to v{remote_ver}!\nPlease restart PlayLimit.", 0x40)
                    return True
                except Exception as e:
                    log(f"Updater: copy failed: {e}")
                    return False
        except SystemExit:
            raise
        except Exception as e:
            log(f"Updater: http simple failed: {e}")
            return False
        return True
    except Exception as e:
        log(f"Updater: http simple error: {e}")
        return False

def self_update(show_ui=False):
    """Called on startup (and on tray click). Pulls latest from git, syncs exe, and maybe relaunches.
       If show_ui=True, shows a popup with result (for manual tray click)."""
    if not AUTO_UPDATE_ENABLED:
        if show_ui:
            show_message("PlayLimit Update", "Auto-update is disabled.", 0x40)
        return
    try:
        log("=== Auto-update check ===")
        # First try simple HTTP version check (most reliable, no git, no storm)
        if _http_simple_update(show_ui=show_ui):
            # _http_simple_update already handled launch/exit if needed, if it returned True it updated as python
            # For exe case it would have exited, so we only get here for python case
            log("Updater: HTTP simple succeeded")
            # Still do git pull in background to keep repo cache fresh, but don't need to launch
            try:
                _try_git_update()
            except Exception:
                pass
            # Check if we should launch new exe (for python case, launch)
            _launch_latest_exe_and_exit()
            return

        # Remember current exe hash before update to detect if update actually happened
        before_hash = None
        try:
            exe_in_cache = UPDATE_CACHE_DIR / EXE_REPO_REL
            if exe_in_cache.exists():
                before_hash = _sha256_file(exe_in_cache)
        except Exception:
            pass

        ok = False
        if _try_git_update():
            ok = True
            log("Updater: git update done")
        else:
            log("Updater: git failed or not available, trying HTTP")
            if _try_http_update():
                ok = True
                log("Updater: HTTP update done")
            else:
                log("Updater: HTTP also failed or no update needed")

        # Check if exe actually changed
        after_hash = None
        updated = False
        try:
            exe_in_cache = UPDATE_CACHE_DIR / EXE_REPO_REL
            if exe_in_cache.exists():
                after_hash = _sha256_file(exe_in_cache)
                if before_hash and after_hash and before_hash != after_hash:
                    updated = True
                    log(f"Updater: exe changed {before_hash[:8]} -> {after_hash[:8]}")
                elif before_hash is None and after_hash:
                    # first time cache, treat as updated if local exe differs
                    if EXE_LOCAL.exists():
                        try:
                            if not _files_equal(exe_in_cache, EXE_LOCAL):
                                updated = True
                        except Exception:
                            pass
                    else:
                        updated = True
        except Exception:
            pass

        if show_ui:
            if updated:
                try:
                    # Show version from file if available
                    ver = __version__
                    try:
                        # Try to read version from cached py
                        cached_py = UPDATE_CACHE_DIR / PY_REPO_NAME
                        if cached_py.exists():
                            txt = cached_py.read_text(encoding="utf-8", errors="ignore")
                            import re
                            m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', txt)
                            if m:
                                ver = m.group(1)
                    except Exception:
                        pass
                    show_message("PlayLimit Update", f"Updated to latest version v{ver}!\n\nRestarting...", 0x40)
                except Exception:
                    pass
            else:
                # Only show 'already latest' when manually triggered, not on silent startup
                show_message("PlayLimit Update", f"Already on latest version v{__version__}.\n\nNo update needed.", 0x40)

        _launch_latest_exe_and_exit()

        try:
            running_py = Path(__file__).resolve()
            cached_py = UPDATE_CACHE_DIR / PY_REPO_NAME
            if cached_py.exists() and running_py.exists():
                if not _files_equal(cached_py, running_py):
                    try:
                        import shutil
                        shutil.copy2(cached_py, running_py)
                        log(f"Updater: updated running py {running_py}")
                    except PermissionError:
                        log(f"Updater: no permission to update {running_py}, will use cached copy next time via exe")
                    except Exception as e:
                        log(f"Updater: py copy failed: {e}")
        except Exception as e:
            log(f"Updater: py sync error: {e}")

        log("=== Auto-update done ===")
    except SystemExit:
        raise
    except Exception as e:
        log(f"Updater: unexpected error: {e}")
        import traceback
        log(traceback.format_exc())

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
        while True:
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
                    log("Hotkey pressed: Ctrl+Alt+D - CLOSING PlayLimit")
                    try:
                        show_warning_async("PlayLimit", "PlayLimit is closing...\n\nTo restart, run PlayLimit again or reboot.", 0x40)
                    except Exception:
                        pass
                    try:
                        # Just exit - don't disable task or create flag, so next boot/startup will be ENABLED
                        # (use Ctrl+Shift+D if you want to stay disabled)
                        pass
                    except Exception:
                        pass
                    log("PlayLimit closed via Ctrl+Alt+D - exiting (next startup will be ENABLED)")
                    # Give popup a moment to show, then exit
                    time.sleep(0.5)
                    os._exit(0)
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

        root = tk.Tk()
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

        # No Close button - kids would click it; window is not closable via X or taskbar either
        # Keep an empty frame for spacing
        tk.Label(btn_frame, text=" ", bg="#1e1e2e").pack()

        # Make window NOT closable via X or taskbar (Alt+F4, system menu)
        try:
            root.protocol("WM_DELETE_WINDOW", lambda: None)
            # Also disable system menu Close via Win32
            try:
                hwnd = ctypes.windll.user32.FindWindowW(None, root.title())
                if hwnd == 0:
                    # Try GetParent for Tk window
                    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
                if hwnd:
                    hMenu = ctypes.windll.user32.GetSystemMenu(hwnd, 0)
                    if hMenu:
                        ctypes.windll.user32.DeleteMenu(hMenu, 0xF060, 0x0)  # SC_CLOSE
                        ctypes.windll.user32.DrawMenuBar(hwnd)
            except Exception:
                pass
        except Exception:
            pass

        refresh()
        # Center on screen
        try:
            root.update_idletasks()
            x = (root.winfo_screenwidth() // 2) - (360 // 2)
            y = (root.winfo_screenheight() // 2) - (220 // 2)
            root.geometry(f"360x240+{x}+{y}")
        except Exception:
            pass
        root.mainloop()
    except Exception as e:
        try:
            log(f"show_time_window error: {e}")
            # Fallback to MessageBox with time
            with _state_lock:
                s = load_state()
                limit = get_effective_limit_sec(datetime.date.today(), s)
                used = int(s.get("used_seconds", 0))
                remaining = max(0, limit - used)
            show_message("PlayLimit - Time Left", f"Used: {format_minutes(used)}\nLimit: {format_minutes(limit)}\nLeft: {format_minutes(remaining)}", 0x40)
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
            # Every time tray icon is clicked, check for updates first and show result, then show time window
            def do_show_with_update():
                try:
                    # Show checking popup via log plus update check with UI
                    log("Tray Show clicked - checking for updates...")
                    # Run update check with UI (shows popup if updated or already latest)
                    try:
                        self_update(show_ui=True)
                    except SystemExit:
                        # self_update launched new exe and exited old process - this thread will die
                        return
                    except Exception as e:
                        log(f"Tray update check error: {e}")
                finally:
                    # Always show time window after update check
                    try:
                        show_time_window()
                    except Exception as e:
                        log(f"Show window error: {e}")
            threading.Thread(target=do_show_with_update, daemon=True).start()

        def on_check(icon, item):
            threading.Thread(target=lambda: self_update(show_ui=True), daemon=True).start()

        def on_exit(icon, item):
            try:
                icon.stop()
            except Exception:
                pass

        menu = pystray.Menu(
            item('Show Time Left', on_show, default=True),
            item('Check for Updates', on_check),
            pystray.Menu.SEPARATOR,
            item('Exit Tray', on_exit)
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
    """Windows MessageBox (MB_OK | MB_ICONWARNING etc). Non-blocking via thread? We use blocking but short."""
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, style)
    except Exception as e:
        log(f"MessageBox failed: {e}")

def show_warning_async(title, text, style=0x30):
    """Show MessageBox without blocking main loop (in separate process)."""
    try:
        # Use powershell popup via subprocess to avoid blocking
        # Or spawn a new python process to show messagebox
        subprocess.Popen(
            [sys.executable, "-c",
             f"import ctypes; ctypes.windll.user32.MessageBoxW(0, {text!r}, {title!r}, {style})"],
            creationflags=subprocess.CREATE_NO_WINDOW
        )
    except Exception:
        # fallback blocking
        show_message(title, text, style)

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
    # Check persisted disabled flag - auto-clean stale close flags (v1.2.2 Ctrl+Alt+D created flag that should not persist)
    global APP_DISABLED
    try:
        if DISABLE_FLAG_FILE.exists():
            try:
                content = DISABLE_FLAG_FILE.read_text(encoding="utf-8", errors="ignore").lower()
                # If flag was created via Ctrl+Alt+D close (should not cause persistent disabled), clean it
                if "closed via ctrl+alt+d" in content:
                    log(f"Startup: cleaning stale close flag at {DISABLE_FLAG_FILE} (was: {content.strip()[:60]}) - starting ENABLED")
                    try:
                        DISABLE_FLAG_FILE.unlink()
                    except Exception:
                        pass
                    APP_DISABLED = False
                else:
                    APP_DISABLED = True
                    log(f"Startup: DISABLED flag found at {DISABLE_FLAG_FILE} - blocking is OFF (Ctrl+Shift+D to disable, delete flag to re-enable)")
            except Exception:
                APP_DISABLED = True
                log(f"Startup: DISABLED flag found at {DISABLE_FLAG_FILE} - blocking is OFF (Ctrl+Shift+D to disable, delete flag to re-enable)")
    except Exception:
        pass

    # Make not closable
    try:
        protect_process()
        log("Protect: anti-close enabled (console close blocked, close button disabled)")
    except Exception as e:
        log(f"Protect failed: {e}")

    log(f"=== AlbionLimiter started ===")
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

    # Start tray icon (click to show time window)
    try:
        tr = threading.Thread(target=tray_thread, daemon=True, name="TrayIcon")
        tr.start()
        log("Tray thread started - click icon to show time")
    except Exception as e:
        log(f"Failed to start tray thread: {e}")

    # Auto-show little time window 2 sec after startup so you SEE the app (GUI, not console)
    try:
        def _auto_show():
            time.sleep(2)
            try:
                show_time_window()
            except Exception:
                pass
        threading.Thread(target=_auto_show, daemon=True).start()
        log("Auto-show time window scheduled (2s)")
    except Exception:
        pass

    with _state_lock:
        state = load_state()
        save_state(state)

    while True:
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

                    # If we just hit the limit during this tick, close now
                    if used >= limit:
                        log(f"Time up! Closing Albion Online (used {format_minutes(used)})")
                        show_warning_async(
                            "Albion Online - Time's up!",
                            f"Your time is up for today!\n\n"
                            f"Limit: {limit//60} minutes ({'Weekend' if is_weekend(today) else 'Weekday'})\n"
                            f"Closing Albion Online now.\n"
                            f"See you tomorrow!",
                            style=0x10
                        )
                        # Give 5 seconds to read message before kill? Kill immediately after
                        time.sleep(3)
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
            time.sleep(POLL_INTERVAL_SEC)

        except KeyboardInterrupt:
            log("Interrupted by user, exiting")
            break
        except Exception as e:
            log(f"Loop error: {e}")
            import traceback
            log(traceback.format_exc())
            time.sleep(POLL_INTERVAL_SEC)

if __name__ == "__main__":
    # Handle --show-time: desktop icon - check for updates with UI, then show time window
    if "--show-time" in sys.argv or "--time" in sys.argv:
        try:
            # Check for updates first and show result (every time desktop icon is clicked)
            try:
                self_update(show_ui=True)
            except SystemExit:
                # self_update launched new exe - new process will show window
                sys.exit(0)
            except Exception:
                pass
            # No mutex needed for just showing time window
            show_time_window()
        except SystemExit:
            raise
        except Exception:
            try:
                with _state_lock:
                    s = load_state()
                    limit = get_effective_limit_sec(datetime.date.today(), s)
                    used = int(s.get("used_seconds", 0))
                    remaining = max(0, limit - used)
                show_message("PlayLimit - Time Left", f"Used: {format_minutes(used)}\nLimit: {format_minutes(limit)}\nLeft: {format_minutes(remaining)}", 0x40)
            except Exception:
                pass
        sys.exit(0)

    # --- Auto-update BEFORE mutex (so new exe can start) ---
    try:
        self_update()
    except SystemExit:
        raise
    except Exception as e:
        try:
            log(f"Startup update error (non-fatal): {e}")
        except Exception:
            pass

    # Ensure single instance
    try:
        import ctypes.wintypes
        mutex_name = "Global\\AlbionLimiterMutex"
        kernel32 = ctypes.windll.kernel32
        mutex = kernel32.CreateMutexW(None, False, mutex_name)
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            log("Another instance is already running, exiting")
            sys.exit(0)
    except Exception:
        pass

    main_loop()
