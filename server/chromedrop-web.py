#!/usr/bin/env python3
"""chromedrop-web: localhost UI + supervisor that runs Owly (AirDrop) on ChromeOS.

Runs as root (Upstart job). Binds 127.0.0.1 only.
"AirDrop on" = stop shill/wpasupplicant -> `owly radio up` -> `owly receive`.
"AirDrop off" = reverse, and Wi-Fi comes back.
"""
import glob, json, os, pty, queue, re, shutil, signal, subprocess, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

E = os.environ.get
CONF = E("CHROMEDROP_CONF", "/etc/chromedrop.conf")
STATE = E("CHROMEDROP_STATE", "/var/lib/chromedrop")
NETDIR = E("CHROMEDROP_NETDIR", "/sys/class/net")
OWLY = E("CHROMEDROP_OWLY", "/usr/local/bin/owly")
WEB = E("CHROMEDROP_WEB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "web"))
ENV = dict(os.environ, PATH="/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", HOME=STATE)
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def load_conf():
    c = {"PORT": "8780", "RECEIVE_DIR": ""}
    try:
        for line in open(CONF):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                c[k.strip()] = v.strip().strip('"')
    except OSError:
        pass
    return c


CV = load_conf()
PORT = int(CV["PORT"])
RECV_DIR = CV["RECEIVE_DIR"] or (sorted(glob.glob("/home/chronos/u-*/MyFiles/Downloads"))
                                 or ["/home/chronos/user/MyFiles/Downloads"])[0]
os.makedirs(os.path.join(STATE, "outbox"), exist_ok=True)
CFG_FILE = os.path.join(STATE, "config.json")
try:
    cfg = json.load(open(CFG_FILE))
except (OSError, ValueError):
    cfg = {}
cfg.setdefault("auto_accept", False)

LOCK, OP = threading.RLock(), threading.Lock()
SUBS, LOG, RECEIVED, P = [], [], [], {}
S = {"mode": "off", "error": "", "pending": False, "sender": None, "files": []}
R = {"buf": "", "tail": ""}


def snap():
    with LOCK:
        return dict(S, auto_accept=cfg["auto_accept"], received=RECEIVED[-30:], log=LOG[-80:], dir=RECV_DIR)


def push():
    msg = json.dumps(snap())
    for q in list(SUBS):
        q.put(msg)


def log(line):
    with LOCK:
        LOG.append(line)
        del LOG[:-200]
    push()


def sh(*cmd, timeout=20):
    try:
        return subprocess.run(cmd, env=ENV, capture_output=True, text=True, timeout=timeout)
    except Exception as e:  # missing binary, timeout...
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


def awdl_up():
    return os.path.exists(os.path.join(NETDIR, "awdl0"))


def wifi_if():
    try:
        for n in sorted(os.listdir(NETDIR)):
            if n.startswith("wl"):
                return n
    except OSError:
        pass
    return "wlan0"


def kill(p):
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()


def fw():  # best effort: let the phone reach the receiver on awdl0
    for ipt in ("iptables", "ip6tables"):
        rule = ["INPUT", "-i", "awdl0", "-p", "tcp", "--dport", "8771", "-j", "ACCEPT"]
        if sh(ipt, "-C", *rule).returncode:
            sh(ipt, "-I", *rule)


# receive-side parsing (owly receive runs on a pty)
RE_FROM = re.compile(r'^"(.*)" \("(.*)"\) wants to send')
RE_FILE = re.compile(r'^\s+"(.*)" (\S+) (\d+) bytes')


def answer(yes):
    fd = P.get("fd")
    if fd is None:
        return
    with LOCK:
        S["pending"] = False
        if not yes:
            S["sender"], S["files"] = None, []
    try:
        os.write(fd, b"y\n" if yes else b"n\n")
    except OSError:
        pass
    push()


def handle_line(line):
    if not line.strip():
        return
    log(line)
    m = RE_FROM.match(line)
    if m:
        with LOCK:
            S["sender"], S["files"] = m.group(1), []
        return
    m = RE_FILE.match(line)
    if m:
        with LOCK:
            S["files"].append({"name": m.group(1), "type": m.group(2), "size": int(m.group(3))})
        return
    if line.startswith("/") and os.path.isfile(line):
        try:  # we are root: hand the file to whoever owns the Downloads dir
            st = os.stat(os.path.dirname(line))
            os.chown(line, st.st_uid, st.st_gid)
            os.chmod(line, 0o644)
        except OSError:
            pass
        with LOCK:
            RECEIVED.append({"name": os.path.basename(line), "path": line, "from": S["sender"],
                             "size": os.path.getsize(line), "time": int(time.time())})
            S["sender"], S["files"] = None, []
        push()


def feed(text):
    R["buf"] += text
    while "\n" in R["buf"]:
        line, R["buf"] = R["buf"].split("\n", 1)
        handle_line(line)
    data = R["tail"] + text
    if "accept?" in data:
        R["tail"] = ""
        with LOCK:
            S["pending"] = True
        push()
        if cfg["auto_accept"]:
            answer(True)
    else:
        R["tail"] = data[-24:]


def pump_pty(fd):
    while True:
        try:
            data = os.read(fd, 4096)
        except OSError:
            break
        if not data:
            break
        feed(ANSI.sub("", data.decode(errors="replace")).replace("\r", ""))


def pump_pipe(p, tag):
    for raw in iter(p.stdout.readline, b""):
        log(f"[{tag}] " + ANSI.sub("", raw.decode(errors="replace")).rstrip())


def watch_recv(p):
    p.wait()
    with LOCK:
        was_on = S["mode"] == "on"
    if was_on:
        log("owly receive exited unexpectedly")
        with LOCK:
            S["error"] = "receiver exited"
        begin("stop")


# AirDrop mode on/off
def begin(what):
    with LOCK:
        if S["mode"] != ("off" if what == "start" else "on"):
            return False
        S["mode"] = "starting" if what == "start" else "stopping"
        S["error"] = "" if what == "start" else S["error"]
    push()
    threading.Thread(target=_start if what == "start" else _stop, daemon=True).start()
    return True


def _start():
    with OP:
        try:
            log("releasing Wi-Fi")
            for j in ("shill", "wpasupplicant"):
                sh("/sbin/stop", j)
            time.sleep(1.5)
            sh("ip", "link", "set", wifi_if(), "down")
            log("starting radio")
            p = P["radio"] = subprocess.Popen([OWLY, "radio", "up"], env=ENV, stdin=subprocess.DEVNULL,
                                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                              start_new_session=True)
            threading.Thread(target=pump_pipe, args=(p, "radio"), daemon=True).start()
            for _ in range(80):
                if awdl_up():
                    break
                rc = p.poll()
                if rc not in (None, 0):
                    raise RuntimeError(f"owly radio up exited with {rc} (see log)")
                time.sleep(0.5)
            else:
                raise RuntimeError("awdl0 never appeared")
            fw()
            os.makedirs(RECV_DIR, exist_ok=True)
            m, s = pty.openpty()
            r = subprocess.Popen([OWLY, "receive", "--dir", RECV_DIR], env=ENV, stdin=s, stdout=s, stderr=s,
                                 close_fds=True, start_new_session=True)
            os.close(s)
            P["recv"], P["fd"] = r, m
            threading.Thread(target=pump_pty, args=(m,), daemon=True).start()
            threading.Thread(target=watch_recv, args=(r,), daemon=True).start()
            with LOCK:
                S["mode"] = "on"
            push()
        except Exception as e:
            log(f"error: {e}")
            with LOCK:
                S["error"] = str(e)
            _stop_locked()


def _stop():
    with OP:
        _stop_locked()


def _stop_locked():
    with LOCK:
        S.update(mode="stopping", pending=False, sender=None, files=[])
    push()
    for k in ("recv", "radio"):
        kill(P.pop(k, None))
    fd = P.pop("fd", None)
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass
    sh(OWLY, "radio", "down", timeout=30)
    for _ in range(20):
        if not awdl_up():
            break
        time.sleep(0.5)
    for j in ("wpasupplicant", "shill"):
        sh("/sbin/start", j)
    with LOCK:
        S["mode"] = "off"
    push()


# send / browse
def dec(x):
    return x.decode(errors="replace") if isinstance(x, bytes) else (x or "")


def do_browse():
    try:
        r = subprocess.run([OWLY, "browse"], env=ENV, capture_output=True, text=True, timeout=10)
        out = r.stdout + r.stderr
    except subprocess.TimeoutExpired as e:
        out = dec(e.stdout) + dec(e.stderr)
    out = ANSI.sub("", out)
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    devices = []
    for l in lines:
        m = re.search(r'"([^"]+)"', l)
        name = m.group(1) if m else l
        if name not in devices:
            devices.append(name)
    return {"devices": devices, "raw": lines[-30:]}


def do_send(to, name, rfile, length):
    safe = re.sub(r"[^\w.\- ()]", "_", os.path.basename(name)) or "file"
    d = os.path.join(STATE, "outbox", str(int(time.time() * 1000)))
    os.makedirs(d)
    path = os.path.join(d, safe)
    try:
        with open(path, "wb") as f:
            left = length
            while left > 0:
                chunk = rfile.read(min(1 << 20, left))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        log(f"sending {safe} to {to}")
        r = subprocess.run([OWLY, "send", "--to", to, path], env=ENV, capture_output=True, text=True, timeout=300)
        out = ANSI.sub("", r.stdout + r.stderr).strip()
        log(f"send {'ok' if r.returncode == 0 else 'failed'}: {out[-200:]}")
        return {"ok": r.returncode == 0, "out": out[-400:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "out": "timed out"}
    finally:
        shutil.rmtree(d, ignore_errors=True)


# HTTP
HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
ORIGINS = {f"http://{h}" for h in HOSTS}
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
          "/sw.js": ("sw.js", "text/javascript"),
          "/icon-192.png": ("icon-192.png", "image/png"),
          "/icon-512.png": ("icon-512.png", "image/png")}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, body, ctype="application/json", extra=()):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Host") not in HOSTS:  # blocks DNS rebinding
            return self.reply(403, {"error": "bad host"})
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/state":
            return self.reply(200, snap())
        if path == "/api/events":
            return self.sse()
        if path in STATIC:
            name, ctype = STATIC[path]
            try:
                return self.reply(200, open(os.path.join(WEB, name), "rb").read(), ctype)
            except OSError:
                pass
        self.reply(404, {"error": "not found"})

    def sse(self):
        q = queue.Queue()
        SUBS.append(q)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(f"data: {json.dumps(snap())}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    self.wfile.write(f"data: {q.get(timeout=15)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            SUBS.remove(q)

    def do_POST(self):
        if self.headers.get("Host") not in HOSTS or self.headers.get("X-Chromedrop") != "1":
            return self.reply(403, {"error": "forbidden"})
        origin = self.headers.get("Origin")
        if origin and origin not in ORIGINS:
            return self.reply(403, {"error": "bad origin"})
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        n = int(self.headers.get("Content-Length") or 0)
        if u.path == "/api/send":
            if S["mode"] != "on":
                return self.reply(409, {"error": "turn AirDrop on first"})
            to, name = qs.get("to", [""])[0].strip(), qs.get("name", ["file"])[0]
            if not to:
                return self.reply(400, {"error": "no target device"})
            return self.reply(200, do_send(to, name, self.rfile, n))
        try:
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError:
            body = {}
        if u.path == "/api/start":
            return self.reply(200, {"ok": begin("start")})
        if u.path == "/api/stop":
            return self.reply(200, {"ok": begin("stop")})
        if u.path == "/api/accept":
            if S["pending"]:
                answer(bool(body.get("accept")))
            return self.reply(200, {"ok": True})
        if u.path == "/api/config":
            cfg["auto_accept"] = bool(body.get("auto_accept"))
            try:
                json.dump(cfg, open(CFG_FILE, "w"))
            except OSError:
                pass
            push()
            return self.reply(200, {"ok": True})
        if u.path == "/api/browse":
            if S["mode"] != "on":
                return self.reply(409, {"error": "turn AirDrop on first"})
            return self.reply(200, do_browse())
        self.reply(404, {"error": "not found"})


def startup_restore():
    """If we crashed with Wi-Fi stopped, bring it back."""
    if awdl_up():
        sh(OWLY, "radio", "down", timeout=30)
    if not awdl_up():
        for j in ("wpasupplicant", "shill"):
            if "stop/waiting" in sh("/sbin/status", j).stdout:
                sh("/sbin/start", j)


def shutdown(*_):
    try:
        with LOCK:
            active = S["mode"] in ("on", "starting")
        if active:
            _stop()
    finally:
        os._exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    startup_restore()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    srv.daemon_threads = True
    print(f"chromedrop-web on http://localhost:{PORT} (saving to {RECV_DIR})", flush=True)
    srv.serve_forever()
