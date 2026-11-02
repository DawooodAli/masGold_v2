"""The iPad reports server INSIDE the program (owner request 2026-11-03): it starts by itself when the program opens and shows the address to type on the iPad.

* One `WebReports` (one login cooldown) is served on 127.0.0.1:PORT and on this computer's Tailscale IPv4 address(es) found by `tailscale status --json`; nothing is bound to any other interface, so only
  your own devices on the tailnet (or this computer) can reach it. No `tailscale serve` / funnel / firewall change is made.
* The address is stable: the MagicDNS name of this computer (`http://<name>.<tailnet>.ts.net:PORT`) and its Tailscale IP. Tailscale must be running on the computer and on the iPad.
* Start-up never blocks the window (a background thread) and never fails the program: a busy port or a missing Tailscale only changes the status text.
* The on / off choice of this computer is kept in LOCALAPPDATA (default ON). Turning it off stops the servers at once."""
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import threading

from PySide6.QtCore import Property, QObject, Signal, Slot

from webapp.api import WebReports
from webapp.server import serve

PORT = 8765
logger = logging.getLogger("webapp")
CREATE_NO_WINDOW = 0x08000000


def config_path():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "masGold v2" / "web_reports.json"


def read_enabled(path=None):
    try:
        return bool(json.loads((path or config_path()).read_text(encoding="utf-8")).get("enabled", True))
    except (OSError, ValueError, AttributeError):
        return True


def write_enabled(value, path=None):
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"enabled": bool(value)}), encoding="utf-8")


def tailscale_exe():
    found = shutil.which("tailscale")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base and (Path(base) / "Tailscale" / "tailscale.exe").exists():
            return str(Path(base) / "Tailscale" / "tailscale.exe")
    return None


