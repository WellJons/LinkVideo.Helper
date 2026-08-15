# LinkVideo.Helper 3.0 — Google Sheets sync schema

Google Sheets рассматривается как зеркало и история, а RouterOS — как источник истины.
Старые строки не считаются актуальными до первой полной сверки.

## Ключ записи
`server + login`

## Лист VPN_Clients
- server
- country
- login
- remote_address
- profile
- disabled
- online
- lifecycle_state
- last_seen
- first_seen
- updated_at
- deleted
- deleted_at
- last_sync_at

## Лист VPN_Ports
- server
- login
- external_port
- internal_port
- protocol
- disabled
- deleted
- updated_at

## Лист VPN_Servers
- server
- country
- active_l2tp
- secrets
- cpu
- ram
- nat_rules
- lv_version
- lv_running
- quarantine_enabled
- last_sync
- status

## Лист Sync_Log
- timestamp
- server
- added
- changed
- enabled
- disabled
- deleted
- errors

## Первая синхронизация — FULL RECONCILIATION
1. Опросить каждый включённый VPN-сервер.
2. Сопоставить строки по `server + login`.
3. Есть на RouterOS, нет в Sheets → создать.
4. Есть в Sheets, нет на RouterOS → `deleted=true`, строку не удалять.
5. Есть в обоих → RouterOS перезаписывает актуальные поля.
6. NAT/порты синхронизировать отдельным листом.
7. Старые данные Google Sheets до завершения этой процедуры считаются непроверенными.

Пароли в рабочий лист не выгружать. Полный аварийный backup с паролями хранить отдельно и шифровать.
