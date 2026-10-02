"""Native QtWebEngine PAC startup hook; sourced by qutebrowser config.py.

qutebrowser 3.7 installs a custom QNetworkProxyFactory at startup. QtWebEngine
then ignores Chromium's command-line proxy preferences. Replace that one
initialization step with Qt's system-configuration mode, which lets Chromium's
PAC preference take precedence. This is a private qutebrowser hook, not a
supported extension API; verify it after qutebrowser/Qt upgrades.
"""

import json
import os
from pathlib import Path
import subprocess

from qutebrowser.browser.network import proxy as _proxy
from qutebrowser.qt.network import QNetworkProxyFactory

_native_dir = Path(__file__).resolve().parent
_settings_path = Path(os.environ.get(
    "QUTE_OMEGA_SETTINGS", str(Path.home() / ".config/qute-switchy/native.json")))
_settings = json.loads(_settings_path.read_text(encoding="utf-8"))
_pac = Path(_settings["pac"]).expanduser().resolve()

# Compile on startup so edits to the backup or overrides take effect on restart.
# A compiler failure stops this config section instead of using a stale PAC.
subprocess.run(
    ["node", str(_native_dir / "omega_native.cjs"),
     "--source", str(Path(_settings["source"]).expanduser()),
     "--profile", _settings["profile"],
     "--proxy", _settings["proxy"],
     "--overrides", str(Path(_settings["overrides"]).expanduser()),
     "--output", str(_pac)],
    check=True,
)


def _native_proxy_init():
    QNetworkProxyFactory.setUseSystemConfiguration(True)


# Runs before WebEngine profiles are created; do not switch proxies on navigation.
_proxy.init = _native_proxy_init
config.set("content.proxy", "system")
# Qt discards qt.args if this environment variable is set. Preserve unrelated
# flags, but let qutebrowser merge its own WebEngine arguments as usual.
import shlex
_extra_args = shlex.split(os.environ.pop("QTWEBENGINE_CHROMIUM_FLAGS", ""))
_existing_args = list(c.qt.args or [])
_proxy_switches = ("proxy-pac-url", "proxy-server", "no-proxy-server", "proxy-auto-detect")
_arguments = [arg for arg in _existing_args + [a.lstrip("-") for a in _extra_args]
              if arg.split("=", 1)[0] not in _proxy_switches]
_arguments.append("proxy-pac-url=" + _pac.as_uri())
config.set("qt.args", _arguments)
