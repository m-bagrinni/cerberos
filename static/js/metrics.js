/**
 * Страница метрик устройства — графики через Chart.js (category scale).
 */

const API_BASE = '/api/v1';
const REFRESH_MS = 30000;
const DEFAULT_HOURS = 24;

const PALETTE = {
    cpu: '#ff780a',
    mem: '#0abfbc',
    temp: '#f2495c',
    default: '#5794f2',
};

const CHART_STYLE = {
    gridColor: 'rgba(255, 255, 255, 0.06)',
    textColor: '#8e8e8e',
    tooltipBg: '#1f2226',
    tooltipBorder: '#3d444b',
};

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

// --- Последние значения ---
async function loadLatest(ip) {
    const data = await apiGet(`/devices/${encodeURIComponent(ip)}/metrics/?latest=true`);
    console.log('[metrics] latest:', data.length, 'записей');
    for (const row of data) {
        // Ищем карточку по data-metric-key, потом внутри — [data-field="value"]
        const card = document.querySelector(`[data-metric-key="${row.metric_key}"]`);
        const el = card ? card.querySelector('[data-field="value"]') : null;
        if (!el) continue;
        let value = row.value;
        if (value !== null && value !== undefined) {
            const n = Number(value);
            value = Number.isInteger(n) ? String(n) : n.toFixed(2);
        } else {
            value = '—';
        }
        el.textContent = row.unit ? `${value} ${row.unit}` : value;
    }
}

// --- История метрики ---
async function loadHistory(ip, key, hours) {
    const data = await apiGet(
        `/devices/${encodeURIComponent(ip)}/metric-history/?key=${encodeURIComponent(key)}&hours=${hours}`
    );
    console.log(`[metrics] history ${key}:`, data.length, 'точек');
    return data;
}

// Форматирование времени для оси X
function fmtTime(iso, rangeHours) {
    const d = new Date(iso);
    if (rangeHours <= 24) {
        return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    }
    return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

// --- Графики ---
const charts = {};

async function buildChart(ip, key, hours = DEFAULT_HOURS) {
    const canvas = document.getElementById(`chart-${key}`);
    if (!canvas) {
        console.warn(`[metrics] canvas chart-${key} не найден`);
        return;
    }

    let data = [];
    try {
        data = await loadHistory(ip, key, hours);
    } catch (e) {
        console.error(`[metrics] loadHistory ${key}:`, e);
    }

    const unit = canvas.dataset.unit || '';
    const color = pickColor(key);

    const labels = data.map(row => fmtTime(row.ts, hours));
    const values = data.map(row => row.value);

    if (charts[key]) {
        charts[key].data.labels = labels;
        charts[key].data.datasets[0].data = values;
        charts[key].update('none');
        return;
    }

    charts[key] = new Chart(canvas, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: key,
                data: values,
                borderColor: color,
                backgroundColor: color + '22',
                borderWidth: 2,
                pointRadius: 0,
                pointHoverRadius: 4,
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
                    backgroundColor: CHART_STYLE.tooltipBg,
                    borderColor: CHART_STYLE.tooltipBorder,
                    borderWidth: 1,
                    titleColor: '#ffffff',
                    bodyColor: '#ccccdc',
                    padding: 8,
                    displayColors: false,
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
                    ticks: {
                        maxTicksLimit: 6,
                        font: { size: 10 },
                        autoSkip: true,
                    },
                    grid: { display: false },
                },
                y: {
                    beginAtZero: key.includes('cpu') || key.includes('mem'),
                    ticks: { font: { size: 10 } },
                    grid: { color: '#f3f4f6' },
                },
            },
        },
    });
}

// --- Range picker ---
function setupRangePickers(ip) {
    document.querySelectorAll('[data-range-picker]').forEach(picker => {
        const key = picker.dataset.metricKey;
        picker.querySelectorAll('button').forEach(btn => {
            btn.addEventListener('click', async () => {
                picker.querySelectorAll('button').forEach(b => {
                    b.style.background = '#f3f4f6';
                    b.style.color = '';
                });
                btn.style.background = '#eef2ff';
                btn.style.color = '#4f46e5';

                const hours = Number(btn.dataset.hours);
                await buildChart(ip, key, hours);
            });
        });
    });
}

// --- Main ---
document.addEventListener('DOMContentLoaded', async () => {
    const info = window.__METRICS__;
    console.log('[metrics] init:', info);

    if (!info || !info.ip || !info.keys?.length) {
        console.warn('[metrics] нет __METRICS__ — выход');
        return;
    }

    const { ip, keys } = info;

    try {
        await loadLatest(ip);
    } catch (e) {
        console.error('[metrics] loadLatest failed:', e);
    }

    for (const key of keys) {
        try {
            await buildChart(ip, key, DEFAULT_HOURS);
        } catch (e) {
            console.error(`[metrics] chart ${key}:`, e);
        }
    }

    setupRangePickers(ip);

    setInterval(async () => {
        try { await loadLatest(ip); } catch (e) { console.error(e); }
        for (const key of keys) {
            try { await buildChart(ip, key, DEFAULT_HOURS); } catch (e) {}
        }
    }, REFRESH_MS);

    console.log('[metrics] готово, авто-обновление каждые', REFRESH_MS / 1000, 'сек');
});
