#!/usr/bin/env python3
"""Native PAC smoke: run real qutebrowser; no routing daemon participates.

Two distinct upstream logging proxies and a direct origin provide observable
routing proof. Requests to different paths on the same HTTP host must split
between proxy and direct. An HTTPS CONNECT must use the selected upstream.
Restart after editing local overrides must change the routing decision.
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent.parent
EVENTS = []


class Surface(BaseHTTPRequestHandler):
    def do_GET(self):
        EVENTS.append((self.server.label, self.path))
        body = (b"<html><head><title>NATIVE PAC</title></head><body>"
                b"NATIVE PAC<img src='/render-proof'></body></html>")
        if self.path.endswith("render-proof"):
            body = b"proof"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_CONNECT(self):
        EVENTS.append((self.server.label, "CONNECT " + self.path))
        self.send_response(502)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args):
        pass


def start_server(label):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Surface)
    server.label = label
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def fixed(name, port):
    return {"name": name, "profileType": "FixedProfile", "bypassList": [],
            "fallbackProxy": {"scheme": "http", "host": "127.0.0.1", "port": port}}


def match(kind, pattern, profile):
    return {"condition": {"conditionType": kind, "pattern": pattern},
            "profileName": profile}


def run_browser(config, settings, urls, expected):
    env = dict(os.environ)
    env["QUTE_OMEGA_SETTINGS"] = str(settings)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.pop("QTWEBENGINE_CHROMIUM_FLAGS", None)
    with tempfile.TemporaryFile(mode="w+") as log:
        proc = subprocess.Popen(["qutebrowser", "-T", "-C", str(config),
                                 "--no-err-windows", *urls], env=env,
                                stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline and not expected():
                if proc.poll() is not None:
                    break
                time.sleep(.1)
            assert expected(), f"Missing native requests: {EVENTS!r}"
        finally:
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=10)
            log.seek(0)
            output = log.read()
            assert "Error while reading config" not in output, output


def main():
    a, b, origin = [start_server(x) for x in ("A", "B", "DIRECT")]
    try:
        with tempfile.TemporaryDirectory(prefix="omega-native-test-") as tmp:
            base = Path(tmp)
            source, overrides = base / "OmegaOptions.bak", base / "overrides.json"
            settings, config = base / "native.json", base / "config.py"
            options = {"schemaVersion": 2,
                       "+proxy": fixed("proxy", a.server_port),
                       "+other": fixed("other", b.server_port),
                       "+auto switch": {"name": "auto switch", "profileType": "SwitchProfile",
                                        "defaultProfileName": "direct", "rules": [
                                            match("HostWildcardCondition", "native-a.invalid", "proxy"),
                                            match("HostWildcardCondition", "native-b.invalid", "other"),
                                            match("UrlWildcardCondition", "http://native-path.invalid:*/via-proxy*", "proxy"),
                                        ]}}
            source.write_text(json.dumps(options))
            overrides.write_text("[]")
            settings.write_text(json.dumps({
                "source": str(source), "profile": "auto switch",
                "proxy": f"http://127.0.0.1:{a.server_port}",
                "overrides": str(overrides), "pac": str(base / "proxy.pac"),
            }))
            config.write_text(
                "config.load_autoconfig(False)\n"
                "c.qt.args = ['disable-gpu', 'host-resolver-rules=MAP *.invalid 127.0.0.1']\n"
                f"config.source({str(ROOT / 'native_proxy.py')!r})\n")
            urls = ["http://native-a.invalid/", "https://native-b.invalid/",
                    f"http://native-path.invalid:{origin.server_port}/via-proxy",
                    f"http://native-path.invalid:{origin.server_port}/via-direct"]

            def first_run_complete():
                return (("A", "http://native-a.invalid/render-proof") in EVENTS
                        and ("B", "CONNECT native-b.invalid:443") in EVENTS
                        and ("A", f"http://native-path.invalid:{origin.server_port}/via-proxy") in EVENTS
                        and ("DIRECT", "/via-direct") in EVENTS)

            run_browser(config, settings, urls, first_run_complete)
            print("PASS native proxy selection, HTTPS CONNECT, rendered subresource")
            print("PASS same-host HTTP paths select proxy versus DIRECT")
            print("EVENTS", EVENTS)
            assert not any(label == "A" and path.endswith("/via-direct") for label, path in EVENTS)

            EVENTS.clear()
            overrides.write_text(json.dumps([{"domain": "native-a.invalid", "profile": "direct"}]))
            run_browser(config, settings,
                        [f"http://native-a.invalid:{origin.server_port}/after-edit"],
                        lambda: ("DIRECT", "/after-edit") in EVENTS)
            assert not any(label == "A" for label, _ in EVENTS), EVENTS
            print("PASS local override takes effect after browser restart")
    finally:
        for server in (a, b, origin):
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
