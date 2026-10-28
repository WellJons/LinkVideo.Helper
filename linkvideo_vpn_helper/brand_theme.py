from __future__ import annotations

"""LinkVideo visual identity and native-widget theme completion.

The runtime LinkVideo palette uses cool gray and white surfaces with a
restrained orange brand accent. Avoid the broad yellow/cream override that
previously turned navigation and workspaces beige. Installer styling remains
independent from the desktop palette.

Native QComboBox fields/popups must also follow the active palette on Windows.
"""


_INSTALLED = False


def _combo_box_theme_qss(key: str) -> str:
    from linkvideo_vpn_helper.theme import THEMES, normalize_theme

    c = THEMES[normalize_theme(key)]
    return f"""
    /* Native combo boxes: field, arrow area and popup must follow the theme. */
    QComboBox {{
        min-height: 38px;
        background: {c['panel']};
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: 12px;
        padding: 0 38px 0 12px;
        selection-background-color: {c['accent_soft']};
        selection-color: {c['text']};
    }}
    QComboBox:hover {{
        background: {c['panel2']};
        border-color: {c['border2']};
    }}
    QComboBox:focus, QComboBox:on {{
        border-color: {c['accent']};
    }}
    QComboBox:on {{
        background: {c['panel']};
    }}
    QComboBox:disabled {{
        color: {c['muted']};
        background: {c['panel2']};
        border-color: {c['border']};
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 34px;
        border: none;
        border-left: 1px solid {c['border']};
        border-top-right-radius: 11px;
        border-bottom-right-radius: 11px;
    }}
    QComboBox::drop-down:hover {{
        background: {c['accent_soft']};
    }}
    QComboBox QAbstractItemView {{
        background: {c['panel']};
        color: {c['text']};
        border: 1px solid {c['border2']};
        border-radius: 10px;
        padding: 5px;
        outline: none;
        selection-background-color: {c['accent_soft']};
        selection-color: {c['text']};
        show-decoration-selected: 1;
    }}
    QComboBox QAbstractItemView::item {{
        min-height: 30px;
        padding: 5px 10px;
        border: none;
    }}
    QComboBox QAbstractItemView::item:hover {{
        background: {c['panel2']};
        color: {c['text']};
    }}
    QComboBox QAbstractItemView::item:selected {{
        background: {c['accent_soft']};
        color: {c['text']};
    }}
    """


def install_linkvideo_brand_theme() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    import linkvideo_vpn_helper.theme as theme_module

    theme = theme_module.THEMES.get("linkvideo_2026")
    if theme:
        # Keep the palette in theme.py: brand identity only adds a name.
        theme["name"] = "LinkVideo"

    original_get_theme_style = theme_module.get_theme_style

    def themed_style(key: str = "rose_milk") -> str:
        # The application and MainWindow both obtain their stylesheet through
        # this function, including every live theme switch in Settings.
        return original_get_theme_style(key) + _combo_box_theme_qss(key)

    theme_module.get_theme_style = themed_style
    _INSTALLED = True
