"""The HTTP server of the iPad reports app (standard library only: no new dependency).

READ ONLY (except the Dead Cash write-off of a customer balance, owner request 2026-11-03) and private by design:
* binds to 127.0.0.1 unless --host is given; reach it from the iPad through Tailscale (`tailscale serve --bg 8765` adds HTTPS inside your tailnet). Never expose it to the public internet.
* routes: static files from a fixed whitelist, POST /api/login, POST /api/logout, GET /api/overview, GET /api/report, GET /api/chart, GET /api/invoice, GET /api/customer_search, GET /api/customer_invoices, GET /api/deadcash/context, POST /api/deadcash/writeoff, POST /api/transfer and POST /api/legacy_balance (each needs the
  user's current password and its own right). Nothing else exists: no export, no other write, no other service.
* the session cookie is HttpOnly + SameSite=Strict (+ Secure behind HTTPS); POSTs need the custom header and a same-origin Origin; request bodies are capped; answers are never cached."""
import argparse
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from webapp.errors import ApiError
from webapp.api import WebReports

STATIC = Path(__file__).resolve().parent / "static"
FILES = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8"),
         "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"), "/icon.svg": ("icon.svg", "image/svg+xml")}
COOKIE = "mgsid"
# read-only extras: path -> method of `webapp.extras.Extras` (reviews and transfers_overview take no parameters)
NO_PARAMS = frozenset({"today", "reviews", "transfers_overview", "attention", "tiles", "bankflow", "cashflow", "currency"})
ROUTES = {"/api/today": "today", "/api/statement": "statement", "/api/piece": "piece", "/api/reviews": "reviews", "/api/transfers": "transfers_overview", "/api/attention": "attention", "/api/tiles": "tiles", "/api/bankflow": "bankflow", "/api/cashflow": "cashflow", "/api/currency": "currency", "/api/activity": "activity"}
MAX_BODY = 4096
CSP = ("default-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; "
       "connect-src 'self'; frame-src https://s.tradingview.com https://www.tradingview.com; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
logger = logging.getLogger("webapp")


def make_handler(api):
    class Handler(BaseHTTPRequestHandler):
        server_version = "masGoldReports"
        sys_version = ""

        def log_message(self, fmt, *args):                                   # no query strings, bodies or cookies in the log
            logger.info("%s %s", self.command, urlparse(self.path).path)

        # ---- helpers
        def _send(self, status, body, content_type, extra=()):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
            for key, value in extra:
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def _json(self, status, payload, extra=()):
            self._send(status, json.dumps(payload, ensure_ascii=False, default=str), "application/json; charset=utf-8", extra)

        def _token(self):
            jar = cookies.SimpleCookie(self.headers.get("Cookie") or "")
            return jar[COOKIE].value if COOKIE in jar else ""

        def _cookie(self, token, clear=False):
            secure = "; Secure" if (self.headers.get("X-Forwarded-Proto") or "").lower() == "https" else ""
            age = "Max-Age=0" if clear else "Max-Age=43200"
            return ("Set-Cookie", f"{COOKIE}={'' if clear else token}; HttpOnly; SameSite=Strict; Path=/; {age}{secure}")

        def _same_origin_post(self):
            if self.headers.get("X-Requested-With") != "masgold":
                return False
            origin = self.headers.get("Origin")
            return origin is None or urlparse(origin).netloc == (self.headers.get("Host") or "")

        def _fail(self, exc):
            self._json(exc.status, {"error": exc.message})

        # ---- routes
        def do_GET(self):
            url = urlparse(self.path)
            if url.path in FILES:
                name, kind = FILES[url.path]
                try:
                    self._send(200, (STATIC / name).read_bytes(), kind)
                except OSError:
                    self._json(404, {"error": "غير موجود."})
                return
            try:
                if url.path == "/api/overview":
                    self._json(200, api.overview(self._token()))
                elif url.path == "/api/deadcash/context":
                    params = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
                    self._json(200, api.deadcash_context(self._token(), params))
                elif url.path == "/api/invoice":
                    params = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
                    self._json(200, api.invoice(self._token(), params))
                elif url.path in ROUTES:
                    params = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
                    method = getattr(api.extras, ROUTES[url.path])
                    self._json(200, method(self._token()) if ROUTES[url.path] in NO_PARAMS else method(self._token(), params))
                elif url.path == "/api/export":
                    params = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
                    name, data = api.extras.export(self._token(), params)
                    self._send(200, data, "text/csv; charset=utf-8", [("Content-Disposition", f'attachment; filename="{name}"')])
                elif url.path == "/api/customer_search":
                    params = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
                    self._json(200, api.customer_search(self._token(), params))
                elif url.path == "/api/customer_invoices":
                    params = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
                    self._json(200, api.customer_invoices(self._token(), params))
                elif url.path == "/api/chart":
                    params = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
                    self._json(200, api.chart(self._token(), params))
                elif url.path == "/api/report":
                    params = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
                    self._json(200, api.report(self._token(), params.pop("key", ""), params))
                else:
                    self._json(404, {"error": "غير موجود."})
            except ApiError as exc:
                self._fail(exc)

        def do_POST(self):
            url = urlparse(self.path)
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            raw = self.rfile.read(length) if 0 < length <= MAX_BODY else b""          # the body is read BEFORE any refusal, so the client never sees a reset connection
            if url.path not in ("/api/login", "/api/logout", "/api/deadcash/writeoff", "/api/transfer", "/api/legacy_balance", "/api/review_done", "/api/settle") or not self._same_origin_post():
                self._json(403, {"error": "مرفوض."})
                return
            try:
                if length > MAX_BODY:
                    raise ApiError(413, "الطلب كبير.")
                body = json.loads(raw or b"{}") if length else {}
                if not isinstance(body, dict):
                    raise ApiError(400, "طلب غير صحيح.")
                if url.path == "/api/login":
                    token = api.login(body.get("username"), body.get("password"))
                    self._json(200, {"ok": True}, [self._cookie(token)])
                elif url.path == "/api/deadcash/writeoff":
                    self._json(200, api.write_off(self._token(), body))
                elif url.path == "/api/legacy_balance":
                    self._json(200, api.extras.legacy_balance(self._token(), body))
                elif url.path == "/api/settle":
                    self._json(200, api.extras.settle(self._token(), body))
                elif url.path == "/api/review_done":
                    self._json(200, api.extras.review_done(self._token(), body))
                elif url.path == "/api/transfer":
                    self._json(200, api.extras.transfer(self._token(), body))
                else:
                    api.logout(self._token())
                    self._json(200, {"ok": True}, [self._cookie("", clear=True)])
            except ApiError as exc:
                self._fail(exc)
            except ValueError:
                self._json(400, {"error": "طلب غير صحيح."})

        def do_PUT(self):
            self._json(405, {"error": "غير مسموح."})

        do_DELETE = do_PATCH = do_PUT

    return Handler


def serve(host, port, api=None):
    server = ThreadingHTTPServer((host, port), make_handler(api or WebReports()))
    server.daemon_threads = True
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description="masGold reports for the iPad (read only)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    from config.database_config import DatabaseConfig
    server = serve(args.host, args.port)
    print(f"masGold reports: http://{args.host}:{args.port}  (database: {DatabaseConfig.from_env().database}) - Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
