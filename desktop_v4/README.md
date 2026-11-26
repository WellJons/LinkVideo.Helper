# LinkVideo.Helper 4.0.0-alpha.3

Отдельное приложение Electron. Не заменяет 3.x и не меняет его settings/update channel. Python сохранён: оригинальные сервисы VPN, Billing, B2O, Archive, Agent, TemplateStore. Встроенные хосты строго ограничены. Пароли рабочей и административной учётки вводятся отдельно и не записываются на диск.

## Запуск
Python 3.12 + Node 22 из корня ветки:
```powershell
python -m pip install -r requirements.txt
cd desktop_v4
npm install
npm start
```
В релизном portable-пакете Python должен быть встроен (отдельный worker.exe). Проверка интерфейса: демонстрация без подключения и без поддельных данных.

## Статус переноса
- VPN: вход, поиск, карточка, реестр встроенных серверов, показатели, неактивные клиенты; создание batch, PPP/NAT/пароль/сессия с одноразовым подтверждением (запись заблокирована по умолчанию).
- LV: статус, подготовка и commit после FULL backup. Включение записи требует отдельного полевого теста.
- CRM/B2O: независимый вход, поиск/карточка CRM, каталог, история, смена тарифа и статуса; архивный поиск, MP4, диагностика с двумя группами сравнений; агент — поиск и настройки.
- Шаблоны: CRUD, поиск, избранное, копирование; отдельная локальная база.
- Невыполненный паритет: безопасное удаление/восстановление VPN, VPN cloud activity, расширенный реестр, WinBox, расписание/Google Sheets и служба Windows, массовые действия и дополнительные сценарии CRM/B2O (перенос камеры, создание B2O), импорт DOCX/изображений, хранение учётных данных, миграция и отдельный updater. Альфа не является заменой 3.x.

Все сетевые write-сценарии блокируются, если не задан LV4_ENABLE_WRITES=1. Не выставлять на рабочих ПК без согласованного теста на выделенной учётке. CI не обращается к production. Изменения не повторяются автоматически; неизвестный результат требует проверки.

## Производительность
Chromium отрисовывает оболочку, Python отдельный дочерний процесс. Сетевые операции не блокируют UI, один foreground запрос за раз; отмена кооперативная, работает только там, где её поддерживает исходный сервис. Измерения потребления RAM, cold-start и latency на рабочем ПК пока не проводились.

## Сборка Windows
Workflow `Helper 4 Electron Preview` формирует отдельный portable x64 и самостоятельный Setup.exe после проверок RPC, UI и запуска bundled worker. Установка per-user в отдельную папку; стабильная версия и её updater не участвуют. Проверить статус и артефакты в GitHub Actions.

Дополнительно: явное копирование VPN в нативный clipboard, WinBox без передачи пароля в командной строке, FULL backup в профиле 4.0, аварийный Sheets-источник из выбранного JSON, импорт/вложения шаблонов, история VPN через самостоятельную облачную сессию, B2O read/create, диагностика камеры, перенос CRM. Удаление и восстановление подключены к сервисам, но также требуют LV4_ENABLE_DESTRUCTIVE=1 и отдельной проверки. Автоматическая установка системной службы и перенос сохранённых секретов всё ещё не выполнены.

## 4.0.0-alpha.3
Новый дизайн Workspace 04: графит/золото, векторная айдентика, сгруппированная навигация, компактные таблицы, прогресс и точный DVR-таймлайн, светлая тема; установщик с фирменными иллюстрациями и отдельным путём установки. Диагностика автоматически подбирает камеры vcore при пустом ручном списке; агент использует подтверждённые коды ориентации 0–3. Рабочие операции записи по-прежнему требуют отдельной полевой проверки. Автообновление, ряд массовых сценариев, live-превью и полная миграция данных 3.x ещё не завершены.

Следующий блок паритета: выбор least-loaded VPN по региону, предпросмотр B2O для CRM и агента, проверка потока RTSP/DVR, массовая смена тарифов с раздельными подтверждениями результата и остановкой после первой неопределённой операции. Превью/поток используют подтверждённые исходные сервисы, не выдаются подписанные URL или токены. Сетевые операции не тестировались на production.

