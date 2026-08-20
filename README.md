# LinkVideo.Helper

Внутренний Windows-инструмент LinkVideo для работы с VPN-клиентами и RouterOS, диагностики и скачивания видеоархива.

Текущая release-candidate версия: **3.0.11**.

## Что хранится в репозитории

В Git находятся исходники, тесты, установщик и release-автоматика. Готовые EXE, старые сборки и исторические release-файлы в корне не хранятся: история уже доступна через Git и GitHub Releases.

Основные каталоги:

- `linkvideo_vpn_helper/` — приложение;
- `installer_next/` — актуальный установщик/деинсталлятор;
- `silent_updater/` и `patcher/` — механизм обновлений;
- `scripts/` — тесты, аудит и сборка;
- `docs/` — актуальная техническая документация.

## Запуск из исходников

Windows + Python 3.12:

```bat
run.bat
```

## Проверка перед релизом

Главная проверка:

```bat
python scripts\release_preflight.py
```

Она компилирует Python-код, запускает полный source-аудит и все `core_tests*.py`.

Полная Windows-сборка:

```bat
build_onedir.bat
powershell -ExecutionPolicy Bypass -File scripts\build_next_installer.ps1
installer_next\output\LinkVideo.Helper_Setup.exe --self-test
```

`--self-test` запускается на **точно том EXE**, который затем используется как RC/релиз: он без установки проверяет встроенный payload, очистку старого runtime, обязательные EXE и отсутствие встроенного FFmpeg.

Тот же pipeline автоматически выполняется в GitHub Actions. Публичный релиз не должен создаваться, пока RC не прошёл ручную проверку на рабочей Windows-системе.

## RC и обновления

Actions artifacts для RC не используются. На release-ветке поддерживается один приватный draft Release `rc-<version>`; следующая успешная сборка заменяет в нём Setup, поэтому старые RC не накапливаются.

Финальный update-channel находится в отдельном репозитории `WellJons/LinkVideo.Helper.Updates`: desktop Helper проверяет manifest, SHA-256 и Windows ProductVersion перед запуском обновления. Google Drive остаётся только переходным fallback-каналом для старых установок.

FFmpeg в Setup не входит: при первом FFmpeg-скачивании архива он загружается в пользовательский LocalAppData-кэш и затем переиспользуется.

> Репозиторий содержит внутренний код LinkVideo и должен оставаться приватным.
