from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def main() -> int:
    path = "linkvideo_vpn_helper/ui/cloud_settings_integration.py"
    source = read(path)
    ast.parse(source, filename=path)

    assert 'QCheckBox("Вход сохранён через Windows DPAPI")' in source
    assert "self.cloud_remember.setChecked(True)" in source
    assert "self.cloud_remember.setEnabled(False)" in source
    assert "remember=True" in source
    assert "Пароль защищён Windows DPAPI" in source
    assert "автоматической истории и восстановления" in source
    assert "Логин VPNSync (не SSH)" in source
    assert "Ubuntu/SSH (Termius)" in source
    assert "Сервер доступен, вход не выполнен" in source
    assert "UTC+7" in source

    print("CLOUD_CREDENTIALS_PERSISTENCE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