Дополнительные архивные кластеры (Здравсервис, Goodline, Провзор) включаются явно, настройки серверов архива сохраняются отдельно от токенов B2O. Перед операциями с существующим NAT-портом проверяется результат инспекции конфликтов; при недоступности проверки запись запрещена.

## 4.0.0-alpha.4 — обновления
Настройки проверяют строго preview-теги 4.x источника WellJons/LinkVideo.Helper. Скачивается только точный Setup.exe, подтверждённый по размеру и SHA-256 из GitHub API, без перезаписи файлов. Ручной запуск, поскольку подпись издателя ещё не настроена. Публикация prerelease только при явном создании соответствующего тега и успешном CI, не меняет latest 3.x. Добавлена проверка фактической установки, запуска и удаления в CI. Публикация не выполнялась.

Миграция локальной библиотеки 3.x: выбор templates.sqlite3 через нативный диалог, read-only источник, транзакционный импорт с изображениями и избранным, пропуск одинаковых записей, отмена без частичной записи. Мониторинг VPN использует параллельные вызовы analyze_server_quick до 6 потоков, агрегирует только реальные ответы и показывает частичные ошибки.

4.0.0-alpha.5 (development): мониторинг общего RX/TX VPN только при открытой карточке, сверка NAT/камер по существующему matcher B2O без RTSP URL и паролей, общий скан жизненного цикла, фильтрация и массовое включение/выключение с повторной проверкой состояния. Sheets — ручной аварийный резерв; существующая задача 3.x только диагностируется. Массовое удаление не включено до проверки recovery.

Preview alpha.5: «Скачать и установить» скачивает в отдельный пользовательский cache, повторно сверяет размер и SHA-256 перед запуском обычного интерактивного установщика, требует нативного подтверждения и закрывает приложение только после успешной передачи запуска Windows. Подпись издателя не настроена, silent install и автоматический обход системных предупреждений не используются. Публичный prerelease не опубликован.

Паритет с v3.0.27: произвольный стартовый Remote Address и подсказка свободного логина, копирование созданного VPN, просмотр NAT-счётчиков при наличии исходных данных, точная проверка конфликтов, обзор и массовое управление LV с FULL backup, совместимость compact NAT восстановления. Параметры облачного аудита проецируются без паролей и токенов. Подтверждения на реальных серверах пока не выполнялись.

## alpha.6 / release parity baseline
Золотой эталон — тег `v3.0.27` (8849d1c1b50ec655ff66c503a6cd1235ed620d45). Сравнение с main: отличается только удалённым одноразовым workflow публикации. Переносим сервисы из этого тега, а не изменяем 3.x. Новый журнал 4.0 пишет только операцию, результат и время (с ротацией), поиск камер по точному владельцу B2O, проверка сервера из настроек, отображение деталей аудита. Порядок NAT compatibility / role guard приведён к релизу, чтобы не блокировать внутренний rollback. Релиз 4.0 пока не опубликован; права записи по умолчанию выключены.

Сверка релиза: копирование шаблона как plain text + HTML со встроенными PNG + нативное первое изображение; отдельное копирование фото и фильтр избранного. Ручная команда FFmpeg использует те же `{input}`/`{output}`, но ограниченный перечень параметров (не произвольный запуск FFmpeg); после операции команда сбрасывается. Повторная загрузка и переход к сохранённому файлу. Вставка нескольких картинок зависит от мессенджера.

### Массовое удаление (alpha.7)
Выбор до 50 неактивных учёток, точная фраза подтверждения, проверка прав и даты последней активности. Проверки до и после FULL backup: сравниваются конфигурации PPP Secret/Profile/NAT (счётчики NAT не участвуют в хеше), копия сверяется по manifest SHA-256. Оригинальный delete_client вызывается только после проверок; повторная проверка отсутствия всех объектов, остановка при неизвестном результате. Нет автоматического повтора записи. RouterOS не предоставляет общей транзакции, поэтому проверка на стенде обязательна. Запись и destructive по умолчанию выключены.

