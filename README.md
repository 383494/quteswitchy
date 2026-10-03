# quteswitchy

Native SwitchyOmega proxy rules for qutebrowser, with a current-website editor.
Chromium selects DIRECT or an upstream proxy using a PAC script compiled from
your `OmegaOptions.bak`. No local routing daemon or extra listening port is needed.

Configure your upstream proxy, for example **SOCKS5 `127.0.0.1:your-port`**.
Your proxy service must already be running; this project does not provide it.

## Requirements

- qutebrowser with QtWebEngine; tested on qutebrowser **3.7.0 / QtWebEngine 6.11.1**.
- Node.js **18+** on `PATH`; no npm installation or dependencies needed.
- A SwitchyOmega schema-version-2 export containing a FixedProfile named `proxy`.
  Website editing requires the selected profile to be a SwitchProfile.

This uses a private qutebrowser startup hook, not a supported extension API.
Re-run the browser smoke check after qutebrowser or Qt upgrades.

## Install

Run from this repository:

```sh
mkdir -p ~/.local/share/qute-switchy-native \
         ~/.config/qute-switchy ~/.config/qutebrowser
cp -R native_proxy.py switchy_ui.py omega_native.cjs vendor README.md LICENSE \
      ~/.local/share/qute-switchy-native/
```

Keep your SwitchyOmega export at a path of your choice, represented below as
`/path/to/OmegaOptions.bak`. This export is the sole permanent website-rule source;
personal exports, settings, and generated PAC files are not part of this repository.

For a fresh installation, create settings:

```sh
cp examples/native.json ~/.config/qute-switchy/native.json
```

On an existing installation, keep your settings and export. After copying the new
code, migrate the old overrides layer once:

```sh
node omega_native.cjs --migrate-settings ~/.config/qute-switchy/native.json
```

This moves any ordered website overrides into standard rules in the selected
SwitchyOmega profile and removes the `overrides` setting. Other settings are
preserved. The old overrides file is left untouched but is no longer read.
The migration also handles an empty overrides array; rerunning it is a no-op.

The settings template uses placeholders:

```json
{
  "source": "/path/to/OmegaOptions.bak",
  "profile": "auto switch",
  "proxy": "socks5://127.0.0.1:your-port",
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
It can also be sourced directly from a checkout: the editor, compiler, and vendored
engine are located relative to `native_proxy.py`. The `pac` setting is a filename
base: startup inserts an instance-specific suffix, for example
`proxy.0123456789abcdef.pac`, so separate instances cannot overwrite each other's
temporary routing during startup.

## Website controls

On an HTTP(S) page, press **`,s`** or run **`:switchy-menu`**. The default binding
is installed only if `,s` is unused. You can choose a different key with
`:bind YOUR-KEY switchy-menu`.

The native dialog provides an editable hostname, an **Include subdomains** checkbox,
a result-profile selector, and buttons to:

- Apply a temporary choice.
- Save a permanent choice.
- Reset temporary choices for that hostname.
- Remove saved hostname rules.
- Clear every temporary choice.

**Every successful routing action automatically restarts the whole qutebrowser
instance**, restoring its windows and tabs through the normal `:restart` mechanism.
It is not a live switch or a page-only refresh. Unsaved page/form state is not
guaranteed to survive. Normal and private windows in the same instance share the
rules and restart together. Normal restart session handling includes private tabs;
this is not a guarantee of disk-free private browsing.

### Commands

These use the current HTTP(S) tab's hostname and include subdomains by default:

```text
:switchy-set proxy
:switchy-set direct
:switchy-set proxy --temporary
:switchy-set direct --temporary
:switchy-reset
:switchy-reset --permanent
:switchy-clear-temporary
```

Use `--domain HOST` to choose another hostname, including from a non-HTTP(S) tab.
Use `--exact` to exclude subdomains:

```text
:switchy-set direct --temporary --domain example.com --exact
```

The hostname is used literally, not expanded to a guessed parent domain. Matching
does not include lookalikes such as `notexample.com` or `example.com.evil`. IP
addresses are always exact-only. Result profiles come from the export and include
the built-in `direct` choice; invalid or cyclic choices are rejected.

### Permanent rules and temporary state

Permanent edits prepend ordinary SwitchyOmega `HostWildcardCondition` rules to
the selected SwitchProfile. The export remains importable in SwitchyOmega; no
private rule schema or persistent overrides file is added. Other profiles, rule
lists, defaults, and unrelated rules are preserved. Keep a backup of the original
export if you want to undo edits later.

A permanent set replaces existing exact-host and `*.hostname` entries for the
same hostname, then adds the chosen scope. Removing saved hostname rules also
removes matching imported host entries; it does not recover a rule replaced by a
previous edit. Other matching patterns and the profile default can still apply.
`--exact` on reset removes only the exact-host entry, leaving a subdomain entry.

Temporary choices take precedence over permanent/imported rules, with the most
recent matching choice winning. They never modify the export. Saving a permanent
choice clears overlapping temporary choices so that the saved choice takes effect.
Removing a saved rule leaves temporary choices alone; reset those separately.

Temporary choices survive both automatic and ordinary `:restart` in that instance,
but clear on a fresh launch from an external shell or an explicit reset. The
restart handoff lives in the browser environment, scoped to the export, selected
profile, and browser data directory—not in another permanent rule file. Separate
instances with different base directories do not share temporary choices, even
if they inherit that environment. Instances sharing an export see permanent
changes on their next restart, not immediately.

After editing the export or installation settings outside the UI, use **`:restart`**,
not merely `:config-source`: Qt snapshots the PAC into its startup proxy preference.
The valid export must remain available for subsequent startups.

To compile manually, without temporary choices:

```sh
node omega_native.cjs \
  --source /path/to/OmegaOptions.bak --profile 'auto switch' \
  --proxy socks5://127.0.0.1:your-port \
  --output ~/.config/qute-switchy/proxy.pac
