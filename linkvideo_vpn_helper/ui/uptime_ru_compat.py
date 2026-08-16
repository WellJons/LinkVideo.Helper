from __future__ import annotations

"""Russian labels/formatting for RouterOS VPN uptime values."""

import re

from PySide6.QtWidgets import QLabel


_INSTALLED = False
_DURATION_RE = re.compile(
    r"^(?:(?P<w>\d+)w)?(?:(?P<d>\d+)d)?(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s)?$",
    re.I,
)


def format_uptime(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "—"
    match = _DURATION_RE.fullmatch(text)
    if not match or not any(match.groupdict().values()):
        return text
    units = (("w", "нед"), ("d", "д"), ("h", "ч"), ("m", "мин"), ("s", "с"))
    parts = [f"{int(match.group(key))} {suffix}" for key, suffix in units if match.group(key) is not None]
    return " ".join(parts) if parts else text


def _localize_labels(widget) -> None:
    if widget is None:
        return
    for label in widget.findChildren(QLabel):
        text = str(label.text() or "")
        changed = text
        match = re.search(r"Uptime:\s*([0-9wdhms]+)", changed, flags=re.I)
        if match:
            changed = changed[:match.start()] + "Время подключения: " + format_uptime(match.group(1)) + changed[match.end():]
        changed = re.sub(r"\bVPN uptime\b", "Время подключения", changed, flags=re.I)
        if changed != text:
            label.setText(changed)


def install_uptime_ru() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from linkvideo_vpn_helper.ui.pages.search_manage_page import SearchManagePage, TrafficDialog

    original_render = SearchManagePage._render_client
    original_live = SearchManagePage._on_live_refresh
    original_dialog_init = TrafficDialog.__init__
    original_dialog_sample = TrafficDialog._on_sample

    def render_client(self, *args, **kwargs):
        result = original_render(self, *args, **kwargs)
        _localize_labels(self)
        return result

    def on_live_refresh(self, *args, **kwargs):
        result = original_live(self, *args, **kwargs)
        _localize_labels(self)
        return result

    def dialog_init(self, *args, **kwargs):
        original_dialog_init(self, *args, **kwargs)
        _localize_labels(self)
        try:
            self.uptime_metric.setValue(format_uptime(getattr(self.client, "uptime", "")))
        except Exception:
            pass

    def dialog_sample(self, client, error):
        result = original_dialog_sample(self, client, error)
        _localize_labels(self)
        if not error and client is not None:
            try:
                self.uptime_metric.setValue(format_uptime(getattr(client, "uptime", "")))
            except Exception:
                pass
        return result

    SearchManagePage._render_client = render_client
    SearchManagePage._on_live_refresh = on_live_refresh
    TrafficDialog.__init__ = dialog_init
    TrafficDialog._on_sample = dialog_sample
    _INSTALLED = True