VPNSync: отдельное зашифрованное Windows-хранилище по явному согласию, нативное подтверждение HTTP, сохранённое подключение можно восстановить из настроек. В соответствии с cloud_activity_nav 3.0.27 глобальная страница истории удалена, журнал VPN показывается только для открытой учётки (admin). PostgreSQL recovery подтверждается повторным чтением живого MikroTik; после успешной операции открывается карточка.

Alpha.7 закрывает проверку доступа AdminChats в UI и ядре, включая быстрые команды и карточки обзора. Добавлены offline-тесты удаления: неподтверждённый backup, конфликт адреса/активная сессия, изменение конфигурации, неизменность revision при росте NAT-счётчика, частичное удаление, массовая операция и остановка после неизвестного результата. Smoke-тест роли не авторизует worker и не обращается к корпоративным адресам. Старая SYSTEM-задача проверяется по фактическому состоянию/receipt, в настройки добавлен переход в системный планировщик. Создание/изменение 3.x задачи не переносится в per-user EXE, Google Sheets остаётся аварийным ручным резервом. Рабочие записи и удаление требуют стендовой проверки.

### CRM parity / alpha.8 development
Повторный вход CRM или B2O с корпоративной учёткой не сбрасывает подтверждённую вторую сессию; изменение учётных данных отзывает обе. Токен не сохраняется на диск. В каталоге используется оригинальный `billing_tariff_filter` (дни архива не равны trial days; точный битрейт, валюта, страна и тип). Фильтры работают на загруженном snapshot, без нового запроса. Полная карточка клиента, повторное чтение камер, отдельные ID CRM/B2O, цены/скидки и подтверждённый статус. Добавлены offline-тесты фильтров и повторного входа. Без обращения к рабочему API.

CI alpha.8 дополнен четырьмя исходными офлайн-проверками релиза 3.0.27: контракт авторизации, токен-транспорт (mock session), парсеры аккаунта и камер. Вход и выход CRM не меняют рабочую сессию MikroTik; явное «Очистить сохранённый вход» отдельно удаляет локальную запись. Фактический HTTP-вход в корпоративные системы пока не выполнялся.

### Archive parity / alpha.9 development
Возвращены 5 разделов отчёта диагностики (итог, разрывы, переезды UTC+7, сравнение, технические детали), копирование, список источников и ограничение количества vcore камер. Сравнения выполняются в одном epoch-окне с контролем оператора/фактического vcore; HLS без DVR не считается подтверждённым разрывом. Непосредственное скачивание связано с одноразовым идентификатором актуального результата поиска; диагностика сбрасывает старый результат. Имя файла строится из ID и локального периода. Технические отчёты не включают сырые ошибки/URL/подписи. Добавлены офлайн-тесты проекций HLS, DVR и переездов.

Alpha.9: настройки агента читаются отдельно, состояние выбранной камеры сбрасывается до нового поиска; проверка исходного значения перед записью, повторное чтение на подтверждённом сервере, пресеты 512/1024/2048/4096/8192, копирование сведений. Time/NTP/SyncMode отслеживаются по этапам без автоматического повтора, не выдаются за readback. Диагностика камеры копируется без URL или паролей. Системная служба 3.x по-прежнему не мигрирована. Результаты проверок только offline.

Recovery parity: независимый поиск PostgreSQL, Sheets только по явному выбору, проверка актуальности аварийной записи, readback восстановленных портов и таблица заменённых внешних NAT-портов. Локальный список FULL копий и проверка SHA-256 по manifest, без чтения конфиденциальных данных в renderer. Проверка хеша не подтверждает подлинность manifest или возможность полного восстановления.

