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
        exe_in_cache = UPDATE_CACHE_DIR / EXE_REPO_REL
        exe_local = EXE_LOCAL
        candidate = None
        if exe_in_cache.exists() and exe_in_cache.stat().st_size > 1024 * 100:
            if not exe_local.exists() or not _files_equal(exe_in_cache, exe_local):
                try:
                    exe_local.parent.mkdir(parents=True, exist_ok=True)
                    import shutil
                    if exe_local.exists():
                        try:
                            exe_local.unlink()
                        except Exception:
                            temp_exe = exe_local.with_name(f"PlayLimit_{int(time.time())}.exe")
                            shutil.copy2(exe_in_cache, temp_exe)
                            candidate = temp_exe
                        else:
                            shutil.copy2(exe_in_cache, exe_local)
                            candidate = exe_local
                    else:
                        shutil.copy2(exe_in_cache, exe_local)
                        candidate = exe_local
                    if candidate is None:
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

        current = Path(sys.executable).resolve() if getattr(sys, 'frozen', False) else None
        if current and current == candidate.resolve():
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

def self_update():
    """Called on startup. Pulls latest from git, syncs exe, and maybe relaunches."""
    if not AUTO_UPDATE_ENABLED:
        return
    try:
        log("=== Auto-update check ===")
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
    """Global hotkey Ctrl+Alt+T -> +15 min. Uses RegisterHotKey (no extra deps)."""
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
    except Exception as e:
        log(f"Hotkey: ctypes not available: {e}")
        return

    HOTKEY_ID = 1
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    VK_T = 0x54  # 'T'
    WM_HOTKEY = 0x0312

    # Register hotkey on this thread's message queue
    if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT, VK_T):
        err = kernel32.GetLastError()
        log(f"Hotkey: RegisterHotKey Ctrl+Alt+T failed, error {err} (maybe already registered)")
        return

    log("Hotkey registered: Ctrl+Alt+T = +15 minutes for today (resets tomorrow)")

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
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                log("Hotkey pressed: Ctrl+Alt+T")
                try:
                    add_bonus_time(BONUS_STEP_SEC)
                except Exception as e:
                    log(f"Hotkey handler error: {e}")
                    import traceback
                    log(traceback.format_exc())
            # Needed to dispatch? For None hwnd, just continue
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        user32.UnregisterHotKey(None, HOTKEY_ID)
        log("Hotkey unregistered")

def console_thread():
    """Opens a console window (if hidden) and prints time left every 60s."""
    # Try to allocate/show console when running as noconsole exe or pythonw
    try:
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        hwnd = kernel32.GetConsoleWindow()
        if hwnd == 0:
            try:
                kernel32.AllocConsole()
                hwnd = kernel32.GetConsoleWindow()
            except Exception:
                pass
        if hwnd:
            try:
                user32.ShowWindow(hwnd, 5)  # SW_SHOW
            except Exception:
                pass
        # Reopen std handles to new console if needed
        try:
            import io
            if hwnd != 0:
                # Reattach stdout/stderr to CONOUT$
                try:
                    sys.stdout = open('CONOUT$', 'w', buffering=1, encoding='utf-8', errors='replace')
                except Exception:
                    pass
                try:
                    sys.stderr = open('CONOUT$', 'w', buffering=1, encoding='utf-8', errors='replace')
                except Exception:
                    pass
                try:
                    sys.stdin = open('CONIN$', 'r', encoding='utf-8')
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass

    # Print header
    try:
        print("=" * 60)
        print(" PlayLimit - Albion Online Time Limiter")
        print("=" * 60)
        print(f" Weekday limit: {WEEKDAY_LIMIT_SEC//60} min | Weekend: {WEEKEND_LIMIT_SEC//60} min | Warning: {WARNING_BEFORE_SEC//60} min before")
        print(f" Hotkey: Ctrl+Alt+T = +15 min for today (resets tomorrow)")
        print(f" State: {STATE_FILE}")
        print(f" Log:   {LOG_FILE}")
        print("-" * 60)
        sys.stdout.flush()
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
            line = f"[{ts}] {day_type} | Base {base//60}min + Bonus {bonus//60}min = {limit//60}min | Used {format_minutes(used)} | Left {format_minutes(remaining)} | Albion {status} {'(WARNED)' if warned else ''}"
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

def kill_albion():
    """Try graceful close via WM_CLOSE, then terminate."""
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
    log(f"=== AlbionLimiter started ===")
    log(f"Data dir: {DATA_DIR}")
    log(f"State file: {STATE_FILE}")
    log(f"Weekday limit: {WEEKDAY_LIMIT_SEC//60} min, Weekend: {WEEKEND_LIMIT_SEC//60} min, Warning: {WARNING_BEFORE_SEC//60} min before, Bonus step: {BONUS_STEP_SEC//60} min via Ctrl+Alt+T")
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
