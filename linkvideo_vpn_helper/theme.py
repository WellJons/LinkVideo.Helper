from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# LinkVideo.Helper themes from 1.1.2, adapted to the 2.0 component system.
# name, bg, panel, panel2, panel3, border, text, muted, accent, accent_hover, danger
_RAW_THEMES = {
    # В 3.0.5 темы снова действительно разные. Общими остаются только
    # геометрия/компоненты и семантика статусов. Акцент, поверхности,
    # hover/selection и характер каждой палитры принадлежат самой теме.
    "rose_milk": ("Розовое молочко", "#FFF7FA", "#FFFBFD", "#FFFFFF", "#FFF0F6", "#F1D4E1", "#2B1821", "#806674", "#E83F8C", "#D92F7D", "#EF5B70"),
    "linkvideo_2026": ("Светлая LinkVideo", "#F4F7FB", "#F9FBFE", "#FFFFFF", "#EAF2FF", "#D8E2F0", "#18202A", "#66758A", "#3478F6", "#2367DC", "#E85767"),
    "lavender_mist": ("Лавандовая", "#F8F5FF", "#FCFAFF", "#FFFFFF", "#F0E9FF", "#DFD3F4", "#241B33", "#786A8A", "#8B5CF6", "#7745E5", "#E85D75"),
    "ocean_blue": ("Тёмно-синяя", "#06141F", "#0A1D2B", "#0E2738", "#123447", "#21485D", "#ECF8FF", "#8FB4C8", "#18A9E6", "#0D91C9", "#FF5B6E"),
    "midnight_purple": ("Полуночная", "#100B18", "#181022", "#21172E", "#2B1D3D", "#49345F", "#FBF6FF", "#B4A2C8", "#A855F7", "#9333EA", "#FF5F7A"),
    "cherry_pink": ("Тёмная вишня", "#190A10", "#240D16", "#30121E", "#3C1726", "#5E2A3B", "#FFF5F8", "#D3A5B4", "#F43F6C", "#E52A59", "#FF6B6B"),
    "soft_gray": ("Графитовая", "#171A1E", "#1F2328", "#282D33", "#333941", "#49515A", "#F5F7F9", "#AEB7C1", "#C88719", "#AC7210", "#FF5D6C"),
}


def _soft(hex_color: str, fallback: str) -> str:
    # Qt stylesheets do not support alpha hex consistently on older PySide builds,
    # so every theme gets a dedicated surface as its soft accent.
    return fallback


def _to_dict(key: str, row: tuple[str, ...]) -> dict:
    name, bg, panel, panel2, panel3, border, text, muted, accent, accent_hover, danger = row
    light = key in {"rose_milk", "linkvideo_2026", "lavender_mist"}
    return {
        "name": name,
        "bg": bg,
        "sidebar": panel,
        "panel": panel2,
        "panel2": panel3,
        "border": border,
        "border2": accent if light else border,
        "text": text,
        "muted": muted,
        "accent": accent,
        "accent_hover": accent_hover,
        "accent_soft": {
            "rose_milk": "#FFE8F2",
            "linkvideo_2026": "#E7F0FF",
            "lavender_mist": "#EEE5FF",
            "ocean_blue": "#0B3448",
            "midnight_purple": "#321A46",
            "cherry_pink": "#45182A",
            "soft_gray": "#3A3120",
        }.get(key, panel3),
        "success": "#168F65" if light else "#31C48D",
        "success_soft": "#E9F8F2" if light else panel3,
        "warning": "#B7791F" if light else "#F2B84B",
        "warning_soft": "#FFF5DF" if light else panel3,
        "danger": danger,
        "danger_soft": "#FFF0F2" if light else panel3,
        "info": accent,
        "info_soft": {
            "rose_milk": "#FFF0F6",
            "linkvideo_2026": "#EAF2FF",
            "lavender_mist": "#F0E9FF",
            "ocean_blue": "#0B3448",
            "midnight_purple": "#321A46",
            "cherry_pink": "#45182A",
            "soft_gray": "#3A3120",
        }.get(key, panel3),
        "input": panel if not light else panel2,
        "graph_rx": {
            "rose_milk": "#7C6CF2",
            "linkvideo_2026": "#14A3C7",
            "lavender_mist": "#D14FA3",
            "ocean_blue": "#61D7FF",
            "midnight_purple": "#D06BFF",
            "cherry_pink": "#FF9A76",
            "soft_gray": "#79B8FF",
        }.get(key, accent),
    }


