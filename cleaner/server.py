"""The dashboard's local server. It listens on 127.0.0.1 only and runs one job at a time (scan, clean or undo).

Security: a page you have open in another tab could try to talk to this server, so every /api call needs this run's
random token (sent in a header, which other sites can't set without our permission, and we never grant it), the Host
header must be this machine, and an Origin header, when present, must be this server. Static files come only from web/.
"""
import json, mimetypes, os, platform, secrets, subprocess, sys, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import engine

WEB = Path(__file__).resolve().parent.parent / "web"


class Job:
    """The current job and everything the dashboard needs to animate it. Events are appended; the page polls with `since`."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset("idle")
        self.cfg = engine.config()
        self.steps, self.skipped, self.stop = None, {}, False

    def reset(self, phase):
        self.phase, self.error, self.done, self.total, self.events, self.result, self.started = phase, None, 0, 0, [], None, time.time()

    def view(self, since):
        with self.lock:
            return {"phase": self.phase, "error": self.error, "done": self.done, "total": self.total, "events": self.events[since:], "next": len(self.events), "result": self.result,
                    "elapsed": round(time.time() - self.started, 2)}


JOB = Job()


def file_list(steps):
    """What the 3D scene draws: one entry per file, in plan order. Only names, never full paths."""
    return [{"i": i, "name": s["name"], "kind": s["kind"], "size": s["size"], "dup": bool(s["dup_of"]), "dest": (s["dest"] or "").rsplit("/", 1)[0], "skip": s["skip"]} for i, s in enumerate(steps)]


def run_scan(cfg):
    def prog(phase, done, total):
        with JOB.lock:
            JOB.phase, JOB.done, JOB.total = phase, done, total
    try:
        rows, skipped = engine.scan(cfg, prog)
        steps = engine.plan(rows, cfg)
        with JOB.lock:
            JOB.cfg, JOB.steps, JOB.skipped = cfg, steps, skipped
            JOB.result = {"summary": engine.summary(steps, skipped), "files": file_list(steps), "folders": [str(p) for p in engine.folders(cfg)]}
            JOB.phase = "planned"
    except Exception as e:  # report every failure to the page; the server keeps running
        with JOB.lock:
            JOB.phase, JOB.error = "error", str(e)


def run_apply():
    cfg, steps = JOB.cfg, JOB.steps

    def prog(i, s):
        with JOB.lock:
            JOB.done = i + 1
            JOB.events.append({"i": i, "ok": s["dest"] is not None and not s.get("skip"), "skip": s.get("skip"), "t": round(time.time() - JOB.started, 3)})
    try:
        m = engine.apply(steps, cfg, prog, lambda: JOB.stop)
        with JOB.lock:
            JOB.result = {"run": m["run"], "action": m["action"], "written": len(m["done"]), "summary": engine.summary(steps, JOB.skipped), "stopped": JOB.stop}
            JOB.phase = "applied"
    except Exception as e:
        with JOB.lock:
            JOB.phase, JOB.error = "error", str(e)


def run_undo(run):
    def prog(i, n):
        with JOB.lock:
            JOB.done, JOB.total = i + 1, n
    try:
        r = engine.undo(JOB.cfg, run, prog)
        with JOB.lock:
            JOB.result, JOB.phase = {"undo": r, "run": run}, "undone"
    except Exception as e:
        with JOB.lock:
            JOB.phase, JOB.error = "error", str(e)


def open_folder(path):
    if platform.system() == "Windows":
        os.startfile(path)  # noqa: S606 (opens Explorer on a folder we created)
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def quick_folders():
    home = Path.home()
    return [str(home / n) for n in ("Downloads", "Desktop", "Documents", "Pictures", "Videos") if (home / n).is_dir()]


def make_handler(token, port):
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{h}" for h in hosts}

    class H(BaseHTTPRequestHandler):
        server_version = "cleaner"

        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def allowed(self, api):
            if self.headers.get("Host") not in hosts:
                return False
            o = self.headers.get("Origin")
            if o is not None and o not in origins:
                return False
            return not api or secrets.compare_digest(self.headers.get("X-Token", ""), token)

        def do_GET(self):
            path = urlparse(self.path).path
            api = path.startswith("/api/")
            if not self.allowed(api):
                return self.send(403, {"error": "forbidden"})
            if path == "/api/state":
                return self.send(200, {"schema": [{"key": k, "default": d, "rule": r, "help": h} for k, d, r, h in engine.SCHEMA], "cfg": JOB.cfg,
                                       "quick": quick_folders(), "runs": self._runs(), "platform": platform.system()})
            if path == "/api/progress":
                q = dict(x.split("=", 1) for x in (urlparse(self.path).query or "").split("&") if "=" in x)
                return self.send(200, JOB.view(int(q.get("since", 0))))
            if api:
                return self.send(404, {"error": "not found"})
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            f = (WEB / rel).resolve()
            if WEB not in f.parents or not f.is_file():
                return self.send(404, b"not found", "text/plain")
            body = f.read_bytes()
            if rel == "index.html":
                body = body.replace(b"%TOKEN%", token.encode())
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if f.suffix == ".js":
                ctype = "text/javascript"
            self.send(200, body, ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))

        def _runs(self):
            try:
                return engine.runs(JOB.cfg)
            except ValueError:
                return []

        def do_POST(self):
            path = urlparse(self.path).path
            if not self.allowed(True):
                return self.send(403, {"error": "forbidden"})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send(415, {"error": "json only"})
            n = int(self.headers.get("Content-Length", 0) or 0)
            try:
                body = json.loads(self.rfile.read(min(n, 1_000_000)) or b"{}")
            except ValueError:
                return self.send(400, {"error": "bad json"})
            busy = JOB.phase in ("scan", "hash", "apply", "undo")
            if path == "/api/stop":
                JOB.stop = True
                return self.send(200, {"ok": True})
            if busy:
                return self.send(409, {"error": "a job is running"})
            if path == "/api/scan":
                try:
                    cfg = engine.config(body.get("cfg") or {})
                    engine.folders(cfg)
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
                JOB.reset("scan")
                threading.Thread(target=run_scan, args=(cfg,), daemon=True).start()
                return self.send(200, {"ok": True})
            if path == "/api/apply":
                if not JOB.steps:
                    return self.send(409, {"error": "scan first"})
                try:
                    same = engine.config(body.get("cfg") or {}) == JOB.cfg
                except ValueError:
                    same = False
                if not same:  # never apply a plan made with other settings, or for another folder
                    return self.send(409, {"error": "settings changed since the scan: scan again"})
                JOB.stop = False
                files = JOB.result["files"] if JOB.result else None
                JOB.reset("apply")
                JOB.total = len(JOB.steps)
                JOB.result = {"files": files}
                threading.Thread(target=run_apply, daemon=True).start()
                return self.send(200, {"ok": True})
            if path == "/api/undo":
                run = str(body.get("run", ""))
                if not run or any(ch in run for ch in "/\\."):
                    return self.send(400, {"error": "bad run"})
                JOB.reset("undo")
                threading.Thread(target=run_undo, args=(run,), daemon=True).start()
                return self.send(200, {"ok": True})
            if path == "/api/open":
                try:
                    _, out = engine.folders(JOB.cfg)
                    if not out.is_dir():
                        return self.send(404, {"error": "nothing sorted yet"})
                    open_folder(str(out))
                    return self.send(200, {"ok": True})
                except Exception as e:
                    return self.send(400, {"error": str(e)})
            if path == "/api/quit":
                self.send(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            return self.send(404, {"error": "not found"})

    return H


def serve(cfg=None, port=8766, open_browser=True):
    if cfg:
        JOB.cfg = cfg
    token = secrets.token_urlsafe(24)
    httpd = None
    for p in [port] + list(range(port + 1, port + 20)):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), make_handler(token, p))
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        sys.exit("no free port near %d" % port)
    url = f"http://127.0.0.1:{port}/"
    print(f"Downloads cleaner dashboard: {url}  (only this computer can open it; Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
