/**
 * Dashboard: master-detail с list устройств слева и панелью справа.
 */

const API_BASE = '/api/v1';
const PALETTE = {
    cpu: '#4f46e5',
    mem: '#10b981',
    temp: '#ef4444',
    default: '#6366f1',
};
const charts = {};

function pickColor(key) {
    if (key.includes('cpu')) return PALETTE.cpu;
    if (key.includes('mem')) return PALETTE.mem;
    if (key.includes('temp')) return PALETTE.temp;
    return PALETTE.default;
}

async function apiGet(path) {
    const r = await fetch(API_BASE + path, { headers: { Accept: 'application/json' } });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
}

// Выбор устройства — загружает правую панель через fetch
window.selectDevice = function (event, ip) {
    event.preventDefault();

    // 1) Обновить активный класс
    document.querySelectorAll('.dash-device').forEach(el => {
        el.classList.toggle('is-active', el.dataset.deviceIp === ip);
    });

    const panel = document.getElementById('device-panel');
    panel.classList.add('is-loading');
    panel.innerHTML = '<div class="dash-empty"><i class="bi bi-hourglass-split"></i><p>Загрузка...</p></div>';

    fetch(`/panel/${encodeURIComponent(ip)}/`, { headers: { 'X-Requested-With': 'fetch' } })
        .then(r => r.text())
        .then(html => {
            // 2) Уничтожаем старые графики ПОСЛЕ получения HTML, но ДО подмены DOM
            destroyAllCharts();

            // 3) Подменяем DOM
            panel.innerHTML = html;
            panel.classList.remove('is-loading');
            history.replaceState(null, '', `?ip=${encodeURIComponent(ip)}`);

            // 4) Ждём ДВА animationFrame — браузер успеет пролэйаутить canvas
            requestAnimationFrame(() => {
                requestAnimationFrame(() => {
                    initPanel(ip).catch(e => console.error('[dashboard] initPanel:', e));
                });
            });
        })
        .catch(e => {
            panel.classList.remove('is-loading');
            panel.innerHTML = `<div class="dash-empty">
                <i class="bi bi-exclamation-triangle" style="color:#ef4444"></i>
                <p>Ошибка загрузки: ${e.message}</p>
            </div>`;
        });

    return false;
};

function destroyAllCharts() {
    for (const id of Object.keys(charts)) {
        try {
            charts[id].destroy();
        } catch (e) { /* ignore */ }
        delete charts[id];
    }
    // На всякий случай — очистим Chart.instances для наших canvas
    if (window.Chart && Chart.instances) {
        for (const key of Object.keys(Chart.instances)) {
            const chart = Chart.instances[key];
            if (chart && chart.canvas && chart.canvas.id && chart.canvas.id.startsWith('chart-')) {
                try { chart.destroy(); } catch (e) { /* ignore */ }
            }
        }
    }
}

// Инициализация активной панели: метрики + графики
async function initPanel(ip) {
    await loadDeviceLatest(ip);
    await loadDeviceCharts(ip);
}

// Последние значения метрик
async function loadDeviceLatest(ip) {
    try {
        const data = await apiGet(`/devices/${encodeURIComponent(ip)}/metrics/?latest=true`);
        const panel = document.getElementById('device-panel');

        for (const row of data) {
            const el = panel.querySelector(
                `[data-metric-key="${row.metric_key}"][data-field="value"]`
            );
            if (!el) continue;
            let value = row.value;
            let numericValue = null;
            if (value !== null && value !== undefined) {
                numericValue = Number(value);
                value = Number.isInteger(numericValue) ? String(numericValue) : numericValue.toFixed(2);
            } else {
                value = '—';
            }
            el.textContent = row.unit ? `${value} ${row.unit}` : value;

            // Подсветка карточки метрики по warn/crit
            const card = panel.querySelector(`.dash-metric[data-metric-key="${row.metric_key}"]`);
            if (card && numericValue !== null) {
                const warn = parseFloat(card.dataset.warn) || null;
                const crit = parseFloat(card.dataset.crit) || null;

                let status = 'ok';
                if (crit !== null && numericValue >= crit) status = 'crit';
                else if (warn !== null && numericValue >= warn) status = 'warn';

                card.dataset.status = status;
                const valueEl = card.querySelector('.dash-metric__value');
                if (valueEl) {
                    valueEl.classList.remove('dash-metric__value--ok', 'dash-metric__value--warn', 'dash-metric__value--crit');
                    if (warn !== null || crit !== null) {
                        valueEl.classList.add(`dash-metric__value--${status}`);
                    }
                }
            }
        }
    } catch (e) {
        console.error(`[dashboard] loadDeviceLatest ${ip}:`, e);
    }
}