Alpha.10: проверка конфликта PostgreSQL возвращает только тип и допустимый номер/IP, список портов привязан к подтверждению и проверяется после восстановления. Для аварийного восстановления Sheets проверенная строка закрепляется в памяти, очистка архива откладывается до readback; изменение метаданных/ошибка очистки оставляют резервную строку. Использование оригинального сервиса не делает RouterOS атомарным. В 3.0.27 Google Sheets является ручным аварийным экспортом; отдельная аварийная кнопка восстановления в 4.0 — явное дополнение, с проверкой live MikroTik, не основной маршрут. SYSTEM-служба не устанавливается в пользовательском preview. Проверка исключительно offline до стенда.

Карточка VPN: тихое обновление каждые 15 сек через targeted get_client без глобального NAT scan, отдельная от форм перерисовка, синхронизация результатов поиска, остановка таймера при навигации/выходе, сохранение новой группы портов и копирование. При изменившихся портах предыдущая сверка B2O сбрасывается; операции записи снова проверяют конфликты. Офлайн-тест проекции не содержит секретов.

Создание VPN: отдельная точная проверка логина (ошибки не означают свободный логин), сохранение стартового IPv4 по каждому доверенному серверу, групповое копирование созданных VPN через native clipboard с подтверждением. Шаблоны: вставка изображения из системного буфера только по явному действию, с временным PNG и ограничением размера. Проверка Windows и целевых приложений ещё необходима.

### Фоновый режим 4.0 (alpha.11)
Перенос в отдельный опциональный администраторский установщик `LinkVideo.Helper-4-Background-Setup`. Обычный desktop EXE не повышает свои права и не может обработать фоновые аргументы. Только worker в фиксированном Program Files, отдельные задача/registry/state, DPAPI machine + ACL Administrators/SYSTEM, фиксированные команды Windows; прежняя задача 3.x не изменяется, одновременное включение заблокировано. Интервалы 6/12/24, проверка UAC, изменение расписания, явный запуск, отключение. Рабочий Google Sheets экспорт только после явного согласия; PostgreSQL остаётся основным. Компонент не подписан, ещё требует проверки установки, UAC, ACL, задач и восстановления на изолированном Windows стенде; CI пока только проверяет отсутствие доступа к фоновому режиму из пользовательской копии. Не использовать для production до этих проверок.

Background дополнительно проверяет владельцев/DACL и точный путь, включая запрет hardlink и reparse points. Отдельный установщик фиксирует ACL и владельца; замена требует отключения и удаления старого компонента. CI запускает установку/удаление без создания расписания; регистрация и реальный экспорт требуют отдельной проверки.

UI review: cached navigation, 170 ms transitions, 140 ms toast, wrapping and local table scrolling, narrow/low-window adaptation, focus/table-scroll preservation. Foreground requests wait for the current lightweight poll; route change invalidates pending UI confirmations and prevents rendering stale results in another card. Tests of populated screens follow in CI.

Alpha.12: restored all eight theme choices of 3.0.27 in new visual system, plus two 4.0 palettes. Offline layout regression uses synthetic long Cyrillic data in VPN, billing, archive diagnosis, camera, agent, templates, recovery and checks all navigation targets at 820×600, 1024×768 with 125% zoom, 1440×900. Captures billing screenshots and DOM overflow report under layout/; no production requests. CI result pending.

Camera parity: returned B2O preview, installation address, original transcode policy, reference codecs/bitrate, authorized camera-streams-urls RTSP probe with verified FFmpeg bootstrap. Separate B2O metadata and measured values; correct bitrate units. Preview/live calls are bound to returned camera ID and operator, invalidated on B2O logout. Network behavior has not been verified on real cameras.

### Packaging size optimization / alpha.13
В Windows worker оставлены PySide6-Essentials/QtCore для QSettings и сервисов. Графические QtGui/QtWidgets/QtNetwork/QtWebEngine исключены из PyInstaller; изображения шаблонов масштабирует штатный Electron/nativeImage (PNG/JPEG/WEBP/GIF). У Chromium оставлены ru/en-US/en-GB; лицензии и рабочие системные DLL не удаляются. CI сохраняет size-report.txt, показывает крупнейшие файлы и проверяет, что в worker нет Qt6Gui/Qt6Widgets/Qt6Network. Корпоративные запросы не затрагиваются.

