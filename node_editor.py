"""init.json node list editor - local web server.

Only uses the Python standard library. Double click "编辑节点.bat" to start:
cmd.exe runs this file with py -3 (or python) and keeps the console window
open while the server runs. The browser page opens automatically; closing
that page stops the server and the cmd window disappears with it.
The server binds to 127.0.0.1 only.

Env vars:
  NODE_EDITOR_NO_BROWSER=1  do not open a browser (headless / tests)
  NODE_EDITOR_KEEP_ALIVE=1  keep running even after the page is closed
"""

import json
import os
import shutil
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INIT_PATH = os.path.join(BASE_DIR, "init.json")
HTML_PATH = os.path.join(BASE_DIR, "node_editor.html")
BACKUP_DIR = os.path.join(BASE_DIR, "init_backups")
MAX_BACKUPS = 10
MAX_BODY = 5 * 1024 * 1024
START_PORT = 8765

HEARTBEAT_INTERVAL_NOTE = 3.0   # page sends /api/ping every 3s
CLOSE_GRACE = 4.0               # wait after /api/bye, in case the page reloads
HEARTBEAT_TIMEOUT = 90.0        # browser crashed / beacon never arrived
NO_PAGE_TIMEOUT = 120.0         # browser never opened a page at all


CONSOLELESS = False


def setup_consoleless():
    """Python started without a console (pythonw.exe) has stdout/stderr = None.

    Keep print() working and leave a small log file for debugging. No-op under
    normal python.exe / cmd, where messages go straight to the console window.
    """
    global CONSOLELESS
    if sys.stdout is not None and sys.stderr is not None:
        return
    CONSOLELESS = True
    try:
        sink = open(os.path.join(BASE_DIR, "node_editor.log"), "a",
                    encoding="utf-8", buffering=1)
    except OSError:
        sink = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = sink
    if sys.stderr is None:
        sys.stderr = sink


def fatal(message):
    """Report a startup failure on the console, or in a dialog if there is none."""
    try:
        print("[ERROR] " + message, flush=True)
    except Exception:
        pass
    if os.name == "nt" and CONSOLELESS:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, "init.json Node Editor", 0x10)
        except Exception:
            pass
    return 1


def read_init():
    if not os.path.isfile(INIT_PATH):
        raise ValueError("init.json not found: " + INIT_PATH)
    with open(INIT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def file_mtime():
    if not os.path.isfile(INIT_PATH):
        return None
    return os.path.getmtime(INIT_PATH)


def validate(data):
    """Validate the whole document, keep any unknown top-level / node keys."""
    if not isinstance(data, dict):
        raise ValueError("top level must be a JSON object")
    items = data.get("select")
    if not isinstance(items, list):
        raise ValueError("'select' must be an array")
    for n, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError("node #%d must be an object" % n)
        url = item.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("node #%d needs a non-empty 'url'" % n)
        for key in ("sel", "sel_all"):
            if key in item:
                val = item[key]
                if not isinstance(val, list) or not all(
                    isinstance(x, str) for x in val
                ):
                    raise ValueError("node #%d: '%s' must be an array of strings" % (n, key))
        if "err" in item:
            err = item["err"]
            if isinstance(err, bool) or not isinstance(err, int) or err < 0:
                raise ValueError("node #%d: 'err' must be a non-negative integer" % n)
        # id is assigned at runtime by v2.py, never store it in the file
        item.pop("id", None)
    return data


def make_backup():
    if not os.path.isfile(INIT_PATH):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    name = "init_%s.json" % datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = os.path.join(BACKUP_DIR, name)
    shutil.copy2(INIT_PATH, dest)
    files = sorted(
        (f for f in os.listdir(BACKUP_DIR) if f.startswith("init_") and f.endswith(".json")),
        reverse=True,
    )
    for old in files[MAX_BACKUPS:]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass
    return name


def write_init(data):
    """Atomic write, same formatting as v2.py (indent=4, utf-8, no trailing newline).

    Keep the existing line endings (v2.py writes CRLF on Windows) so a save
    only shows the nodes that actually changed in a diff.
    """
    text = json.dumps(data, ensure_ascii=False, indent=4)
    try:
        with open(INIT_PATH, "rb") as f:
            old = f.read(4096)
        crlf = b"\r\n" in old
    except OSError:
        crlf = os.name == "nt"
    if crlf:
        text = text.replace("\n", "\r\n")
    tmp = INIT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, INIT_PATH)
    return text


class AppState:
    """Tracks whether at least one editor page is still alive."""

    def __init__(self, httpd, auto_exit=True):
        self.httpd = httpd
        self.auto_exit = auto_exit
        self.lock = threading.Lock()
        self.started_at = time.time()
        self.last_seen = self.started_at
        self.ever_seen = False
        self.close_at = None
        self.stopped = False

    def touch(self):
        """A page loaded / reloaded / heartbeat. Cancels a pending exit."""
        with self.lock:
            self.last_seen = time.time()
            self.ever_seen = True
            self.close_at = None

    def bye(self):
        """The page said goodbye. Exit shortly unless another page shows up."""
        with self.lock:
            if not self.stopped:
                self.close_at = time.time() + CLOSE_GRACE

    def reason_to_stop(self):
        if not self.auto_exit:
            return None
        with self.lock:
            if self.stopped:
                return None
            now = time.time()
            if self.close_at is not None and now >= self.close_at:
                return "page closed"
            # Background tabs can have timers throttled to ~1 minute,
            # so the crash fallback needs a generous margin.
            if self.ever_seen and now - self.last_seen > HEARTBEAT_TIMEOUT:
                return "no heartbeat"
            if not self.ever_seen and now - self.started_at > NO_PAGE_TIMEOUT:
                return "no page opened"
        return None