```

`QUTE_OMEGA_SETTINGS=/path/to/native.json` selects a separate settings file for an
isolated browser instance. It does not change the compiler location.

## Verification

From the repository:

```sh
node --test tests/test_native_pac.cjs tests/test_native_rules.cjs
python3 tests/test_native_qutebrowser.py
```

The rule tests cover upstream selection, rule-list exceptions and defaults,
HTTP paths, per-scheme proxies, compatible export edits, hostname/IDNA/IP
boundaries, temporary precedence, invalid-edit preservation, and legacy migration.
The browser smoke runs real qutebrowser with ephemeral logging upstreams and a
direct origin. It clicks actual temporary/permanent dialog buttons, observes the
changed routing after automatic restart, checks normal/private-window restoration,
ordinary restart, reset, fresh-instance isolation, and distinct instance PAC files.
It also checks HTTPS CONNECT and rendered subresources. No forwarding daemon
participates; personal settings and the installed integration are not used.

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

### Editor/application boundary

`switchy_ui.py` separates `NodeEditor` (export edits and PAC compilation) from
`RestartApply.apply(temporary_rules)` (native application through browser restart).
The controller's apply object can be replaced by a future helper implementation;
the website-choice model, export editor, and UI do not depend on network forwarding.
A helper would also need its own startup/routing integration; it is not included.

`node omega_native.cjs --request` reads one JSON request on stdin and returns one
JSON response on stdout. Errors go to stderr with a nonzero exit code. Requests
carry `source`, `profile`, and the explicit `proxy`, plus one of:

- `action: "describe"`: returns `profile`, valid `profiles`, and the selected
  SwitchProfile's `rules`.
- `action: "set"`: takes `choice: {domain, profile, subdomains}` and atomically
  saves standard hostname rules after compilation validates the prospective export.
- `action: "remove"`: takes `domain` and boolean `subdomains` and removes that scope.
- `action: "compile"`: takes `output` and optional `temporaryRules`, writing only
  the generated PAC. Temporary choices use the same `{domain, profile, subdomains}`
  shape in chronological order; the last matching choice wins.


## Limits

- Chromium sanitizes HTTPS URLs supplied to PAC. Host rules work; do not rely on
  HTTPS path/query matching. HTTP path routing is tested.
- Rules change on browser restart, not immediately in an existing process.
- Rule lists and PAC profiles use content stored in the export. No automatic
  subscription downloads, general extension loader, or browser sync is added.
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
