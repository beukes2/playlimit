#!/usr/bin/env python3
"""PlayLimit LAN server + local web dashboard.

Clients POST a heartbeat every 30 seconds to http://<this-pc>:8765/api/heartbeat.
This process stores their status/history/logs and returns the parent's commands
(enable/disable, bonus time, daily limit, exit). The dashboard is bound to
127.0.0.1 only; the LAN port only accepts authenticated heartbeats.
"""
import datetime
import json
import os
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_NAME = "PlayLimit Server"
APP_VERSION = "1.0.0"
API_PORT = 8765
DASHBOARD_PORT = API_PORT  # dashboard and API share one port; dashboard is localhost-only
DISCOVERY_PORT = 8766
OFFLINE_AFTER_SEC = 90
LOG_KEEP = 250


def _data_dir():
    try:
        p = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "PlayLimitServer"
        p.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        p = Path.home() / "PlayLimitServer"
        p.mkdir(parents=True, exist_ok=True)
        return p


DATA_DIR = _data_dir()
DATA_FILE = DATA_DIR / "server_data.json"
SECRET_FILE = DATA_DIR / "server_secret.txt"
SERVER_LOG = DATA_DIR / "server.log"


def log(msg):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(SERVER_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_secret():
    try:
        s = SECRET_FILE.read_text(encoding="utf-8").strip()
        if s:
            return s
    except Exception:
        pass
    s = secrets.token_urlsafe(18)
    try:
        SECRET_FILE.write_text(s, encoding="utf-8")
    except Exception as e:
        log("could not save secret: %s" % e)
    return s


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        try:
            s.close()
        except Exception:
            pass


HOSTNAME = socket.gethostname()
SECRET = load_secret()
LAN_IP = lan_ip()
API_URL = "http://%s:%d" % (LAN_IP, API_PORT)
DASHBOARD_FILE = Path(__file__).resolve().parent / "server" / "dashboard.html"
if not DASHBOARD_FILE.exists():
    DASHBOARD_FILE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "server" / "dashboard.html"

_lock = threading.Lock()
_store = {"machines": {}}
_command_events = {}
_events_lock = threading.Lock()
LONGPOLL_WAIT_SEC = 25


def event_for(host):
    with _events_lock:
        ev = _command_events.get(host)
        if ev is None:
            ev = threading.Event()
            _command_events[host] = ev
        return ev


def load_store():
    global _store
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("machines"), dict):
            _store = data
            return
    except Exception:
        pass
    _store = {"machines": {}}


def save_store():
    try:
        tmp = DATA_FILE.with_name(DATA_FILE.name + ".tmp")
        tmp.write_text(json.dumps(_store, indent=2), encoding="utf-8")
        tmp.replace(DATA_FILE)
    except Exception as e:
        log("store save error: %s" % e)


