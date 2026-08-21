from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "linkvideo_vpn_helper" / "brand_theme.py"
METHODS = ROOT / "linkvideo_vpn_helper" / "services" / "archive_download_methods.py"


def main() -> int:
    brand = BRAND.read_text(encoding="utf-8")
    methods = METHODS.read_text(encoding="utf-8")

    required_theme_fragments = (
        "def _combo_box_theme_qss(key: str) -> str:",
        "QComboBox {",
        "QComboBox:hover {",
        "QComboBox:focus, QComboBox:on {",
        "QComboBox::drop-down {",
        "QComboBox::drop-down:hover {",
        "QComboBox QAbstractItemView {",
        "QComboBox QAbstractItemView::item:selected {",
        "selection-background-color: {c['accent_soft']};",
        "border-color: {c['accent']};",
        "theme_module.get_theme_style = themed_style",
    )
    for fragment in required_theme_fragments:
        assert fragment in brand, fragment

    # The archive transport selector remains a normal QComboBox, so it receives
    # the same palette-aware field and popup styling as every other combo.
    assert "self.archive_method_combo = QComboBox()" in methods
    for label in ("1. FFmpeg", "2. Curl", "3. Без звука"):
        assert label in methods, label

    # Every shipped visual palette must participate through THEMES rather than
    # a hard-coded rose/light-only stylesheet.
    assert "THEMES[normalize_theme(key)]" in brand
    assert "#E83F8C" not in brand
    assert "#3478F6" not in brand
    assert "#18A9E6" not in brand

    print("CORE TESTS 3.0.12 THEMED COMBO OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
