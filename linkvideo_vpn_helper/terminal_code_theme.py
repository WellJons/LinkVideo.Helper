from __future__ import annotations

"""Terminal Code visual skin for all LinkVideo.Helper Qt workspaces.

This module is intentionally presentation-only. It never changes VPN operations,
credentials, data models, or widget signals. Keep the shell usable with native
Windows text scaling, Russian labels, and the existing responsive navigation.
"""

TERMINAL_THEME_KEY = "terminal_code"


def terminal_console_qss(key: str) -> str:
    """A focused IDE/console style; empty for every other existing theme."""
    from linkvideo_vpn_helper.theme import THEMES, normalize_theme

    if normalize_theme(key) != TERMINAL_THEME_KEY:
        return ""

    c = THEMES[TERMINAL_THEME_KEY]
    return f"""
    /* TERMINAL // CODE — global type and editor-like surfaces */
    * {{
        font-family: "Cascadia Code", "JetBrains Mono", "Consolas", "DejaVu Sans Mono", monospace;
        font-size: 12px;
        color: {c['text']};
        outline: none;
    }}
    QMainWindow, QDialog, QWidget#AppRoot, QWidget#ContentRoot {{
        background: {c['bg']};
    }}
    QWidget#PageCanvas, QWidget#PageHost, QWidget#PageViewport,
    QScrollArea#PageScroll, QScrollArea#PageScroll > QWidget > QWidget {{
        background: transparent;
    }}
    QLabel#PageTitle {{
        color: #E8FFF7; font-size: 25px; font-weight: 750;
        letter-spacing: -0.4px;
    }}
    QLabel#PageSubtitle, QLabel#Muted, QLabel#TinyMuted {{
        color: #86A8A8;
    }}
    QLabel#SectionTitle {{
        color: #64E6B6; font-size: 17px; font-weight: 750;
    }}
    QLabel#CardTitle {{
        color: #D5F5E9; font-size: 13px; font-weight: 750;
    }}
    QLabel#AppLogo {{
        color: #D9FFF0; font-size: 17px; font-weight: 800;
    }}
    QLabel#Value, QLabel#BigValue, QLabel#PortNumber {{
        color: #E9FBF4; font-weight: 720;
    }}
    QLabel#InfoText {{ color: #7ECFFF; }}
    QLabel#SuccessText {{ color: #57ECA6; }}
    QLabel#WarningText {{ color: #F9C56B; }}
    QLabel#DangerText {{ color: #FF7E99; }}

    /* IDE shell and command-bar. Decorative status never implies a live VPN. */
    QFrame#Sidebar {{
        background: #0C131B; border: none;
        border-right: 1px solid #28444A;
    }}
    QFrame#Sidebar QLabel#AppLogo,
    QFrame#Sidebar QLabel#Value {{
        color: #E9FFF6;
    }}
    QFrame#Sidebar QLabel#NavSection {{
        color: #5DAB99;
        font-size: 10px; font-weight: 800;
        letter-spacing: 1px;
    }}
    QFrame#Sidebar QLabel#TinyMuted {{ color: #83A5A7; }}
    QFrame#Sidebar QFrame#SidebarUserCard,
    QFrame#Sidebar QFrame#SidebarAdminCard {{
        background: #131F28;
        border: 1px solid #2A454B;
        border-radius: 7px;
    }}
    QFrame#Sidebar QLabel#Avatar {{
        background: #173C34; color: #66F4BC;
        border: 1px solid #316B59; border-radius: 7px;
    }}
    QFrame#Sidebar QPushButton[nav="true"] {{
        min-height: 40px; border-radius: 5px;
        background: transparent; border: 1px solid transparent;
        border-left: 3px solid transparent;
        color: #9FBDBA; font-size: 12px; font-weight: 620;
    }}
    QFrame#Sidebar QPushButton[nav="true"]:hover {{
        background: #142832; color: #E4FFF5;
        border-left: 3px solid #336B6E;
    }}
    QFrame#Sidebar QPushButton[nav="true"][active="true"] {{
        background: #14332F;
        border: 1px solid #24534A;
        border-left: 3px solid #64E6B6;
        border-radius: 5px;
        color: #76F6C4;
        font-weight: 800;
    }}
    QFrame#Sidebar QPushButton[role="icon"] {{
        background: #172A31; border: 1px solid #34525A;
        color: #C6F4E2; border-radius: 5px;
    }}

    QFrame#TerminalTopbar {{
        background: #0D151D; border: none;
        border-bottom: 1px solid #25434A;
        border-radius: 0px;
    }}
    QLabel#TerminalPrompt {{
        color: #6DE2B1; font-size: 11px; font-weight: 760;
    }}
    QLabel#TerminalCommand {{
        color: #ECFAF3; font-size: 11px; font-weight: 620;
    }}
    QLabel#TerminalCaption {{
        color: #749A9D; font-size: 10px;
    }}
    QLabel#TerminalSignal {{
        color: #65DBA8; font-size: 11px; font-weight: 800;
    }}

    /* Flat code-editor containers, no soft consumer-app bubbles. */
    QFrame#Card, QFrame#HeroCard, QFrame#ClientWorkspaceCard,
    QFrame#ArchiveClientsToolbar, QFrame#ArchiveClientCard,
    QFrame#SettingsTabCard, QFrame#BusyDialogCard, QFrame#LoginForm,
    QFrame#TemplateLibraryPanel, QFrame#TemplateEditorPanel {{
        background: #121F28; border: 1px solid #2A464D;
        border-radius: 8px;
    }}
    QFrame#SubtleCard, QFrame#ServerPickerFrame,
    QFrame#TemplateReplyTile, QFrame#DialogBody,
    QFrame#CreateResultEmpty {{
        background: #172731; border: 1px solid #2C4850;
        border-radius: 6px;
    }}
    QFrame#AccentCard {{
        background: #14352F; border: 1px solid #377F66;
        border-radius: 7px;
    }}
    QFrame#SuccessCard {{
        background: #133C2F; border: 1px solid #2D8F68;
        border-radius: 7px;
    }}
    QFrame#DangerCard, QFrame#LoginError {{
        background: #321C2A; border: 1px solid #985567;
        border-radius: 7px;
    }}
    QFrame#ConflictCard {{
        background: #332A1D; border: 1px solid #A6834A;
        border-radius: 7px;
    }}
    QFrame#ResultCard[selected="true"],
    QFrame#ResultCard[checked="true"] {{
        background: #143B31;
        border: 1px solid #64E6B6;
        border-radius: 7px;
    }}
    QFrame#ResultCard[selected="false"][checked="false"] {{
        background: #121F28;
        border: 1px solid #2A464D;
        border-radius: 7px;
    }}
    QFrame#ArchiveClientCard:hover {{
        background: #1B3037; border-color: #46736D;
    }}
    QFrame#ArchiveClientCard[bulk="true"],
    QFrame#ArchiveClientCard[focused="true"] {{
        background: #163A32; border-color: #60D6A6;
    }}

    /* Code input controls and keyboard focus */
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox,
    QTimeEdit, QDateEdit, QDateTimeEdit, QComboBox {{
        background: #0D1921; color: #E4F9F0;
        border: 1px solid #345057; border-radius: 5px;
        padding: 8px 10px;
        selection-background-color: #2D7C61;
        selection-color: #FFFFFF;
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
    QSpinBox:focus, QTimeEdit:focus, QDateEdit:focus, QDateTimeEdit:focus,
    QComboBox:focus, QComboBox:on {{
        border: 1px solid #59DDA9;
        background: #11242A;
    }}
    QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled {{
        background: #16232A; color: #759494;
    }}
    QLineEdit[error="true"] {{
        background: #311D27; border-color: #F47392;
    }}
    QLineEdit[success="true"] {{ border-color: #5BE7AC; }}
    QTextEdit#TemplateBodyInput, QLabel#TemplateBodyPreview {{
        background: #0D1921; color: #E4F9F0;
        border: 1px solid #345057; border-radius: 5px;
    }}
    QComboBox::drop-down {{
        width: 31px; border: none;
        border-left: 1px solid #345057;
        border-top-right-radius: 4px; border-bottom-right-radius: 4px;
    }}
    QComboBox::drop-down:hover {{ background: #1C3838; }}
    QComboBox QAbstractItemView {{
        background: #14242D; color: #E4FFF5;
        border: 1px solid #355E5B; border-radius: 4px;
        selection-background-color: #255243;
        selection-color: #FFFFFF;
    }}
    QComboBox QAbstractItemView::item:selected {{
        background: #255243; color: #FFFFFF;
    }}

    /* Actions: clear severity without neon-overload. */
    QPushButton {{
        background: #1A2B35; color: #DDF5EB;
        border: 1px solid #36545C; border-radius: 5px;
        min-height: 38px; font-weight: 650;
    }}
    QPushButton:hover {{
        background: #24404A; border-color: #538278; color: #FFFFFF;
    }}
    QPushButton:pressed {{ background: #173D36; border-color: #64E6B6; }}
    QPushButton:disabled {{
        background: #14222A; border-color: #2A3D43;
        color: #6C8988;
    }}
    QPushButton[role="primary"] {{
        background: #56E2AC; color: #09231C;
        border: 1px solid #56E2AC; font-weight: 800;
    }}
    QPushButton[role="primary"]:hover {{
        background: #7CF3C3; color: #072018;
        border-color: #7CF3C3;
    }}
    QPushButton[role="primary"]:disabled {{
        background: #24463C; color: #829F96; border-color: #335348;
    }}
    QPushButton[role="danger"] {{
        background: #BF4565; color: #FFFFFF; border-color: #BF4565;
    }}
    QPushButton[role="danger"]:hover {{
        background: #D95674; border-color: #D95674;
    }}
    QPushButton[role="soft"] {{
        background: #16382F; color: #75EDB8;
        border: 1px solid #2B6A52;
    }}
    QPushButton[role="soft"]:hover {{ background: #215245; }}
    QPushButton[role="ghost"] {{
        background: transparent; color: #93B9B4;
        border-color: transparent;
    }}
    QPushButton[role="ghost"]:hover {{
        background: #1B3037; color: #E1F9F0;
    }}
    QPushButton[themeChoice="true"] {{
        background: #17252E; border: 1px solid #355057;
        border-radius: 5px; text-align: left;
        min-height: 45px;
    }}
    QPushButton[themeChoice="true"][active="true"] {{
        background: #163A31; border: 1px solid #55E7AA;
        color: #89FFD0; font-weight: 800;
    }}
    QPushButton[segment="true"][active="true"] {{
        background: #1A3F36;
        color: #A0FFD4; border: 1px solid #468A70;
        border-radius: 4px;
    }}
    QWidget#SegmentHost {{ background: #10222A; border-radius: 6px; }}

    /* Tables become editor grids with strongly readable rows. */
    QTableWidget, QTableView, QTreeWidget, QTreeView {{
        background: #101E27; alternate-background-color: #162732;
        border: 1px solid #2C4A51; border-radius: 6px;
        gridline-color: #29414B;
        selection-background-color: #244E45;
        selection-color: #F1FFF9;
    }}
    QTableWidget::item, QTableView::item {{
        padding: 8px 10px;
        border-bottom: 1px solid #243D45;
    }}
    QTableWidget::item:selected, QTableView::item:selected,
    QTreeWidget::item:selected, QTreeView::item:selected {{
        background: #225041; color: #F5FFF9;
    }}
    QHeaderView::section {{
        background: #192A34;
        color: #77DAB5; border: none;
        border-bottom: 1px solid #43675E;
        border-right: 1px solid #2B454E;
        padding: 9px 10px; font-size: 11px; font-weight: 800;
    }}
    QListWidget {{ background: transparent; border: none; }}
    QListWidget::item {{ border-radius: 4px; }}
    QListWidget::item:selected {{
        background: #204D40; color: #EBFFF5;
    }}
    QListWidget#TemplateResultsList::item {{
        background: transparent; border: none;
    }}
    QFrame#TemplateReplyTile[selected="true"] {{
        background: #163C32; border: 1px solid #52CA98;
    }}

    /* Dialogs, menus, tabs, progress, scrollbar and status. */
    QDialog[helperModal="true"], QFrame#BusyDialogCard {{
        background: #111D27; border: 1px solid #37585C;
        border-radius: 8px;
    }}
    QMenu {{
        background: #14222C; color: #E8FDF4;
        border: 1px solid #38585A; border-radius: 6px;
        padding: 5px;
    }}
    QMenu::item {{ padding: 9px 22px 9px 10px; border-radius: 3px; }}
    QMenu::item:selected {{ background: #23513E; color: #FFFFFF; }}
    QMenu::separator {{ height: 1px; background: #2B4750; margin: 5px 8px; }}
    QTabWidget#SettingsTabs QTabBar::tab {{
        background: #101E27; color: #8DAEAE; border: none;
        border-bottom: 2px solid #2B4750;
        border-top-left-radius: 5px; border-top-right-radius: 5px;
        margin-right: 5px; padding: 12px 18px;
    }}
    QTabWidget#SettingsTabs QTabBar::tab:selected {{
        background: #17322F; color: #7AF6C0;
        border-bottom: 2px solid #67E9AF;
    }}
    QTabWidget#SettingsTabs QTabBar::tab:hover {{
        background: #1B3239; color: #E6FFF3;
    }}
    QLabel[pill="success"] {{
        color: #78F4B8; background: #154437;
        border: 1px solid #317D61; border-radius: 4px;
    }}
    QLabel[pill="warning"] {{
        color: #FAD288; background: #403323;
        border: 1px solid #9B7948; border-radius: 4px;
    }}
    QLabel[pill="danger"] {{
        color: #FF9FAE; background: #492534;
        border: 1px solid #9C4C65; border-radius: 4px;
    }}
    QLabel[pill="info"] {{
        color: #9CD9FF; background: #173A50;
        border: 1px solid #346D85; border-radius: 4px;
    }}
    QLabel[pill="neutral"] {{
        color: #AAC5C1; background: #1C3038;
        border: 1px solid #32515A; border-radius: 4px;
    }}
    QProgressBar {{
        background: #152931; border: 1px solid #355057;
        border-radius: 4px; min-height: 10px; max-height: 10px;
        color: transparent;
    }}
    QProgressBar::chunk {{ background: #55E2A8; border-radius: 3px; }}
    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 1px;
    }}
    QScrollBar::handle:vertical {{
        background: #345A5D; border-radius: 4px; min-height: 40px;
    }}
    QScrollBar:horizontal {{
        background: transparent; height: 10px; margin: 1px;
    }}
    QScrollBar::handle:horizontal {{
        background: #345A5D; border-radius: 4px; min-width: 40px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
    }}
    QToolTip {{
        background: #21343C; color: #E6FFF4;
        border: 1px solid #476A6A; border-radius: 5px;
        padding: 7px 10px;
    }}
    QCalendarWidget#CompactCalendar,
    QCalendarWidget#CompactCalendar QWidget {{
        background: #13232C; color: #E6FFF4;
    }}
    QCalendarWidget#CompactCalendar QAbstractItemView {{
        background: #13232C; color: #E6FFF4;
        selection-background-color: #255C48;
        selection-color: #FFFFFF;
    }}
    QFrame#ThemePreviewSidebar {{
        background: #0C131B; border: 1px solid #315256;
        border-radius: 6px;
    }}
    QFrame#ThemePreviewSidebar QLabel#PreviewBrand {{ color: #E3FFF1; }}
    QFrame#ThemePreviewSidebar QLabel#PreviewActive {{
        background: #245141; color: #8AFCCA; border-radius: 4px;
    }}
    """
