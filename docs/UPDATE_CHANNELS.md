# Каналы обновлений LinkVideo.Helper

## Цель

Перевести обновления LinkVideo.Helper с Google Drive на GitHub без поломки уже установленных версий.

## Переходная схема

1. Старые версии Helper продолжают читать старый `version.json` из Google Drive.
2. Переходный релиз получает через Google Drive обычным способом.
3. В переходном релизе обновлятор уже знает два канала:
   - основной: GitHub release channel;
   - резервный: старый Google Drive manifest.
4. После того как переходный релиз установлен у сотрудников, новые версии публикуются через GitHub.
5. Google Drive оставляется fallback-каналом на ограниченный переходный период.

## Важно про приватный репозиторий

Исходный код `WellJons/LinkVideo.Helper` остаётся приватным.

Нельзя встраивать персональный GitHub token в desktop-приложение только ради скачивания assets приватного release: токен можно извлечь из установленной программы.

Поэтому бинарный update-channel должен быть доступен клиенту без секрета. Практичные варианты:

### Вариант A — отдельный публичный release-репозиторий

Например `WellJons/LinkVideo.Helper.Releases`:

- исходников нет;
- Issues/Wiki можно отключить;
- публикуются только manifest и release assets;
- приватный исходный репозиторий остаётся закрытым.

### Вариант B — update endpoint LinkVideo

Например `https://updates.linkvideo.ru/helper/...`:

- backend может забирать артефакты из приватного GitHub;
- desktop Helper не хранит GitHub credentials;
- можно централизованно управлять каналами stable/beta и отзывом релизов.

Для текущего этапа проще начать с отдельного публичного release-репозитория и позднее при необходимости перенести выдачу на собственный endpoint.

## Manifest

Рекомендуемый manifest:

```json
{
  "version": "3.0.8",
  "channel": "stable",
  "setup_url": "https://.../LinkVideo_VPN_Helper_Setup.exe",
  "patch_url": "https://.../LinkVideo.Helper_Patch.exe",
  "sha256": "...",
  "notes": "...",
  "min_patch_from": "3.0.7",
  "published_at": "2026-08-15T00:00:00Z"
}
```

`patch_url` может быть `null`, если версия распространяется только полным Setup.

## Безопасность

Helper перед запуском скачанного файла обязан проверить:

1. HTTP status / фактическую загрузку файла;
2. SHA-256 из manifest;
3. Windows ProductVersion;
4. что версия файла соответствует `version` из manifest.

Только после этого разрешается запуск Setup/Patch.
