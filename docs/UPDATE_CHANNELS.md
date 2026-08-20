# Каналы обновлений LinkVideo.Helper

## Исходный репозиторий

`WellJons/LinkVideo.Helper` остаётся приватным и содержит только исходный код, тесты и release-автоматику. Desktop-приложение не получает GitHub token для доступа к приватному репозиторию.

## RC

На активной release-ветке CI после полного успешного прогона поддерживает **один** приватный draft/prerelease с тегом `rc-<version>`.

Каждая следующая успешная RC-сборка:

1. пересобирает runtime и Setup с нуля;
2. запускает source-аудит, regressions, Ruff и `go vet`;
3. запускает `LinkVideo.Helper_Setup.exe --self-test` на точном произведённом EXE;
4. пересчитывает SHA-256;
5. заменяет Setup в существующем `rc-<version>` вместо накопления Actions artifacts/старых RC.

RC не является публичным обновлением и требует ручной проверки на Windows.

## Финальный релиз

Только version tag `vX.Y.Z` создаёт/обновляет приватный final draft Release. В нём сохраняются:

- точный `LinkVideo.Helper_Setup.exe`;
- `Uninstall.exe`;
- приватный payload ZIP;
- payload manifest для будущих дифференциальных патчей.

После создания final draft временный `rc-<version>` удаляется вместе с RC-тегом.

Публичная публикация выполняется отдельно и переносит проверенный Setup/manifest в `WellJons/LinkVideo.Helper.Updates`.

## Production update-channel

Desktop Helper читает публичный manifest из:

`WellJons/LinkVideo.Helper.Updates/main/update-manifest.json`

Перед запуском скачанного обновления Helper обязан проверить:

1. успешную загрузку файла;
2. SHA-256 из manifest;
3. Windows ProductVersion;
4. совпадение версии файла с `version` из manifest.

Google Drive сохраняется только как переходный fallback для старых установок и не является основным каналом новых версий.

## Почему не GitHub Actions artifacts

Actions artifacts — временное CI-хранилище с квотой и не должны быть частью production release-chain. Они больше не используются для RC LinkVideo.Helper. Это одновременно устраняет зависимость от квоты и не создаёт множество временных сборок.