def iso(epoch):
    try:
        return datetime.datetime.fromtimestamp(float(epoch)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def sanitize_machine(payload):
    status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    history = payload.get("history") if isinstance(payload.get("history"), dict) else {}
    logs = payload.get("logs") if isinstance(payload.get("logs"), list) else []
    logs = [str(x)[:500] for x in logs[-LOG_KEEP:]]
    return status, history, logs


def handle_heartbeat(payload):
    host = str(payload.get("host") or "").strip()[:64]
    if not host:
        raise ValueError("missing host")
    status, history, logs = sanitize_machine(payload)
    version = str(payload.get("version") or "")[:32]
    date = str(payload.get("date") or status.get("date") or datetime.date.today().isoformat())[:10]
    now = time.time()

    with _lock:
        machine = _store["machines"].get(host)
        if not machine:
            machine = {"host": host, "first_seen": now, "commands": {}}
            _store["machines"][host] = machine
        commands = machine.setdefault("commands", {})
        if "disabled" not in commands:
            commands["disabled"] = bool(status.get("disabled", False))
        if commands.get("bonus_seconds") is None:
            commands["bonus_seconds"] = max(0, int(status.get("bonus_sec", 0) or 0))
        if machine.get("date") != date:
            machine["date"] = date
            # New day on the client: start today's bonus from its current value.
            commands["bonus_seconds"] = max(0, int(status.get("bonus_sec", 0) or 0))
        machine["last_seen"] = now
        machine["ip"] = str(payload.get("_source_ip") or "")[:64]
        machine["version"] = version
        machine["status"] = status
        machine["history"] = history
        machine["logs"] = logs
        machine["date"] = date
        response = dict(commands)
        if response.get("exit"):
            commands["exit"] = False
        save_store()

    if payload.get("_longpoll"):
        # Keep the client's request open until a dashboard command arrives (or
        # the wait expires). This makes buttons apply in ~1s instead of waiting
        # for the next 30s status heartbeat.
        ev = event_for(host)
        if not ev.is_set():
            ev.wait(LONGPOLL_WAIT_SEC)
        ev.clear()
        with _lock:
            machine = _store["machines"].get(host)
            commands = machine.get("commands", {}) if machine else {}
            response = dict(commands)
            if response.get("exit"):
                commands["exit"] = False
                save_store()
    return response


def machine_summary(host, machine, now):
    status = machine.get("status") or {}
    commands = machine.get("commands") or {}
    last = float(machine.get("last_seen") or 0)
    return {
        "host": host,
        "online": (now - last) < OFFLINE_AFTER_SEC,
        "last_seen": last,
        "last_seen_text": iso(last),
        "ip": machine.get("ip") or "",
        "version": machine.get("version") or "",
        "date": machine.get("date") or "",
        "status": status,
        "history": machine.get("history") or {},
        "commands": {
            "disabled": commands.get("disabled"),
            "bonus_seconds": commands.get("bonus_seconds"),
            "daily_limit_sec": commands.get("daily_limit_sec"),
        },
    }


def machines_payload():
    now = time.time()
    with _lock:
        machines = [machine_summary(h, m, now) for h, m in sorted(_store["machines"].items())]
    return {
        "server": {
            "name": APP_NAME,
            "version": APP_VERSION,
            "host": HOSTNAME,
            "lan_ip": LAN_IP,
            "api_url": API_URL,
            "api_port": API_PORT,
            "dashboard_port": DASHBOARD_PORT,
            "secret": SECRET,
        },
        "machines": machines,
    }


def machine_payload(host):
    with _lock:
        machine = _store["machines"].get(host)
        if not machine:
            return None
        data = machine_summary(host, machine, time.time())
        data["logs"] = list(machine.get("logs") or [])
        return data


def apply_command(host, action, value):
    with _lock:
        machine = _store["machines"].get(host)
        if not machine:
            raise ValueError("unknown machine")
        commands = machine.setdefault("commands", {})
        if action == "enable":
            commands["disabled"] = False
        elif action == "disable":
            commands["disabled"] = True
        elif action == "bonus":
            current = commands.get("bonus_seconds")
            if current is None:
                current = int((machine.get("status") or {}).get("bonus_sec", 0) or 0)
            commands["bonus_seconds"] = max(0, int(current) + int(value or 0))
        elif action == "bonus_reset":
            commands["bonus_seconds"] = 0
        elif action == "set_limit":
            try:
                minutes = int(value)
            except Exception:
                minutes = 0
            commands["daily_limit_sec"] = None if minutes <= 0 else max(60, minutes * 60)
        elif action == "clear_limit":
            commands["daily_limit_sec"] = None
        elif action == "exit":
            commands["exit"] = True
        else:
            raise ValueError("unknown action")
        save_store()
    event_for(host).set()
    log("command %s for %s -> %s" % (action, host, value))
    return {"ok": True}


class APIHandler(BaseHTTPRequestHandler):
    """One HTTP server: LAN heartbeats + localhost-only dashboard/commands."""
    server_version = "PlayLimitServer/1.0"

    def log_message(self, fmt, *args):
        pass

    def _is_local(self):
        ip = str(self.client_address[0] if self.client_address else "")
        return ip in ("127.0.0.1", "::1") or ip.startswith("::ffff:127.0.0.1")

    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _send_html(self):
        try:
            body = DASHBOARD_FILE.read_bytes()
        except Exception as e:
            self._send(500, {"ok": False, "error": "dashboard missing: %s" % e})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _local_only(self):
        if self._is_local():
            return True
        self._send(403, {"ok": False, "error": "dashboard is localhost-only"})
        return False

    def do_GET(self):
        if not self._local_only():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            self._send_html()
        elif path == "/api/debug":
            self._send(200, {
                "pid": os.getpid(),
                "data_file": str(DATA_FILE),
                "data_exists": DATA_FILE.exists(),
                "machines": len(_store.get("machines", {})),
                "cwd": os.getcwd(),
                "executable": sys.executable,
            })
        elif path == "/api/machines":
            self._send(200, machines_payload())
        elif path == "/api/machine":
            host = (parse_qs(parsed.query).get("host") or [""])[0]
            data = machine_payload(host)
            if data:
                self._send(200, data)
            else:
                self._send(404, {"ok": False, "error": "unknown machine"})
        elif path == "/server.json":
            body = json.dumps({"url": API_URL, "secret": SECRET}, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition", "attachment; filename=server.json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                pass
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/heartbeat":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                payload["_source_ip"] = self.client_address[0] if self.client_address else ""
                payload["_longpoll"] = bool(payload.get("longpoll"))
                supplied = (self.headers.get("X-PlayLimit-Secret") or payload.get("secret") or "").strip()
                # Empty secret = LAN auto-discovery heartbeat, so a fresh client can
                # appear without copying server.json. A wrong non-empty secret is
                # still rejected. Dashboard/commands are localhost-only regardless.
                if supplied and supplied != SECRET:
                    self._send(401, {"ok": False, "error": "bad secret"})
                    return
                commands = handle_heartbeat(payload)
                self._send(200, {"ok": True, "commands": commands, "longpoll": True})
            except Exception as e:
                self._send(400, {"ok": False, "error": str(e)})
            return
        if path == "/api/command":
            if not self._local_only():
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                result = apply_command(str(body.get("host") or ""),
                                       str(body.get("action") or ""),
                                       body.get("value"))
                self._send(200, result)
            except Exception as e:
                self._send(400, {"ok": False, "error": str(e)})
            return
        self._send(404, {"ok": False, "error": "not found"})


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "PlayLimitDashboard/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _send_html(self):
        try:
            body = DASHBOARD_FILE.read_bytes()
        except Exception as e:
            self._send_json(500, {"ok": False, "error": "dashboard missing: %s" % e})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            self._send_html()
        elif path == "/api/machines":
            self._send_json(200, machines_payload())
        elif path == "/api/machine":
            host = (parse_qs(parsed.query).get("host") or [""])[0]
            data = machine_payload(host)
            if data:
                self._send_json(200, data)
            else:
                self._send_json(404, {"ok": False, "error": "unknown machine"})
        elif path == "/server.json":
            body = json.dumps({"url": API_URL, "secret": SECRET}, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition", "attachment; filename=server.json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                pass
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if urlparse(self.path).path != "/api/command":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            result = apply_command(str(body.get("host") or ""), str(body.get("action") or ""), body.get("value"))
            self._send_json(200, result)
        except Exception as e:
            self._send_json(400, {"ok": False, "error": str(e)})


def discovery_thread():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", DISCOVERY_PORT))
    except Exception as e:
        log("discovery disabled: %s" % e)
        return
    log("discovery listening on UDP %d" % DISCOVERY_PORT)
    while True:
        try:
            data, addr = sock.recvfrom(2048)
            if data.strip() == b"PLAYLIMIT_DISCOVER":
                reply = json.dumps({"url": API_URL, "server": HOSTNAME}).encode("utf-8")
                sock.sendto(reply, addr)
        except Exception:
            time.sleep(1)


def serve(handler, host, port, name):
    httpd = ThreadingHTTPServer((host, port), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True, name=name)
    t.start()
    return httpd


def main():
    load_store()
    log("=== %s %s starting ===" % (APP_NAME, APP_VERSION))
    log("dashboard: http://127.0.0.1:%d" % DASHBOARD_PORT)
    log("clients:   %s" % API_URL)
    log("secret:    %s" % SECRET)
    print("")
    print("PlayLimit Server is running.")
    print("Open the dashboard: http://localhost:%d" % DASHBOARD_PORT)
    print("Client config:      http://localhost:%d/server.json" % DASHBOARD_PORT)
    print("LAN address:        %s" % API_URL)
    print("Keep this window open. Press Ctrl+C to stop.")
    print("")
    serve(APIHandler, "0.0.0.0", API_PORT, "HTTP")
    threading.Thread(target=discovery_thread, daemon=True, name="Discovery").start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    main()
