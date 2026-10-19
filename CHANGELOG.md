# Cerberus — Changelog

## 2026-10-19 — Admin UI (5 этапов) + багфиксы Olencom + карта

### ✅ Admin UI (`/admin-ui/`) — 5 этапов

**1. Каркас:**
- Отдельный layout (без `extends base.html`)
- Sidebar (Мониторинг / Инфраструктура / Система / Переходы)
- Dashboard (5 stat-cards + активные алерты + устройства)

**2. Devices:**
- `DeviceListView` — фильтры (q, vendor, category, status), пагинация
- `DeviceDetailView` — графики 24ч (Chart.js, все метрики профиля)
- CRUD (`CreateView`, `UpdateView`, `DeleteView`)
- Poll now (AJAX → `poll_device_task.delay()`)
- Тест SNMP (snmpget через `subprocess`, v1/v2c)

**3. Profiles:**
- `ProfileListView` — фильтры (q, vendor, category)
- `ProfileDetailView` — шапка + inline-метрики (name, OID, warn, crit)
- CRUD профилей (`Create/Update/Delete/Clone`)
- CRUD метрик (`MetricDefinition`)
- Тест OID — snmpget в реальном времени
- Клонирование профиля (со всеми метриками)

**4. Vendors / NE / Subnets / Users:**
- **Vendors** — CRUD
- **NE** — список + фильтр по шлюзу + inline rename + карточка (51 метрика SFP+eth)
- **Subnets** — карточки + прогресс-бары + Scan now + CRUD
- **Users** — CRUD + защита от self-delete

**5. Alerts UI:**
- `AlertListView` — фильтры (severity/state/entity/q), счётчики (firing/crit/warn)
- `AlertDetailView` — полная карточка
- **Resolve** (single + bulk) — API + JS
- Dashboard-ссылки (stat-card «Алерты» → `/admin-ui/alerts/?state=firing`)

### 🐛 Багфиксы Olencom (основной проект)

- **`poll_olencom_all` FieldError** — `effective_profile__slug` (property, не поле) → **`Q(profile__slug)|Q(profile_override__slug)`**
  - **Симптом:** SFP-метрики не писались в БД (0 записей `sfp_*`)
  - **Фикс:** правильный Q-фильтр
- **`poll_all_devices`** ставил `poll_device_task` для Olencom → **worker зависал** (SNMP v1, OID другого вендора)
  - **Фикс:** `.exclude(profile__device_category="multiplexer").exclude(profile_override__device_category="multiplexer")`
- **`poll_olencom_all`** имел `.exclude(status="unreachable")` → **stuck-цикл** (статус `unreachable` → не опрашивается → статус не меняется)
  - **Фикс:** убран `exclude`
- **Координаты станций** — 6 обновлены из OSM:
  - `balykchy`, `chaldovar`, `kemin`, `rzd-3639`, `tokmak`, `kochkor`

### 🗺 Карта (`/stations/`)

- **`OpticalLink.path_geojson`** — routing по ж/д (networkx + OSM)
  - `build_link_paths` — snap to railway + хвосты от станций
  - 19 линков → реальный ж/д трек
- **OpenRailwayMap overlay** — прокси (`apps/core/proxy_views.py`, обход 403), **сигналы**
- **6 базовых слоёв:** OpenTopoMap, Спутник (Esri), OSM HOT, Esri Dark Gray, Esri Light Gray, Esri Topo
- **Ж/д из OSM** — 9767 ways (`static/geojson/kyrgyzstan_railways.geojson`, 3.2 MB)
- **Прокси** `/tiles/orm/*` — Django view (`cache_page` 1ч)

### 📦 Изменения в основном проекте

- `apps/alerts/models.py` — `ip_address` FK (nullable)
- `apps/stations/models.py` — `path_geojson` JSONField
- `apps/stations/serializers.py` — `path_geojson` в fields
- `apps/monitoring/tasks.py` — 3 багфикса
- `cerber/settings/base.py` — `apps.adminui`
- `cerber/urls.py` — `admin-ui/`, `tiles/orm/`
- `pyproject.toml` — `networkx>=3.0`
- `docker-compose.dev.yml`, `docker/Dockerfile` — fast-poll 15s

### 🔜 Что не сделано

- **Telegram-бот** (aiogram 3) — уведомления при алертах
- **MikroTik** — 4-й вендор
- **SNMPv3**
- **Уведомления (email)**
- **Discovery UI** — «создать метрики из снимка»
- **MikroTik/D-Link SFP в optical links** (сейчас только Olencom NE)
- **MikroTik seed** — `seed_mikrotik.py`
