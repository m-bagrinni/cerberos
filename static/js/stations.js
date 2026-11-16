/**
 * Cerberus — станции.
 * Список + модалки + карта + live-обновление + fullscreen.
 */
(function () {
    "use strict";

    const API = {
        stations:      "/api/v1/stations/",
        stationBySlug: (slug) => `/api/v1/stations/${slug}/`,
        ports:         (slug) => `/api/v1/stations/${slug}/ports/`,
        links:         "/api/v1/optical-links/",
        attachDevice:  (slug) => `/api/v1/stations/${slug}/attach-device/`,
        detachDevice:  (slug, id) => `/api/v1/stations/${slug}/detach-device/${id}/`,
    };

    const LIVE_INTERVAL_MS = 15000;
    const VB_W = 1400;
    const VB_H = 900;

    
// ── Вставка муфт в маршрут линии ──────────────────────────────

    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }

function insertClosuresIntoPath(pts, closures) {
    if (!pts || pts.length < 2 || !closures || closures.length === 0) {
        return pts;
    }

    // Убираем ограничение — вставляем ВСЕ муфты, даже если они далеко
    const closurePts = closures.map(c => [c.lat, c.lon]);
    const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

    const insertions = [];
    closurePts.forEach((cp, idx) => {
        let bestSeg = 0;
        let bestDist = Infinity;
        for (let i = 0; i < pts.length - 1; i++) {
            const p1 = pts[i];
            const p2 = pts[i + 1];
            const mid = [(p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2];
            const d = dist(cp, mid);
            if (d < bestDist) {
                bestDist = d;
                bestSeg = i;
            }
        }
        insertions.push({ segIdx: bestSeg, point: cp, order: idx });
    });

    insertions.sort((a, b) => a.segIdx - b.segIdx || a.order - b.order);

    const result = [];
    let insertIdx = 0;

    for (let i = 0; i < pts.length; i++) {
        result.push(pts[i]);
        while (insertIdx < insertions.length && insertions[insertIdx].segIdx === i) {
            const ins = insertions[insertIdx];
            const last = result[result.length - 1];
            if (dist(last, ins.point) > 0.000001) {
                result.push(ins.point);
            }
            insertIdx++;
        }
    }

    return result;
}

const STATUS_COLOR = {
        ok:       "#00ff00",   // зелёный
        warn:     "#ffcc00",   // жёлтый
        degraded: "#ff8800",   // оранжевый — link up, но RX плохой
        crit:     "#ff0000",   // красный — обрыв
        power:    "#9b59b6",   // фиолетовый — нет питания
        no_signal:"#ff0000",   // красный — нет сигнала
        unknown:  "#888888",   // синий — нет данных
    };

    const STATUS_RU = {
        ok: "норма", warn: "предупреждение",
        crit: "обрыв", power: "нет питания",
        no_signal: "нет сигнала", unknown: "нет данных",
    };

    let _map = null;
    let _railLayer;
    let _markersLayer = null;
    let _linksLayer = null;
    let _liveTimer = null;
    let _liveLastOk = null;
    let _liveErrors = 0;

    // ── Утилиты ─────────────────────────────────────────────
    // Общие хелперы — static/js/core.js
    function getCsrf() { return Cerber.csrfToken(); }
    function escapeHtml(s) { return Cerber.escapeHtml(s); }

    // ── Fetch ───────────────────────────────────────────────
    async function fetchStations() {
        const r = await fetch(API.stations, { credentials: "same-origin" });
        const d = await r.json();
        return d.results || d;
    }
    async function fetchLinks() {
        const r = await fetch(API.links, { credentials: "same-origin" });
        const d = await r.json();
        return d.results || d;
    }
    async function fetchStation(slug) {
        const r = await fetch(API.stationBySlug(slug), { credentials: "same-origin" });
        if (!r.ok) throw new Error("station HTTP " + r.status);
        return r.json();
    }

    // ── Вкладки ─────────────────────────────────────────────
    function bindTabs() {
        const tabs = document.querySelectorAll(".stations-tab");
        tabs.forEach((tab) => {
            tab.addEventListener("click", () => {
                tabs.forEach((t) => t.classList.remove("is-active"));
                tab.classList.add("is-active");
                const name = tab.dataset.tab;
                document.querySelectorAll(".stations-tab-pane").forEach((p) => {
                    p.classList.toggle("is-active", p.dataset.tabPane === name);
                });
                if (name === "map" && window.renderStationsMap) {
                    setTimeout(() => window.renderStationsMap(), 50);
                }
            });
        });
    }

    // ── Reload списка ───────────────────────────────────────
    async function reloadList() {
        const root = document.getElementById("stationsListRoot");
        if (!root) return;
        const r = await fetch("/local/stations/?partial=1", {
            credentials: "same-origin",
            headers: { "X-Requested-With": "XMLHttpRequest" },
        });
        if (!r.ok) return;
        root.innerHTML = await r.text();
        bindRowClicks();
    }
    window.reloadStationsList = reloadList;
    window.reloadStationsGrid = reloadList;
    window.reloadStationsCanvas = reloadList;

    // ── Клик по строке (делегирование) ──────────────────────
    function bindRowClicks() {
        const list = document.getElementById("stationsList");
        if (!list || list.dataset.bound === "1") return;
        list.dataset.bound = "1";

        list.addEventListener("click", (ev) => {
            const addBtn = ev.target.closest(".js-add-link");
            if (addBtn) {
                ev.stopPropagation();
                alert("Добавить линию к станции: " + addBtn.dataset.slug);
                return;
            }
            const infoBtn = ev.target.closest(".js-open-station");
            if (infoBtn) {
                ev.stopPropagation();
                openStationModal(infoBtn.dataset.slug);
                return;
            }
            const row = ev.target.closest(".station-row");
            if (row && row.dataset.slug) {
                openStationModal(row.dataset.slug);
            }
        });
    }

    // ── Модалка станции ─────────────────────────────────────
    async function openStationModal(slug) {
        const root = document.getElementById("stationModalRoot");
        root.innerHTML = '<div class="sm-overlay"><div class="sm-modal"><div class="sm-left" style="padding:20px">Загрузка…</div></div></div>';

        let station, links;
        try {
            station = await fetchStation(slug);
            const all = await fetchLinks();
            links = all.filter((l) => l.station_a === slug || l.station_b === slug);
        } catch (e) {
            root.innerHTML = `<div class="sm-overlay"><div class="sm-modal"><div class="sm-left" style="padding:20px">Ошибка: ${escapeHtml(e.message)}</div></div></div>`;
            return;
        }

        // Сохраняем для highlightLink()
        window._currentStationLinks = links || [];
        window._currentStationSlug = slug;

        root.innerHTML = renderStationModal(station, links);
        loadStationIps(slug);

        // Закрытие по клику на фон
        const overlay = root.querySelector(".sm-overlay");
        if (overlay) {
            overlay.addEventListener("click", (ev) => {
                if (ev.target === overlay) closeStationModal();
            });
        }

        // Авто-отрисовка первой линии на карте (через 500мс — ждём загрузки iframe)
        setTimeout(() => {
            if (links && links.length > 0) {
                window.highlightLink(links[0].id);
            }
        }, 500);
    }

    function closeStationModal() {
        document.getElementById("stationModalRoot").innerHTML = "";
        window._currentStationLinks = [];
        window._currentStationSlug = null;
    }
    window.closeStationModal = closeStationModal;

    function renderStationModal(st, links) {
        const devs = (st.devices || []).map((sd) => `
            <div class="sm-card">
                <div class="sm-card-head">
                    <i class="bi bi-router sm-icon-dev"></i>
                    <span class="sm-card-name">${escapeHtml(sd.device_name)}</span>
                    <span class="sm-badge sm-badge--${sd.device_status}">${escapeHtml(sd.device_status)}</span>
                </div>
                <div class="sm-card-body">
                    <div class="sm-metric-row"><span class="sm-metric-label">IP</span><span class="sm-metric-value">${escapeHtml(sd.device_ip)}</span></div>
                    <div class="sm-metric-row"><span class="sm-metric-label">Вендор</span><span class="sm-metric-value">${escapeHtml(sd.vendor || "—")}</span></div>
                </div>
            </div>
        `).join("") || `<div class="sm-empty"><i class="bi bi-inbox"></i> Нет коммутаторов</div>`;

        const nes = (st.nes || []).map((sn) => `
            <div class="sm-card">
                <div class="sm-card-head">
                    <i class="bi bi-hdd-network sm-icon-ne"></i>
                    <span class="sm-card-name">NE-${escapeHtml(String(sn.ne_id))}${sn.ne_display_name ? ' · ' + escapeHtml(sn.ne_display_name) : ''}</span>
                    <span class="sm-badge sm-badge--${sn.ne_status}">${escapeHtml(sn.ne_status)}</span>
                </div>
                <div class="sm-card-body">
                    <div class="sm-metric-row"><span class="sm-metric-label">Шлюз</span><span class="sm-metric-value">${escapeHtml(sn.gateway_ip || "—")}</span></div>
                </div>
            </div>
        `).join("") || `<div class="sm-empty"><i class="bi bi-inbox"></i> Нет мультиплексоров</div>`;

        const lnks = (links || []).map((l) => {
            const isA = l.station_a === st.slug;
            const peer = isA ? l.station_b : l.station_a;
            const portLabel = isA
                ? `${(l.port_a_type || "").toUpperCase()}${l.port_a_num}`
                : `${(l.port_b_type || "").toUpperCase()}${l.port_b_num}`;
            const rx = isA ? l.rx_a_dbm : l.rx_b_dbm;
            const cls = l.link_status === "ok" ? "ok" : l.link_status === "warn" ? "warn" : l.link_status === "crit" ? "crit" : l.link_status === "power" ? "power" : "unknown";
            return `
            <div class="sm-card sm-card--link" onclick="highlightLink(${l.id})">
                <div class="sm-card-head">
                    <span class="sm-link-status sm-link-status--${l.link_status}"></span>
                    <span class="sm-card-name">↔ ${escapeHtml(peer)} · ${escapeHtml(portLabel)}</span>
                    <span class="sm-badge sm-badge--${cls}">${STATUS_RU[l.link_status] || l.link_status}</span>
                </div>
                <div class="sm-card-body">
                    <div class="sm-metric-row"><span class="sm-metric-label">${escapeHtml(l.label || "—")}</span><span class="sm-metric-value">RX: ${rx == null ? "—" : rx.toFixed(2) + " dBm"}</span></div>
                </div>
            </div>
            `;
        }).join("") || `<div class="sm-empty"><i class="bi bi-inbox"></i> Нет оптических линий</div>`;

        const ips = `<div id="station-ips-list"><div class="sm-empty">Загрузка IP...</div></div>`;

        return `
        <div class="sm-overlay" onclick="if(event.target === this) closeStationModal()">
            <div class="sm-modal">
                <div class="sm-header">
                    <div class="sm-title-wrap">
                        <h2 class="sm-title">${escapeHtml(st.name)}</h2>
                        <span class="sm-subtitle">${escapeHtml(st.slug)}</span>
                    </div>
                    <button class="sm-close" onclick="closeStationModal()">&times;</button>
                </div>
                <div class="sm-body">
                    <div class="sm-left">
                        <div class="sm-section">
                            <div class="sm-section-title"><i class="bi bi-router"></i> Коммутаторы <span class="c-muted">${(st.devices || []).length}</span></div>
                            <div class="sm-card-list">${devs}</div>
                        </div>
                        <div class="sm-section">
                            <div class="sm-section-title"><i class="bi bi-hdd-network"></i> Мультиплексоры <span class="c-muted">${(st.nes || []).length}</span></div>
                            <div class="sm-card-list">${nes}</div>
                        </div>
                        <div class="sm-section">
                            <div class="sm-section-title"><i class="bi bi-lightning-charge"></i> Оптические линии <span class="c-muted">${(links || []).length}</span></div>
                            <div class="sm-card-list">${lnks}</div>
                        </div>
                        <div class="sm-section">
                            <div class="sm-section-title"><i class="bi bi-pc-display"></i> IP-адреса <span class="c-muted" id="station-ips-count">0</span></div>
                            <div class="sm-card-list">${ips}</div>
                            <button class="sm-btn-add-ip" data-need-edit="subnets" onclick="showAddIpForm('${st.slug}', ${st.id})">
                                <i class="bi bi-plus-circle"></i> Добавить IP
                            </button>
                        </div>
                    </div>
                    <div class="sm-right">
                        <iframe id="sm-map-iframe" src="/static/map_station_preview.html?v=1" style="width:100%; height:100%; border:none;"></iframe>
                    </div>
                </div>
            </div>
        </div>
        `;
    }

    // Глобальная функция для подсветки линии на карте
    window.highlightLink = function(linkId) {
        const iframe = document.getElementById('sm-map-iframe');
        if (!iframe || !iframe.contentWindow) return;
        // Ищем линию среди всех загруженных (links кешируются на странице)
        // Проще: у нас в модалке links хранятся в замыкании, но для onclick нужен глобал.
        // Сохраним текущие links в window._currentStationLinks при рендере.
        const links = window._currentStationLinks || [];
        const link = links.find(l => l.id == linkId);
        if (!link) return;
        // Определяем координаты станции (A или B)
        const isA = link.station_a === (window._currentStationSlug || '');
        const lat = isA ? link.station_a_lat : link.station_b_lat;
        const lon = isA ? link.station_a_lon : link.station_b_lon;
        iframe.contentWindow.postMessage({
            type: 'drawRoute',
            path_geojson: link.path_geojson,
            station_lat: lat,
            station_lon: lon
        }, '*');
    };

    // ── Статус-агрегат станции ──────────────────────────────
    function aggStatusOf(s) {
        // Если API уже отдал agg_status (с учётом BFS) — используем его
        if (s.agg_status) return s.agg_status;

        // Fallback (если agg_status нет в ответе — например, ?light=1 без него)
        const statuses = [];
        (s.nes || []).forEach((sn) => statuses.push(sn.ne_status));
        (s.devices || []).forEach((sd) => statuses.push(sd.device_status));
        if (!statuses.length) return "unknown";
        if (statuses.indexOf("unreachable") !== -1) return "crit";
        if (statuses.every((x) => x === "ok")) return "ok";
        if (statuses.some((x) => x === "ok")) return "warn";
        return "unknown";
    }

    // ══════════════════════════════════════════════════════════
    // КАРТА (Leaflet)
    // ══════════════════════════════════════════════════════════
    // ── Tooltip для линии с DDM ─────────────────────────────
    function fmtDdm(v, unit, digits) {
        if (v === null || v === undefined) return '<span class="tt-na">—</span>';
        const d = (digits === undefined) ? 2 : digits;
        return '<span class="tt-val">' + Number(v).toFixed(d) + '</span> ' + unit;
    }

    function fmtStatusBadge(status) {
        const map = {
            ok:       ["🟢", "норма"],
            warn:     ["🟡", "предупреждение"],
            degraded: ["🟠", "деградация сигнала"],
            crit:     ["🔴", "обрыв / авария"],
            no_signal:["🔴", "нет сигнала"],
            unknown:  ["⚪", "нет данных"],
        };
        const [icon, text] = map[status] || ["?", status];
        return '<span class="tt-status tt-status--' + status + '">' + icon + " " + text + '</span>';
    }

    function fmtAge(isoStr) {
        if (!isoStr) return "";
        const sec = Math.round((Date.now() - new Date(isoStr).getTime()) / 1000);
        if (sec < 60) return sec + "с";
        if (sec < 3600) return Math.round(sec / 60) + "м";
        return Math.round(sec / 3600) + "ч";
    }

    function buildLinkTooltip(l) {
        // Заголовок
        const title = l.label || ("Линия #" + l.id);
        const labelPart = l.label
            ? '<b>' + escapeHtml(l.label) + '</b>'
            : '<b>Линия #' + l.id + '</b>';

        // Статус
        const statusRow = fmtStatusBadge(l.link_status);

        // Конец A
        const ddmA = l.link_status === 'crit' ? {} : (l.ddm_a || {});
        if (l.link_status !== 'crit') {
            if (!ddmA.rx_dbm && l.rx_a_dbm !== null && l.rx_a_dbm !== undefined) {
                ddmA.rx_dbm = l.rx_a_dbm;
            }
            if (!ddmA.tx_dbm && l.tx_a_dbm !== null && l.tx_a_dbm !== undefined) {
                ddmA.tx_dbm = l.tx_a_dbm;
            }
        }
        if (!ddmA.temp_c && l.temp_a_c !== null && l.temp_a_c !== undefined) {
            ddmA.temp_c = l.temp_a_c;
        }
        if (!ddmA.bias_ma && l.bias_a_ma !== null && l.bias_a_ma !== undefined) {
            ddmA.bias_ma = l.bias_a_ma;
        }
        if (!ddmA.volt_v && l.volt_a_v !== null && l.volt_a_v !== undefined) {
            ddmA.volt_v = l.volt_a_v;
        }
        const ageA = fmtAge(ddmA.updated_at);
        const reachA = l.device_a_reachable === false ? '<span style="color:#ff6b6b;font-size:10px;margin-left:5px">(нет связи)</span>' : '';
        const endA = [
            '<div class="tt-side">',
            '<div class="tt-side__head">А: ' + escapeHtml(l.end_a_label || "—") + reachA + '</div>',
            '<div class="tt-side__row"><span class="tt-k">RX:</span>   ' + fmtDdm(ddmA.rx_dbm, "dBm")
                + (ageA ? ' <span style="color:#8e8e8e;font-size:10px">(' + ageA + ')</span>' : '')
                + '</div>',
            '<div class="tt-side__row"><span class="tt-k">TX:</span>   ' + fmtDdm(ddmA.tx_dbm, "dBm") + '</div>',
            '<div class="tt-side__row"><span class="tt-k">Temp:</span> ' + fmtDdm(ddmA.temp_c, "°C", 1) + '</div>',
            '<div class="tt-side__row"><span class="tt-k">Bias:</span> ' + fmtDdm(ddmA.bias_ma, "mA", 1) + '</div>',
            '<div class="tt-side__row"><span class="tt-k">Volt:</span> ' + fmtDdm(ddmA.volt_v, "V", 2) + '</div>',
            '</div>',
        ].join("");

        // Конец B
        const reachB = l.device_b_reachable === false ? '<span style="color:#ff6b6b;font-size:10px;margin-left:5px">(нет связи)</span>' : '';
        const ddmB = l.link_status === 'crit' ? {} : (l.ddm_b || {});
        if (l.link_status !== 'crit') {
            if (!ddmB.rx_dbm && l.rx_b_dbm !== null && l.rx_b_dbm !== undefined) {
                ddmB.rx_dbm = l.rx_b_dbm;
            }
            if (!ddmB.tx_dbm && l.tx_b_dbm !== null && l.tx_b_dbm !== undefined) {
                ddmB.tx_dbm = l.tx_b_dbm;
            }
        }
        if (!ddmB.temp_c && l.temp_b_c !== null && l.temp_b_c !== undefined) {
            ddmB.temp_c = l.temp_b_c;
        }
        if (!ddmB.bias_ma && l.bias_b_ma !== null && l.bias_b_ma !== undefined) {
            ddmB.bias_ma = l.bias_b_ma;
        }
        if (!ddmB.volt_v && l.volt_b_v !== null && l.volt_b_v !== undefined) {
            ddmB.volt_v = l.volt_b_v;
        }
        const ageB = fmtAge(ddmB.updated_at);
        const staleB = l.link_status === "crit";
        const endB = [
            '<div class="tt-side">',
            '<div class="tt-side__head" style="' + (staleB ? 'color:#f2495c' : '') + '">Б: ' + escapeHtml(l.end_b_label || "—")
                + (staleB ? ' ⚠' : '')
                + '</div>',
            '<div class="tt-side__row"><span class="tt-k">RX:</span>   ' + fmtDdm(ddmB.rx_dbm, "dBm")
                + (ageB ? ' <span style="color:' + (staleB ? "#f2495c" : "#8e8e8e") + ';font-size:10px">(' + ageB + ')</span>' : '')
                + '</div>',
            '<div class="tt-side__row"><span class="tt-k">TX:</span>   ' + fmtDdm(ddmB.tx_dbm, "dBm") + '</div>',
            '<div class="tt-side__row"><span class="tt-k">Temp:</span> ' + fmtDdm(ddmB.temp_c, "°C", 1) + '</div>',
            '<div class="tt-side__row"><span class="tt-k">Bias:</span> ' + fmtDdm(ddmB.bias_ma, "mA", 1) + '</div>',
            '<div class="tt-side__row"><span class="tt-k">Volt:</span> ' + fmtDdm(ddmB.volt_v, "V", 2) + '</div>',
            '</div>',
        ].join("");

        return '<div class="tt-line">' + labelPart + ' ' + statusRow + '</div>' +
               '<div class="tt-grid">' + endA + endB + '</div>';
    }

    window.buildLinkTooltip = buildLinkTooltip;

    async function renderStationsMap() {
        const container = document.getElementById("stationsMap");
        if (!container) return;
        if (typeof L === "undefined") {
            container.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-muted)">Leaflet не загрузился — проверьте интернет.</div>';
            return;
        }

        // Сохранение вида при перерисовке
        let savedView = null;
        if (_map) {
            try {
                savedView = { center: _map.getCenter(), zoom: _map.getZoom() };
            } catch (e) { savedView = null; }
        }

        const [stations, links] = await Promise.all([fetchStations(), fetchLinks()]);

        if (!_map) {
            _map = L.map("stationsMap", {
                center: [42.7, 74.6],
                zoom: 8,
                zoomControl: true,
                attributionControl: true,
            });

            // Подстройка карты под размер контейнера
            window.addEventListener('resize', () => {
                if (_map) {
                    setTimeout(() => _map.invalidateSize(), 100);
                }
            });
            // И при изменении размера панели (если есть ResizeObserver)
            if (window.ResizeObserver) {
                const ro = new ResizeObserver(() => {
                    if (_map) _map.invalidateSize();
                });
                ro.observe(document.getElementById('stationsMapWrap'));
            }

            // Инициализация линейки
            if (typeof initRuler === 'function') {
                initRuler(_map);
            }
            // ============================================================
            // Базовые слои карты (переключатель в правом верхнем углу)
            // ============================================================
            const baseLayers = {
                "🛰 Спутник (Google)": L.tileLayer(
                    "https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
                    {
                        attribution: 'Imagery &copy; Google',
                        maxZoom: 20,
                        crossOrigin: true,
                    }
                ),
                "🌍 OpenTopoMap (топография)": L.tileLayer(
                    "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
                    {
                        attribution: '&copy; OSM, SRTM | &copy; OpenTopoMap',
                        maxZoom: 17,
                        crossOrigin: true,
                    }
                ),
                "🛰 Спутник (Esri)": L.tileLayer(
                    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                    {
                        attribution: '&copy; Esri, Maxar, Earthstar Geographics',
                        maxZoom: 19,
                        crossOrigin: true,
                    }
                ),
                "🗺 Улицы (OSM HOT)": L.tileLayer(
                    "https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png",
                    {
                        attribution: '&copy; OSM contributors, HOT',
                        maxZoom: 19,
                        subdomains: "ab",
                        // crossOrigin НЕ указываем — OSM блокирует CORS-запросы
                    }
                ),
                "🌑 Тёмная (Esri Dark Gray)": L.tileLayer(
                    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
                    {
                        attribution: '&copy; Esri, HERE, Garmin',
                        maxZoom: 16,
                        crossOrigin: true,
                    }
                ),
                "☀ Светлая (Esri Light Gray)": L.tileLayer(
                    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
                    {
                        attribution: '&copy; Esri, HERE, Garmin',
                        maxZoom: 16,
                        crossOrigin: true,
                    }
                ),
                "🏔 Топо (Esri)": L.tileLayer(
                    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
                    {
                        attribution: '&copy; Esri, HERE, Garmin, USGS',
                        maxZoom: 19,
                        crossOrigin: true,
                    }
                ),
            };

            // Слой по умолчанию — восстанавливаем из localStorage
            const savedLayer = localStorage.getItem("cerber-map-layer") || "🛰 Спутник (Google)";
            const initialLayer = baseLayers[savedLayer] || baseLayers["🌍 OpenTopoMap (топография)"];
            initialLayer.addTo(_map);

            // Ж/Д линии — отдельный слой ПОД маркерами/линками.
            // ~10 тыс. отрезков: рисуем на canvas (SVG на столько путей тормозит)
            // в своей панели ниже overlayPane, чтобы canvas не перекрывал клики по линкам.
            _map.createPane("railPane").style.zIndex = 350;
            _railLayer = L.layerGroup().addTo(_map);

            _markersLayer = L.layerGroup().addTo(_map);
            _linksLayer = L.layerGroup().addTo(_map);

            // Контрол переключения слоёв
            // Overlay: OpenRailwayMap (инфраструктура, сигналы, стрелки)
            const overlays = {
                "🚦 OpenRailwayMap (сигналы)": L.tileLayer(
                    "/tiles/orm/signals/{z}/{x}/{y}.png",
                    {
                        attribution: '&copy; OpenRailwayMap, &copy; OSM',
                        maxZoom: 19,
                        opacity: 0.85,
                    }
                ),
            };

            L.control.layers(baseLayers, overlays, {
                position: "topright",
                collapsed: true,
            }).addTo(_map);
            L.control.scale({ position: 'bottomleft', metric: true, imperial: false }).addTo(_map);

            // Запоминаем выбор
            _map.on("baselayerchange", function (e) {
                localStorage.setItem("cerber-map-layer", e.name);
            });
        } else {
            _markersLayer.clearLayers();
            _linksLayer.clearLayers();
        }

        // Ж/д линии грузим в фоне (один раз) — станции и линки не ждут ~0.5 МБ geojson.
        // Слой не очищаем при перерисовке: пути статичны.
        loadRailTracks();

        const bySlug = {};
        stations.forEach((s) => { bySlug[s.slug] = s; });

        // Линии — дуги Безье
        // Группируем линии по пути (path_geojson)
        const groups = {};
        links.forEach((l) => {
            const a = bySlug[l.station_a];
            const b = bySlug[l.station_b];
            if (!a || !b) return;
            if (!a.latitude || !a.longitude || !b.latitude || !b.longitude) return;
            
            // Ключ группы — строка path_geojson (или координаты станций)
            let key;
            if (l.path_geojson && l.path_geojson.length > 1) {
                key = l.path_geojson.map(p => p[0].toFixed(4) + ',' + p[1].toFixed(4)).join('|');
            } else {
                key = `${l.station_a}-${l.station_b}`;
            }
            
            if (!groups[key]) groups[key] = [];
            groups[key].push(l);
        });
        
        // Для каждой группы — выбираем самую критичную линию
        const statusPriority = { crit: 3, warn: 2, ok: 1, unknown: 0 };
        const representativeLinks = [];
        Object.values(groups).forEach(group => {
            // Сортируем по приоритету статуса (crit первый)
            group.sort((a, b) => (statusPriority[b.link_status] || 0) - (statusPriority[a.link_status] || 0));
            representativeLinks.push({
                main: group[0],
                all: group,
            });
        });
        
        representativeLinks.forEach(({ main: l, all: groupLinks }) => {
            const a = bySlug[l.station_a];
            const b = bySlug[l.station_b];
            if (!a || !b) return;
            if (!a.latitude || !a.longitude || !b.latitude || !b.longitude) return;

            const A = [a.latitude, a.longitude];
            const B = [b.latitude, b.longitude];
            const midLat = (A[0] + B[0]) / 2;
            const midLng = (A[1] + B[1]) / 2;
            const dLat = B[0] - A[0];
            const dLng = B[1] - A[1];
            const len = Math.sqrt(dLat * dLat + dLng * dLng) || 0.001;
            const sign = (l.id % 2 === 0) ? 1 : -1;
            const offset = sign * len * 0.08;
            const cosLat = Math.cos(midLat * Math.PI / 180) || 1;
            const perpLat = -dLng / len;
            const perpLng = dLat / len / cosLat;
            const ctrlLat = midLat + perpLat * offset;
            const ctrlLng = midLng + perpLng * offset;

            // Если есть path_geojson — используем реальный ж/д трек
            // Иначе — Безье-дуга (fallback)
            let pts;
            if (l.path_geojson && l.path_geojson.length > 1) {
                pts = l.path_geojson;
                // Вставляем муфты в путь (если есть)
                if (l.closures && l.closures.length > 0) {
                    pts = insertClosuresIntoPath(pts, l.closures);
                }
            } else {
                pts = [];
                const N = 20;
                for (let i = 0; i <= N; i++) {
                    const t = i / N;
                    const mt = 1 - t;
                    pts.push([
                        mt*mt*A[0] + 2*mt*t*ctrlLat + t*t*B[0],
                        mt*mt*A[1] + 2*mt*t*ctrlLng + t*t*B[1],
                    ]);
                }
            }

            const color = STATUS_COLOR[l.link_status] || STATUS_COLOR.unknown;
            const statusClass = `optical-link optical-link--${l.link_status}`;

            // Одна линия (без glow и обводки) — для скорости
            const line = L.polyline(pts, {
                color: color,
                weight: 5,           // Толще, чтобы было видно под путями
                opacity: 0.85,        // Чуть прозрачная
                dashArray: l.link_status === "unknown" ? "12,8" : null,
                lineCap: "round", lineJoin: "round",
                className: statusClass,
            }).addTo(_linksLayer);

            // Если в группе несколько линий — показываем все в тултипе
            let tooltipHtml;
            if (groupLinks.length > 1) {
                // Разделитель и все линии
                tooltipHtml = groupLinks.map((gl, idx) => {
                    const sep = idx > 0 ? '<hr style="border-color:#3d444b;margin:6px 0;">' : '';
                    return sep + buildLinkTooltip(gl);
                }).join('');
            } else {
                tooltipHtml = buildLinkTooltip(l);
            }
            // Оборачиваем в контейнер, чтобы Leaflet не обрезал
            tooltipHtml = '<div class="link-tooltip-content">' + tooltipHtml + '</div>';
            
            console.log('[bindTooltip] link', l.id, 'groupLinks:', groupLinks.map(g => g.id), 'html lines:', (tooltipHtml.match(/tt-line/g) || []).length);
            // Невидимая толстая линия для удобного клика
            const clickLine = L.polyline(pts, {
                color: 'transparent',
                weight: 20,
                opacity: 0,
            }).addTo(_linksLayer);

            // Функция для обновления попапа свежими данными (с учётом групп)
            const refreshPopup = async (e) => {
                const popup = e.popup;
                try {
                    if (groupLinks.length > 1) {
                        // Если это группа — запрашиваем данные для ВСЕХ линий параллельно
                        const promises = groupLinks.map(gl => 
                            fetch(`/api/v1/optical-links/${gl.id}/`).then(r => r.ok ? r.json() : gl)
                        );
                        const freshDataArray = await Promise.all(promises);
                        
                        // Пересобираем HTML для группы
                        const freshHtml = freshDataArray.map((gl, idx) => {
                            const sep = idx > 0 ? '<hr style="border-color:#3d444b;margin:6px 0;">' : '';
                            return sep + buildLinkTooltip(gl);
                        }).join('');
                        
                        popup.setContent('<div class="link-tooltip-content">' + freshHtml + '</div>');
                    } else {
                        // Если линия одиночная — запрашиваем только её
                        const resp = await fetch(`/api/v1/optical-links/${l.id}/`);
                        if (resp.ok) {
                            const freshData = await resp.json();
                            let freshHtml = buildLinkTooltip(freshData);
                            freshHtml = '<div class="link-tooltip-content">' + freshHtml + '</div>';
                            popup.setContent(freshHtml);
                        }
                    }
                } catch (err) {
                    console.error('[popup refresh] error:', err);
                }
            };

            // Не открывать popup при Shift (режим линейки)
            clickLine.on('click', (e) => {
                if (e.originalEvent.shiftKey) {
                    return;   // Shift — режим линейки, не открываем popup
                }
                clickLine.bindPopup(tooltipHtml, {
                    maxWidth: 640,
                    className: "link-popup",
                    closeButton: false,
                    autoClose: true,
                    closeOnClick: true,
                }).openPopup();
            });
            clickLine.on('popupopen', refreshPopup);

            // Тонкая линия (без popup)

            // ── Отрисовка муфт на линии ──
            if (l.closures && l.closures.length > 0) {
                l.closures.forEach(c => {
                    if (!c.lat || !c.lon) return;

                    // Ищем ближайшую точку на маршруте (pts), чтобы "посадить" муфту на линию
                    let bestPt = [c.lat, c.lon];
                    let bestDist = Infinity;
                    const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
                    pts.forEach(p => {
                        const d = dist([c.lat, c.lon], p);
                        if (d < bestDist) {
                            bestDist = d;
                            bestPt = p;
                        }
                    });

                    // Используем bestPt (точку на линии) вместо реальных координат
                    const closureIcon = L.divIcon({
                        className: 'closure-icon',
                        html: '<div class="closure-icon__inner"><i class="bi bi-record-circle"></i></div>',
                        iconSize: [16, 16],
                        iconAnchor: [8, 8],
                    });
                    const closureMarker = L.marker(bestPt, { icon: closureIcon }).addTo(_linksLayer);

                    let extraInfo = '';
                    const nameA = l.ne_a_name || l.device_a_name || 'А';
                    const nameB = l.ne_b_name || l.device_b_name || 'Б';
                    if (c.expected_rx_dbm !== null && c.expected_rx_dbm !== undefined) {
                        extraInfo += `<br><span style="color:#7dd3c0">RX (от ${escapeHtml(nameA)}): ${c.expected_rx_dbm} dBm</span>`;
                    }
                    if (c.expected_rx_b_dbm !== null && c.expected_rx_b_dbm !== undefined) {
                        extraInfo += `<br><span style="color:#f2495c">RX (от ${escapeHtml(nameB)}): ${c.expected_rx_b_dbm} dBm</span>`;
                    }
                    if (c.segment_loss_db !== null && c.segment_loss_db !== undefined) {
                        extraInfo += `<br><span style="color:#8e8e8e">Потери на участке: ${c.segment_loss_db} dB</span>`;
                    }
                    
                    closureMarker.bindTooltip(
                        `<b>${escapeHtml(c.name)}</b><br><span class="c-muted">Муфта</span>${extraInfo}`,
                        { direction: "top", className: "closure-tooltip-wrap" }
                    );
                });
            }

            // IDENTIFIKATOR для live-обновления
            line.__linkId = l.id;
        });

        // Маркеры
        const points = [];
        stations.forEach((s) => {
            if (!s.latitude || !s.longitude) return;
            points.push([s.latitude, s.longitude]);

            const status = aggStatusOf(s);
            const icon = L.divIcon({
                className: "",
                html: `<div class="station-marker station-marker--${status}">
                          <span class="station-marker__dot"></span>
                          <span>${escapeHtml(s.name)}</span>
                       </div>`,
                iconSize: [140, 26],
                iconAnchor: [70, 13],
            });

            const m = L.marker([s.latitude, s.longitude], { icon: icon }).addTo(_markersLayer);
            m.__slug = s.slug;
            
            // Клик по маркеру — загружаем данные станции
            m.on('click', () => {
                if (typeof window.loadStationDetails === 'function') {
                    window.loadStationDetails(s.slug);
                }
            });

            const neCount = (s.nes || []).length;
            const devCount = (s.devices || []).length;
            const linksCount = links.filter(
                (l) => l.station_a === s.slug || l.station_b === s.slug
            ).length;

            m.bindPopup(
                '<div>' +
                '<div class="station-popup__title">' + escapeHtml(s.name) + '</div>' +
                '<div class="station-popup__slug">' + escapeHtml(s.slug) + '</div>' +
                '<div class="station-popup__row">🛰 Мультиплексоры: ' + neCount + '</div>' +
                '<div class="station-popup__row">🔌 Коммутаторы: ' + devCount + '</div>' +
                '<div class="station-popup__row">🔗 Линий: ' + linksCount + '</div>' +
                '</div>'
            );
        });

        // Восстановить вид или fitBounds
        if (savedView) {
            _map.setView(savedView.center, savedView.zoom, { animate: false });
        } else if (points.length > 0) {
            _map.fitBounds(points, { padding: [40, 40] });
        }

        setTimeout(() => { if (_map) _map.invalidateSize(); }, 100);

        
        // Обработка ?ne=<pk> (переход с мультиплексоров)
        const urlParams = new URLSearchParams(window.location.search);
        const nePk = urlParams.get('ne');
        if (nePk) {
            // Ищем станцию, в которой есть NE с таким pk
            let foundStation = null;
            for (const s of stations) {
                if (s.nes && s.nes.some(ne => String(ne.id) === String(nePk))) {
                    foundStation = s;
                    break;
                }
            }
            
            if (foundStation && foundStation.latitude && foundStation.longitude) {
                _map.setView([foundStation.latitude, foundStation.longitude], 14);
                // Открываем модалку станции
                if (typeof window.openStationModal === 'function') {
                    window.openStationModal(foundStation.slug);
                }
                // Заполняем левую панель
                if (typeof window.loadStationDetails === 'function') {
                    window.loadStationDetails(foundStation.slug);
                }
            }
        }
    }

    // ── Live: перекраска линий без пересоздания ─────────────
    async function refreshMapStyles() {
        if (!_map || !_linksLayer) return;
        try {
            const links = await fetchLinks();
            let updated = 0;
            _linksLayer.eachLayer((layer) => {
                const id = layer.__linkId;
                if (!id) return; // пропускаем обводку
                const found = links.find((x) => x.id === id);
                if (!found) return;
                const color = STATUS_COLOR[found.link_status] || STATUS_COLOR.unknown;

                if (layer.options.color !== color) {
                    layer.setStyle({
                        color: color,
                        dashArray: found.link_status === "unknown" ? "12,8" : null,
                    });
                    updated++;
                }
                if (layer.getTooltip()) {
                    layer.setTooltipContent(buildLinkTooltip(found));
                }
            });
            _liveErrors = 0;
            _liveLastOk = new Date();
        } catch (e) {
            _liveErrors++;
            console.warn("[live] map error", e);
        }
    }

    // ── Live: обновление маркеров ────────────────────────────
    async function refreshMapMarkers() {
        if (!_map || !_markersLayer) return;
        try {
            const stations = await fetchStations();
            const bySlug = {};
            stations.forEach((s) => { bySlug[s.slug] = s; });

            let updated = 0;
            _markersLayer.eachLayer((marker) => {
                const slug = marker.__slug;
                if (!slug) return;
                const s = bySlug[slug];
                if (!s) return;

                const status = aggStatusOf(s);
                const el = marker.getElement();
                if (el) {
                    const inner = el.querySelector(".station-marker");
                    if (inner) {
                        const want = "station-marker station-marker--" + status;
                        if (inner.className !== want) {
                            inner.className = want;
                            updated++;
                        }
                    }
                }
            });
            if (updated > 0) console.log("[live] markers: " + updated + " recolored");
        } catch (e) {
            console.warn("[live] markers error", e);
        }
    }

    // ── LIVE-ТИК ────────────────────────────────────────────
    function setLiveStatus(ok, text) {
        const ind = document.getElementById("stationsLive");
        const txt = document.getElementById("stationsLiveText");
        if (!ind || !txt) return;
        ind.classList.toggle("live-indicator--error", !ok);
        txt.textContent = text;
    }

    async function liveTick() {
        if (document.hidden) return;

        // Обновляем список станций всегда
        await reloadList();

        // Если карта видна — обновляем её
        const mapPane = document.querySelector('[data-tab-pane="map"]');
        const mapVisible = mapPane && mapPane.classList.contains("is-active");
        if (mapVisible) {
            await refreshMapStyles();
            await refreshMapMarkers();
        }

        // Индикатор
        if (_liveErrors > 2) {
            setLiveStatus(false, "ошибка обновления");
        } else if (_liveLastOk) {
            const sec = Math.round((Date.now() - _liveLastOk.getTime()) / 1000);
            setLiveStatus(true, "обновлено " + sec + " сек назад");
        }
    }

    function startStationsLive() {
        if (_liveTimer) return;
        setLiveStatus(true, "запущено");
        // Cerber.poll сам ставит паузу в фоновой вкладке и обновляет при возврате
        _liveTimer = Cerber.poll(liveTick, LIVE_INTERVAL_MS, { immediate: true });
    }
    function stopStationsLive() {
        if (_liveTimer) { _liveTimer.stop(); _liveTimer = null; }
    }

    // ══════════════════════════════════════════════════════════
    // FULLSCREEN (CSS overlay)
    // ══════════════════════════════════════════════════════════
    function toggleMapFullscreen() {
        const wrap = document.getElementById("stationsMapWrap");
        const btn = document.getElementById("btnMapFullscreen");
        const lbl = document.getElementById("btnMapFullscreenLabel");
        if (!wrap) return;

        const isFs = wrap.classList.toggle("is-fullscreen");
        document.body.classList.toggle("map-fullscreen-active", isFs);

        // ВЫНОСИМ КАРТУ В BODY (чтобы position: fixed работал на весь экран)
        if (isFs) {
            // Запоминаем родителя и место, куда вернуть
            wrap._originalParent = wrap.parentElement;
            wrap._originalNextSibling = wrap.nextSibling;
            document.body.appendChild(wrap);
        } else {
            // Возвращаем обратно
            if (wrap._originalParent) {
                wrap._originalParent.insertBefore(wrap, wrap._originalNextSibling);
            }
        }

        const existing = wrap.querySelector(".map-fullscreen-exit");
        if (isFs) {
            if (!existing) {
                const exit = document.createElement("button");
                exit.className = "map-fullscreen-exit";
                exit.innerHTML = '<i class="bi bi-x-lg"></i> Выйти (Esc)';
                exit.addEventListener("click", toggleMapFullscreen);
                wrap.appendChild(exit);
            }
            if (lbl) lbl.textContent = "Свернуть";
            if (btn) btn.title = "Выйти из полноэкранного режима";
        } else {
            if (existing) existing.remove();
            if (lbl) lbl.textContent = "На весь экран";
            if (btn) btn.title = "Развернуть на весь экран";
        }

        // Только invalidateSize — БЕЗ renderStationsMap
        setTimeout(() => {
            if (_map && typeof _map.invalidateSize === "function") {
                _map.invalidateSize({ animate: false });
                console.log("[map] invalidateSize (fullscreen " + (isFs ? "on" : "off") + ")");
            }
        }, 150);
    }

    window.toggleMapFullscreen = toggleMapFullscreen;

    document.addEventListener("keydown", (e) => {
        if (e.key !== "Escape") return;
        const wrap = document.getElementById("stationsMapWrap");
        if (wrap && wrap.classList.contains("is-fullscreen")) {
            toggleMapFullscreen();
        } else {
            closeStationModal();
        }
    });

    // ── Экспорт ─────────────────────────────────────────────
    window.renderStationsMap = renderStationsMap;

    // Автоинициализация карты, если она активна при загрузке
    if (document.querySelector('[data-tab-pane="map"]')?.classList.contains('is-active')) {
        setTimeout(() => {
            if (window.renderStationsMap) window.renderStationsMap();
        }, 100);
    }
    window.openStationModal = openStationModal;
    window.startStationsLive = startStationsLive;
    window.stopStationsLive = stopStationsLive;
    window.liveTick = liveTick;

    // ── Boot ────────────────────────────────────────────────
    document.addEventListener("DOMContentLoaded", () => {
        bindTabs();
        bindRowClicks();

        const btnReload = document.getElementById("btnReloadStations");
        if (btnReload) btnReload.addEventListener("click", () => { reloadList(); liveTick(); });

        const btnFull = document.getElementById("btnMapFullscreen");
        if (btnFull) btnFull.addEventListener("click", toggleMapFullscreen);

        // Запускаем live
        startStationsLive();
    });

    // ============================================================
    // Ж/Д линии из OSM (один раз, кеш)
    // ============================================================
    let _railLoaded = false;

    async function loadRailTracks() {
        if (_railLoaded || !_railLayer) return;
        _railLoaded = true;   // сразу — чтобы перерисовка не запустила вторую загрузку
        try {
            // .min — округлённая копия; рядом лежит .gz, WhiteNoise отдаёт его (~0.5 МБ)
            const resp = await fetch("/static/geojson/kyrgyzstan_railways.min.geojson");
            if (!resp.ok) {
                console.warn("[rail] не удалось загрузить:", resp.status);
                _railLoaded = false;
                return;
            }
            const geojson = await resp.json();

            L.geoJSON(geojson, {
                renderer: L.canvas({ pane: "railPane", tolerance: 4 }),
                style: function () {
                    return {
                        color: "#ff8c00",       // ярко-оранжевый (заметно на любом фоне)
                        weight: 3,
                        opacity: 0.9,
                        lineCap: "round",
                        lineJoin: "round",
                    };
                },
                onEachFeature: function (feature, layer) {
                    const name = (feature.properties && feature.properties.name) || "Ж/Д линия";
                    const osmId = feature.properties && feature.properties.osm_id;
                    layer.bindTooltip(
                        "<b>" + escapeHtml(name) + "</b><br>OSM: " + escapeHtml(osmId),
                        { sticky: true, className: "rail-tooltip", direction: "top" }
                    );
                },
            }).addTo(_railLayer);

        } catch (e) {
            console.warn("[rail] ошибка:", e);
            _railLoaded = false;
        }
    }

    window.showAddIpForm = async function(slug, stationId) {
        const list = document.getElementById('station-ips-list');
        if (!list) return;
        
        // Загружаем подсети
        let subnets = [];
        try {
            const resp = await fetch('/api/v1/subnets/');
            const data = await resp.json();
            subnets = data.results || data;
        } catch (e) {
            list.innerHTML = '<div class="station-modal__empty">Ошибка загрузки подсетей</div>';
            return;
        }
        
        list.innerHTML = `
            <div style="padding: 10px; background: var(--bg-elevated); border-radius: var(--radius);">
                <div style="margin-bottom: 8px;">
                    <label style="font-size: 11px; color: var(--text-muted); display: block; margin-bottom: 4px;">Подсеть</label>
                    <select id="add-ip-subnet" style="width: 100%; background: var(--bg-input); border: 1px solid var(--border); color: var(--text); padding: 6px; border-radius: var(--radius);">
                        <option value="">— Выберите подсеть —</option>
                        ${subnets.map(s => `<option value="${escapeHtml(s.id)}">${escapeHtml(s.cidr)} ${s.name ? '(' + escapeHtml(s.name) + ')' : ''}</option>`).join('')}
                    </select>
                </div>
                <div style="margin-bottom: 8px;">
                    <label style="font-size: 11px; color: var(--text-muted); display: block; margin-bottom: 4px;">IP-адрес</label>
                    <select id="add-ip-address" style="width: 100%; background: var(--bg-input); border: 1px solid var(--border); color: var(--text); padding: 6px; border-radius: var(--radius);">
                        <option value="">— Сначала выберите подсеть —</option>
                    </select>
                </div>
                <div style="margin-bottom: 8px;">
                    <label style="font-size: 11px; color: var(--text-muted); display: block; margin-bottom: 4px;">Подпись (описание)</label>
                    <input id="add-ip-desc" type="text" placeholder="Например: Касса, Принтер..." style="width: 100%; background: var(--bg-input); border: 1px solid var(--border); color: var(--text); padding: 6px; border-radius: var(--radius);">
                </div>
                <div style="display: flex; gap: 8px;">
                    <button id="add-ip-save" class="btn btn--primary" style="flex: 1; padding: 6px; font-size: 12px;">Привязать</button>
                    <button id="add-ip-cancel" class="btn" style="flex: 1; padding: 6px; font-size: 12px;">Отмена</button>
                </div>
            </div>
        `;
        
        // Загружаем IP при выборе подсети
        const subnetSelect = document.getElementById('add-ip-subnet');
        const ipSelect = document.getElementById('add-ip-address');
        subnetSelect.addEventListener('change', async () => {
            const subnetId = subnetSelect.value;
            if (!subnetId) {
                ipSelect.innerHTML = '<option value="">— Сначала выберите подсеть —</option>';
                return;
            }
            try {
                const resp = await fetch(`/api/v1/ip-addresses/?subnet=${subnetId}`);
                const data = await resp.json();
                const ips = (data.results || data).filter(ip => !ip.station);
                ipSelect.innerHTML = '<option value="">— Выберите IP —</option>' +
                    ips.map(ip => `<option value="${escapeHtml(ip.id)}">${escapeHtml(ip.ip)} ${ip.description ? '(' + escapeHtml(ip.description) + ')' : ''}</option>`).join('');
            } catch (e) {
                ipSelect.innerHTML = '<option value="">Ошибка загрузки IP</option>';
            }
        });
        
        // Сохранение
        document.getElementById('add-ip-save').addEventListener('click', async () => {
            const ipId = ipSelect.value;
            const description = document.getElementById('add-ip-desc').value;
            if (!ipId) {
                showToast('Выберите IP', 'error');
                return;
            }
            
            const csrftoken = getCookie('csrftoken');
            const resp = await fetch(`/api/v1/ip-addresses/${ipId}/update-station/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrftoken,
                },
                body: JSON.stringify({ station: stationId, description: description }),
            });
            
            if (resp.ok) {
                showToast('IP привязан', 'success');
                loadStationIps(slug);
            } else {
                showToast('Ошибка', 'error');
            }
        });
        
        // Отмена
        document.getElementById('add-ip-cancel').addEventListener('click', () => {
            loadStationIps(slug);
        });
    };
    async function loadStationIps(slug) {
        const list = document.getElementById('station-ips-list');
        const count = document.getElementById('station-ips-count');
        if (!list) return;
        
        try {
            const resp = await fetch(`/api/v1/stations/${slug}/ips/`);
            const data = await resp.json();
            const ips = data.ips || [];
            if (count) count.textContent = ips.length;
            
            if (ips.length === 0) {
                list.innerHTML = '<div class="sm-empty"><i class="bi bi-inbox"></i> Нет привязанных IP</div>';
                return;
            }
            
            list.innerHTML = ips.map(ip => `
                <div class="sm-card sm-card--ip">
                    <div class="sm-card-head">
                        <i class="bi bi-pc-display sm-icon-pc"></i>
                        <span class="sm-card-name">${escapeHtml(ip.ip)}</span>
                        <span class="sm-badge sm-badge--${ip.is_alive ? 'ok' : 'crit'}">${ip.is_alive ? 'живой' : 'мёртвый'}</span>
                    </div>
                    <div class="sm-card-body">
                        <div class="sm-metric-row"><span class="sm-metric-label">Подсеть</span><span class="sm-metric-value">${escapeHtml(ip.subnet_cidr || '—')}</span></div>
                        <div class="sm-metric-row"><span class="sm-metric-label">Описание</span><span class="sm-metric-value">${escapeHtml(ip.description || '—')}</span></div>
                    </div>
                </div>
            `).join('');
        } catch (e) {
            list.innerHTML = '<div class="sm-empty">Ошибка загрузки IP</div>';
        }
    }
    
    // Вызываем после открытия модалки
    document.addEventListener('DOMContentLoaded', () => {
        const origOpen = openStationModal;
        openStationModal = async function(slug) {
            await origOpen(slug);
            loadStationIps(slug);
        };
    });

    function getCSRFToken() {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, 'csrftoken'.length + 1) === ('csrftoken=')) {
                    cookieValue = decodeURIComponent(cookie.substring('csrftoken'.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }

    // ─── Привязка к ближайшей линии ────────────────────────────
    function snapToLine(map, latlng) {
        if (!_linksLayer) return { latlng: latlng, layer: null, segmentIndex: -1 };
        
        const zoom = map.getZoom();
        const snapRadiusMeters = 200 / Math.pow(2, zoom - 12);
        
        let best = { latlng: latlng, layer: null, segmentIndex: -1 };
        let bestDist = snapRadiusMeters;
        
        _linksLayer.eachLayer((layer) => {
            if (!layer.getLatLngs) return;
            const latlngs = layer.getLatLngs();
            const points = Array.isArray(latlngs[0]) ? latlngs[0] : latlngs;
            
            for (let i = 1; i < points.length; i++) {
                const p1 = points[i-1];
                const p2 = points[i];
                
                const x = latlng.lat, y = latlng.lng;
                const x1 = p1.lat, y1 = p1.lng;
                const x2 = p2.lat, y2 = p2.lng;
                const dx = x2 - x1, dy = y2 - y1;
                const denom = dx * dx + dy * dy;
                let t = denom > 0 ? ((x - x1) * dx + (y - y1) * dy) / denom : 0;
                t = Math.max(0, Math.min(1, t));
                const candidate = L.latLng(x1 + t * dx, y1 + t * dy);
                
                const dist = map.distance(latlng, candidate);
                if (dist < bestDist) {
                    bestDist = dist;
                    best = { latlng: candidate, layer: layer, segmentIndex: i };
                }
            }
        });
        
        return best;
    }

    // ─── Сводка по сети (правая панель) ──────────────────────
    async function loadNetworkSummary() {
        try {
            const [stationsResp, linksResp, alertsResp] = await Promise.all([
                fetch('/api/v1/stations/'),
                fetch('/api/v1/optical-links/'),
                fetch('/api/v1/alerts/?state=firing').catch(() => ({ json: () => ({ count: 0, results: [] }) })),
            ]);
            const stationsData = await stationsResp.json();
            const linksData = await linksResp.json();
            const alertsData = await alertsResp.json();
            
            const stations = stationsData.results || stationsData;
            const links = linksData.results || linksData;
            const alerts = alertsData.results || alertsData;
            
            let online = 0, offline = 0;
            stations.forEach(s => {
                if (s.agg_status === 'crit') offline++;
                else online++;
            });
            
            const statOnline = document.getElementById('stat-online');
            const statOffline = document.getElementById('stat-offline');
            const statAlerts = document.getElementById('stat-alerts');
            const statLinks = document.getElementById('stat-links');
            
            if (statOnline) statOnline.textContent = online;
            if (statOffline) statOffline.textContent = offline;
            if (statAlerts) statAlerts.textContent = alerts.length || 0;
            if (statLinks) statLinks.textContent = links.length;
            
            // График трафика сети
            loadNetworkTrafficChart(stations);
        } catch (e) {
            console.error('[summary]', e);
        }
    }

    // График суммарного трафика сети
    async function loadNetworkTrafficChart(stations) {
        const canvas = document.getElementById('network-traffic-chart');
        if (!canvas) return;
        
        try {
            const existing = Chart.getChart(canvas);
            if (existing) existing.destroy();
            
            // Сумма по минутам считается на сервере одним запросом
            // (раньше — ~130 запросов interfaces/ + traffic-history/ из браузера).
            const resp = await fetch('/api/v1/stations/traffic-summary/?hours=24', { credentials: 'same-origin' });
            if (!resp.ok) return;
            const byMinute = {};
            (await resp.json()).forEach(p => {
                byMinute[p.ts] = { in: p.in_bps || 0, out: p.out_bps || 0 };
            });

            const minutes = Object.keys(byMinute).sort();
            if (!minutes.length) return;
            
            const labels = minutes.map(ts => {
                const d = new Date(ts);
                return d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0');
            });
            
            // В Мбит/с
            const inMbps = minutes.map(ts => (byMinute[ts].in || 0) / 1_000_000);
            const outMbps = minutes.map(ts => (byMinute[ts].out || 0) / 1_000_000);
            
            // Рисуем два графика (IN и OUT)
            const wrapper = canvas.parentElement;
            wrapper.innerHTML = '<div style="display: flex; flex-direction: column; gap: 12px; height: 100%;"><div style="flex: 1; position: relative;"><canvas id="chart-in"></canvas></div><div style="flex: 1; position: relative;"><canvas id="chart-out"></canvas></div></div>';
            
            // IN
            new Chart(document.getElementById('chart-in').getContext('2d'), {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'IN (Мбит/с)',
                        data: inMbps,
                        borderColor: '#0abfbc',
                        backgroundColor: 'rgba(10,191,188,0.15)',
                        borderWidth: 2,
                        pointRadius: 0,
                        tension: 0.3,
                        fill: true,
                    }],
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { display: true, labels: { color: '#8e8e8e', font: { size: 10 } } } },
                    scales: {
                        x: { display: false },
                        y: { ticks: { color: '#8e8e8e', font: { size: 9 }, callback: v => v.toFixed(1) + ' M' }, grid: { color: 'rgba(44,50,53,0.5)' } },
                    },
                },
            });
            
            // OUT
            new Chart(document.getElementById('chart-out').getContext('2d'), {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'OUT (Мбит/с)',
                        data: outMbps,
                        borderColor: '#ff780a',
                        backgroundColor: 'rgba(255,120,10,0.15)',
                        borderWidth: 2,
                        pointRadius: 0,
                        tension: 0.3,
                        fill: true,
                    }],
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { display: true, labels: { color: '#8e8e8e', font: { size: 10 } } } },
                    scales: {
                        x: { ticks: { color: '#8e8e8e', maxTicksLimit: 6, font: { size: 9 }, maxRotation: 0 }, grid: { color: 'rgba(44,50,53,0.5)' } },
                        y: { ticks: { color: '#8e8e8e', font: { size: 9 }, callback: v => v.toFixed(1) + ' M' }, grid: { color: 'rgba(44,50,53,0.5)' } },
                    },
                },
            });
        } catch (e) {
            console.error('[traffic chart]', e);
        }
    }

    // Загружаем сводку при загрузке страницы
    document.addEventListener('DOMContentLoaded', () => {
        Cerber.poll(loadNetworkSummary, 60000, { immediate: true });
    });

    // ─── Линейка (Shift + клик) ──────────────────────────────
    let _rulerPoints = [];
    let _rulerLine = null;
    let _rulerMarkers = [];

    function initRuler(map) {
        // Курсор меняется, если зажат Shift
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Shift' && map) {
                map.getContainer().style.cursor = 'crosshair';
            }
        });
        document.addEventListener('keyup', (e) => {
            if (e.key === 'Shift' && map) {
                map.getContainer().style.cursor = '';
            }
        });

        // Клик по карте
        map.on('click', (e) => {
            // Если Shift НЕ зажат — очищаем линейку
            if (!e.originalEvent.shiftKey) {
                clearRuler(map);
                return;
            }

            // Shift зажат — привязываем к ближайшей линии
            const snap = snapToLine(map, e.latlng);
            _rulerPoints.push({ latlng: snap.latlng, layer: snap.layer, segmentIndex: snap.segmentIndex });

            // Маркер точки
            const marker = L.circleMarker(snap.latlng, {
                radius: 5,
                color: '#ff780a',
                fillColor: '#ff780a',
                fillOpacity: 1,
                weight: 2,
            }).addTo(map);
            _rulerMarkers.push(marker);

            // Если точек >= 2 — рисуем линию и считаем расстояние
            if (_rulerPoints.length >= 2) {
                const p1 = _rulerPoints[_rulerPoints.length - 2];
                const p2 = _rulerPoints[_rulerPoints.length - 1];

                // 1. Если обе точки на ОДНОЙ линии — считаем по сегментам
                if (p1.layer && p2.layer && p1.layer === p2.layer) {
                    const latlngs = p1.layer.getLatLngs();
                    const points = Array.isArray(latlngs[0]) ? latlngs[0] : latlngs;

                    const segStart = Math.min(p1.segmentIndex, p2.segmentIndex);
                    const segEnd = Math.max(p1.segmentIndex, p2.segmentIndex);

                    const path = [p1.latlng];
                    for (let i = segStart; i <= segEnd; i++) {
                        if (i > segStart || (i === segStart && p1.segmentIndex > p2.segmentIndex)) {
                            path.push(points[i]);
                        }
                    }
                    path.push(p2.latlng);
                    if (p1.segmentIndex > p2.segmentIndex) path.reverse();

                    if (_rulerLine) {
                        _rulerLine.setLatLngs(path);
                    } else {
                        _rulerLine = L.polyline(path, {
                            color: '#0abfbc', weight: 3, dashArray: '5, 5',
                        }).addTo(map);
                    }

                    let totalMeters = 0;
                    for (let i = 1; i < path.length; i++) {
                        totalMeters += path[i - 1].distanceTo(path[i]);
                    }
                    const km = (totalMeters / 1000).toFixed(2);

                    if (_rulerLine.getTooltip()) {
                        _rulerLine.setTooltipContent(`📏 ${km} км (по линии)`);
                    } else {
                        _rulerLine.bindTooltip(`📏 ${km} км (по линии)`, {
                            permanent: true, direction: 'center', className: 'ruler-tooltip',
                        }).openTooltip();
                    }
                } else {
                    // 2. Разные линии — вызываем API для маршрута
                    if (p1.layer && p2.layer) {
                        const from = [p1.latlng.lat, p1.latlng.lng];
                        const to = [p2.latlng.lat, p2.latlng.lng];

                        fetch('/api/v1/route/', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'X-CSRFToken': getCSRFToken(),
                            },
                            credentials: 'same-origin',
                            body: JSON.stringify({ from, to }),
                        })
                        .then(res => {
                            if (!res.ok) throw new Error('HTTP ' + res.status);
                            return res.json();
                        })
                        .then(data => {
                            if (data.path && data.path.length > 0) {
                                const path = data.path.map(p => L.latLng(p[0], p[1]));
                                if (_rulerLine) {
                                    _rulerLine.setLatLngs(path);
                                } else {
                                    _rulerLine = L.polyline(path, {
                                        color: '#0abfbc', weight: 3, dashArray: '5, 5',
                                    }).addTo(map);
                                }

                                const km = data.distance_km.toFixed(2);
                                if (_rulerLine.getTooltip()) {
                                    _rulerLine.setTooltipContent(`📏 ${km} км (маршрут)`);
                                } else {
                                    _rulerLine.bindTooltip(`📏 ${km} км (маршрут)`, {
                                        permanent: true, direction: 'center', className: 'ruler-tooltip',
                                    }).openTooltip();
                                }
                            } else {
                                throw new Error('Нет пути');
                            }
                        })
                        .catch(err => {
                            console.warn('[ruler] fallback:', err);
                            // Fallback — по прямой
                            const pts = _rulerPoints.map(p => p.latlng);
                            if (_rulerLine) {
                                _rulerLine.setLatLngs(pts);
                            } else {
                                _rulerLine = L.polyline(pts, {
                                    color: '#0abfbc', weight: 3, dashArray: '5, 5',
                                }).addTo(map);
                            }

                            let totalMeters = 0;
                            for (let i = 1; i < pts.length; i++) {
                                totalMeters += pts[i - 1].distanceTo(pts[i]);
                            }
                            const km = (totalMeters / 1000).toFixed(2);

                            if (_rulerLine.getTooltip()) {
                                _rulerLine.setTooltipContent(`📏 ${km} км`);
                            } else {
                                _rulerLine.bindTooltip(`📏 ${km} км`, {
                                    permanent: true, direction: 'center', className: 'ruler-tooltip',
                                }).openTooltip();
                            }
                        });
                    } else {
                        // Одна из точек не на линии — по прямой
                        const pts = _rulerPoints.map(p => p.latlng);
                        if (_rulerLine) {
                            _rulerLine.setLatLngs(pts);
                        } else {
                            _rulerLine = L.polyline(pts, {
                                color: '#0abfbc', weight: 3, dashArray: '5, 5',
                            }).addTo(map);
                        }

                        let totalMeters = 0;
                        for (let i = 1; i < pts.length; i++) {
                            totalMeters += pts[i - 1].distanceTo(pts[i]);
                        }
                        const km = (totalMeters / 1000).toFixed(2);

                        if (_rulerLine.getTooltip()) {
                            _rulerLine.setTooltipContent(`📏 ${km} км`);
                        } else {
                            _rulerLine.bindTooltip(`📏 ${km} км`, {
                                permanent: true, direction: 'center', className: 'ruler-tooltip',
                            }).openTooltip();
                        }
                    }
                }
            }
        });
    }

    function clearRuler(map) {
        _rulerPoints = [];
        if (_rulerLine) {
            map.removeLayer(_rulerLine);
            _rulerLine = null;
        }
        _rulerMarkers.forEach(m => map.removeLayer(m));
        _rulerMarkers = [];
    }

    window.initRuler = initRuler;

    // ─── Загрузка данных станции (левая колонка) ──────────────
    async function loadStationDetails(slug) {
        const el = document.getElementById('dispatch-station-details');
        if (!el) return;
        
        el.innerHTML = '<div class="dispatch-loading">Загрузка...</div>';
        
        try {
            const resp = await fetch(`/api/v1/stations/${slug}/`);
            const station = await resp.json();
            
            const statusClass = station.agg_status === 'crit' ? 'crit' : station.agg_status === 'warn' ? 'warn' : 'ok';
            const statusText = station.agg_status === 'crit' ? 'CRIT' : station.agg_status === 'warn' ? 'WARN' : 'OK';
            
            let html = '';
            html += '<div class="dispatch-station-title"><i class="bi bi-geo-alt"></i> ' + escapeHtml(station.name) + '</div>';
            html += '<div class="dispatch-station-row"><span class="dispatch-station-label">Статус</span><span class="dispatch-station-value dispatch-station-value--' + statusClass + '">' + statusText + '</span></div>';
            
            // NE (DDM)
            if (station.nes && station.nes.length) {
                html += '<div class="dispatch-station-section"><div class="dispatch-station-section-title">Мультиплексоры (' + station.nes.length + ')</div>';
                station.nes.forEach(ne => {
                    const nePk = ne.id || ne.ne_id;
                    html += '<div class="dispatch-chart-card" id="chart-ne-' + nePk + '">';
                    html += '<div class="dispatch-chart-card__header"><div class="dispatch-chart-card__title"><i class="bi bi-hdd-network"></i> NE-' + ne.ne_id + '</div></div>';
                    html += '<div id="ne-ports-' + nePk + '"><div class="dispatch-loading" style="padding: 10px; font-size: 11px;">Загрузка портов...</div></div>';
                    html += '</div>';
                });
                html += '</div>';
            }
            
            // Devices (трафик)
            if (station.devices && station.devices.length) {
                html += '<div class="dispatch-station-section"><div class="dispatch-station-section-title">Коммутаторы (' + station.devices.length + ')</div>';
                station.devices.forEach(d => {
                    const devId = d.id || d.device_id;
                    html += '<div class="dispatch-chart-card" id="chart-dev-' + devId + '">';
                    html += '<div class="dispatch-chart-card__header"><div class="dispatch-chart-card__title"><i class="bi bi-router"></i> ' + escapeHtml(d.device_name) + '</div><span class="dispatch-chart-card__badge">Трафик</span></div>';
                    html += '<div class="dispatch-chart-card__canvas-wrap"><canvas id="canvas-dev-' + devId + '"></canvas></div>';
                    html += '<div class="dispatch-chart-card__footer"><span>IN <span class="val" id="val-dev-' + devId + '-in">—</span></span><span>OUT <span class="val" id="val-dev-' + devId + '-out">—</span></span></div>';
                    html += '</div>';
                });
                html += '</div>';
            }
            
            el.innerHTML = html;
            
            // Загружаем графики для NE (по всем портам)
            if (station.nes && station.nes.length) {
                station.nes.forEach(ne => {
                    const nePk = ne.id || ne.ne_id;
                    loadNePorts(nePk, ne.ne_status);
                });
            }
            
            // Загружаем графики для устройств
            if (station.devices && station.devices.length) {
                station.devices.forEach(d => {
                    const devId = d.id || d.device_id;
                    const devIp = d.device_ip;
                    loadDeviceChart(devId, devIp);
                });
            }
            
            // Загружаем IP-адреса
            loadStationIpsInPanel(slug);
            
        } catch (e) {
            el.innerHTML = '<div class="dispatch-loading">Ошибка: ' + e.message + '</div>';
        }
    }

    window.loadStationDetails = loadStationDetails;

    // Загрузка графика DDM для NE
    // Загрузка всех портов с DDM для NE
    async function loadNePorts(nePk, neStatus) {
        const container = document.getElementById('ne-ports-' + nePk);
        if (neStatus === 'unreachable') {
            if (container) {
                container.innerHTML = '<div class="dispatch-empty" style="padding: 10px; font-size: 11px; color: #8e8e8e;">Нет связи с NE (SNMP timeout)</div>';
            }
            return;
        }
        try {
            const resp = await fetch(`/api/v1/ne/${nePk}/ddm-ports/`);
            const data = await resp.json();
            const ports = data.ports || [];
            
            const container = document.getElementById('ne-ports-' + nePk);
            if (!container) return;
            
            if (!ports.length) {
                container.innerHTML = '<div class="dispatch-empty" style="padding: 10px; font-size: 11px; color: #8e8e8e;">Нет портов с DDM</div>';
                return;
            }
            
            let html = '';
            ports.forEach(port => {
                const inst = port.instance;
                html += '<div class="dispatch-chart-card" style="margin-bottom: 8px;" id="chart-ne-' + nePk + '-' + inst + '">';
                html += '<div class="dispatch-chart-card__header"><div class="dispatch-chart-card__title"><i class="bi bi-hdd-network"></i> Порт ' + inst + '</div><span class="dispatch-chart-card__badge">DDM</span></div>';
                html += '<div class="dispatch-chart-card__canvas-wrap"><canvas id="canvas-ne-' + nePk + '-' + inst + '"></canvas></div>';
                html += '<div class="dispatch-chart-card__footer"><span>RX <span class="val" id="val-ne-' + nePk + '-' + inst + '-rx">' + (port.rx != null ? port.rx.toFixed(2) + ' dBm' : '—') + '</span></span><span>TX <span class="val" id="val-ne-' + nePk + '-' + inst + '-tx">' + (port.tx != null ? port.tx.toFixed(2) + ' dBm' : '—') + '</span></span></div>';
                html += '</div>';
            });
            
            container.innerHTML = html;
            
            // Загружаем графики для каждого порта
            ports.forEach(port => {
                loadNeChartForPort(nePk, port.instance);
            });
        } catch (e) {
            console.error('[NE ports]', e);
        }
    }

    // Загрузка графика DDM для конкретного порта NE
    async function loadNeChartForPort(nePk, instance) {
        try {
            const [rxResp, txResp] = await Promise.all([
                fetch(`/api/v1/ne/${nePk}/metric-history/?key=sfp_rx_dbm&instance=${instance}&hours=24`),
                fetch(`/api/v1/ne/${nePk}/metric-history/?key=sfp_tx_dbm&instance=${instance}&hours=24`),
            ]);
            const rxData = await rxResp.json();
            const txData = await txResp.json();
            
            const rxPoints = rxData.points || [];
            const txPoints = txData.points || [];
            
            if (!rxPoints.length && !txPoints.length) return;
            
            const canvas = document.getElementById('canvas-ne-' + nePk + '-' + instance);
            if (!canvas) return;
            
            const labels = rxPoints.map(p => {
                const d = new Date(p.ts);
                return d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0');
            });
            
            new Chart(canvas.getContext('2d'), {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [
                        { label: 'RX', data: rxPoints.map(p => p.value), borderColor: '#0abfbc', borderWidth: 2, pointRadius: 0, tension: 0.2, fill: false },
                        { label: 'TX', data: txPoints.map(p => p.value), borderColor: '#ff780a', borderWidth: 2, pointRadius: 0, tension: 0.2, fill: false },
                    ],
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: { x: { display: false }, y: { display: false } },
                },
            });
            
            if (rxPoints.length) {
                const lastRx = rxPoints[rxPoints.length - 1].value;
                document.getElementById('val-ne-' + nePk + '-' + instance + '-rx').textContent = lastRx.toFixed(2) + ' dBm';
            }
            if (txPoints.length) {
                const lastTx = txPoints[txPoints.length - 1].value;
                document.getElementById('val-ne-' + nePk + '-' + instance + '-tx').textContent = lastTx.toFixed(2) + ' dBm';
            }
        } catch (e) {
            console.error('[NE chart port]', e);
        }
    }

    async function loadNeChart(nePk) {
        try {
            const [rxResp, txResp] = await Promise.all([
                fetch(`/api/v1/ne/${nePk}/metric-history/?key=sfp_rx_dbm&instance=1&hours=24`),
                fetch(`/api/v1/ne/${nePk}/metric-history/?key=sfp_tx_dbm&instance=1&hours=24`),
            ]);
            const rxData = await rxResp.json();
            const txData = await txResp.json();
            
            const rxPoints = rxData.points || [];
            const txPoints = txData.points || [];
            
            if (!rxPoints.length && !txPoints.length) return;
            
            const canvas = document.getElementById('canvas-ne-' + nePk);
            if (!canvas) return;
            
            const labels = rxPoints.map(p => {
                const d = new Date(p.ts);
                return d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0');
            });
            
            new Chart(canvas.getContext('2d'), {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [
                        { label: 'RX', data: rxPoints.map(p => p.value), borderColor: '#0abfbc', borderWidth: 2, pointRadius: 0, tension: 0.2, fill: false },
                        { label: 'TX', data: txPoints.map(p => p.value), borderColor: '#ff780a', borderWidth: 2, pointRadius: 0, tension: 0.2, fill: false },
                    ],
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: { x: { display: false }, y: { display: false } },
                },
            });
            
            // Обновляем значения
            if (rxPoints.length) {
                const lastRx = rxPoints[rxPoints.length - 1].value;
                document.getElementById('val-ne-' + nePk + '-rx').textContent = lastRx.toFixed(2) + ' dBm';
            }
            if (txPoints.length) {
                const lastTx = txPoints[txPoints.length - 1].value;
                document.getElementById('val-ne-' + nePk + '-tx').textContent = lastTx.toFixed(2) + ' dBm';
            }
        } catch (e) {
            console.error('[NE chart]', e);
        }
    }

    // Загрузка графика трафика для устройства
    async function loadDeviceChart(devId, devIp) {
        try {
            const portsResp = await fetch(`/api/v1/devices/${devIp}/interfaces/`);
            const ports = await portsResp.json();
            const physicalPorts = (Array.isArray(ports) ? ports : (ports.results || []))
                .filter(p => p.is_physical && p.oper_status === 1);
            
            if (!physicalPorts.length) return;
            
            // Загружаем трафик для всех портов
            const allTraffic = await Promise.all(
                physicalPorts.map(p => 
                    fetch(`/api/v1/interfaces/${p.id}/traffic-history/?hours=24`)
                        .then(r => r.json())
                        .catch(() => [])
                )
            );
            
            const byTs = {};
            allTraffic.forEach(points => {
                (points || []).forEach(p => {
                    const ts = p.ts;
                    if (!byTs[ts]) byTs[ts] = { in: 0, out: 0 };
                    byTs[ts].in += p.in_bps || 0;
                    byTs[ts].out += p.out_bps || 0;
                });
            });
            
            const tsList = Object.keys(byTs).sort();
            if (!tsList.length) return;
            
            const labels = tsList.map(ts => {
                const d = new Date(ts);
                return d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0');
            });
            
            const inMbps = tsList.map(ts => (byTs[ts].in || 0) / 1_000_000);
            const outMbps = tsList.map(ts => (byTs[ts].out || 0) / 1_000_000);
            
            const canvas = document.getElementById('canvas-dev-' + devId);
            if (!canvas) return;
            
            new Chart(canvas.getContext('2d'), {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [
                        { label: 'IN (все порты)', data: inMbps, borderColor: '#0abfbc', borderWidth: 2, pointRadius: 0, tension: 0.2, fill: false },
                        { label: 'OUT (все порты)', data: outMbps, borderColor: '#ff780a', borderWidth: 2, pointRadius: 0, tension: 0.2, fill: false },
                    ],
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: { x: { display: false }, y: { display: false } },
                },
            });
            
            if (inMbps.length) {
                document.getElementById('val-dev-' + devId + '-in').textContent = inMbps[inMbps.length - 1].toFixed(2) + ' Mbps';
            }
            if (outMbps.length) {
                document.getElementById('val-dev-' + devId + '-out').textContent = outMbps[outMbps.length - 1].toFixed(2) + ' Mbps';
            }
        } catch (e) {
            console.error('[Device chart]', e);
        }
    }


    // ─── Загрузка IP-адресов в левую панель ──────────────────
    async function loadStationIpsInPanel(slug) {
        const el = document.getElementById('dispatch-station-details');
        if (!el) return;
        
        try {
            const resp = await fetch(`/api/v1/stations/${slug}/ips/`);
            const data = await resp.json();
            const ips = data.ips || [];
            
            if (!ips.length) return;
            
            let html = '<div class="dispatch-station-section"><div class="dispatch-station-section-title">IP-адреса (' + ips.length + ')</div>';
            ips.forEach(ip => {
                const statusClass = ip.is_alive ? 'ok' : 'crit';
                const statusText = ip.is_alive ? 'живой' : 'мёртвый';
                html += '<div class="dispatch-ip-item" style="display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-bottom: 1px solid #22252b;">';
                html += '<i class="bi bi-pc-display" style="color: #0abfbc; font-size: 16px;"></i>';
                html += '<span style="font-family: monospace; color: #fff; font-size: 13px;">' + escapeHtml(ip.ip) + '</span>';
                html += '<span class="dispatch-chart-card__badge" style="margin-left: auto; background: ' + (ip.is_alive ? 'rgba(115,191,105,0.15)' : 'rgba(242,73,92,0.15)') + '; color: ' + (ip.is_alive ? '#73bf69' : '#f2495c') + '; font-size: 10px;">' + statusText + '</span>';
                if (ip.description) {
                    html += '<div style="font-size: 11px; color: #8e8e8e; margin-top: 2px; width: 100%;">' + escapeHtml(ip.description) + '</div>';
                }
                html += '</div>';
            });
            html += '</div>';
            
            el.insertAdjacentHTML('beforeend', html);
        } catch (e) {
            console.error('[IPs]', e);
        }
    }

    window.loadStationIpsInPanel = loadStationIpsInPanel;

    })();


// ── Контекстное меню карты ──────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    const mapEl = document.getElementById('stationsMap');
    const ctx = document.getElementById('map-context-menu');
    if (!mapEl || !ctx) return;

    mapEl.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        // «Опросить линии» меняет состояние — только с правом «Диспетчерская: изменение»
        const pollItem = ctx.querySelector('[data-action="poll"]');
        if (pollItem) pollItem.style.display = (window.Cerber && Cerber.can('stations')) ? '' : 'none';
        ctx.style.display = 'block';
        const rect = ctx.getBoundingClientRect();
        const x = Math.min(e.clientX, window.innerWidth - rect.width - 10);
        const y = Math.min(e.clientY, window.innerHeight - rect.height - 10);
        ctx.style.left = x + 'px';
        ctx.style.top = y + 'px';
    });

    document.addEventListener('click', (e) => {
        if (!ctx.contains(e.target)) ctx.style.display = 'none';
    });

    ctx.querySelectorAll('.map-ctx-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.stopPropagation();
            const action = item.dataset.action;
            ctx.style.display = 'none';

            if (action === 'refresh') {
                location.reload();
            } else if (action === 'poll') {
                const csrf = document.cookie.match(/csrftoken=([^;]+)/);
                fetch('/api/v1/optical-links/poll/', {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': csrf ? csrf[1] : '',
                        'Content-Type': 'application/json',
                    },
                })
                .then(r => r.json())
                .then(d => alert(d.ok ? 'Опрос запущен' : ('Ошибка: ' + d.error)))
                .catch(err => alert('Ошибка: ' + err));
            } else if (action === 'add-station') {
                window.location.href = '/adminui/stations/new/';
            } else if (action === 'add-link') {
                window.location.href = '/adminui/links/new/';
            }
        });
    });
});
