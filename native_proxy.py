"""Native QtWebEngine PAC startup hook; sourced by qutebrowser config.py.

qutebrowser 3.7 installs a custom QNetworkProxyFactory at startup. QtWebEngine
then ignores Chromium's command-line proxy preferences. Replace that one
initialization step with Qt's system-configuration mode, which lets Chromium's
PAC preference take precedence. This is a private qutebrowser hook, not a
supported extension API; verify it after qutebrowser/Qt upgrades.
"""

import importlib.util
import json
import os
from pathlib import Path
import sys
from qutebrowser.browser.network import proxy as _proxy
from qutebrowser.qt.network import QNetworkProxyFactory
from qutebrowser.utils import objreg

_native_dir = Path(__file__).resolve().parent
_settings_path = Path(os.environ.get(
    "QUTE_OMEGA_SETTINGS", str(Path.home() / ".config/qute-switchy/native.json")))
_settings = json.loads(_settings_path.read_text(encoding="utf-8"))
if "overrides" in _settings:
    raise RuntimeError(
        "Migrate website rules first: node "
        + str(_native_dir / "omega_native.cjs")
        + " --migrate-settings " + str(_settings_path))

# qutebrowser removes newly imported modules from sys.modules after reading
# config.py. Retain the UI in its registry so re-sourcing cannot register commands
# twice. Widgets are created only by commands, after QApplication exists.
_ui = objreg.get("quteswitchy-ui", default=None)
if _ui is None:
    _module_name = "_quteswitchy_ui"
    _spec = importlib.util.spec_from_file_location(
        _module_name, _native_dir / "switchy_ui.py")
    _ui = importlib.util.module_from_spec(_spec)
    sys.modules[_module_name] = _ui
    try:
        _spec.loader.exec_module(_ui)
    except Exception:
        sys.modules.pop(_module_name, None)
        raise
    objreg.register("quteswitchy-ui", _ui)
_controller = _ui.install(_native_dir, _settings)
_pac = _controller.pac

# Do not replace an existing user binding.
if ",s" not in c.bindings.commands.get("normal", {}):
    config.bind(",s", "switchy-menu")


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
