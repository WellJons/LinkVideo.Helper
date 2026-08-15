from __future__ import annotations

"""LinkVideo brand palette aligned with LinkVideo.Monitor.

The internal key ``linkvideo_2026`` is intentionally preserved so existing
QSettings values and update migrations stay compatible. Only the visible name
and palette are replaced.
"""


def install_linkvideo_brand_theme() -> None:
    from linkvideo_vpn_helper.theme import THEMES

    theme = THEMES.get("linkvideo_2026")
    if not theme:
        return

    # Palette copied from the visual language used by LinkVideo.Monitor:
    # neutral #f4f5f7 workspace, white cards, graphite text and LinkVideo orange.
    theme.update(
        {
            "name": "LinkVideo",
            "bg": "#F4F5F7",
            "sidebar": "#FFFFFF",
            "panel": "#FFFFFF",
            "panel2": "#F7F8FA",
            "border": "#DFE3E8",
            "border2": "#EB9600",
            "text": "#20252B",
            "muted": "#69727C",
            "accent": "#FFAD19",
            "accent_hover": "#EB9600",
            "accent_soft": "#FFF9EC",
            "success": "#168F4A",
            "success_soft": "#EDF8F1",
            "warning": "#B7791F",
            "warning_soft": "#FFF7E6",
            "danger": "#BD3737",
            "danger_soft": "#FFF0F0",
            "info": "#4C6F9E",
            "info_soft": "#EEF3F8",
            "input": "#FFFFFF",
            "graph_rx": "#4C6F9E",
        }
    )
