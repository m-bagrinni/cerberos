# Cerberus

Система мониторинга сети связи железной дороги: коммутаторы, оптические мультиплексоры, оптические линии между станциями, подсети, серверы. Плюс оповещения в Telegram, корреляция аварий по топологии и система электронного документооборота (СЭД).

Стек: **Django 5.2 · PostgreSQL 16 · Redis 7 · Celery + django-celery-beat · Channels/Daphne · aiogram 3 · pysnmp**. Python 3.12, зависимости через `uv` (`pyproject.toml`, `uv.lock`). Интерфейс на русском, часовой пояс по умолчанию — `Asia/Bishkek`.

---

## Возможности

### Мониторинг сети (модуль «Локальная сеть», `/local/`)
- **SNMP-опрос** устройств D-Link (DGS-3120, DGS-3630), Eltex (MES2300B), MikroTik и мультиплексоров **Olencom** (NE). Метрики описываются в **профилях устройств** (`DeviceProfile` + `MetricDefinition`: OID, пороги warn/crit, преобразования).
- **Автоопределение** вендора/модели/профиля по SNMP (`apps/monitoring/detection.py`), discovery Olencom NE.
- Интерфейсы, VLAN, **SFP/DDM** (мощность, температура), история статусов портов, ARP и **FDB** (MAC ↔ порт).
- **Ping/fping** устройств и IP-адресов; детектор **broadcast-штормов** (tcpdump через контейнер-сниффер).
- Дерево устройств по категориям/подкатегориям с drag-and-drop, карточки, модалки, графики (Chart.js).

### Станции и оптика (`/local/stations/`)
- Станции с координатами, оптические линии и муфты, привязка устройств и NE к станциям.
- Карта: трассы линий построены по реальным ж/д путям (OSM + networkx, `build_link_paths`), слой OpenRailwayMap через прокси `/tiles/orm/…`, несколько базовых подложек.
- Расчёт ожидаемого уровня приёма (`calc_expected_rx`), достижимость станций от центра (BFS, `apps/stations/chain.py`).

### Подсети (`/local/subnets/`)
- Сканирование подсетей, учёт IP, fingerprint/тип устройства, hostnames, «подключён к порту …» по FDB, алерты по наблюдаемым IP.

### Алерты (`/alerts/`, `apps/alerts`)
- Уровни warn / crit / «нет питания», состояния firing/resolved.
- **«Взять в работу» → «Решено» / «Отказаться»** с уведомлением остальных получателей.
- **Эскалация**: Critical, не взятые в работу за 15 мин, — повторное напоминание (каждые 5 мин проверка).
- **Корреляция по топологии** (`apps/alerts/correlation.py`): граф устройство → NE → оптическая линия → центр; следствия подавляются, уведомление идёт по первопричине. Зависимости задаются в `/admin-ui/dependencies/`.

### Серверы (`/servers/`)
- Пинг серверов, метрики от агента (`agent_dc.py` → `POST /servers/api/agent/report/`), службы, статистика пользователей.
- Управление **Active Directory / Samba DC** через LDAP(S) и SSH: пользователи, группы, сброс паролей, синхронизация.

### СЭД (`/edocs/`)
- Входящие / исходящие / черновики, регистрационные номера, сроки и приоритет, несколько адресатов и вложений, комментарии, маршрут документа, журнал и отчёт.
- Конвертация вложений в PDF (LibreOffice), живое обновление карточки по WebSocket, уведомления и ежедневное (пн–пт, 9:00) напоминание о сроках в Telegram.
- Вложения отдаются только участникам документа.

### Telegram-бот (`apps/accounts/bot`)
- Привязка аккаунта кодом из профиля, вход в Telegram WebApp.
- Экраны: алерты, устройства, станции, мультиплексоры, инфраструктура, СЭД, профиль.
- Рассылка алертов и уведомлений СЭД; **сторож Celery** — сообщение администраторам, если очередь молчит дольше 5 минут (`apps/core/watchdog.py`).