THEMES = {key: _to_dict(key, row) for key, row in _RAW_THEMES.items()}


def theme_names() -> list[tuple[str, str]]:
    return [(key, data["name"]) for key, data in THEMES.items()]


def system_theme_key() -> str:
    app = QApplication.instance()
    if not app:
        return "ocean_blue"
    try:
        c = app.palette().color(QPalette.ColorRole.Window)
        return "ocean_blue" if c.lightness() < 128 else "linkvideo_2026"
    except Exception:
        return "ocean_blue"


def normalize_theme(key: str) -> str:
    key = str(key or "ocean_blue").strip().lower()
    if key == "system":
        return system_theme_key()
    if key == "dark":
        return "ocean_blue"
    if key == "light":
        return "linkvideo_2026"
    # Темы «Персиковая» и «Лесная» удалены из 3.0.2. Если они были
    # сохранены в старых настройках, мягко переносим пользователя на
    # ближайшую актуальную тему вместо неожиданного сброса интерфейса.
    if key == "peach_light":
        return "rose_milk"
    if key == "forest_green":
        return "ocean_blue"
    if key == "linkvideo_light":
        return "linkvideo_2026"
    if key == "night_owl":
        return "ocean_blue"
    return key if key in THEMES else "ocean_blue"


def colors(key: str = "ocean_blue") -> dict:
    return dict(THEMES[normalize_theme(key)])


def _apply_qt_palette(c: dict) -> None:
    """Синхронизирует нативные/рисуемые Qt-элементы с выбранной темой.

    QSS не перекрашивает QPalette полностью, из-за чего в 3.0.1 часть
    календаря, спиннеров, switch и custom-painted элементов могла оставаться
    в цветах Windows или предыдущей темы.
    """
    app = QApplication.instance()
    if not app:
        return
    pal = app.palette()
    mapping = {
        QPalette.ColorRole.Window: c["bg"],
        QPalette.ColorRole.WindowText: c["text"],
        QPalette.ColorRole.Base: c["panel"],
        QPalette.ColorRole.AlternateBase: c["panel2"],
        QPalette.ColorRole.Text: c["text"],
        QPalette.ColorRole.Button: c["panel"],
        QPalette.ColorRole.ButtonText: c["text"],
        QPalette.ColorRole.Highlight: c["accent"],
        QPalette.ColorRole.HighlightedText: "#FFFFFF",
        QPalette.ColorRole.ToolTipBase: c["text"],
        QPalette.ColorRole.ToolTipText: c["panel"],
        QPalette.ColorRole.Link: c["graph_rx"],
        QPalette.ColorRole.Mid: c["border2"],
        QPalette.ColorRole.PlaceholderText: c["muted"],
    }
    for role, value in mapping.items():
        pal.setColor(role, QColor(value))
    app.setPalette(pal)


