# Каналы обновлений LinkVideo.Helper

## Исходный репозиторий

`WellJons/LinkVideo.Helper` остаётся приватным и содержит только исходный код, тесты и release-автоматику. Desktop-приложение не получает GitHub token для доступа к приватному репозиторию.

## RC

Тяжёлая Windows release-проверка не запускается на каждом development-коммите. Она обязательна для PR в `main`, чтобы результат был виден до merge. Когда код готов к кандидату, создаётся/перемещается временная ветка `rc/<version>` на тот же проверяемый commit; только эта ветка после успешного полного CI создаёт или заменяет приватный RC.

Каждая успешная RC-проверка:

1. выполняет единый `scripts/verify_release.ps1`;
2. пересобирает runtime и Setup с нуля;
3. запускает source-аудит, regressions, Ruff и `go vet`;
4. компилирует differential patch pipeline;
5. запускает `LinkVideo.Helper_Setup.exe --self-test` на точном произведённом EXE;
6. проверяет ProductVersion и SHA-256;
7. создаёт `verification.json`;
8. заменяет Setup/отчёт в **одном** приватном draft/prerelease `rc-<version>`.

Actions artifacts для RC не используются. RC не является публичным обновлением и требует ручной проверки на Windows против реальных RouterOS, Google Sheets и архивных endpoint'ов.

## Финальный релиз

Только version tag `vX.Y.Z` повторно проходит тот же verifier и создаёт/обновляет приватный final draft Release. В нём сохраняются:

- точный `LinkVideo.Helper_Setup.exe`;
- `Uninstall.exe`;
- `verification.json`;
- приватный payload ZIP;
- payload manifest для будущих дифференциальных патчей.

После создания final draft временный `rc-<version>` удаляется вместе с RC-тегом. Final tag повторно собирает Setup, поэтому перед **Publish Release** обязательна ручная smoke-проверка именно точного `LinkVideo.Helper_Setup.exe` из final draft. Только нечерновой и не prerelease-релиз может попасть в публичный update-channel.

## Production update-channel

Desktop Helper читает публичный manifest из:

`WellJons/LinkVideo.Helper.Updates/main/update-manifest.json`

Перед запуском скачанного обновления Helper обязан проверить:

1. успешную загрузку файла;
2. SHA-256 из manifest;
3. Windows ProductVersion;
4. совпадение версии файла с `version` из manifest.

Публикатор до записи manifest сверяет Setup с `verification.json` и commit финального тега, затем скачивает Setup уже из публичного Release и повторно проверяет SHA-256. Он блокирует откат manifest на более старую версию и замену уже опубликованной версии другими байтами. Manifest после публикации также проходит проверку версии, URL и SHA-256.

Для 3.0.11 production manifest всегда содержит пустой объект `patches`. Все 3.0.10 получают полный Setup с атомарной заменой runtime. Дифференциальные патчи не публикуются до появления эквивалентного восстановления после сбоя питания в середине операции.

Google Drive сохраняется только как переходный fallback для старых установок и не является основным каналом новых версий.

## Почему не GitHub Actions artifacts

Actions artifacts — временное CI-хранилище с квотой и не должны быть частью production release-chain. RC хранится одним приватным draft Release, а generated-файлы остаются вне Git благодаря `.gitignore`.