### Прочее
- **Аналитика** (`/analytics/`): сводка состояния сети и печатный отчёт.
- **Службы** (`/services/`): список и перезапуск контейнеров, ручной запуск beat-задач — через изолированный агент `ops`.
- **Админ-панель** (`/admin-ui/`): устройства, профили и метрики, вендоры, категории, NE, станции, линии, муфты, подсети, зависимости, пользователи и регистрация, роли, шаблоны прав, модули, объявления, алерты. Стандартная Django-админка — `/admin/`.
- **REST API** `/api/v1/…` (DRF), только для вошедших пользователей.

---

## Права доступа и безопасность

- Модель прав: **раздел × уровень** (0 — нет, 1 — просмотр, 2 — изменение) + отдельные возможности. Проверка в одном месте — `AccessMiddleware` (`apps/accounts/access.py`); шаблоны прав (`AccessPreset`) лишь заполняют форму. Изменения прав пишутся в журнал.
- Регистрация с подтверждением администратором, журнал входов и активных сессий.
- Ограничение перебора паролей: 10 неудач на логин / 30 на IP за окно (`apps/accounts/backends.py`, Redis).
- `/media/` — только для вошедших, вложения СЭД — только через проверку участника.
- Контейнер `web` не имеет доступа к `docker.sock`. Всё, что требует Docker, — через **`ops`** (`ops/agent.py`): узкий HTTP API с токеном `OPS_TOKEN` и белым списком команд для сниффера.

---

## Архитектура

```
             cloudflared (туннель)
                    │
   ┌────────────────▼───────────────┐
   │ web  (Daphne, ASGI: HTTP + WS) │──► ops ──► docker.sock / sniffer (tcpdump)
   └───────┬────────────────┬───────┘
           │                │
     PostgreSQL           Redis ◄── брокер Celery, кэш, channel layer, пульсы
           │                │
   ┌───────┴────────────────┴──────────────────────────────┐
   │ beat (DatabaseScheduler)                               │
   │ worker_critical  оптика, доступность NE, алерты, корреляция│
   │ worker_high      ping, traffic light, Telegram, СЭД     │
   │ worker_main      default: SNMP-опросы                   │
   │ worker_low       SFP, discovery, сканы, FDB, аналитика, ретенция │
   │ worker_traffic   broadcast-штормы (NET_RAW/NET_ADMIN)   │
   │ telegram_bot     aiogram + сторож Celery                │
   └────────────────────────────────────────────────────────┘
```

Маршрутизация задач по очередям и часть расписания — в `cerber/celery.py`; остальное расписание хранится в БД (django-celery-beat) и правится в админке или на странице «Службы».

### Приложения (`apps/`)

| Приложение | Назначение |
|---|---|
| `accounts` | Пользователь, роли, права, сессии, Telegram (привязка, бот, рассылки) |
| `adminui` | Админ-панель `/admin-ui/` |
| `alerts` | Алерты, взятие в работу, эскалация, корреляция |
| `analytics` | Сводная аналитика и отчёты |
| `core` | Главная (плитки модулей), модули, объявления, темы, middleware, сторож, прокси тайлов, клиент ops |
| `devices` | Вендоры, профили, метрики, категории, устройства, NE, журнал действий, страницы `/local/` |
| `edocs` | СЭД |
| `monitoring` | SNMP, опросы, интерфейсы/VLAN/SFP/ARP/FDB, ping, трафик, CLI (netmiko), ретенция |
| `servers` | Серверы, метрики агента, Active Directory |
| `services` | Управление контейнерами и задачами beat |
| `stations` | Станции, оптические линии, муфты, трассы на карте |
| `subnets` | Подсети, IP, сканер, fingerprint, трафик и штормы |
| `integrations`, `mibs` | Заготовки (моделей нет) |

