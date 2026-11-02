"""The iPad reports server inside the program: start-up, the Tailscale addresses, the on / off choice and failure handling. Fakes only: no socket, no Tailscale, no database."""
import json
from pathlib import Path
import tempfile
import threading
import unittest

from tests.market_trip_fakes import session_for
from webapp import embedded


class FakeServer:
    def __init__(self, host, port):
        self.host, self.port = host, port
        self.running, self.closed = threading.Event(), False
        self.stopped = threading.Event()

    def serve_forever(self):
        self.running.set()
        self.stopped.wait(5)

    def shutdown(self):
        self.stopped.set()

    def server_close(self):
        self.closed = True


class Harness:
    def __init__(self, info, permissions=frozenset({"APP_LOGIN", "SETTINGS_MANAGE"}), busy=False, enabled=True):
        self.directory = tempfile.TemporaryDirectory()
        self.config = Path(self.directory.name) / "web_reports.json"
        if not enabled:
            embedded.write_enabled(False, self.config)
        self.created, self.busy = [], busy

        def serve(host, port, api):
            if self.busy and host != "127.0.0.1":
                raise OSError("address in use")
            server = FakeServer(host, port)
            self.created.append(server)
            return server
        self.vm = embedded.EmbeddedReports(session_for(permissions), api_factory=lambda: object(), serve_factory=serve, info=lambda: info, port=8765, config=self.config)

    def wait_started(self):
        for _ in range(200):
            if not self.vm._starting:
                return
            threading.Event().wait(0.01)
        raise AssertionError("did not finish starting")

    def close(self):
        self.vm.stop()
        self.directory.cleanup()


INFO = {"ips": ["100.91.52.23"], "dns": "desktop-s6g2sm6.tailbb84e0.ts.net"}


class EmbeddedReportsTests(unittest.TestCase):
    def test_it_serves_on_localhost_and_the_tailscale_ip_and_shows_the_stable_name_first(self):
        h = Harness(INFO)
        try:
            h.vm.start()
            h.wait_started()
            self.assertEqual([(s.host, s.port) for s in h.created], [("127.0.0.1", 8765), ("100.91.52.23", 8765)])
            self.assertTrue(h.vm.running)
            self.assertEqual(h.vm.addresses, ["http://desktop-s6g2sm6.tailbb84e0.ts.net:8765", "http://100.91.52.23:8765"])
            self.assertEqual(h.vm.primary, "http://desktop-s6g2sm6.tailbb84e0.ts.net:8765")
            self.assertTrue(h.vm.status.startswith("شغال"))
            h.vm.start()                                                           # starting twice never binds twice
            self.assertEqual(len(h.created), 2)
        finally:
            h.close()

    def test_without_tailscale_it_serves_this_computer_only_and_says_so(self):
        h = Harness(None)
        try:
            h.vm.start()
            h.wait_started()
            self.assertEqual([s.host for s in h.created], ["127.0.0.1"])
            self.assertEqual(h.vm.addresses, ["http://127.0.0.1:8765"])
            self.assertIn("Tailscale", h.vm.status)
        finally:
            h.close()

    def test_a_busy_port_is_reported_and_nothing_stays_bound(self):
        h = Harness(INFO, busy=True)
        try:
            h.vm.start()
            h.wait_started()
            self.assertFalse(h.vm.running)
            self.assertEqual(h.vm.addresses, [])
            self.assertIn("مشغول", h.vm.status)
            self.assertTrue(all(s.closed for s in h.created))
        finally:
            h.close()

    def test_a_failed_start_never_raises_into_the_program(self):
        h = Harness(INFO)
        h.vm._api_factory = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            h.vm.start()
            h.wait_started()
            self.assertFalse(h.vm.running)
            self.assertIn("تعذر", h.vm.status)
        finally:
            h.close()

    def test_a_switched_off_computer_does_not_start_and_only_a_settings_manager_can_switch(self):
        h = Harness(INFO, permissions=frozenset({"APP_LOGIN"}), enabled=False)
        try:
            h.vm.start()
            self.assertEqual((h.created, h.vm.running, h.vm.enabled), ([], False, False))
            h.vm.setEnabled(True)
            self.assertEqual(h.created, [])
            self.assertFalse(embedded.read_enabled(h.config))
        finally:
            h.close()
        manager = Harness(INFO, enabled=False)
        try:
            manager.vm.setEnabled(True)
            manager.wait_started()
            self.assertTrue(embedded.read_enabled(manager.config))
            self.assertTrue(manager.vm.running)
            manager.vm.setEnabled(False)
            self.assertFalse(manager.vm.running)
            self.assertFalse(embedded.read_enabled(manager.config))
            self.assertTrue(all(s.stopped.is_set() for s in manager.created))
        finally:
            manager.close()

    def test_the_choice_file_is_small_json_and_a_missing_or_broken_file_means_on(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "nested" / "web_reports.json"
            self.assertTrue(embedded.read_enabled(path))
            embedded.write_enabled(False, path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"enabled": False})
            path.write_text("not json", encoding="utf-8")
            self.assertTrue(embedded.read_enabled(path))

    def test_tailscale_info_reads_only_a_running_backend_and_ipv4(self):
        class Done:
            def __init__(self, payload):
                self.stdout = json.dumps(payload).encode()
        running = {"BackendState": "Running", "Self": {"DNSName": "pc.tail1234.ts.net.", "TailscaleIPs": ["100.1.2.3", "fd7a::1"]}}
        run = lambda command, **kw: Done(running)
        self.assertEqual(embedded.tailscale_info(run=run, exe=lambda: "tailscale.exe"), {"ips": ["100.1.2.3"], "dns": "pc.tail1234.ts.net"})
        stopped = dict(running, BackendState="Stopped")
        self.assertIsNone(embedded.tailscale_info(run=lambda command, **kw: Done(stopped), exe=lambda: "tailscale.exe"))
        self.assertIsNone(embedded.tailscale_info(run=run, exe=lambda: None))
        self.assertIsNone(embedded.tailscale_info(run=lambda command, **kw: (_ for _ in ()).throw(OSError()), exe=lambda: "tailscale.exe"))


