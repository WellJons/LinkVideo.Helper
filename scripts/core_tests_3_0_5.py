from pathlib import Path
import re
ROOT=Path(__file__).resolve().parents[1]
theme=(ROOT/'linkvideo_vpn_helper/theme.py').read_text(encoding='utf-8')
ver=(ROOT/'linkvideo_vpn_helper/version.py').read_text(encoding='utf-8')
assert re.search(r'^APP_VERSION\s*=\s*"\d+\.\d+\.\d+"\s*$', ver, re.MULTILINE)
expected={
 'rose_milk':'#E83F8C',
 'linkvideo_2026':'#3478F6',
 'lavender_mist':'#8B5CF6',
 'ocean_blue':'#18A9E6',
 'midnight_purple':'#A855F7',
 'cherry_pink':'#F43F6C',
 'soft_gray':'#C88719',
}
for key, accent in expected.items():
    assert f'"{key}"' in theme and accent in theme
assert len(set(expected.values())) == len(expected)
assert 'фирменный LinkVideo-розовый остаётся основным' not in theme
print('CORE TESTS 3.0.7 DISTINCT THEMES OK')


# Runtime regression: get_theme_style() must actually render every QSS f-string.
# Stub only the tiny PySide surface imported by theme.py so this also works on build/check hosts without Qt.
import importlib.util, sys, types
qtgui = types.ModuleType("PySide6.QtGui")
qtwidgets = types.ModuleType("PySide6.QtWidgets")
class _ColorRole:
    Window=1; WindowText=2; Base=3; AlternateBase=4; Text=5; Button=6; ButtonText=7
    Highlight=8; HighlightedText=9; ToolTipBase=10; ToolTipText=11; Link=12; Mid=13; PlaceholderText=14
class _QPalette:
    ColorRole = _ColorRole
class _QColor:
    def __init__(self, *args, **kwargs): pass
class _QApplication:
    @staticmethod
    def instance(): return None
qtgui.QColor=_QColor; qtgui.QPalette=_QPalette; qtwidgets.QApplication=_QApplication
pkg=types.ModuleType("PySide6"); pkg.QtGui=qtgui; pkg.QtWidgets=qtwidgets
sys.modules.setdefault("PySide6", pkg)
sys.modules["PySide6.QtGui"]=qtgui
sys.modules["PySide6.QtWidgets"]=qtwidgets
spec=importlib.util.spec_from_file_location("_lv_theme_runtime_test", ROOT/'linkvideo_vpn_helper/theme.py')
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
for key in expected:
    qss=mod.get_theme_style(key)
    assert isinstance(qss, str) and "QPushButton" in qss and len(qss) > 1000, key
print('CORE TESTS 3.0.7 THEME RUNTIME RENDER OK')
# Regression: Settings must be constructible; its renderer cannot disappear during theme refactors.
settings_text = (ROOT / "linkvideo_vpn_helper" / "ui" / "pages" / "settings_page.py").read_text(encoding="utf-8")
assert "def _render_servers(self):" in settings_text
assert "self._render_servers()" in settings_text
print("CORE TESTS 3.0.7 SETTINGS RENDER CONTRACT OK")