// Графики правой панели
async function loadDeviceCharts(ip) {
    const panel = document.getElementById('device-panel');
    if (!panel) return;

    const canvases = panel.querySelectorAll('canvas[data-metric-key]');
    for (const canvas of canvases) {
        const key = canvas.dataset.metricKey;
        await buildChart(ip, key, 24);
    }
}

async function refreshCharts(ip) {
    // Обновляем ТОЛЬКО данные существующих графиков, не пересоздавая их
    for (const [canvasId, chart] of Object.entries(charts)) {
        if (!canvasId.startsWith(`chart-${ip}-`)) continue;

        // Проверим что canvas ещё в DOM
        if (!document.getElementById(canvasId)) {
            try { chart.destroy(); } catch (e) {}
            delete charts[canvasId];
            continue;
        }

        const key = canvasId.replace(`chart-${ip}-`, '');
        const hours = chart.__hours || 24;

        try {
            const data = await apiGet(
                `/devices/${encodeURIComponent(ip)}/metric-history/?key=${encodeURIComponent(key)}&hours=${hours}`
            );
            const fmtTime = (iso) => {
                const d = new Date(iso);
                if (hours <= 24) {
                    return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
                }
                return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
            };
            chart.data.labels = data.map(r => fmtTime(r.ts));
            chart.data.datasets[0].data = data.map(r => r.value);
            chart.update('none');
        } catch (e) {
            console.error(`[dashboard] refreshCharts ${canvasId}:`, e);
        }
    }
}

async function buildChart(ip, key, hours) {
    const canvasId = `chart-${ip}-${key}`;
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;

    let data = [];
    try {
        data = await apiGet(
            `/devices/${encodeURIComponent(ip)}/metric-history/?key=${encodeURIComponent(key)}&hours=${hours}`
        );
    } catch (e) {
        console.error(`[dashboard] history ${ip} ${key}:`, e);
        return;
    }

    const fmtTime = (iso) => {
        const d = new Date(iso);
        if (hours <= 24) {
            return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
        }
        return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
    };

    const labels = data.map(row => fmtTime(row.ts));
    const values = data.map(row => row.value);
    const color = pickColor(key);
    const unit = canvas.dataset.unit || '';

    // Если chart уже существует — обновляем данные
    if (charts[canvasId]) {
        // Проверим что canvas ещё тот же
        if (charts[canvasId].canvas === canvas) {
            charts[canvasId].data.labels = labels;
            charts[canvasId].data.datasets[0].data = values;
            charts[canvasId].update('none');
            return;
        }
        // Canvas заменили — старый chart мёртв, уничтожаем
        try { charts[canvasId].destroy(); } catch (e) {}
        delete charts[canvasId];
    }

    // Если по какой-то причине на этом canvas уже висит Chart — снять
    if (window.Chart && Chart.getChart) {
        const existing = Chart.getChart(canvas);
        if (existing) {
            try { existing.destroy(); } catch (e) {}
        }
    }

    charts[canvasId] = new Chart(canvas, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: key,
                data: values,
                borderColor: color,
                backgroundColor: color + '22',
                borderWidth: 2,
                pointRadius: 0,
                pointHoverRadius: 3,
                tension: 0.25,
                fill: true,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: (ctx) => {
                            const v = ctx.parsed.y;
                            const val = v !== null && v !== undefined ? Number(v).toFixed(2) : '—';
                            return unit ? `${val} ${unit}` : String(val);
                        },
                    },
                },
            },
            scales: {
                x: {
                    type: 'category',
                    ticks: { maxTicksLimit: 5, font: { size: 9 }, autoSkip: true },
                    grid: { display: false },
                },
                y: {
                    beginAtZero: key.includes('cpu') || key.includes('mem'),
                    ticks: { font: { size: 9 } },
                    grid: { color: '#f3f4f6' },
                },
            },
        },
    });
}

// Range picker
document.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-range-picker] button');
    if (!btn) return;
    e.stopPropagation();

    const picker = btn.closest('[data-range-picker]');
    const key = picker.dataset.metricKey;
    const ip = picker.dataset.deviceIp;
    const hours = Number(btn.dataset.hours);

    picker.querySelectorAll('button').forEach(b => {
        b.classList.remove('is-active');
    });
    btn.classList.add('is-active');

    await buildChart(ip, key, hours);

    // обновим запомненное окно
    const canvasId = `chart-${ip}-${key}`;
    if (charts[canvasId]) {
        charts[canvasId].__hours = hours;
    }
});

// Initial
document.addEventListener('DOMContentLoaded', () => {
    const ip = window.__SELECTED_IP__;
    if (ip) {
        initPanel(ip);
    }

    // автообновление правой панели раз в минуту
    setInterval(() => {
        const active = document.querySelector('.dash-device.is-active');
        if (active) {
            const ip = active.dataset.deviceIp;
            loadDeviceLatest(ip);
            loadDeviceCharts(ip);
        }
    }, 60000);
});
