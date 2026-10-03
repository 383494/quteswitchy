"""Website choices edited through Node and applied by a normal browser restart.

Import and install are safe before QApplication exists. Only switchy-menu creates
widgets; the editor and apply operation are independent of that dialog.
"""

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess

from qutebrowser.api import cmdutils
from qutebrowser.qt.core import QUrl
from qutebrowser.utils import standarddir


_SESSION_ENV = "QUTE_SWITCHY_SESSION"
_controller = None


class SwitchyError(RuntimeError):
    """An actionable editor, compiler, session, or restart failure."""


def normalize_domain(value):
    """Accept a hostname, never a URL, wildcard, port, or guessed parent domain."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise SwitchyError("Enter a hostname without spaces, a URL, or a port")
    domain = value.removesuffix(".").lower()
    try:
        return str(ipaddress.ip_address(domain))
    except ValueError:
        pass
    try:
        # Qt uses the browser's non-transitional IDNA conversion (unlike
        # Python's legacy idna codec, which changes e.g. faß.de to fass.de).
        domain = bytes(QUrl.toAce(domain)).decode("ascii").lower()
    except UnicodeError as error:
        raise SwitchyError("Invalid IDNA hostname: " + value) from error
    if len(domain) > 253 or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in domain.split(".")):
        raise SwitchyError("Invalid hostname: " + value)
    return domain


def _choice(domain, profile, subdomains):
    domain = normalize_domain(domain)
    if not isinstance(profile, str) or not profile:
        raise SwitchyError("Choose an existing result profile or direct")
    if not isinstance(subdomains, bool):
        raise SwitchyError("The subdomains choice must be a boolean")
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        subdomains = False
    return {"domain": domain, "profile": profile, "subdomains": subdomains}


def _covers(choice, hostname):
    return (hostname == choice["domain"] or
            (choice["subdomains"] and hostname.endswith("." + choice["domain"])))


def _overlaps(first, second):
    return _covers(first, second["domain"]) or _covers(second, first["domain"])


def _consume_session(source, profile, instance):
    # Keep the handoff for normal :restart, but reject another instance's choices
    # even if that browser was spawned from this one and inherited its env.
    raw = os.environ.get(_SESSION_ENV)
    if raw is None:
        return []
    try:
        session = json.loads(raw)
    except (ValueError, TypeError) as error:
        raise SwitchyError("Invalid " + _SESSION_ENV + " JSON") from error
    if not isinstance(session, dict):
        raise SwitchyError(_SESSION_ENV + " must contain a JSON object")
    if (session.get("source") != str(source) or session.get("profile") != profile or
            session.get("instance") != instance):
        os.environ.pop(_SESSION_ENV, None)
        return []
    rules = session.get("rules")
    if not isinstance(rules, list):
        raise SwitchyError(_SESSION_ENV + " rules must be an array")
    result = []
    for rule in rules:
        if not isinstance(rule, dict):
            raise SwitchyError(_SESSION_ENV + " rules must contain website choices")
        try:
            result.append(_choice(rule["domain"], rule["profile"], rule["subdomains"]))
        except KeyError as error:
            raise SwitchyError(_SESSION_ENV + " has an incomplete website choice") from error
    return result


class NodeEditor:
    """Read/edit the export and compile PAC without deciding how to apply it."""

    def __init__(self, native_dir, settings):
        self.script = Path(native_dir).resolve() / "omega_native.cjs"
        source = settings.get("source")
        if not isinstance(source, str) or not source:
            raise SwitchyError("native.json must configure a SwitchyOmega source path")
        self.source = Path(source).expanduser().resolve()
        self.profile = settings.get("profile")
        self.proxy = settings.get("proxy")

    def request(self, action, **fields):
        request = {"action": action, "source": str(self.source),
                   "profile": self.profile, "proxy": self.proxy, **fields}
        try:
            result = subprocess.run(
                ["node", str(self.script), "--request"],
                input=json.dumps(request), text=True, capture_output=True,
                check=False)
        except OSError as error:
            raise SwitchyError("Cannot run the SwitchyOmega editor: " + str(error)) from error
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip()
            raise SwitchyError(detail or "SwitchyOmega editor failed (exit {}): {}".format(
                result.returncode, self.source))
        try:
            response = json.loads(result.stdout)
        except ValueError as error:
            raise SwitchyError("SwitchyOmega editor returned invalid JSON: " +
                               result.stdout.strip()) from error
        if not isinstance(response, dict):
            raise SwitchyError("SwitchyOmega editor returned a non-object response")
        return response

    def describe(self):
        return self.request("describe")

    def set(self, choice):
        return self.request("set", choice=choice)

    def remove(self, domain, subdomains):
        return self.request("remove", domain=domain, subdomains=subdomains)

    def compile(self, pac, temporary_rules):
        return self.request("compile", output=str(pac), temporaryRules=temporary_rules)


class RestartApply:
    """Compile first, hand off temporary choices, then restart the whole instance."""

    def __init__(self, editor, pac, instance):
        self.editor = editor
        self.pac = pac
        self.instance = instance

    def apply(self, temporary_rules):
        from qutebrowser.misc import quitter

        if quitter.instance is None or quitter.instance.is_shutting_down:
            raise SwitchyError("The browser is not ready to restart")
        self.editor.compile(self.pac, temporary_rules)
        old_session = os.environ.get(_SESSION_ENV)
        os.environ[_SESSION_ENV] = json.dumps({
            "source": str(self.editor.source), "profile": self.editor.profile,
            "instance": self.instance, "rules": temporary_rules})
        try:
            # The normal command saves _restart, including every window/tab,
            # and shuts down the entire instance, not only the current window.
            quitter.restart()
            if not quitter.instance.is_shutting_down:
                raise SwitchyError("qutebrowser could not restart; the new PAC was compiled "
                                   "but has not been applied. See the browser log.")
        except Exception:
            if old_session is None:
                os.environ.pop(_SESSION_ENV, None)
            else:
                os.environ[_SESSION_ENV] = old_session
            raise


class Controller:
    def __init__(self, editor, settings, temporary_rules, instance):
        self.editor = editor
        pac = settings.get("pac")
        if not isinstance(pac, str) or not pac:
            raise SwitchyError("native.json must configure a PAC output path")
        output = Path(pac).expanduser().resolve()
        # Chromium snapshots the file at startup. Separate files prevent two
        # instances compiling different temporary choices from racing that read.
        identity = hashlib.sha256(instance.encode("utf-8")).hexdigest()[:16]
        self.pac = output.with_name(output.stem + "." + identity + output.suffix)
        self.temporary_rules = temporary_rules
        self.dialogs = {}
        self.editor.compile(self.pac, self.temporary_rules)
        self.apply = RestartApply(self.editor, self.pac, instance)

    def _apply(self, rules):
        self.apply.apply(rules)
        self.temporary_rules = rules

    def set(self, domain, profile, subdomains=True, temporary=False):
        choice = _choice(domain, profile, subdomains)
        if temporary:
            rules = [rule for rule in self.temporary_rules
                     if (rule["domain"], rule["subdomains"]) !=
                     (choice["domain"], choice["subdomains"])]
            rules.append(choice)
        else:
            self.editor.set(choice)
            # An encompassing temporary parent would otherwise hide this saved
            # choice; intersecting child scopes would hide part of a saved tree.
            rules = [rule for rule in self.temporary_rules if not _overlaps(rule, choice)]
        self._apply(rules)

    def reset(self, domain, subdomains=True, permanent=False):
        domain = normalize_domain(domain)
        if permanent:
            self.editor.remove(domain, subdomains)
            rules = list(self.temporary_rules)
        else:
            rules = [rule for rule in self.temporary_rules
                     if not (rule["domain"] == domain and
                             (subdomains or not rule["subdomains"]))]
        self._apply(rules)

    def clear_temporary(self):
        self._apply([])

    def close_dialogs(self):
        for dialog in list(self.dialogs.values()):
            dialog.close()

    def show_menu(self, win_id):
        from qutebrowser.qt.core import Qt
        from qutebrowser.qt.widgets import (
            QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
            QLineEdit, QPushButton, QVBoxLayout)
        from qutebrowser.utils import message, objreg

        if win_id in self.dialogs:
            dialog = self.dialogs[win_id]
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            return
        domain = _current_host(win_id)
        description = self.editor.describe()
        parent = objreg.get("main-window", scope="window", window=win_id)
        dialog = QDialog(parent)
        dialog.setObjectName("quteswitchy-menu")
        dialog.setWindowTitle("Switchy website routing")
        layout = QVBoxLayout(dialog)
        explanation = QLabel(
            "Every successful action restarts this entire browser instance, restoring "
            "all windows and tabs. No route changes happen live.\n\n"
            "Temporary choices last through these restarts, but clear on a fresh "
            "independently launched browser session or explicit reset. Permanent "
            "choices are saved as normal rules in your SwitchyOmega export.")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        form = QFormLayout()
        hostname = QLineEdit(domain)
        hostname.setObjectName("switchy-domain")
        host_label = QLabel("&Hostname:")
        host_label.setBuddy(hostname)
        form.addRow(host_label, hostname)
        subdomains = QCheckBox("&Include subdomains")
        subdomains.setObjectName("switchy-subdomains")
        subdomains.setChecked(True)
        form.addRow("", subdomains)
        profile = QComboBox()
        profile.setObjectName("switchy-profile")
        profile.addItems(description["profiles"])
        selected = "direct"
        for rule in reversed(self.temporary_rules):
            if _covers(rule, domain):
                selected = rule["profile"]
                break
        else:
            for rule in description["rules"]:
                condition = rule.get("condition", {})
                if condition.get("conditionType") != "HostWildcardCondition":
                    continue
                pattern = condition.get("pattern", "")
                if pattern == domain or (pattern.startswith("*.") and
                                          domain.endswith("." + pattern[2:])):
                    selected = rule["profileName"]
                    break
        index = profile.findText(selected)
        if index >= 0:
            profile.setCurrentIndex(index)
        profile_label = QLabel("&Result profile:")
        profile_label.setBuddy(profile)
        form.addRow(profile_label, profile)
        layout.addLayout(form)
        errors = QLabel()
        errors.setObjectName("switchy-error")
        errors.setTextFormat(Qt.TextFormat.PlainText)
        errors.setWordWrap(True)
        layout.addWidget(errors)

        def perform(operation):
            try:
                operation()
            except (SwitchyError, cmdutils.CommandError, OSError, ValueError) as error:
                text = "Switchy: " + str(error)
                errors.setText(text)
                message.error(text)
                return
            dialog.accept()

        def add_buttons(specs):
            row = QHBoxLayout()
            for name, text, operation in specs:
                button = QPushButton(text)
                button.setObjectName(name)
                button.setAutoDefault(False)
                button.clicked.connect(lambda checked=False, op=operation: perform(op))
                row.addWidget(button)
            layout.addLayout(row)

        add_buttons([
            ("switchy-temporary", "Apply &temporary + restart", lambda: self.set(
                hostname.text(), profile.currentText(), subdomains.isChecked(), temporary=True)),
            ("switchy-permanent", "Save &permanent + restart", lambda: self.set(
                hostname.text(), profile.currentText(), subdomains.isChecked()))])
        add_buttons([
            ("switchy-reset-temporary", "Reset temporary for &hostname", lambda: self.reset(
                hostname.text(), subdomains.isChecked())),
            ("switchy-reset-permanent", "Remove &saved hostname rule", lambda: self.reset(
                hostname.text(), subdomains.isChecked(), permanent=True))])
        add_buttons([
            ("switchy-clear-temporary", "Clear &all temporary choices", self.clear_temporary)])
        close = QPushButton("&Close")
        close.setAutoDefault(False)
        close.clicked.connect(dialog.reject)
        layout.addWidget(close)
        dialog.finished.connect(lambda result: self.dialogs.pop(win_id, None))
        dialog.finished.connect(dialog.deleteLater)
        self.dialogs[win_id] = dialog
        dialog.resize(680, dialog.sizeHint().height())
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        hostname.setFocus()
        hostname.selectAll()


def _current_host(win_id):
    from qutebrowser.utils import objreg, qtutils

    browser = objreg.get("tabbed-browser", scope="window", window=win_id)
    try:
        url = browser.current_url()
    except qtutils.QtValueError as error:
        raise SwitchyError("The current tab has no valid URL") from error
    if url.scheme().lower() not in ("http", "https") or not url.host():
        raise SwitchyError("Use an HTTP(S) tab, or pass --domain HOST")
    return normalize_domain(url.host())


def _installed():
    if _controller is None:
        raise SwitchyError("Switchy is not installed; source native_proxy.py first")
    return _controller


def _command(operation):
    try:
        operation()
    except (SwitchyError, OSError, ValueError) as error:
        raise cmdutils.CommandError("Switchy: " + str(error)) from error


@cmdutils.register()
@cmdutils.argument("win_id", value=cmdutils.Value.win_id)
def switchy_menu(win_id: int):
    """Open the current website's native routing dialog."""
    _command(lambda: _installed().show_menu(win_id))