### Desktop navigation parity (development after alpha.13)
За основу навигации принят `linkvideo_vpn_helper/ui/main_window.py` из desktop 3.0.27: девять рабочих разделов в исходном порядке и настройки. После входа открывается «Создание клиента», а не обзор веб-ориентированной рабочей области. Дополнительные экраны Electron не удалены: камеры внутри поиска, CRM/B2O внутри клиентов и тарифов, LV и резервирование внутри VPN-серверов. Обзор и быстрый переход сохранены через Ctrl+K. Ограничения ролей и RPC не изменены. Smoke проверяет порядок, названия, старт, переходы и запрет инфраструктурных маршрутов для сотрудника. Это первый шаг восстановления интерфейса, не заявление о полном паритете форм, операций или оформления.

Создание клиента приведено к структуре оригинальной формы: страна → сервер → логин → счётчики (3 порта, 1 учётка) → создание вручную или с автоподбором в выбранной стране. Список доверенных серверов загружается автоматически после реального входа. Недоступная страна не подменяется другой. Результат в отдельной карточке, данные копируются через прежний нативный clipboard. IP берётся из настроек выбранного сервера, без сброса сохранённого начала пула; одноразовое подтверждение перед записью сохранено. Офлайн-тест проверяет фильтрацию регионов, отсутствие fallback и сохранение IP. Подготовка/создание блокируются в read-only режиме; стендовая проверка записи необходима.

Поиск и управление: восстановлены вкладки VPN/информация по камере, компактная строка клиента с возвратом к поиску, отдельные прокручиваемые панели данных/портов и действий. Тихое обновление 15 сек. изменяет только карточку, ручное «Обновить статус» не закрывает её, сохраняет мониторинг и результаты сверки до изменения портов. При перерисовке сохраняются позиции документа, обеих панелей и вложенных таблиц; фокус в форме управления при тихом обновлении не меняется. Регрессионный smoke с 48 портами и 25 событиями проверяет обе прокрутки, обновление статуса, фокус, введённый текст и возврат к поиску. Рабочие серверы в тесте не используются.

### Desktop billing parity (development)
Восстановлены три рабочие вкладки оригинального TariffsPage: поиск клиента, создание B2O и все тарифы. Карточка клиента открывается на «Услуги», общая информация отделена; финансовые операции и документы не имитируются. Таблица услуг заменена карточками с ID CRM/B2O, превью по запросу, ценой, скидкой, статусом и отдельными действиями по выбранной камере. Каталог сохраняет исходные фильтры, область client/all не смешивается. Смена тарифа, статуса, перенос и массовые изменения используют существующие RPC и подтверждения; новый B2O-аккаунт создаётся только по проверенному контракту. Пароль не сохраняется в renderer после подготовки. Добавлена офлайн-проверка вкладок и карточек. Автоматическое превью первых камер, доступ к полному B2O read-back и копирование созданных клиентских реквизитов пока требуют отдельной реализации; реальное API не проверялось.

Дополнение: перенесена проверка существующего B2O-аккаунта по ID; изменение статуса CRM передаёт дату/комментарий и предлагает только противоположный подтверждённому статус. При недоступном readback массовой операции результат сохраняется отдельно от карточки.

### Archive and New Agent desktop workflow
Архив: отдельные параметры и папка сохранения, исходный 5-минутный период, выбор папки через нативный диалог Electron, отдельные формы поиска и диагностики, дополнительные камеры адреса с добавлением/удалением строк, отчёт с сохранением прокрутки. Папка хранится в текущем сеансе; конечный MP4 по-прежнему подтверждается SaveDialog. Новый Агент: рабочая панель поиска/настроек слева, сведения/кадр/результаты справа, кнопки режимов и ориентации. Сохранены исходный DVR/AgentService, подтверждения и ограничения записи. Smoke проверяет наличие форм и переключение режимов без сети.

