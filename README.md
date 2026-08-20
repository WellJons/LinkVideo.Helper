# LinkVideo.Helper

Внутренний Windows-инструмент LinkVideo для работы с VPN-клиентами и RouterOS, диагностики и скачивания видеоархива.

Текущая release-candidate версия: **3.0.11**.

## Структура

В Git находятся исходники, тесты и release-автоматика. Готовые EXE, старые сборочные BAT-файлы и исторические release-notes в корне не хранятся: история уже есть в Git/GitHub Releases.

- `linkvideo_vpn_helper/` — приложение;
- `installer_next/` — актуальный установщик и деинсталлятор;
- `silent_updater/`, `patcher/` — обновления;
- `scripts/` — аудит, regressions и сборка;
- `docs/` — актуальная техническая документация.

## Запуск из исходников

Windows + Python 3.12:

```bat
run.bat
```

## Полная проверка релиза

Есть один авторитетный локальный entry point:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_release.ps1
```

Он выполняет весь release gate:

1. проверяет чистоту tracked source tree и версию;
2. запускает `release_preflight.py`: compile Python, полный source-аудит и все `core_tests*.py`;
3. запускает Ruff correctness checks;
4. запускает `go vet` для installer/patcher/updater;
5. пересобирает PyInstaller runtime с нуля;
6. собирает актуальные Setup/Uninstall и differential patch pipeline;
7. запускает `--self-test` на **точно том Setup.exe**, который пойдёт в RC/final draft;
8. проверяет Windows ProductVersion приложения, Setup и Uninstall;
9. считает SHA-256;
10. создаёт локальный `release_candidate/verification.json` и RC EXE.

`--self-test` не устанавливает программу: он в temp-каталоге проверяет встроенный payload, staging/атомарную замену, восстановление после прерванного upgrade, обязательные EXE и отсутствие встроенного FFmpeg.

## CI и RC

Обычные development-коммиты вне PR больше не запускают тяжёлую Windows release-сборку. Полный CI запускается для PR в `main`, отдельной временной ветки `rc/**`, `v*` final tag либо вручную через `workflow_dispatch`. PR-проверка только подтверждает код; приватный RC создаётся исключительно из ветки `rc/**`.

Actions artifacts для RC не используются. У версии существует один приватный draft Release `rc-<version>`; следующая успешно проверенная RC-сборка заменяет его содержимое вместо накопления старых сборок.

После создания final draft временный RC Release/tag удаляется. Поскольку final tag повторно собирает EXE, перед **Publish Release** обязательно вручную проверяется именно точный `LinkVideo.Helper_Setup.exe` из final draft. Только после этого он попадает в публичный GitHub-канал.

## Канал обновлений

Production manifest находится в отдельном `WellJons/LinkVideo.Helper.Updates`. Helper проверяет SHA-256 и Windows ProductVersion перед запуском скачанного обновления. Google Drive остаётся только переходным fallback для старых установок.

Публичный workflow дополнительно сверяет Setup с `verification.json` и commit тега. Он не разрешает понизить версию manifest или заменить уже опубликованную версию другим EXE.

Релиз 3.0.11 распространяется только полным Setup (`patches: {}`). Это сознательный выбор надёжности: атомарный полный установщик безопаснее текущего дифференциального patcher при внезапном прерывании записи файлов.

FFmpeg в Setup не входит: при первом FFmpeg-скачивании архива он загружается в LocalAppData-кэш пользователя и затем переиспользуется.

> Репозиторий содержит внутренний код LinkVideo и должен оставаться приватным.