def watchdog(state):
    while True:
        time.sleep(0.3)
        with state.lock:
            if state.stopped:
                return
        reason = state.reason_to_stop()
        if reason:
            with state.lock:
                state.stopped = True
            print("Editor page gone (%s), stopping server." % reason, flush=True)
            # shutdown() must run outside the serve_forever() thread
            state.httpd.shutdown()
            return


class Handler(BaseHTTPRequestHandler):
    server_version = "NodeEditor/1.0"

    @property
    def state(self):
        return getattr(self.server, "app_state", None)

    def touch(self):
        if self.state:
            self.state.touch()

    def log_message(self, fmt, *args):
        pass

    def handle_one_request(self):
        # A browser tab closing mid-request must not dump a traceback
        # into the console window.
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            self.close_connection = True

    def _send(self, status, obj=None, raw=None, ctype="application/json; charset=utf-8"):
        if raw is None:
            raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        elif isinstance(raw, str):
            raw = raw.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _ok(self, **kwargs):
        payload = {"ok": True}
        payload.update(kwargs)
        self._send(200, payload)

    def _fail(self, status, message):
        self._send(status, {"ok": False, "error": message})

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            raise ValueError("invalid Content-Length")
        if length <= 0 or length > MAX_BODY:
            raise ValueError("invalid request size")
        raw = self.rfile.read(length)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("request body is not valid JSON")
        if not isinstance(obj, dict):
            raise ValueError("request body must be a JSON object")
        return obj

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html", "/node_editor.html"):
            self.touch()
            if not os.path.isfile(HTML_PATH):
                self._fail(500, "node_editor.html not found next to node_editor.py")
                return
            with open(HTML_PATH, "r", encoding="utf-8") as f:
                self._send(200, raw=f.read(), ctype="text/html; charset=utf-8")
            return
        if path == "/api/ping":
            self.touch()
            self._ok(alive=True)
            return
        if path == "/api/data":
            self.touch()
            try:
                data = read_init()
                self._ok(data=data, mtime=file_mtime(), path=INIT_PATH)
            except (ValueError, OSError, json.JSONDecodeError) as e:
                self._fail(500, str(e))
            return
        if path == "/api/backups":
            files = []
            if os.path.isdir(BACKUP_DIR):
                files = sorted(
                    (
                        {"name": f, "size": os.path.getsize(os.path.join(BACKUP_DIR, f))}
                        for f in os.listdir(BACKUP_DIR)
                        if f.startswith("init_") and f.endswith(".json")
                    ),
                    key=lambda x: x["name"],
                    reverse=True,
                )
            self._ok(files=files)
            return
        self._fail(404, "not found")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/save":
                body = self._body()
                data = validate(body.get("data"))
                base = body.get("base_mtime")
                current = file_mtime()
                if (
                    not body.get("force")
                    and base is not None
                    and current is not None
                    and abs(float(current) - float(base)) > 1e-6
                ):
                    self._fail(409, "init.json was changed outside this page since it was loaded")
                    return
                # The page asks for a backup only on its first save per
                # session, so rapid auto-saves cannot flush out the useful
                # "state before editing" backup.
                backup = None
                if body.get("backup", True):
                    backup = make_backup()
                write_init(data)
                self._ok(mtime=file_mtime(), backup=backup, count=len(data["select"]))
                return

            if path == "/api/bye":
                # sendBeacon on pagehide. Flush any write still queued in the
                # browser before this process goes away.
                try:
                    body = self._body()
                except ValueError:
                    body = {}
                if body.get("data") is not None:
                    try:
                        write_init(validate(body["data"]))
                    except (ValueError, TypeError, OSError, json.JSONDecodeError) as e:
                        print("[WARN] bye flush failed: %s" % e, flush=True)
                self._ok(closing=True)
                if self.state:
                    self.state.bye()
                return

            if path == "/api/restore":
                body = self._body()
                name = body.get("name")
                if not isinstance(name, str) or os.path.basename(name) != name:
                    self._fail(400, "invalid backup name")
                    return
                src = os.path.join(BACKUP_DIR, name)
                if not os.path.isfile(src):
                    self._fail(404, "backup not found")
                    return
                with open(src, "r", encoding="utf-8") as f:
                    data = validate(json.load(f))
                make_backup()
                write_init(data)
                self._ok(mtime=file_mtime(), count=len(data["select"]))
                return

            self._fail(404, "not found")
        except (ValueError, TypeError, OSError, json.JSONDecodeError) as e:
            self._fail(400, str(e))


def pick_port():
    import socket

    for port in range(START_PORT, START_PORT + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port available")


def main():
    setup_consoleless()
    if not os.path.isfile(INIT_PATH):
        return fatal("init.json not found: " + INIT_PATH)
    port = pick_port()
    url = "http://127.0.0.1:%d/" % port
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    state = AppState(
        server,
        auto_exit=os.environ.get("NODE_EDITOR_KEEP_ALIVE") != "1",
    )
    server.app_state = state
    threading.Thread(target=watchdog, args=(state,), daemon=True).start()
    print("Node editor running at " + url)
    print("Editing: " + INIT_PATH)
    print("Closing the editor page stops this server automatically.")
    # NODE_EDITOR_NO_BROWSER=1 is handy for headless / test runs.
    if os.environ.get("NODE_EDITOR_NO_BROWSER") != "1":
        def open_browser():
            try:
                if not webbrowser.open(url):
                    print("[WARN] could not open a browser; open %s manually" % url, flush=True)
            except Exception as e:
                print("[WARN] could not open a browser: %s" % e, flush=True)
        threading.Timer(0.5, open_browser).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    setup_consoleless()
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        sys.exit(fatal(traceback.format_exc()))