### Support library / VPN clients
Шаблоны: двухпанельная библиотека и выбранный ответ, избранное/категории, редактирование с сохранением черновика в ходе перерисовки, импорт DOCX, перенос 3.x, вложения и копирование через существующий SQLite/IPC. При открытии раздела автоматически читается локальная библиотека. Вложения новой записи добавляются после сохранения. VPN-клиенты: возвращено название и блок списка учёток; выбор целей сбрасывает предыдущую подготовку массового изменения.

### Settings and infrastructure desktop parity
Настройки разделены по исходным пяти вкладкам. Возвращены сетка выбора тем, предпросмотр, отдельные облачные и архивные параметры, реестр VPN и фоновая служба, страница версии/обновлений и локального журнала. В VPN-серверах восстановлены колонки и общие/индивидуальные действия FULL backup/LV, легенда состояний, подтверждение изменений и отображение ошибок. LV и ресурсы запрашиваются отдельно; неподтверждённое состояние не подменяется. Все основные страницы 3.0.27 теперь имеют Electron-представление, но полное функциональное соответствие и эксплуатация на реальной инфраструктуре не подтверждены.

Дополнительное восстановление функций: B2O access-check использует read-only адаптер, сообщение с реквизитами созданного аккаунта копируется напрямую через main-process, только после успешного commit, без обратной выдачи пароля renderer. Срок 5 минут, очистка при входе/выходе/повторной авторизации. Папка архива теперь сохраняется в отдельном профиле 4.0, агент пытается получить кадр после поиска при доступной B2O-сессии. Реальные записи не выполнялись.

Поиск камеры теперь повторно использует оригинальный search_all_cameras (логин/точный ID/точный серийный номер). Карточка открывается в текущем разделе и возвращается к списку подтверждённых совпадений. Добавлены офлайн-тесты унифицированного поиска и проекции B2O access-check. Превью списка камер пока не подгружаются автоматически, кадр доступен в карточке.

Превью списков: фоновые read-only запросы на первые 12 камер с лимитом 8 MiB renderer-кэша, остановкой при смене карточки/раздела, ожиданием пользовательской операции и отсутствием сети в smoke-режиме. ID результата поиска проверяется в Python worker перед выдачей превью; тест проверяет отказ для постороннего ID. Добавлен переход из VPN-клиентов в карточку поиска.

Карточка VPN: вместо универсального выпадающего списка возвращены выбор отдельного NAT-порта, кнопки управления выбранным портом, счётчики, подтверждённые ID B2O и раскрываемые группы NVR. При обновлении выбранный порт и позиции обеих панелей сохраняются; изменение выбора инвалидирует подготовленную операцию. Автоматическая сверка B2O при открытии выполняется при подтверждённой сессии, ручная повторная сверка остаётся. Пароль VPN не выводится renderer; штатное копирование данных идёт через native clipboard. Добавлен smoke на сохранение выбранного порта.

Подготовленная операция привязана к поколению локальных параметров, поэтому смена выбора во время подготовки не покажет подтверждение для устаревшей цели. Реквизиты B2O очищаются из объекта main-process при завершении срока и явном сбросе.

### UI audit and visual QA
- Foreground RPC owns the execution slot before waiting for a background read; duplicate clicks are rejected; thumbnail scheduling keeps one timer. B2O confirmation expires even while native prompt is open.
- Progress display is an overlay rather than inserting a row into the workspace. Active section clicks do not reset scroll. Search draft/mode and semantic focus survive non-navigation rendering. Motion uses short opacity/transform transitions on a few visible blocks, no full-workspace transform; reduced-motion cancels current effects. Invalid image uses a stable placeholder.
- Unified editor/input styling, theme swatches, consistent active tabs, compact sidebar at narrow effective viewport, content-width responsive layout, focus outlines. No external fonts/assets.
- Offline smoke now checks 100%, 125%, 150% scaling, controls outside viewport, draft/focus, no scroll jump from progress, reduced motion, foreground lock and renderer runtime exceptions. Diagnostics artifact captures all primary and secondary sections. Python compile covers original package plus adapters; JavaScript check includes layout script. No production API writes.