@cmdutils.register()
@cmdutils.argument("win_id", value=cmdutils.Value.win_id)
def switchy_set(target: str, win_id: int, *, temporary: bool = False,
                domain: str = None, exact: bool = False):
    """Set a website's result profile, then restart all windows.

    Args:
        target: An existing result profile name, or direct.
        temporary: Keep the choice only for this browser session.
        domain: Hostname to edit instead of the current HTTP(S) tab.
        exact: Match only this hostname, not its subdomains.
    """
    _command(lambda: _installed().set(
        _current_host(win_id) if domain is None else domain,
        target, not exact, temporary=temporary))


@cmdutils.register()
@cmdutils.argument("win_id", value=cmdutils.Value.win_id)
def switchy_reset(win_id: int, *, permanent: bool = False,
                  domain: str = None, exact: bool = False):
    """Reset a matching website choice, then restart all windows.

    Args:
        permanent: Remove saved hostname rules instead of temporary choices.
        domain: Hostname to reset instead of the current HTTP(S) tab.
        exact: Remove only the exact-host scope, keeping its subdomain choice.
    """
    _command(lambda: _installed().reset(
        _current_host(win_id) if domain is None else domain,
        not exact, permanent=permanent))


@cmdutils.register()
def switchy_clear_temporary():
    """Clear every temporary website choice, then restart all windows."""
    _command(lambda: _installed().clear_temporary())


def install(native_dir: Path, settings: dict):
    """Compile startup PAC and replace the active controller without re-registering."""
    global _controller
    editor = NodeEditor(native_dir, settings)
    instance = str(Path(standarddir.data()).resolve())
    handed_off = _consume_session(editor.source, editor.profile, instance)
    if (_controller is not None and _controller.editor.source == editor.source and
            _controller.editor.profile == editor.profile):
        rules = [dict(rule) for rule in _controller.temporary_rules]
    else:
        rules = handed_off
    controller = Controller(editor, settings, rules, instance)
    if _controller is not None:
        _controller.close_dialogs()
    _controller = controller
    return controller