### Каталоги

| Путь | Что там |
|---|---|
| `cerber/` | settings (`base`, `dev`, `prod`), urls, asgi/wsgi, celery |
| `templates/`, `apps/*/templates/` | Шаблоны (тема «Стекло», мобильная навигация) |
| `static/` | CSS/JS, vendor-библиотеки, GeoJSON ж/д Кыргызстана |
| `mibs/` | MIB-файлы вендоров (`active/` — подключённые) |
| `docker/` | Dockerfile, entrypoint'ы, nginx.conf |
| `ops/` | Агент доступа к Docker |
| `scripts/` | `backup.sh`, `restore.sh`, отладочные скрипты |
| `agent_dc.py` | Агент метрик для Linux/Samba DC |
| `kgz_railways.json` | Исходные данные ж/д сети (OSM) для построения трасс |

---

## Запуск

### 1. Окружение

```bash
cp .env.example .env
# заполнить: DJANGO_SECRET_KEY, POSTGRES_*, TELEGRAM_BOT_TOKEN, CERBER_WEBAPP_URL,
# OPS_TOKEN (обязателен для сервиса ops), AD_* / DC_SSH_* при работе с AD
```

Основные переменные:

| Переменная | Назначение |
|---|---|
| `DJANGO_ENV` | `dev` / `prod` — какой модуль настроек |
| `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_DEBUG` | Django |
| `POSTGRES_*` | БД |
| `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | Redis/Celery |
| `SNMP_DEFAULT_TIMEOUT`, `SNMP_DEFAULT_RETRIES`, `SNMP_MAX_CONCURRENCY` | SNMP |
| `TELEGRAM_BOT_TOKEN`, `CERBER_WEBAPP_URL` | Бот и WebApp |
| `OPS_TOKEN`, `OPS_URL` | Токен и адрес агента ops |
| `AD_SERVER`, `AD_USER`, `AD_PASSWORD`, `AD_BASE_DN`, `AD_USE_SSL`, `AD_CA_CERT_FILE` | Active Directory (LDAPS) |
| `DC_SSH_USER`, `DC_SSH_PASSWORD`, `DC_SSH_PORT` | SSH к Samba DC |
| `RETENTION_*_DAYS/HOURS` | Сроки хранения истории метрик |
| `CUID`, `CGID`, `DOCKER_GID` | uid/gid процессов в контейнерах, gid группы docker хоста |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | prod: доверенные адреса со схемой |

### 2. Стек

Рабочий стек — базовый compose + dev-оверлей (Daphne, код смонтирован томом, отдельные воркеры по очередям, бот, ops, сниффер, cloudflared):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

Веб — `http://<хост>:8888` (порт 8000 на хосте занят NetXMS). PostgreSQL и Redis наружу не публикуются.

В dev-режиме `entrypoint.sh` применяет миграции при старте. Первичная настройка:

```bash
DC="docker compose -f docker-compose.yml -f docker-compose.dev.yml"
$DC exec web python manage.py createsuperuser
$DC exec web python manage.py seed_modules            # плитки модулей на главной
```

> **Важно.** В dev-стеке процессы не перезагружаются при изменении кода. После правок нужен рестарт сервисов:
> `$DC restart web telegram_bot beat worker_critical worker_high worker_main worker_low worker_traffic`

Продакшн-вариант (код в образе, `DJANGO_ENV=prod`, наружу — только nginx на 80):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Состав сервисов тот же, что в dev. Web работает на Daphne (ASGI нужен для WebSocket СЭД). `entrypoint.prod.sh` применяет миграции и собирает статику. nginx отдаёт только `/static/`, а `/media/` проходит через Django с проверкой прав. Для прода в `.env` обязательно задать `DJANGO_ALLOWED_HOSTS` и `DJANGO_CSRF_TRUSTED_ORIGINS`.

### 3. Начальные данные (по необходимости)