def tailscale_info(run=subprocess.run, exe=tailscale_exe):
    """{'ips': [IPv4...], 'dns': 'name.tailnet.ts.net'} when Tailscale is installed AND running, otherwise None. Read only (`tailscale status --json`)."""
    path = exe()
    if not path:
        return None
    try:
        done = run([path, "status", "--json"], capture_output=True, timeout=8, creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
        data = json.loads(done.stdout.decode("utf-8", "replace"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if data.get("BackendState") != "Running":
        return None
    me = data.get("Self") or {}
    ips = [ip for ip in me.get("TailscaleIPs") or [] if "." in ip]                 # IPv4 only (the iPad reaches it by name or by this address)
    return {"ips": ips, "dns": str(me.get("DNSName") or "").rstrip(".")} if ips else None


def https_url(info, port=PORT, run=subprocess.run, exe=tailscale_exe):
    """`https://<name>.<tailnet>.ts.net` when `tailscale serve` already proxies this port (read only: `tailscale serve status --json`), otherwise ''."""
    path = exe()
    if not path or not info or not info.get("dns"):
        return ""
    try:
        done = run([path, "serve", "status", "--json"], capture_output=True, timeout=8, creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
        text = done.stdout.decode("utf-8", "replace")
        data = json.loads(text) if text.strip() else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    for site in (data.get("Web") or {}).values():
        for handler in (site.get("Handlers") or {}).values():
            if str(handler.get("Proxy") or "").rstrip("/").endswith(f":{port}"):
                return f"https://{info['dns']}"
    return ""


def enable_https(port=PORT, run=subprocess.run, exe=tailscale_exe):
    """(ok, message): `tailscale serve --bg PORT` (HTTPS for this computer's name inside your tailnet; it needs HTTPS certificates enabled in the Tailscale admin page once). Never a funnel: nothing becomes public."""
    path = exe()
    if not path:
        return False, "Tailscale غير مثبت على هذا الكمبيوتر."
    try:
        done = run([path, "serve", "--bg", str(port)], capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError):
        return False, "تعذر تشغيل أمر Tailscale."
    output = (done.stdout or b"").decode("utf-8", "replace") + (done.stderr or b"").decode("utf-8", "replace")
    if done.returncode == 0:
        return True, "تم تفعيل HTTPS."
    return False, "لم يتفعل HTTPS: " + (" ".join(output.split())[:240] or "فعّل شهادات HTTPS من صفحة Tailscale ثم أعد المحاولة.")


class EmbeddedReports(QObject):
    changed = Signal()

    def __init__(self, session=None, parent=None, *, api_factory=WebReports, serve_factory=serve, info=tailscale_info, port=PORT, config=None):
        super().__init__(parent)
        self._session, self._api_factory, self._serve, self._info, self._port = session, api_factory, serve_factory, info, port
        self._config = config
        self._enabled = read_enabled(config)
        self._lock = threading.RLock()
        self._servers, self._threads = [], []
        self._status, self._addresses, self._starting = "متوقف", [], False
        self._https_message, self._https_run = "", {"enable": enable_https, "url": https_url}

    # ---- state for the Settings card
    @Property(bool, notify=changed)
    def running(self):
        return bool(self._servers)

    @Property(bool, notify=changed)
    def enabled(self):
        return self._enabled

    @Property(str, notify=changed)
    def status(self):
        return self._status

    @Property("QStringList", notify=changed)
    def addresses(self):
        return list(self._addresses)

    @Property(str, notify=changed)
    def primary(self):
        return self._addresses[0] if self._addresses else ""

    @Property(bool, notify=changed)
    def canManage(self):
        actor = self._session.current_actor() if self._session is not None else None
        return bool(actor and "SETTINGS_MANAGE" in actor.permissions)

    # ---- actions
    @Slot()
    def start(self):
        """Begin serving in the background (a no-op when already running, starting, or switched off)."""
        with self._lock:
            if self._servers or self._starting or not self._enabled:
                return
            self._starting = True
            self._status = "جارٍ التشغيل…"
        self.changed.emit()
        threading.Thread(target=self._start, name="web-reports-start", daemon=True).start()

    def _start(self):
        servers, addresses, notes = [], [], []
        try:
            api = self._api_factory()
            hosts = ["127.0.0.1"]
            info = self._info()
            if info:
                hosts += info["ips"]
            else:
                notes.append("Tailscale غير شغال أو غير مثبت على هذا الكمبيوتر: العنوان يعمل من هذا الجهاز فقط.")
            busy = False
            for host in hosts:
                try:
                    servers.append(self._serve(host, self._port, api))
                except OSError:
                    busy = True
                    break
            if busy:
                for server in servers:
                    server.server_close()
                servers = []
                notes = ["المنفذ %d مشغول (ربما البرنامج مفتوح مرتين). أغلق النسخة الأخرى ثم أعد التشغيل من هنا." % self._port]
            elif info:
                secure = self._https_run["url"](info, self._port)
                addresses = ([secure] if secure else []) + ([f"http://{info['dns']}:{self._port}"] if info["dns"] else []) + [f"http://{ip}:{self._port}" for ip in info["ips"]]
            else:
                addresses = [f"http://127.0.0.1:{self._port}"]
        except Exception:
            logger.exception("reports server failed to start")
            for server in servers:
                server.server_close()
            servers, addresses, notes = [], [], ["تعذر تشغيل خادم تقارير الأيباد (راجع السجل)."]
        threads = []
        for server in servers:
            thread = threading.Thread(target=server.serve_forever, name="web-reports", daemon=True)
            thread.start()
            threads.append(thread)
        with self._lock:
            self._starting = False
            if not self._enabled:                                         # switched off while starting
                self._servers, self._threads = servers, threads
                self._stop_locked()
                return
            self._servers, self._threads, self._addresses = servers, threads, addresses
            self._status = ("شغال" if servers else "متوقف") + ((" — " + " ".join(notes)) if notes else "")
        self.changed.emit()

    @Slot()
    def stop(self):
        with self._lock:
            self._stop_locked()
        self.changed.emit()

    def _stop_locked(self):
        for server in self._servers:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                logger.exception("reports server stop failed")
        self._servers, self._threads, self._addresses = [], [], []
        self._status = "متوقف"

    @Slot(bool)
    def setEnabled(self, value):
        if not self.canManage:
            return
        value = bool(value)
        write_enabled(value, self._config)
        self._enabled = value
        if value:
            self.changed.emit()
            self.start()
        else:
            self.stop()

    @Property(bool, notify=changed)
    def httpsActive(self):
        return bool(self._addresses) and self._addresses[0].startswith("https://")

    @Property(str, notify=changed)
    def httpsMessage(self):
        return self._https_message

    @Slot()
    def enableHttps(self):
        """Opt-in (a button in Settings): HTTPS for the iPad address through `tailscale serve`; the result is shown next to the button."""
        if not self.canManage or not self._servers:
            return
        self._https_message = "جارٍ التفعيل…"
        self.changed.emit()
        threading.Thread(target=self._enable_https, name="web-reports-https", daemon=True).start()

    def _enable_https(self):
        ok, message = self._https_run["enable"](self._port)
        with self._lock:
            self._https_message = message
            if ok:
                info = self._info()
                secure = self._https_run["url"](info, self._port) if info else ""
                if secure and secure not in self._addresses:
                    self._addresses = [secure] + self._addresses
        self.changed.emit()

    @Slot(str)
    def copy(self, text):
        from PySide6.QtGui import QGuiApplication
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(str(text))


def create_embedded_reports(session, parent=None):
    return EmbeddedReports(session, parent)