class HttpsTests(unittest.TestCase):
    class Done:
        def __init__(self, stdout=b"", stderr=b"", code=0):
            self.stdout, self.stderr, self.returncode = stdout, stderr, code

    def test_the_https_address_is_known_only_when_tailscale_serve_proxies_this_port(self):
        serving = json.dumps({"Web": {"pc.tail1234.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}}}}).encode()
        info = {"ips": ["100.1.2.3"], "dns": "pc.tail1234.ts.net"}
        run = lambda command, **kw: self.Done(serving)
        self.assertEqual(embedded.https_url(info, 8765, run=run, exe=lambda: "t.exe"), "https://pc.tail1234.ts.net")
        self.assertEqual(embedded.https_url(info, 9999, run=run, exe=lambda: "t.exe"), "")
        self.assertEqual(embedded.https_url(info, 8765, run=lambda command, **kw: self.Done(b""), exe=lambda: "t.exe"), "")
        self.assertEqual(embedded.https_url(None, 8765, run=run, exe=lambda: "t.exe"), "")

    def test_enabling_https_runs_serve_in_the_background_and_reports_a_refusal_in_plain_words(self):
        seen = []
        ok, message = embedded.enable_https(8765, run=lambda command, **kw: seen.append(command) or self.Done(), exe=lambda: "t.exe")
        self.assertEqual((ok, seen), (True, [["t.exe", "serve", "--bg", "8765"]]))             # never `funnel`: nothing becomes public
        refused = embedded.enable_https(8765, run=lambda command, **kw: self.Done(b"", b"HTTPS not enabled for your tailnet", 1), exe=lambda: "t.exe")
        self.assertFalse(refused[0])
        self.assertIn("HTTPS not enabled", refused[1])
        self.assertFalse(embedded.enable_https(8765, run=None, exe=lambda: None)[0])

    def test_the_https_address_comes_first_once_it_is_active(self):
        h = Harness(INFO)
        h.vm._https_run = {"url": lambda info, port: "https://desktop-s6g2sm6.tailbb84e0.ts.net", "enable": lambda port: (True, "ok")}
        try:
            h.vm.start()
            h.wait_started()
            self.assertEqual(h.vm.addresses[0], "https://desktop-s6g2sm6.tailbb84e0.ts.net")
            self.assertTrue(h.vm.httpsActive)
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main()