Дополнительно: подтверждение показывает оставшийся срок действия, по истечении автоматически блокируется и освобождает фоновые чтения. Черновики строк поиска CRM, камер и шаблонов не сбрасываются при служебном обновлении. Положение курсора/выделение текста восстанавливается, пароли не переносятся при перерисовке. Smoke проверяет истечение подтверждения и сохранение выделения текста.

### Live rendering optimization
Client status and NAT counters now update existing elements instead of rebuilding the entire card. NAT rows are keyed by port; unchanged rows retain identity and focus, and expanded recorder groups remain open. The RX/TX chart also keeps its SVG and patches paths/labels. Structural changes still update only affected rows; scroll is preserved. Traffic reads pause during confirmation. Offline smoke asserts DOM identity and graph updates in addition to prior scroll tests.

### Archive workspace polish
Quick period buttons (5/15 minutes, 1/3/24 hours), selected duration, accessible comparison-row removal, stable report tabs and saved FFmpeg draft. Editing camera, period or comparison invalidates the displayed result immediately and prevents a delayed response for old parameters from appearing. Timeline intervals are clipped to the selected time range. Offline tests verify the report node is reused and stale archive results disappear.

### Theme contrast checks
Measured contrast of all ten palettes. Adjusted muted text, selected control backgrounds and primary hover in light palettes; placeholders now use the full text color. Added offline Chromium contrast regression check for eight foreground/background pairs in every palette (minimum 4.5:1). This checks specified pairs, not an exhaustive accessibility certification.

Visual audit of the 14 Windows section screenshots from run 45760990694: long synthetic account names and paths could stretch narrow information cards. Long values now span the full row and no longer stretch adjacent metrics; compact session headers and template titles improve density, with full title available in the selected response. Navigation and field labels are slightly larger. Offline test covers wide layout for a long login.

### Qt-free worker
The 4.x worker now uses an atomic, allowlisted JSON profile (`servers.json`) instead of QSettings. The prior 4.x `servers.ini` is migrated once without deleting or changing it; credentials are never migrated. The 3.x template default path still uses Qt lazily, while the 4.x worker passes its own path. Windows CI generates a real Qt INI reference fixture, uninstalls Qt before running tests, and rejects any Qt/shiboken runtime files in the packaged worker. Electron/Chromium graphics and fallback libraries remain intact. The Electron ASAR excludes Python source/build scripts already included in the separate worker.

### Verified Qt-free baseline and further compression
Windows run 45764732955 measured 38,065,425 bytes for the Python worker (formerly 81,877,782), 363,831,175 bytes installed, and a 121,667,630-byte setup artifact. The remaining largest worker files are Python, OpenSSL and the cryptography backend required by Google service-account authentication. They are retained, as are Chromium graphics/fallback libraries. Both installers use LZMA2 ultra (32 MiB dictionary) instead of max; the portable ZIP uses level 9. CI now runs a local-only runtime self-test on source, the frozen worker, and the installed copy, exercising lazy adapters, synthetic settings/templates, and actual Google service-account RSA signing with a generated key and mocked token endpoint. No production service is contacted. Exact size savings must be measured after CI.

### Original Windows wizard
The 3.0.27 native Go/Win32 wizard is reused in `installer_v4` for installation and removal. Its 900×580 original UI, sidebar, logo, cards, buttons and page flow remain; the text and paths are scoped to 4.x. The compressed Inno payload is executed silently to preserve compression and existing isolated per-user registration; Windows Apps & Features points to the native `Uninstall.exe`. No 3.x installer code or registration is changed. Existing 4.x installs in custom locations must be removed before installing this fixed-path preview. CI tests real installation and removal via the native executable. Optional background installer remains separate.

Native setup CI captures all seven installer/uninstaller pages in non-mutating layout preview mode and tests installation twice (upgrade), then removal. Registry reads use Windows Unicode APIs and the inner Inno uninstall command is restored only during its upgrade operation, preventing duplicate uninstall logs. GUI previews do not execute installation or deletion.
