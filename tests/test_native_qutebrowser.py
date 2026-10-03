#!/usr/bin/env python3
"""Exercise native PAC routing and real restart-based quteswitchy controls.

Uses isolated qutebrowser instances, logging upstreams, a direct origin, and
actual Qt dialog buttons. No personal configuration or routing helper is used.
"""

import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import ExitStack

ROOT = Path(__file__).resolve().parent.parent
EVENTS = []


class Surface(BaseHTTPRequestHandler):
    def do_GET(self):
        EVENTS.append((self.server.label, self.path))
        body = (b"<html><head><title>QUTESWITCHY</title></head><body>"
                b"QUTESWITCHY<img src='/render-proof'></body></html>")
        if self.path.endswith("render-proof"):
            body = b"proof"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Cache-Control", "no-store")
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


class Browser:
    """Drive an isolated instance through IPC, including its restarted process."""

    def __init__(self, base, config, settings, urls, inherited_session=None):
        self.base, self.config = base, config
        self.env = dict(os.environ)
        self.env.update(QUTE_OMEGA_SETTINGS=str(settings), QT_QPA_PLATFORM="offscreen")
        self.env.pop("QTWEBENGINE_CHROMIUM_FLAGS", None)
        self.env.pop("QUTE_SWITCHY_SESSION", None)
        if inherited_session is not None:
            self.env["QUTE_SWITCHY_SESSION"] = json.dumps(inherited_session)
        self.log_path = base.parent / (base.name + ".log")
        self.log = self.log_path.open("w")
        self.args = ["qutebrowser", "-B", str(base), "-C", str(config), "--no-err-windows"]
        self.proc = subprocess.Popen(self.args + urls, env=self.env,
                                     stdout=self.log, stderr=subprocess.STDOUT,
                                     start_new_session=True)

    def command(self, *commands):
        result = subprocess.run(self.args + list(commands), env=self.env,
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr

    def wait(self, expected):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if expected():
                return
            time.sleep(.1)
        raise AssertionError(f"Missing browser behavior: {EVENTS!r}\n{self.log_path.read_text()}")

    def state(self, path):
        path.unlink(missing_ok=True)
        self.command(":switchy-smoke-state")
        self.wait(path.is_file)
        return json.loads(path.read_text())

    def click_menu(self, lifetime):
        self.command(f":cmd-later 600 switchy-smoke-click {lifetime}", ":switchy-menu")

    def close(self):
        try:
            self.command(":quit")
            time.sleep(.5)
        finally:
            if hasattr(os, "killpg"):
                try:
                    os.killpg(self.proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            self.proc.wait(timeout=10)
            self.log.close()
        output = self.log_path.read_text()
        for error in ("Error while reading config", "Errors occurred while reading",
                      "Unhandled exception", "CalledProcessError"):
            assert error not in output, output


def smoke_commands(state, screenshot):
    return f'''
import json, os
from pathlib import Path
from qutebrowser.api import cmdutils
from qutebrowser.qt.widgets import QApplication, QComboBox, QCheckBox, QPushButton
from qutebrowser.utils import objreg
@cmdutils.register(name="switchy-smoke-state")
def smoke_state():
    """Record actual instance and window state for routing smoke."""
    Path({str(state)!r}).write_text(json.dumps({{
        "pid": os.getpid(),
        "private": [bool(win.is_private) for win in objreg.window_registry.values()],
        "session": json.loads(os.environ.get("QUTE_SWITCHY_SESSION", "null")),
        "pac": str(objreg.get("quteswitchy-ui")._controller.pac),
    }}))
@cmdutils.register(name="switchy-smoke-click")
def smoke_click(lifetime):
    """Click a real editor button, not a mocked command callback."""
    dialogs = [w for w in QApplication.topLevelWidgets()
               if w.objectName() == "quteswitchy-menu" and w.isVisible()]
    assert len(dialogs) == 1, "Editor dialog did not open"
    dialog = dialogs[0]
    combo = dialog.findChild(QComboBox, "switchy-profile")
    index = combo.findText("direct")
    assert index >= 0
    combo.setCurrentIndex(index)
    dialog.findChild(QCheckBox, "switchy-subdomains").setChecked(False)
    assert dialog.grab().save({str(screenshot)!r}), "Could not capture editor"
    button = dialog.findChild(QPushButton, "switchy-" + lifetime)
    assert button is not None
    button.click()
'''


def main():
    a, b, origin = [start_server(x) for x in ("A", "B", "DIRECT")]
    try:
        with ExitStack() as stack:
            tmp = stack.enter_context(tempfile.TemporaryDirectory(prefix="omega-native-test-"))
            base = Path(tmp)
            source = base / "OmegaOptions.bak"
            settings, config = base / "native.json", base / "config.py"
            state = base / "state.json"
            screenshot = Path(os.environ.get("QUTE_SWITCHY_SMOKE_SCREENSHOT", str(base / "menu.png")))
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
            original = source.read_bytes()
            settings.write_text(json.dumps({
                "source": str(source), "profile": "auto switch",
                "proxy": f"http://127.0.0.1:{a.server_port}", "pac": str(base / "proxy.pac"),
            }))
            config.write_text(
                "config.load_autoconfig(False)\n"
                "c.qt.args = ['disable-gpu', 'host-resolver-rules=MAP *.invalid 127.0.0.1']\n"
                f"config.source({str(ROOT / 'native_proxy.py')!r})\n"
                + smoke_commands(state, screenshot))
            urls = ["http://native-a.invalid/", "https://native-b.invalid/",
                    f"http://native-path.invalid:{origin.server_port}/via-proxy",
                    f"http://native-path.invalid:{origin.server_port}/via-direct"]
            browser = Browser(base / "browser", config, settings, urls)
            stack.callback(browser.close)
            browser.wait(lambda: ("A", "http://native-a.invalid/render-proof") in EVENTS
                         and ("B", "CONNECT native-b.invalid:443") in EVENTS
                         and ("A", f"http://native-path.invalid:{origin.server_port}/via-proxy") in EVENTS
                         and ("DIRECT", "/via-direct") in EVENTS)
            assert not any(label == "A" and path.endswith("/via-direct") for label, path in EVENTS)
            print("PASS native selection, HTTPS CONNECT, rendered subresource and HTTP path split")
            browser.command(":config-source " + str(ROOT / "native_proxy.py"))

            browser.command(f":open -w http://native-window.invalid:{origin.server_port}/normal-window")
            browser.wait(lambda: ("DIRECT", "/normal-window") in EVENTS)
            browser.command(f":open -p http://native-private.invalid:{origin.server_port}/private-window")
            browser.wait(lambda: ("DIRECT", "/private-window") in EVENTS)
            url = f"http://native-a.invalid:{origin.server_port}/choice-proof"
            browser.command(":open -t " + url)
            browser.wait(lambda: ("A", url) in EVENTS)
            before = browser.state(state)
            assert sorted(before["private"]) == [False, False, True], before

            EVENTS.clear()
            browser.click_menu("temporary")
            browser.wait(lambda: ("DIRECT", "/choice-proof") in EVENTS)
            after = browser.state(state)
            assert after["pid"] != before["pid"], (before, after)
            assert sorted(after["private"]) == [False, False, True], after
            assert source.read_bytes() == original, "Temporary choice modified export"
            assert after["session"]["rules"] == [
                {"domain": "native-a.invalid", "profile": "direct", "subdomains": False}], after
            print("PASS real menu temporary action restarts and restores normal/private windows without editing export")

            EVENTS.clear()
            browser.command(":restart")
            browser.wait(lambda: ("DIRECT", "/choice-proof") in EVENTS)
            assert browser.state(state)["pid"] != after["pid"]
            assert source.read_bytes() == original
            print("PASS temporary choice survives an ordinary restart")

            EVENTS.clear()
            browser.command(":switchy-reset --domain native-a.invalid --exact")
            browser.wait(lambda: ("A", url) in EVENTS)
            assert source.read_bytes() == original
            assert not browser.state(state)["session"]["rules"]
            print("PASS temporary reset returns to export routing")

            EVENTS.clear()
            browser.click_menu("permanent")
            browser.wait(lambda: ("DIRECT", "/choice-proof") in EVENTS)
            permanent = source.read_bytes()
            saved = json.loads(permanent)
            assert saved["schemaVersion"] == 2
            assert saved["+auto switch"]["rules"][0] == match("HostWildcardCondition", "native-a.invalid", "direct")
            assert saved["+other"] == options["+other"]
            print("PASS real menu permanent action saves a standard SwitchyOmega rule")

            EVENTS.clear()
            browser.command(":switchy-set proxy --temporary --domain native-a.invalid --exact")
            browser.wait(lambda: ("A", url) in EVENTS)
            assert source.read_bytes() == permanent
            print("PASS temporary proxy takes precedence without changing permanent direct rule")

            EVENTS.clear()
            fresh = Browser(base / "fresh", config, settings,
                            [f"http://native-a.invalid:{origin.server_port}/fresh-session"],
                            inherited_session=browser.state(state)["session"])
            try:
                fresh.wait(lambda: ("DIRECT", "/fresh-session") in EVENTS)
                assert not any(label == "A" and path.endswith("/fresh-session") for label, path in EVENTS)
                assert fresh.state(state)["pac"] != browser.state(state)["pac"]
            finally:
                fresh.close()
            assert source.read_bytes() == permanent
            print("PASS independent fresh instance uses permanent rules, not another instance's temporary choice")

            EVENTS.clear()
            browser.command(":switchy-clear-temporary")
            browser.wait(lambda: ("DIRECT", "/choice-proof") in EVENTS)
            assert not browser.state(state)["session"]["rules"]
            print("PASS clearing all temporary choices restores permanent routing")
    finally:
        for server in (a, b, origin):
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