| Команда | Что делает |
|---|---|
| `seed_dgs3120`, `seed_eltex_mes2300b`, `seed_olencom` | Профили устройств и метрики |
| `discover <ip>`, `detect_device <ip>` | Определить устройство и профиль |
| `discover_olencom_nes` | Найти NE за шлюзами Olencom |
| `seed_stations_from_nes`, `seed_station_coords` | Станции из NE, координаты |
| `import_closures`, `build_link_paths[_with_closures]`, `build_link_path_single` | Муфты и трассы линий по ж/д |
| `calc_expected_rx` | Ожидаемый уровень приёма линий |
| `seed_known_subnets`, `seed_subnets_from_interfaces`, `scan_subnet` | Подсети |
| `poll_device`, `poll_all`, `poll_sfp`, `poll_interfaces`, `poll_olencom`, … | Ручные опросы |
| `manage_port`, `cli_set_pvid` | Управление портами через CLI |
| `check_alerts` | Ручная проверка порогов |
| `convert_existing_docs` | PDF для старых документов СЭД |
| `run_telegram_bot` | Запуск бота (в стеке — отдельный сервис) |

---

## Эксплуатация

### Бэкапы

```bash
scripts/backup.sh [каталог]     # pg_dump + архив media, проверка дампа, ротация KEEP_DAYS (14)
scripts/restore.sh backups/cerber_YYYYMMDD_HHMMSS.dump
```

По умолчанию работают с dev-стеком (`COMPOSE_FILES`). Для регулярного запуска добавьте в cron, например `30 3 * * * /opt/cerber/scripts/backup.sh >> /opt/cerber/backups/backup.log 2>&1`, и копируйте дампы на другой диск/хост. Каталог `backups/` в `.gitignore`.

### Выкатка изменений

1. Бэкап затрагиваемых таблиц (`pg_dump -Fc -t … -t django_migrations`).
2. `$DC exec -T web python manage.py migrate`.
3. Рестарт сервисов (см. выше).
4. Проверить логи **после** рестарта и дождаться полного прогона ключевых задач (`poll_optical_links_fast`, `ping_all_devices`, `alerts.correlate`). Задачи-синглтоны первые ~5 минут после рестарта могут отвечать `already running` — это замок прерванного прогона.

### Ретенция

`monitoring.cleanup_old_metric_values` (ежечасно) и `servers.cleanup_metrics` (ежесуточно) чистят историю согласно `MONITORING_RETENTION` в `cerber/settings/base.py`.

---

## Разработка и тесты

```bash
$DC exec -T web python manage.py test \
    apps.edocs apps.alerts apps.accounts apps.subnets apps.core apps.monitoring apps.adminui apps.devices --noinput
```

- В тестах **миграции отключены** (`_NoMigrations` в `cerber/settings/base.py`): история перехода `devices.User → accounts.User` не собирается на пустой БД. Схема создаётся по моделям.
- Data-миграции и страницы на реальных данных проверяйте на копии БД (`createdb cerber_migcheck` + дамп без больших таблиц метрик), запуская команды с `-e POSTGRES_DB=cerber_migcheck`.
- Из контейнера `makemigrations` не может писать в `/app`; миграции пишутся вручную, сверка — `makemigrations --check --dry-run`.
- Линтер: `ruff` (длина строки 100, py312).

Тестами пока не покрыты в должной мере `monitoring` (опросы), `devices`, `stations`.

---

## Планы

- Плановые работы (режим тишины для станции/устройства).
- Приём SNMP trap и syslog.
- График дежурств для эскалации.
- Бэкап конфигураций коммутаторов с diff, инвентарь, топология LLDP/CDP.
- SLA-отчёт по станциям и линиям; алерт → поручение в СЭД.
- СЭД: шаблоны, печатная форма, связанные документы, справочник отделов.

История изменений — `CHANGELOG.md` (ведётся не регулярно; основное — в истории git).
