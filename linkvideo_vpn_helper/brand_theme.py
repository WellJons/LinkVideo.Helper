from __future__ import annotations

"""Visible LinkVideo theme identity.

The application already has a balanced light ``linkvideo_2026`` palette in
``theme.py``: cool neutral surfaces with a clear blue interaction accent. An
earlier 3.0.8 integration replaced that palette with the orange/cream web
palette from LinkVideo.Monitor. In Helper that orange was applied much more
widely (navigation, selected cards, buttons and borders), which made the whole
workspace look yellow/beige.

Keep the stable settings key and the LinkVideo name, but let the native Helper
palette define the colors. Installer branding is independent from this runtime
theme and is not changed here.
"""


def install_linkvideo_brand_theme() -> None:
    from linkvideo_vpn_helper.theme import THEMES

    theme = THEMES.get("linkvideo_2026")
    if not theme:
        return

    # Do not repaint the whole desktop UI with Monitor's orange/cream web
    # palette. The built-in Helper palette intentionally uses neutral white/blue
    # surfaces and blue selection, while the product still keeps its LinkVideo
    # name and iconography.
    theme["name"] = "LinkVideo"
