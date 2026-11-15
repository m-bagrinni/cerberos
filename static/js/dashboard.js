/*
 * Страница /local/ — дерево устройств + панель деталей.
 *
 *  - раскрытие категорий/подкатегорий (состояние запоминается в localStorage);
 *  - поиск по имени/IP, фильтр «только проблемные» и по вендору;
 *  - выбранное устройство — в адресе (#ip), переживает перезагрузку;
 *  - раз в 30 с опрос /local/tree-status/: лампочки, счётчики, подсказки, алерты в панели;
 *  - все данные с сервера вставляются через esc() — sysDescr и прочее приходят с устройств.
 *
 * Глобальные функции loadDeviceTab и window._currentDeviceIp использует port-control.js.
 */
(function () {
    "use strict";

    const root = document.getElementById("device-detail-content");
    if (!root) return;

    const STATUS_URL = root.dataset.statusUrl;
    const SHOW_SECRETS = root.dataset.showSecrets === "1";
    const INTERVAL_MS = 30000;
    const STALE_POLL_MS = 10 * 60 * 1000;     // устройство опрашивается раз в 3 мин — 10 мин тишины = проблема
    const STALE_PAGE_MS = 3 * INTERVAL_MS;    // страница не получала статусы 1.5 мин
    const LAMPS = ["ok", "warn", "crit", "unknown"];
    const STORE_KEY = "cerber.dashboard.tree";

    const tree = document.getElementById("devices-tree");
    const panel = document.getElementById("device-detail-panel");
    const searchEl = document.getElementById("tree-search");
    const problemsEl = document.getElementById("tree-problems");
    const vendorEl = document.getElementById("tree-vendor");
    const emptyEl = document.getElementById("tree-empty");
    const updatedEl = document.getElementById("dash-updated");

    let lastState = null;      // последний ответ tree-status
    let lastOkAt = Date.now();

    // ── утилиты ────────────────────────────────────────────────
    function esc(v) {
        return String(v ?? "").replace(/[&<>"']/g, c => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        }[c]));
    }
    const orDash = v => (v === null || v === undefined || v === "") ? "—" : esc(v);

    function ago(iso) {
        if (!iso) return "—";
        const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
        if (s < 60) return `${s} с назад`;
        if (s < 3600) return `${Math.round(s / 60)} мин назад`;
        if (s < 86400) return `${Math.round(s / 3600)} ч назад`;
        return new Date(iso).toLocaleString("ru-RU");
    }
    const isStale = iso => !iso || (Date.now() - new Date(iso).getTime()) > STALE_POLL_MS;

    function setLamp(dot, base, lamp) {
        if (!dot || !LAMPS.includes(lamp)) return;
        LAMPS.forEach(l => dot.classList.remove(`${base}--${l}`));
        dot.classList.add(`${base}--${lamp}`);
    }
    function lampOf(deviceEl) {
        const dot = deviceEl.querySelector(".tree-device__dot");
        return LAMPS.find(l => dot && dot.classList.contains(`tree-device__dot--${l}`)) || "unknown";
    }

    function storeLoad() {
        try { return JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch (e) { return {}; }
    }
    function storeSave(patch) {
        try { localStorage.setItem(STORE_KEY, JSON.stringify({ ...storeLoad(), ...patch })); } catch (e) { /* приватный режим */ }
    }

    // ── раскрытие веток ────────────────────────────────────────
    function saveOpenState() {
        if (filterActive()) return;  // во время поиска ветки раскрыты принудительно — не запоминаем
        storeSave({
            closedCats: [...tree.querySelectorAll(".tree-category:not(.is-open)")].map(el => el.dataset.category),
            openSubs: [...tree.querySelectorAll(".tree-subcategory.is-open")].map(el => el.dataset.subcategory),
        });
    }
    function restoreOpenState() {
        const st = storeLoad();
        const closed = new Set(st.closedCats || []);
        const open = new Set(st.openSubs || []);
        tree.querySelectorAll(".tree-category").forEach(el => el.classList.toggle("is-open", !closed.has(el.dataset.category)));
        tree.querySelectorAll(".tree-subcategory").forEach(el => el.classList.toggle("is-open", open.has(el.dataset.subcategory)));
    }

    tree.querySelectorAll(".tree-category__title").forEach(title => {
        title.addEventListener("click", () => {
            title.closest(".tree-category").classList.toggle("is-open");
            saveOpenState();
        });
    });
    tree.querySelectorAll(".tree-subcategory__title").forEach(title => {
        title.addEventListener("click", e => {
            e.stopPropagation();
            title.closest(".tree-subcategory").classList.toggle("is-open");
            saveOpenState();
        });
    });

    // ── поиск и фильтры ────────────────────────────────────────
    function filterActive() {
        return Boolean(searchEl?.value.trim() || problemsEl?.checked || vendorEl?.value);
    }

    function applyFilter() {
        const q = (searchEl?.value || "").trim().toLowerCase();
        const onlyProblems = Boolean(problemsEl?.checked);
        const vendor = vendorEl?.value || "";
        const active = filterActive();
        let shown = 0;

        tree.querySelectorAll(".tree-device").forEach(el => {
            const okText = !q || el.dataset.name.toLowerCase().includes(q) || el.dataset.slug.includes(q);
            const okProblem = !onlyProblems || lampOf(el) !== "ok";
            const okVendor = !vendor || el.dataset.vendor === vendor;
            const visible = okText && okProblem && okVendor;
            el.hidden = !visible;
            if (visible) shown++;
        });

        tree.querySelectorAll(".tree-subcategory").forEach(sub => {
            const any = sub.querySelector(".tree-device:not([hidden])");
            sub.hidden = active && !any;
            if (active && any) sub.classList.add("is-open");
        });
        tree.querySelectorAll(".tree-category").forEach(cat => {
            const any = cat.querySelector(".tree-device:not([hidden])");
            cat.hidden = active && !any;
            if (active && any) cat.classList.add("is-open");
        });

        if (!active) restoreOpenState();
        if (emptyEl) emptyEl.hidden = !(active && shown === 0);
        storeSave({ problems: onlyProblems, vendor });
    }

    searchEl?.addEventListener("input", applyFilter);
    searchEl?.addEventListener("keydown", e => {
        if (e.key === "Escape") { searchEl.value = ""; applyFilter(); }
        if (e.key === "Enter") {  // Enter — открыть первое найденное
            const first = tree.querySelector(".tree-device:not([hidden])");
            if (first) selectDevice(first);
        }
    });
    // «/» — фокус в поиск (как в GitHub/Grafana), если курсор не в поле ввода
    document.addEventListener("keydown", e => {
        if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
        if (e.target.closest("input, textarea, select, [contenteditable]")) return;
        e.preventDefault();
        searchEl?.focus();
        searchEl?.select();
    });
    problemsEl?.addEventListener("change", applyFilter);
    vendorEl?.addEventListener("change", applyFilter);

    // ── выбор устройства ───────────────────────────────────────
    function selectDevice(el, { scroll = false } = {}) {
        tree.querySelectorAll(".tree-device").forEach(x => x.classList.remove("is-active"));
        el.classList.add("is-active");
        if (scroll) {
            el.closest(".tree-category")?.classList.add("is-open");
            el.closest(".tree-subcategory")?.classList.add("is-open");
            el.scrollIntoView({ block: "center" });
        }
        history.replaceState(null, "", `#${el.dataset.slug}`);
        loadDeviceDetail(el.dataset.slug);
    }
    tree.querySelectorAll(".tree-device").forEach(el => el.addEventListener("click", () => selectDevice(el)));

    function alertsHtml(alerts) {
        if (!alerts || !alerts.length) return "";
        return alerts.map(a => `
            <div class="detail-alert detail-alert--${esc(a.severity)}">
                <i class="bi bi-exclamation-triangle"></i>
                <span>${esc(a.message)}</span>
                <span class="detail-alert__since" title="${esc(a.started_at ? new Date(a.started_at).toLocaleString("ru-RU") : "")}">с ${esc(ago(a.started_at))}</span>
            </div>`).join("");
    }

    const STATUS_VIEW = {
        ok: ["Доступно", "var(--ok)"],
        unreachable: ["Недоступно", "var(--crit)"],
        unknown: ["Неизвестно", "var(--unknown)"],
    };

    // Живые строки панели: статус, алерты, давность опроса — обновляются опросом без перерисовки
    function refreshPanelLive() {
        const ip = window._currentDeviceIp;
        const live = lastState?.devices?.[ip];
        if (!live) return;
        const [text, color] = STATUS_VIEW[live.status] || STATUS_VIEW.unknown;
        const statusEl = panel.querySelector("[data-live=status]");
        if (statusEl) { statusEl.textContent = text; statusEl.style.color = color; }
        setLamp(panel.querySelector("[data-live=lamp]"), "dot", live.lamp);
        const alertsEl = panel.querySelector("[data-live=alerts]");
        if (alertsEl) alertsEl.innerHTML = alertsHtml(live.alerts);
        const polledEl = panel.querySelector("[data-live=polled]");
        if (polledEl) {
            polledEl.textContent = ago(live.last_polled_at);
            polledEl.title = live.last_polled_at ? new Date(live.last_polled_at).toLocaleString("ru-RU") : "";
            polledEl.classList.toggle("is-stale", isStale(live.last_polled_at));
        }
        const pingEl = panel.querySelector("[data-live=ping]");
        if (pingEl) {
            pingEl.textContent = ago(live.last_ping_at);
            pingEl.classList.toggle("is-stale", isStale(live.last_ping_at));
        }
    }

    async function loadDeviceDetail(ip) {
        window._currentDeviceIp = ip;
        panel.innerHTML = '<div class="detail-empty"><div>Загрузка...</div></div>';
        try {
            const resp = await fetch(`/api/v1/devices/${encodeURIComponent(ip)}/`, { credentials: "same-origin" });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const d = await resp.json();
            if (window._currentDeviceIp !== ip) return;  // пока грузилось, выбрали другое
            const eip = encodeURIComponent(d.ip);
            const tab = (name, label) =>
                `<a href="#" class="detail-tab" data-tab="${name}">${label}</a>`;

            const lamp = lastState?.devices?.[ip]?.lamp || "unknown";
            const row = (label, value, mod = "", live = "") =>
                `<div class="detail-row"><span class="detail-row__label">${label}</span>` +
                `<span class="detail-row__value${mod ? " detail-row__value--" + mod : ""}"${live ? ` data-live="${live}"` : ""}>${value}</span></div>`;

            panel.innerHTML = `
                <div class="detail-head">
                    <div>
                        <h2 class="detail-title"><span class="dot dot--${esc(lamp)}" data-live="lamp"></span>${esc(d.name)}</h2>
                        <p class="detail-subtitle">${esc(d.ip)}${d.description ? " · " + esc(d.description) : ""}</p>
                    </div>
                    <div class="detail-actions">
                        <a href="http://${esc(d.ip)}/" target="_blank" rel="noopener" class="btn-ghost btn-ghost--accent"><i class="bi bi-box-arrow-up-right"></i> Веб-интерфейс</a>
                        <a href="/local/devices/${eip}/" class="btn-ghost" title="Открыть страницу устройства"><i class="bi bi-arrows-angle-expand"></i></a>
                    </div>
                </div>
                <nav class="detail-tabs">
                    ${tab("overview", "Обзор")}${tab("metrics", "Метрики")}${tab("interfaces", "Интерфейсы")}
                    ${tab("vlans", "VLAN")}${tab("sfp", "SFP")}${tab("alerts", "События")}
                </nav>
                <div class="detail-alerts" data-live="alerts"></div>
                <div class="detail-grid">
                    <section class="detail-section">
                        <div class="detail-section__title">Состояние</div>
                        ${row("Статус", orDash(d.status_display), "", "status")}
                        ${row("Последний опрос", "—", "", "polled")}
                        ${row("Последний ping", "—", "", "ping")}
                    </section>
                    <section class="detail-section">
                        <div class="detail-section__title">Устройство</div>
                        ${row("Вендор", orDash(d.vendor_name))}
                        ${row("Профиль", orDash(d.profile_name))}
                        ${row("Модель", orDash(d.ent_model))}
                        ${row("Категория", orDash(d.category_display) + (d.subcategory_name ? " / " + esc(d.subcategory_name) : ""))}
                    </section>
                    <section class="detail-section">
                        <div class="detail-section__title">SNMP</div>
                        ${row("Версия", orDash(d.snmp_version), "mono")}
                        ${row("Порт", orDash(d.snmp_port), "mono")}
                        ${SHOW_SECRETS ? row("Community", orDash(d.snmp_community), "mono") : ""}
                        ${row("sysObjectID", orDash(d.sys_object_id), "mono")}
                    </section>
                    ${d.sys_descr ? `<section class="detail-section detail-section--wide">
                        <div class="detail-section__title">sysDescr</div>
                        <div class="detail-row__value detail-row__value--small">${esc(d.sys_descr)}</div>
                    </section>` : ""}
                </div>`;

            panel.querySelectorAll(".detail-tab[data-tab]").forEach(a => a.addEventListener("click", e => {
                e.preventDefault();
                loadDeviceTab(ip, a.dataset.tab, a);
            }));
            refreshPanelLive();
        } catch (e) {
            panel.innerHTML = `<div class="detail-empty"><div style="color: #f2495c;">Ошибка: ${esc(e.message)}</div></div>`;
        }
    }

    // ── вкладки панели (HTML-фрагменты с сервера) ──────────────
    async function loadDeviceTab(ip, tab, btn) {
        panel.querySelectorAll(".detail-tab").forEach(x => x.classList.remove("is-active"));
        if (btn) btn.classList.add("is-active");
        panel.querySelectorAll(".detail-grid").forEach(el => { el.style.display = "none"; });

        let contentDiv = document.getElementById("device-tab-content");
        if (!contentDiv) {
            contentDiv = document.createElement("div");
            contentDiv.id = "device-tab-content";
            panel.appendChild(contentDiv);
        }
        contentDiv.style.display = "block";
        contentDiv.innerHTML = '<div class="detail-empty"><div>Загрузка...</div></div>';

        try {
            const resp = await fetch(`/local/devices/${encodeURIComponent(ip)}/tab/${encodeURIComponent(tab)}/`, { credentials: "same-origin" });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            contentDiv.innerHTML = await resp.text();

            // innerHTML не исполняет <script> — пересоздаём (внешние грузим один раз)
            const loaded = window.__loadedScripts = window.__loadedScripts || {};
            contentDiv.querySelectorAll("script").forEach(old => {
                const src = old.src;
                if (src && loaded[src]) return;
                if (src) loaded[src] = true;
                const s = document.createElement("script");
                if (src) s.src = src; else s.textContent = old.textContent;
                if (old.type) s.type = old.type;
                contentDiv.appendChild(s);
            });

            if (tab === "metrics") {
                if (!window.__metricsLoaded) {
                    window.__metricsLoaded = true;
                    const s = document.createElement("script");
                    s.type = "module";
                    s.src = "/static/js/metrics.js?v=1793429171";
                    s.onload = () => { if (typeof window.initMetrics === "function") window.initMetrics(); };
                    contentDiv.appendChild(s);
                } else if (typeof window.initMetrics === "function") {
                    window.initMetrics();
                }
            }
            if (tab === "interfaces" && !window.__portControlLoaded) {
                window.__portControlLoaded = true;
                ["/static/js/port-control.js?v=1793429171", "/static/js/interface-edit.js?v=1793429171"].forEach(src => {
                    const s = document.createElement("script");
                    s.src = src;
                    contentDiv.appendChild(s);
                });
            }
        } catch (e) {
            contentDiv.innerHTML = `<div class="detail-empty"><div style="color: #f2495c;">Ошибка: ${esc(e.message)}</div></div>`;
        }
    }
    window.loadDeviceTab = loadDeviceTab;
    window.loadDeviceDetail = loadDeviceDetail;

    // ── автообновление статусов ────────────────────────────────
    function applyState(data) {
        lastState = data;

        tree.querySelectorAll(".tree-device[data-slug]").forEach(el => {
            const dev = data.devices[el.dataset.slug];
            if (!dev) return;
            setLamp(el.querySelector(".tree-device__dot"), "tree-device__dot", dev.lamp);
            const [text] = STATUS_VIEW[dev.status] || STATUS_VIEW.unknown;
            el.title = [text, ...dev.alerts.map(a => a.message)].join(" · ");
        });
        tree.querySelectorAll(".tree-category[data-category]").forEach(el => {
            const c = data.categories[el.dataset.category];
            if (!c) return;
            setLamp(el.querySelector(".tree-category__dot"), "tree-category__dot", c.lamp);
            const cnt = el.querySelector(".tree-category__title [data-count]");
            if (cnt) cnt.textContent = c.count;
        });
        tree.querySelectorAll(".tree-subcategory[data-subcategory]").forEach(el => {
            const s = data.subcategories[el.dataset.subcategory];
            if (!s) return;
            setLamp(el.querySelector(".tree-subcategory__dot"), "tree-subcategory__dot", s.lamp);
        });

        Object.entries(data.stats).forEach(([k, v]) => {
            root.querySelectorAll(`[data-stat="${k}"]`).forEach(el => { el.textContent = v; });
            const box = root.querySelector(`[data-stat-box="${k}"]`);
            if (box) box.hidden = !v;
        });
        const ne = data.ne_stats || {};
        root.querySelectorAll("[data-ne]").forEach(el => { el.textContent = ne[el.dataset.ne] ?? "—"; });
        const neBox = root.querySelector("[data-ne-box]");
        if (neBox) neBox.hidden = !ne.problem;
        document.getElementById("dash-ne")?.classList.toggle("chip--warn", Boolean(ne.problem));

        if (problemsEl?.checked) applyFilter();  // цвета поменялись — состав «проблемных» тоже
        refreshPanelLive();
        renderAttention(data);
    }

    // Пока устройство не выбрано — правая панель показывает, что требует внимания
    const LAMP_ORDER = { crit: 0, warn: 1, unknown: 2 };
    function renderAttention(data) {
        const box = document.getElementById("attention");
        const list = document.getElementById("attention-list");
        if (!box || !list) return;  // панель уже занята выбранным устройством

        const items = [...tree.querySelectorAll(".tree-device[data-slug]")]
            .map(el => ({ el, dev: data.devices[el.dataset.slug] }))
            .filter(x => x.dev && x.dev.lamp !== "ok")
            .sort((a, b) => (LAMP_ORDER[a.dev.lamp] ?? 9) - (LAMP_ORDER[b.dev.lamp] ?? 9));

        box.hidden = items.length === 0;
        list.innerHTML = items.map(({ el, dev }) => {
            const [statusText] = STATUS_VIEW[dev.status] || STATUS_VIEW.unknown;
            const reason = dev.alerts.length ? dev.alerts[0].message : statusText;
            const more = dev.alerts.length > 1 ? ` <span class="attention__more">+${dev.alerts.length - 1}</span>` : "";
            return `<button type="button" class="attention__item" data-ip="${esc(el.dataset.slug)}">
                        <span class="dot dot--${esc(dev.lamp)}"></span>
                        <span class="attention__name">${esc(el.dataset.name)}</span>
                        <span class="attention__ip">${esc(el.dataset.slug)}</span>
                        <span class="attention__reason">${esc(reason)}${more}</span>
                    </button>`;
        }).join("");
        list.querySelectorAll(".attention__item").forEach(btn => btn.addEventListener("click", () => {
            const el = [...tree.querySelectorAll(".tree-device")].find(x => x.dataset.slug === btn.dataset.ip);
            if (el) selectDevice(el, { scroll: true });
        }));
    }

    function renderUpdated() {
        if (!updatedEl) return;
        const stale = Date.now() - lastOkAt > STALE_PAGE_MS;
        updatedEl.textContent = stale
            ? `нет связи с сервером ${ago(new Date(lastOkAt).toISOString())}`
            : `обновлено ${ago(new Date(lastOkAt).toISOString())}`;
        updatedEl.classList.toggle("is-stale", stale);
    }

    async function refresh() {
        try {
            const r = await fetch(STATUS_URL, { credentials: "same-origin", cache: "no-store" });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            applyState(await r.json());  // истёкшая сессия вернёт HTML логина — упадёт в catch
            lastOkAt = Date.now();
        } catch (e) {
            console.warn("tree-status:", e);
        }
        renderUpdated();
    }

    let timer = null;
    const start = () => { if (!timer) timer = setInterval(refresh, INTERVAL_MS); };
    const stop = () => { clearInterval(timer); timer = null; };
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) { stop(); } else { refresh(); start(); }
    });
    setInterval(renderUpdated, 5000);  // «обновлено N с назад» тикает между опросами

    // ── старт ──────────────────────────────────────────────────
    const saved = storeLoad();
    if (problemsEl) problemsEl.checked = Boolean(saved.problems);
    if (vendorEl && saved.vendor && [...vendorEl.options].some(o => o.value === saved.vendor)) vendorEl.value = saved.vendor;
    restoreOpenState();
    applyFilter();

    const hashIp = decodeURIComponent(location.hash.slice(1));
    const fromHash = hashIp && [...tree.querySelectorAll(".tree-device")].find(el => el.dataset.slug === hashIp);
    if (fromHash) selectDevice(fromHash, { scroll: true });

    refresh();  // сразу подтягиваем алерты/давность опроса для панели
    if (!document.hidden) start();
})();

// Используется разметкой меню на других страницах с тем же шаблоном
function closeAllMenus() {
    document.querySelectorAll(".device-ctx-menu.is-open").forEach(m => m.classList.remove("is-open"));
}
