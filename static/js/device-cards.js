/**
 * Плитки устройств + модальное окно с 4 вкладками.
 *
 * Поведение:
 *   - Клик по .device-card → открывается модалка
 *   - Клик по .device-modal__tab → переключение вкладки + fetch (lazy)
 *   - Клик по overlay / [data-modal-close] / Esc → закрыть
 *
 * Графики (charts): модуль modal-charts.js подгружается динамически,
 * т.к. <script> в innerHTML не выполняется браузером.
 */

const MODAL_URLS = window.__DEVICE_MODAL_URLS__ || {};

let currentIp = null;
let currentTab = null;
const tabCache = {};   // { "ip:tab": htmlString }
let chartsModuleLoaded = false;

// --- Утилиты ---
function $(sel, root = document) { return root.querySelector(sel); }
function $$(sel, root = document) { return Array.from(root.querySelectorAll(sel)); }

// --- Открытие модалки ---
function openModal(card) {
    const ip = card.dataset.deviceIp;
    if (!ip) return;

    const modal = $('#device-modal');
    if (!modal) return;

    $('#device-modal-name').textContent = card.dataset.deviceName || ip;
    $('#device-modal-subtitle').textContent = `${ip} · ${card.dataset.deviceSubtitle || ''}`;

    const statusDot = $('#device-modal-status');
    statusDot.className = 'device-modal__status device-modal__status--' + (card.dataset.deviceStatus || 'unknown');

    const fullLink = $('#device-modal-full-link');
    fullLink.href = card.dataset.deviceFullUrl || `/devices/${ip}/`;

    modal.hidden = false;
    document.body.style.overflow = 'hidden';

    currentIp = ip;
    activateTab('overview');
}

// --- Закрытие модалки ---
function closeModal() {
    const modal = $('#device-modal');
    if (!modal) return;
    modal.hidden = true;
    document.body.style.overflow = '';

    destroyModalCharts();
    currentIp = null;
    currentTab = null;
}

// --- Переключение вкладок ---
async function activateTab(tabName) {
    if (!currentIp) return;
    currentTab = tabName;

    $$('.device-modal__tab').forEach(btn => {
        btn.classList.toggle('is-active', btn.dataset.modalTab === tabName);
    });

    const body = $('#device-modal-body');
    const cacheKey = `${currentIp}:${tabName}`;

    if (tabCache[cacheKey]) {
        body.innerHTML = tabCache[cacheKey];
        afterTabRender(tabName, currentIp);
        return;
    }

    body.innerHTML = `<div class="device-modal__loading">
        <i class="bi bi-hourglass-split"></i>
        <p>Загрузка...</p>
    </div>`;

    destroyModalCharts();

    const urlTpl = MODAL_URLS[tabName];
    if (!urlTpl) {
        body.innerHTML = `<div class="c-empty"><p>Неизвестная вкладка: ${tabName}</p></div>`;
        return;
    }
    const url = urlTpl.replace('{ip}', encodeURIComponent(currentIp));

    try {
        const resp = await fetch(url, {
            headers: { 'X-Requested-With': 'fetch' },
            credentials: 'same-origin',
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const html = await resp.text();

        tabCache[cacheKey] = html;
        body.innerHTML = html;
        afterTabRender(tabName, currentIp);
    } catch (e) {
        body.innerHTML = `<div class="c-empty">
            <i class="bi bi-exclamation-triangle" style="color:#ef4444;font-size:32px"></i>
            <p style="margin-top:8px">Ошибка: ${e.message}</p>
        </div>`;
    }
}

// --- Пост-обработка после вставки HTML вкладки ---
async function afterTabRender(tabName, ip) {
    if (tabName === 'charts') {
        // Динамически подгружаем модуль графиков (если ещё не загружен)
        if (!chartsModuleLoaded) {
            try {
                await import('/static/js/modal-charts.js?v=2');
                chartsModuleLoaded = true;
            } catch (e) {
                console.error('[device-cards] failed to load modal-charts.js:', e);
                return;
            }
        }
        // Сообщаем модулю отрисовать графики для текущего ip
        window.dispatchEvent(new CustomEvent('cerber:modal-charts', { detail: { ip } }));
    }
}

// --- Уничтожение графиков в модалке ---
function destroyModalCharts() {
    if (!window.Chart) return;
    const canvases = $$('#device-modal-body canvas');
    canvases.forEach(canvas => {
        const existing = Chart.getChart(canvas);
        if (existing) {
            try { existing.destroy(); } catch (e) {}
        }
    });
}

// --- Делегирование событий ---
document.addEventListener('click', (e) => {
    const card = e.target.closest('.device-card');
    if (card) {
        e.preventDefault();
        openModal(card);
        return;
    }

    const tabBtn = e.target.closest('.device-modal__tab');
    if (tabBtn) {
        e.preventDefault();
        activateTab(tabBtn.dataset.modalTab);
        return;
    }

    const closeEl = e.target.closest('[data-modal-close]');
    if (closeEl) {
        e.preventDefault();
        closeModal();
        return;
    }
});

// --- Esc закрывает модалку ---
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const modal = $('#device-modal');
        if (modal && !modal.hidden) {
            closeModal();
        }
    }
});

// --- Инициализация: ?ip=... в URL → открыть модалку сразу ---
document.addEventListener('DOMContentLoaded', () => {
    const params = new URLSearchParams(location.search);
    const ip = params.get('ip');
    if (ip) {
        const card = document.querySelector(`.device-card[data-device-ip="${ip}"]`);
        if (card) openModal(card);
    }
});