def get_theme_style(key: str = "rose_milk") -> str:
    c = THEMES[normalize_theme(key)]
    _apply_qt_palette(c)
    return f"""
    * {{
        font-family: "Segoe UI Variable Text", "Segoe UI", Arial;
        font-size: 13px;
        color: {c['text']};
        outline: none;
    }}
    QMainWindow, QDialog, QWidget#AppRoot {{ background: {c['bg']}; }}
    QWidget#PageHost, QWidget#PageCanvas, QWidget#PageViewport, QScrollArea#PageScroll,
    QScrollArea#PageScroll > QWidget > QWidget {{ background: transparent; }}

    QToolTip {{
        background: {c['text']}; color: {c['panel']}; border: none;
        padding: 7px 10px; border-radius: 7px;
    }}

    /* Shell */
    QFrame#Sidebar {{ background: {c['sidebar']}; border-right: 1px solid {c['border']}; }}
    QWidget#ContentRoot {{ background: {c['bg']}; }}
    QLabel#BrandMark {{ background: transparent; border: none; }}
    QLabel#Avatar {{
        background: {c['accent_soft']}; color: {c['accent']};
        border: none; border-radius: 12px; font-weight: 800;
    }}
    QLabel#NavSection {{
        color: {c['muted']}; font-size: 10px; font-weight: 750;
        letter-spacing: 1.1px; padding: 12px 10px 4px 10px;
    }}
    QLabel#AppLogo {{ font-size: 20px; font-weight: 760; letter-spacing: -0.4px; }}

    /* Surfaces: subtle hierarchy, not a frame around everything */
    QFrame#Card, QFrame#HeroCard {{
        background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 18px;
    }}
    QFrame#SubtleCard {{
        background: {c['panel2']}; border: none; border-radius: 16px;
    }}
    QFrame#AccentCard {{
        background: {c['accent_soft']}; border: 1px solid {c['border']}; border-radius: 16px;
    }}
    QFrame#ClientWorkspaceCard {{
        background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 18px;
    }}
    QFrame#DangerCard {{ background: {c['danger_soft']}; border: none; border-radius: 14px; }}
    QFrame#SuccessCard {{ background: {c['success_soft']}; border: none; border-radius: 14px; }}
    QFrame#ResultCard[checked="true"], QFrame#ResultCard[selected="true"] {{
        background: {c['accent_soft']}; border: 1px solid {c['accent']}; border-radius: 14px;
    }}
    QFrame#ResultCard[selected="false"][checked="false"] {{
        background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 14px;
    }}
    QFrame#ArchiveClientsToolbar {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 16px; }}
    QFrame#ArchiveClientCard {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 15px; }}
    QFrame#ArchiveClientCard:hover {{ background: {c['panel2']}; border-color: {c['border2']}; }}
    QFrame#ArchiveClientCard[bulk="true"] {{ background: {c['accent_soft']}; border-color: {c['accent']}; }}
    QFrame#ArchiveClientCard[focused="true"] {{ border-color: {c['accent']}; }}

    /* Typography */
    QLabel#PageTitle {{ font-size: 28px; font-weight: 760; letter-spacing: -0.6px; }}
    QLabel#PageSubtitle {{ color: {c['muted']}; font-size: 13px; }}
    QLabel#SectionTitle {{ font-size: 18px; font-weight: 720; letter-spacing: -0.2px; }}
    QLabel#CardTitle {{ font-size: 14px; font-weight: 700; }}
    QLabel#Muted {{ color: {c['muted']}; }}
    QLabel#TinyMuted {{ color: {c['muted']}; font-size: 11px; }}
    QLabel#Value {{ font-size: 14px; font-weight: 650; }}
    QLabel#BigValue {{ font-size: 22px; font-weight: 760; }}
    QLabel#PortNumber {{ font-size: 18px; font-weight: 760; }}
    QLabel#DangerText {{ color: {c['danger']}; font-weight: 650; }}
    QLabel#SuccessText {{ color: {c['success']}; font-weight: 650; }}
    QLabel#WarningText {{ color: {c['warning']}; font-weight: 650; }}
    QFrame#ConflictCard {{ background: {c['warning_soft']}; border: 1px solid {c['warning']}; border-radius: 16px; }}
    QLabel#InfoText {{ color: {c['info']}; font-weight: 650; }}


    /* Modal operation state */
    QDialog#BusyDialog {{ background: transparent; }}
    QFrame#BusyDialogCard {{
        background: {c['panel']}; border: 1px solid {c['border2']}; border-radius: 18px;
    }}

    /* Buttons */
    QPushButton {{
        min-height: 40px; border-radius: 12px; border: 1px solid {c['border']};
        background: {c['panel']}; padding: 0 14px; font-weight: 630;
    }}
    QPushButton:hover {{ background: {c['panel2']}; border-color: {c['border2']}; }}
    QPushButton:pressed {{ background: {c['accent_soft']}; }}
    QPushButton:disabled {{ color: {c['muted']}; background: {c['panel2']}; border-color: {c['border']}; }}
    QPushButton[role="primary"] {{ background: {c['accent']}; color: white; border-color: {c['accent']}; }}
    QPushButton[role="primary"]:hover {{ background: {c['accent_hover']}; border-color: {c['accent_hover']}; }}
    QPushButton[role="danger"] {{ background: {c['danger']}; color: white; border-color: {c['danger']}; }}
    QPushButton[role="ghost"] {{ background: transparent; border-color: transparent; color: {c['muted']}; }}
    QPushButton[role="ghost"]:hover {{ background: {c['panel2']}; color: {c['text']}; }}
    QPushButton[role="soft"] {{ background: {c['accent_soft']}; border-color: transparent; color: {c['accent']}; }}
    QPushButton[role="icon"] {{ min-width: 40px; max-width: 40px; padding: 0; font-size: 16px; }}
    QPushButton[role="dangerGhost"] {{ background: transparent; border-color: transparent; color: {c['danger']}; }}
    QPushButton[feedback="success"] {{ background: {c['success_soft']}; border-color: transparent; color: {c['success']}; }}
    QPushButton[themeChoice="true"] {{
        text-align: left; min-height: 44px; background: {c['panel2']};
        border: 1px solid transparent; padding: 0 14px;
    }}
    QPushButton[themeChoice="true"]:hover {{ background: {c['panel']}; border-color: {c['border2']}; }}
    QPushButton[themeChoice="true"][active="true"] {{
        background: {c['accent_soft']}; color: {c['accent']}; border-color: {c['accent']}; font-weight: 720;
    }}

    /* Navigation */
    QPushButton[nav="true"] {{
        text-align: left; min-height: 44px; padding-left: 13px; border-radius: 12px;
        background: transparent; border: none; color: {c['muted']}; font-weight: 620;
    }}
    QPushButton[nav="true"]:hover {{ color: {c['text']}; background: {c['panel2']}; }}
    QPushButton[nav="true"][active="true"] {{ color: {c['accent']}; background: {c['accent_soft']}; font-weight: 700; }}

    /* Inputs */
    QLineEdit, QTextEdit, QSpinBox, QTimeEdit {{
        background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 12px;
        padding: 8px 12px; selection-background-color: {c['accent']};
    }}
    QLineEdit {{ min-height: 28px; }}
    QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QTimeEdit:focus {{ border-color: {c['accent']}; }}
    QLineEdit[error="true"] {{ border-color: {c['danger']}; background: {c['danger_soft']}; }}
    QLineEdit[success="true"] {{ border-color: {c['success']}; }}

    QFrame#ServerPickerFrame {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 12px; }}
    QFrame#ServerPickerFrame:hover {{ background: {c['panel2']}; border-color: {c['border2']}; }}
    QPushButton#DateTimeField {{ text-align: center; background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 12px; }}
    QPushButton#DateTimeField:hover {{ border-color: {c['border2']}; background: {c['panel2']}; }}
    QFrame#DateTimePopup {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 14px; }}
    QLineEdit#TimePart {{ font-size: 18px; font-weight: 720; padding: 5px 6px; }}
    QCalendarWidget#CompactCalendar {{ background: {c['panel']}; color: {c['text']}; border: none; }}
    QCalendarWidget#CompactCalendar QWidget {{ background: {c['panel']}; color: {c['text']}; }}
    QCalendarWidget#CompactCalendar QAbstractItemView {{
        background: {c['panel']}; color: {c['text']}; border: none; outline: none;
        selection-background-color: {c['accent']}; selection-color: white;
    }}
    QCalendarWidget#CompactCalendar QAbstractItemView::item {{ min-height: 30px; border-radius: 7px; }}
    QCalendarWidget#CompactCalendar QAbstractItemView::item:hover {{ background: {c['panel2']}; }}

    /* Segments */
    QWidget#SegmentHost {{ background: {c['panel2']}; border: none; border-radius: 12px; }}
    QPushButton[segment="true"] {{ min-height: 34px; border: none; background: transparent; border-radius: 9px; color: {c['muted']}; }}
    QPushButton[segment="true"]:hover {{ color: {c['text']}; background: {c['panel']}; }}
    QPushButton[segment="true"][active="true"] {{ background: {c['panel']}; color: {c['text']}; font-weight: 700; border: 1px solid {c['border']}; }}

    /* Status chips */
    QLabel[pill="neutral"] {{ background: {c['panel2']}; color: {c['muted']}; border-radius: 9px; border: none; }}
    QLabel[pill="success"] {{ background: {c['success_soft']}; color: {c['success']}; border-radius: 9px; border: none; }}
    QLabel[pill="warning"] {{ background: {c['warning_soft']}; color: {c['warning']}; border-radius: 9px; border: none; }}
    QLabel[pill="danger"] {{ background: {c['danger_soft']}; color: {c['danger']}; border-radius: 9px; border: none; }}
    QLabel[pill="info"] {{ background: {c['info_soft']}; color: {c['info']}; border-radius: 9px; border: none; }}

    /* Lists & tables */
    QListWidget {{ background: transparent; border: none; padding: 1px; }}
    QListWidget::item {{ border-radius: 11px; padding: 9px; margin: 2px 0; }}
    QListWidget#SearchResultsList {{ padding: 0; }}
    QListWidget#SearchResultsList::item {{ padding: 0; margin: 4px 0; border: none; }}
    QListWidget::item:hover {{ background: {c['panel2']}; }}
    QListWidget::item:selected {{ background: {c['accent_soft']}; color: {c['text']}; }}

    QTableWidget {{
        background: {c['panel']}; alternate-background-color: {c['panel2']};
        border: none; border-radius: 12px; gridline-color: transparent;
    }}
    QTableWidget::item {{ padding: 9px 10px; border-bottom: 1px solid {c['border']}; }}
    QTableWidget::item:selected {{ background: {c['accent_soft']}; color: {c['text']}; }}
    QHeaderView::section {{
        background: {c['panel2']}; color: {c['muted']}; border: none;
        border-bottom: 1px solid {c['border']}; padding: 9px 10px; font-weight: 700;
    }}

    QProgressBar {{ background: {c['panel2']}; border: none; border-radius: 5px; min-height: 10px; max-height: 10px; }}
    QProgressBar::chunk {{ background: {c['accent']}; border-radius: 5px; }}

    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ width: 9px; background: transparent; margin: 4px 2px; }}
    QScrollBar::handle:vertical {{ background: {c['border2']}; border-radius: 4px; min-height: 36px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ height: 9px; background: transparent; margin: 2px 4px; }}
    QScrollBar::handle:horizontal {{ background: {c['border2']}; border-radius: 4px; min-width: 36px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    QMenu {{ background: {c['panel']}; border: 1px solid {c['border']}; padding: 6px; border-radius: 12px; }}
    QMenu::item {{ padding: 9px 26px 9px 12px; border-radius: 8px; }}
    QMenu::item:selected {{ background: {c['accent_soft']}; color: {c['text']}; }}
    QMenu::separator {{ height: 1px; background: {c['border']}; margin: 5px 8px; }}
    """
