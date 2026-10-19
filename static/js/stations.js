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

    const STATUS_COLOR = {
        ok:       "#00ff00",   // зелёный
        warn:     "#ffcc00",   // жёлтый
        degraded: "#ff8800",   // оранжевый — link up, но RX плохой
        crit:     "#ff0000",   // красный — обрыв
        unknown:  "#00aaff",   // синий — нет данных
    };

    const STATUS_RU = {
        ok: "норма", warn: "предупреждение",
        crit: "авария", unknown: "нет данных",
    };

    let _map = null;
    let _railLayer;
    let _markersLayer = null;
    let _linksLayer = null;
    let _liveTimer = null;
    let _liveLastOk = null;
    let _liveErrors = 0;

    // ── Утилиты ─────────────────────────────────────────────
    function getCsrf() {
        const m = document.cookie.match(/csrftoken=([^;]+)/);
        return m ? m[1] : "";
    }
    function escapeHtml(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;")
            .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

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
        const r = await fetch("/stations/?partial=1", {
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
        root.innerHTML = '<div class="station-modal"><div class="station-modal__panel" style="padding:20px">Загрузка…</div></div>';

        let station, links;
        try {
            station = await fetchStation(slug);
            const all = await fetchLinks();
            links = all.filter((l) => l.station_a === slug || l.station_b === slug);
        } catch (e) {
            root.innerHTML = `<div class="station-modal"><div class="station-modal__panel" style="padding:20px">Ошибка: ${escapeHtml(e.message)}</div></div>`;
            return;
        }

        root.innerHTML = renderStationModal(station, links);
        root.querySelector("[data-action=close]").addEventListener("click", closeStationModal);
        root.querySelector(".station-modal").addEventListener("click", (ev) => {
            if (ev.target.classList.contains("station-modal")) closeStationModal();
        });
    }

    function closeStationModal() {
        document.getElementById("stationModalRoot").innerHTML = "";
    }

    function renderStationModal(st, links) {
        const devs = (st.devices || []).map((sd) => `
            <div class="station-modal__item">
                <span class="station-modal__item-icon">🔌</span>
                <div class="station-modal__item-body">
                    <span class="station-modal__item-name">${escapeHtml(sd.device_name)}</span>
                    <span class="station-modal__item-meta">
                        <span>${escapeHtml(sd.device_ip)}</span>
                        <span>${escapeHtml(sd.vendor || "")}</span>
                        <span class="station-modal__item-status station-modal__item-status--${sd.device_status}">${escapeHtml(sd.device_status)}</span>
                    </span>
                </div>
            </div>
        `).join("") || `<div class="station-modal__empty">Нет коммутаторов</div>`;

        const nes = (st.nes || []).map((sn) => `
            <div class="station-modal__item">
                <span class="station-modal__item-icon">🛰</span>
                <div class="station-modal__item-body">
                    <span class="station-modal__item-name">NE-${escapeHtml(String(sn.ne))}${sn.ne_display_name ? ' · ' + escapeHtml(sn.ne_display_name) : ''}</span>
                    <span class="station-modal__item-meta">
                        <span>${escapeHtml(sn.gateway_ip || "")}</span>
                        <span class="station-modal__item-status station-modal__item-status--${sn.ne_status}">${escapeHtml(sn.ne_status)}</span>
                    </span>
                </div>
            </div>
        `).join("") || `<div class="station-modal__empty">Нет мультиплексоров</div>`;

        const lnks = (links || []).map((l) => {
            const isA = l.station_a === st.slug;
            const peer = isA ? l.station_b : l.station_a;
            const portLabel = isA
                ? `${(l.port_a_type || "").toUpperCase()}${l.port_a_num}`
                : `${(l.port_b_type || "").toUpperCase()}${l.port_b_num}`;
            const rx = isA ? l.rx_a_dbm : l.rx_b_dbm;
            const cls = l.link_status === "ok" ? "ok" : l.link_status === "warn" ? "warn" : l.link_status === "crit" ? "crit" : "unknown";
            return `
            <div class="station-modal__item">
                <span class="station-modal__link-status station-modal__link-status--${l.link_status}"></span>
                <div class="station-modal__item-body">
                    <span class="station-modal__item-name">↔ ${escapeHtml(peer)} · ${escapeHtml(portLabel)}</span>
                    <span class="station-modal__item-meta">
                        <span>${escapeHtml(l.label || "—")}</span>
                        <span>RX: ${rx == null ? "—" : rx.toFixed(2) + " dBm"}</span>
                        <span class="station-modal__item-status station-modal__item-status--${cls}">${STATUS_RU[l.link_status] || l.link_status}</span>
                    </span>
                </div>
            </div>`;
        }).join("") || `<div class="station-modal__empty">Линий нет</div>`;

        return `
        <div class="station-modal">
            <div class="station-modal__panel">
                <div class="station-modal__head">
                    <div class="station-modal__title">
                        <span class="station-modal__name">${escapeHtml(st.name)}</span>
                        <span class="station-modal__slug">${escapeHtml(st.slug)}</span>
                    </div>
                    <div class="station-modal__actions">
                        <button class="btn btn--ghost btn-xs" data-action="close">✕</button>
                    </div>
                </div>
                <div class="station-modal__body">
                    <div class="station-modal__section">
                        <div class="station-modal__section-head">
                            <span class="station-modal__section-title">Коммутаторы</span>
                            <span class="station-modal__section-count">${(st.devices || []).length}</span>
                        </div>
                        <div class="station-modal__list">${devs}</div>
                    </div>
                    <div class="station-modal__section">
                        <div class="station-modal__section-head">
                            <span class="station-modal__section-title">Мультиплексоры</span>
                            <span class="station-modal__section-count">${(st.nes || []).length}</span>
                        </div>
                        <div class="station-modal__list">${nes}</div>
                    </div>
                    <div class="station-modal__section">
                        <div class="station-modal__section-head">
                            <span class="station-modal__section-title">Оптические линии</span>
                            <span class="station-modal__section-count">${(links || []).length}</span>
                        </div>
                        <div class="station-modal__list">${lnks}</div>
                    </div>
                </div>
            </div>
        </div>`;
    }

    // ── Статус-агрегат станции ──────────────────────────────
    function aggStatusOf(s) {
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
            unknown:  ["⚪", "нет данных"],
        };
        const [icon, text] = map[status] || ["?", status];
        return '<span class="tt-status tt-status--' + status + '">' + icon + " " + text + '</span>';
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
        const ddmA = l.ddm_a || {};
        const endA = [
            '<div class="tt-side">',
            '<div class="tt-side__head">А: ' + escapeHtml(l.end_a_label || "—") + '</div>',
            '<div class="tt-side__row"><span class="tt-k">RX:</span>   ' + fmtDdm(ddmA.rx_dbm, "dBm") + '</div>',
            '<div class="tt-side__row"><span class="tt-k">TX:</span>   ' + fmtDdm(ddmA.tx_dbm, "dBm") + '</div>',
            '<div class="tt-side__row"><span class="tt-k">Temp:</span> ' + fmtDdm(ddmA.temp_c, "°C", 1) + '</div>',
            '<div class="tt-side__row"><span class="tt-k">Bias:</span> ' + fmtDdm(ddmA.bias_ma, "mA", 1) + '</div>',
            '<div class="tt-side__row"><span class="tt-k">Volt:</span> ' + fmtDdm(ddmA.volt_v, "V", 2) + '</div>',
            '</div>',
        ].join("");

        // Конец B
        const ddmB = l.ddm_b || {};
        const endB = [
            '<div class="tt-side">',
            '<div class="tt-side__head">Б: ' + escapeHtml(l.end_b_label || "—") + '</div>',
            '<div class="tt-side__row"><span class="tt-k">RX:</span>   ' + fmtDdm(ddmB.rx_dbm, "dBm") + '</div>',
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

        const stations = await fetchStations();
        const links = await fetchLinks();

        if (!_map) {
            _map = L.map("stationsMap", {
                center: [42.7, 74.6],
                zoom: 8,
                zoomControl: true,
                attributionControl: true,
            });
            // ============================================================
            // Базовые слои карты (переключатель в правом верхнем углу)
            // ============================================================
            const baseLayers = {
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
            const savedLayer = localStorage.getItem("cerber-map-layer") || "🌍 OpenTopoMap (топография)";
            const initialLayer = baseLayers[savedLayer] || baseLayers["🌍 OpenTopoMap (топография)"];
            initialLayer.addTo(_map);

            // Ж/Д линии — отдельный слой ПОД маркерами/линками
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

            // Запоминаем выбор
            _map.on("baselayerchange", function (e) {
                localStorage.setItem("cerber-map-layer", e.name);
            });
        } else {
            _markersLayer.clearLayers();
            _linksLayer.clearLayers();
            if (_railLayer) _railLayer.clearLayers();
        }

        // Загружаем ж/д линии (один раз, кеш)
        await loadRailTracks();

        const bySlug = {};
        stations.forEach((s) => { bySlug[s.slug] = s; });

        // Линии — дуги Безье
        links.forEach((l) => {
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

            // 1) Внешний glow (самый широкий, полупрозрачный)
            L.polyline(pts, {
                color: color, weight: 12, opacity: 0.18,
                lineCap: "round", lineJoin: "round",
            }).addTo(_linksLayer);

            // 2) Белая обводка (средняя)
            L.polyline(pts, {
                color: "#ffffff", weight: 7, opacity: 0.85,
                lineCap: "round", lineJoin: "round",
            }).addTo(_linksLayer);

            // 3) Цветная линия (основная)
            const line = L.polyline(pts, {
                color: color,
                weight: 4,
                opacity: 1,
                dashArray: l.link_status === "unknown" ? "12,8" : null,
                lineCap: "round", lineJoin: "round",
                className: statusClass,
            }).addTo(_linksLayer);

            line.bindTooltip(buildLinkTooltip(l), {
                sticky: true,
                direction: "top",
                opacity: 0.95,
                className: "link-tooltip",
            });

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
                '<button class="station-popup__btn" onclick="window.openStationModal(\'' + s.slug + '\')">Подробности</button>' +
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

        console.log("[map] rendered: " + stations.length + " stations, " + links.length + " links");
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
            console.log("[live] map: " + updated + " lines recolored");
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
        _liveTimer = setInterval(liveTick, LIVE_INTERVAL_MS);
        liveTick();
    }
    function stopStationsLive() {
        if (_liveTimer) { clearInterval(_liveTimer); _liveTimer = null; }
    }

    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) liveTick();
    });

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
        try {
            const resp = await fetch("/static/geojson/kyrgyzstan_railways.geojson");
            if (!resp.ok) {
                console.warn("[rail] не удалось загрузить:", resp.status);
                return;
            }
            const geojson = await resp.json();
            _railLoaded = true;

            L.geoJSON(geojson, {
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
                        "<b>" + name + "</b><br>OSM: " + osmId,
                        { sticky: true, className: "rail-tooltip", direction: "top" }
                    );
                },
            }).addTo(_railLayer);

            console.log("[rail] загружено:", geojson.features.length, "сегментов");
        } catch (e) {
            console.warn("[rail] ошибка:", e);
        }
    }

    })();
