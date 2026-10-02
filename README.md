# quteswitchy

Native SwitchyOmega proxy rules for qutebrowser. Chromium selects DIRECT or an
upstream proxy using a PAC script compiled from your `OmegaOptions.bak`.
No local routing daemon is required.

Configure your upstream proxy, for example **SOCKS5 `127.0.0.1:your-port`**.
Your proxy service must already be running; this project does not provide it.

## Requirements

- qutebrowser with QtWebEngine; tested on qutebrowser **3.7.0 / QtWebEngine 6.11.1**.
- Node.js **18+** on `PATH`; no npm installation or dependencies needed.
- A SwitchyOmega schema-version-2 export containing a FixedProfile named `proxy`.

This uses a private qutebrowser startup hook, not a supported extension API.
Re-run the browser smoke check after qutebrowser or Qt upgrades.

## Install

Run from this repository:

```sh
mkdir -p ~/.local/share/qute-switchy-native \
         ~/.config/qute-switchy ~/.config/qutebrowser
cp -R native_proxy.py omega_native.cjs vendor README.md LICENSE \
      ~/.local/share/qute-switchy-native/
```

Keep your SwitchyOmega export at a path of your choice, represented below as
`/path/to/OmegaOptions.bak`. Personal exports, overrides, and generated PAC files
are not part of this repository.

For a fresh installation, create settings and overrides:

```sh
cp examples/native.json ~/.config/qute-switchy/native.json
cp examples/overrides.json ~/.config/qute-switchy/overrides.json
```

On an existing installation, preserve your settings and overrides instead of
running those two copy commands. The settings template uses placeholders:

```json
{
  "source": "/path/to/OmegaOptions.bak",
  "profile": "auto switch",
  "proxy": "socks5://127.0.0.1:your-port",
  "overrides": "~/.config/qute-switchy/overrides.json",
  "pac": "~/.config/qute-switchy/proxy.pac"
}
```

Replace `/path/to/OmegaOptions.bak` with your export's actual path and `your-port`
with your proxy's numeric listening port before starting qutebrowser. The compiler
requires an explicit source and proxy; neither has a built-in default.

Adjust `profile` if your exported switch profile has a different name.
The configured upstream replaces the imported `proxy` profile's HTTP, HTTPS,
FTP, and fallback proxies. Other profiles retain their imported settings.

If you have **no existing** `~/.config/qutebrowser/config.py`:

```sh
cp examples/config.py ~/.config/qutebrowser/config.py
```

If you already have a config, keep it and add this near the end:

```python
import os

config.source(os.path.expanduser(
    "~/.local/share/qute-switchy-native/native_proxy.py"
))
```

The fresh-config example also calls `config.load_autoconfig()` to preserve
settings saved through qutebrowser's UI. Do not add a fixed `content.proxy`
setting after the native hook.

Restart qutebrowser with `:restart`. The hook compiles a new PAC at every startup.
It can also be sourced directly from a checkout: the compiler and vendored
engine are located relative to `native_proxy.py`.

## Manual website rules

Edit `~/.config/qute-switchy/overrides.json`:

```json
[
  {"domain": "example.com", "profile": "proxy"},
  {"domain": "internal.example", "profile": "direct"}
]
```

Each entry covers the hostname and its subdomains. Entries are checked in listed
order before imported rules. An empty array uses the export unchanged, apart
from the configured upstream. The export itself is never modified.

After changing the export, settings, or overrides, use **`:restart`**, not merely
`:config-source`: Qt snapshots a file PAC into its startup proxy preference.

To compile manually:

```sh
node omega_native.cjs \
  --source /path/to/OmegaOptions.bak --profile 'auto switch' \
  --proxy socks5://127.0.0.1:your-port \
  --overrides ~/.config/qute-switchy/overrides.json \
  --output ~/.config/qute-switchy/proxy.pac
```

`QUTE_OMEGA_SETTINGS=/path/to/native.json` selects a separate settings file for an
isolated browser instance. It does not change the compiler location.

## Verification

From the repository:

```sh
node --test tests/test_native_pac.cjs
python3 tests/test_native_qutebrowser.py
```

The rule tests cover upstream selection, rule-list exceptions and defaults,
HTTP path selection, per-scheme proxies, and override hostname boundaries.
The browser smoke starts real qutebrowser with ephemeral logging upstreams and
a direct origin. It checks native proxy selection, HTTPS CONNECT, rendered
subresources, same-host HTTP paths routed differently, and an override after a
restart. No forwarding daemon participates. It uses temporary files and does
not depend on your personal settings or the installed integration.

For a manual isolated startup check after installation:

```sh
qutebrowser --temp-basedir \
  --config-py ~/.config/qutebrowser/config.py \
  https://www.google.com/ http://example.net/
```

Check `:messages` for config or PAC errors. Opening a page alone does not prove
its route; a Chromium NetLog or upstream access log can verify the selected
proxy. Verify that proxied requests reach your configured upstream and that
requests intended to use DIRECT do not appear in its access log.

## How it works

qutebrowser normally installs a custom `QNetworkProxyFactory`. QtWebEngine then
uses the Qt application proxy rather than Chromium's command-line proxy prefs.
The startup hook replaces `qutebrowser.browser.network.proxy.init` with
`QNetworkProxyFactory.setUseSystemConfiguration(True)` before WebEngine profiles
are created. QtWebEngine's system-config branch honors the explicit Chromium
PAC preference supplied through `qt.args`.

`omega_native.cjs` uses **SwitchyOmega's original `omega-pac` compiler**, not a
translation into a new rule grammar. It preserves the compiler's profile,
condition, and AutoProxy/Switchy rule-list semantics.

## Limits

- Chromium sanitizes HTTPS URLs supplied to PAC. Host rules work; do not rely on
  HTTPS path/query matching. HTTP path routing is tested.
- Rules change on browser restart, not immediately in an existing process.
- Rule lists and PAC profiles use content stored in the export. No automatic
  subscription downloads, popup UI, quick-switch menu, or browser sync is added.
- This does not import SwitchyOmega's proxy authentication manager. Chromium does
  not support SOCKS5 username/password authentication.
- PAC governs QtWebEngine. Auxiliary QtNetwork requests use Qt system proxy
  settings instead; this is not a system-wide VPN or WebRTC/UDP privacy guarantee.
- Config/compiler errors are reported by qutebrowser. This is **not fail-closed**:
  do not assume a config error prevents DIRECT connections.

## License and upstream source

GPL-3.0-or-later; see `LICENSE`. Original authors and license are retained in
`vendor/AUTHORS` and `vendor/COPYING`.

The vendored `omega_pac.min.js` comes from `js/omega_pac.min.js` in the official
[SwitchyOmega v2.5.20 release XPI](https://github.com/FelisCatus/SwitchyOmega/releases/tag/v2.5.20).
Its ZIP member CRC was verified. SHA-256:

```text
e904b7c41b9991a42758e016b74bd1d234c63978d1bd95d7b97c22e36879ca41
```

Corresponding upstream source:
<https://github.com/FelisCatus/SwitchyOmega/tree/v2.5.20/omega-pac>.
The npm package named `omega-pac` is a security holding package and is not used.

Qt proxy implementation:
<https://github.com/qt/qtwebengine/blob/6.11/src/core/net/proxy_config_service_qt.cpp>.
Chromium proxy/PAC documentation:
<https://chromium.googlesource.com/chromium/src/+/HEAD/net/docs/proxy.md>.
