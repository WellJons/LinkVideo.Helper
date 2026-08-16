from __future__ import annotations

"""Restore visual hierarchy without moving the 3.0 workspace layout.

The 3.0 geometry is intentionally kept: pages, cards and controls stay in their
current places.  This module only strengthens surface separation, typography,
selection and table/status affordances so the desktop tool does not read like a
flat web wireframe.
"""

_INSTALLED = False


def _density_qss(c: dict) -> str:
    return f"""
    /* 3.0.8 visual hierarchy pass: geometry intentionally unchanged */
    QFrame#SubtleCard {{
        background: {c['panel2']};
        border: 1px solid {c['border']};
        border-radius: 16px;
    }}
    QFrame#Card, QFrame#HeroCard, QFrame#ClientWorkspaceCard,
    QFrame#ArchiveClientsToolbar, QFrame#ArchiveClientCard {{
        border: 1px solid {c['border']};
    }}
    QFrame#HeroCard {{
        background: {c['panel']};
    }}
    QFrame#AccentCard {{
        border: 1px solid {c['accent']};
    }}
    QFrame#DangerCard {{
        border: 1px solid {c['danger']};
    }}
    QFrame#SuccessCard {{
        border: 1px solid {c['success']};
    }}

    QLabel#SectionTitle {{
        font-size: 18px;
        font-weight: 780;
        letter-spacing: -0.25px;
    }}
    QLabel#CardTitle {{
        font-size: 15px;
        font-weight: 750;
    }}
    QLabel#Value {{
        font-size: 14px;
        font-weight: 670;
    }}
    QLabel#TinyMuted {{
        color: {c['muted']};
        font-size: 12px;
        font-weight: 520;
    }}
    QLabel#Muted {{
        color: {c['muted']};
        font-weight: 520;
    }}

    QPushButton {{
        font-weight: 660;
    }}
    QPushButton[role="primary"], QPushButton[role="danger"] {{
        font-weight: 700;
    }}
    QPushButton[nav="true"][active="true"] {{
        border: 1px solid {c['border']};
        font-weight: 740;
    }}

    QLabel[pill="neutral"] {{ border: 1px solid {c['border']}; }}
    QLabel[pill="success"] {{ border: 1px solid {c['success']}; }}
    QLabel[pill="warning"] {{ border: 1px solid {c['warning']}; }}
    QLabel[pill="danger"] {{ border: 1px solid {c['danger']}; }}
    QLabel[pill="info"] {{ border: 1px solid {c['info']}; }}

    QListWidget#PortList::item {{
        border: 1px solid transparent;
    }}
    QListWidget#PortList::item:hover {{
        border-color: {c['border']};
        background: {c['panel2']};
    }}
    QListWidget#PortList::item:selected {{
        border: 1px solid {c['accent']};
        background: {c['accent_soft']};
        color: {c['text']};
    }}

    QFrame#ResultCard[selected="false"][checked="false"] {{
        border: 1px solid {c['border']};
    }}
    QFrame#ResultCard[checked="true"], QFrame#ResultCard[selected="true"] {{
        border: 2px solid {c['accent']};
    }}

    QTableWidget {{
        border: 1px solid {c['border']};
        gridline-color: {c['border']};
    }}
    QTableWidget::item {{
        border-bottom: 1px solid {c['border']};
    }}
    QHeaderView::section {{
        color: {c['text']};
        background: {c['panel2']};
        border-right: 1px solid {c['border']};
        border-bottom: 1px solid {c['border']};
        font-weight: 760;
    }}

    QLineEdit, QTextEdit, QSpinBox, QTimeEdit,
    QFrame#ServerPickerFrame, QPushButton#DateTimeField {{
        border-width: 1px;
    }}
    """


def install_visual_density() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    import linkvideo_vpn_helper.theme as theme

    if getattr(theme.get_theme_style, "_lv_visual_density", False):
        _INSTALLED = True
        return

    original = theme.get_theme_style

    def patched_get_theme_style(key: str = "rose_milk") -> str:
        base = original(key)
        c = theme.colors(key)
        return base + _density_qss(c)

    patched_get_theme_style._lv_visual_density = True
    theme.get_theme_style = patched_get_theme_style
    _INSTALLED = True
