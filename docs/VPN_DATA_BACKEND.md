# Центральное хранилище VPN-клиентов

Google Sheets удобно использовать как временный аудит/экспорт, но не как основную базу для автоматизации VPN.

## Рекомендуемая архитектура

**PostgreSQL** как источник истории и централизованного состояния.

Постоянно работающий `LinkVideo.VPNSync` на сервере/VM:

```text
RouterOS vpn01..vpnNN
        ↓
LinkVideo.VPNSync
        ↓
PostgreSQL
        ↓
LinkVideo.Helper / отчёты / резервные выгрузки
```

Helper не должен быть обязан работать круглосуточно. Sync-сервис работает независимо от рабочих компьютеров сотрудников.

## Почему PostgreSQL лучше Google Sheets

- транзакции;
- уникальные ограничения;
- нормальные связи клиент ↔ сервер ↔ NAT-порты;
- история изменений;
- безопасные параллельные обновления;
- SQL-поиск и отчёты;
- автоматические backup/restore;
- проще перейти к RADIUS в будущем;
- можно поверх базы сделать web/API и при необходимости экспортировать представления обратно в Google Sheets.

## Минимальные таблицы

### vpn_servers

- `id`
- `hostname` UNIQUE
- `country`
- `enabled`
- `active_l2tp`
- `cpu_percent`
- `ram_percent`
- `lv_version`
- `lv_running`
- `quarantine_enabled`
- `last_sync_at`

### vpn_clients

Уникальный ключ: `(server_id, login)`.

- `id`
- `server_id`
- `login`
- `remote_address`
- `profile`
- `disabled`
- `lifecycle_state`
- `last_seen_at`
- `first_seen_at`
- `deleted`
- `deleted_at`
- `last_sync_at`

### vpn_nat_ports

Уникальный ключ: `(server_id, external_port, protocol)`.

- `id`
- `server_id`
- `client_id`
- `external_port`
- `internal_port`
- `protocol`
- `to_address`
- `disabled`
- `deleted`
- `last_sync_at`

Это ограничение позволит на уровне БД обнаруживать ситуацию, когда один внешний TCP-порт на одном VPN назначен двум учёткам.

### vpn_change_log

- `id`
- `server_id`
- `client_id`
- `event_type`
- `old_value` JSONB
- `new_value` JSONB
- `source` (`sync`, `helper`, `routeros_automation`)
- `created_at`

### vpn_backups

- `id`
- `server_id`
- `created_at`
- `sha256`
- `storage_key`
- `changed_since_previous`
- `status`

Сами полные backup-файлы лучше хранить не в PostgreSQL, а в S3-совместимом Object Storage; в БД хранить метаданные и путь к объекту.

## Пароли

PPP-пароли не рекомендуется хранить открытым текстом в общей рабочей таблице.

Если они необходимы для аварийного восстановления:

- отдельное зашифрованное поле/хранилище;
- доступ только серверному Sync/Backup сервису;
- шифрование ключом, который не лежит в desktop Helper и не хранится в GitHub.

## Возможные managed PostgreSQL

Для старта можно использовать любой управляемый PostgreSQL: Supabase, Neon, Yandex Managed Service for PostgreSQL, собственный PostgreSQL на LinkVideo VM и т.п.

Для внутренней инфраструктуры LinkVideo предпочтителен вариант, где доступ к БД можно ограничить VPN/private network и где есть автоматические резервные копии.
