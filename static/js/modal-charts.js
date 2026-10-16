/**
 * Графики в модалке — сгруппированные метрики.
 * Каждая группа — один canvas с несколькими линиями (например, CPU 5s/1m/5m).
 */

const API_BASE = '/api/v1';

// Палитра для линий внутри группы
const GROUP_PALETTE = [
    '#ff780a',  // primary (оранжевый)
    '#0abfbc',  // tiffany
    '#5794f2',  // blue
    '#73bf69',  // green
    '#f2cc0c',  // yellow
    '#f2495c',  // red
    '#b877d9',  // purple
];

// Отдельная палитра для типов групп (оставляем для возможного использования)
const TYPE_COLORS = {
    cpu: '#ff780a',
    mem: '#0abfbc',
    flash: '#5794f2',
    temp: '#f2495c',
    uptime: '#73bf69',
    power: '#f2cc0c',
    other: '#8e8e8e',
};

async function apiGet(path) {
    const r = await fetch(API_BASE + path, { headers: { Accept: 'application/json' } });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
}

function fmtTime(iso, rangeHours) {
    const d = new Date(iso);
    if (rangeHours <= 24) {
        return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    }
    return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

const charts = {};

async function buildGroupChart(ip, groupId, keys, hours = 24) {
    const canvasId = `modal-chart-${ip}-${groupId}`;
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;

    // Загружаем истории всех метрик группы
    const datasetsData = [];
    let commonLabels = null;

    for (let i = 0; i < keys.length; i++) {
        const key = keys[i];
        let data = [];
        try {
            data = await apiGet(
                `/devices/${encodeURIComponent(ip)}/metric-history/?key=${encodeURIComponent(key)}&hours=${hours}`
            );
        } catch (e) {
            console.error(`[modal-charts] history ${ip} ${key}:`, e);
            continue;
        }

        const labels = data.map(r => fmtTime(r.ts, hours));
        const values = data.map(r => r.value);
        // Используем labels от первой метрики (у всех примерно одинаковые)
        if (!commonLabels) commonLabels = labels;

        datasetsData.push({
            label: key,
            data: values,
            color: GROUP_PALETTE[i % GROUP_PALETTE.length],
        });
    }

    if (!datasetsData.length) return;

    const datasets = datasetsData.map(d => ({
        label: d.label,
        data: d.data,
        borderColor: d.color,
        backgroundColor: d.color + '22',
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 3,
        tension: 0.25,
        fill: false,
    }));

    // Если chart уже есть — обновляем
    if (charts[canvasId]) {
        if (charts[canvasId].canvas === canvas) {
            charts[canvasId].data.labels = commonLabels;
            charts[canvasId].data.datasets = datasets;
            charts[canvasId].update('none');
            return;
        }
        try { charts[canvasId].destroy(); } catch (e) {}
        delete charts[canvasId];
    }

    // Если на canvas висит Chart (после innerHTML) — снять
    if (window.Chart && Chart.getChart) {
        const existing = Chart.getChart(canvas);
        if (existing) {
            try { existing.destroy(); } catch (e) {}
        }
    }

    charts[canvasId] = new Chart(canvas, {
        type: 'line',
        data: {
            labels: commonLabels,
            datasets: datasets,
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },   // легенда в HTML под canvas
                tooltip: {
                    callbacks: {
                        label: (ctx) => {
                            const v = ctx.parsed.y;
                            const val = v !== null && v !== undefined ? Number(v).toFixed(2) : '—';
                            return `${ctx.dataset.label}: ${val}`;
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
                    beginAtZero: groupId === 'cpu' || groupId === 'mem',
                    ticks: { font: { size: 9 } },
                    grid: { color: 'rgba(255,255,255,0.06)' },
                },
            },
        },
    });

    charts[canvasId].__hours = hours;
}

async function initAll(ip) {
    const info = window.__MODAL_METRICS__;
    if (!info || info.ip !== ip) return;

    // Если структура новая (groups) — используем её
    if (Array.isArray(info.groups)) {
        for (const g of info.groups) {
            await buildGroupChart(ip, g.id, g.keys, 24);
        }
    } else if (Array.isArray(info.keys)) {
        // fallback на старую структуру
        for (const key of info.keys) {
            await buildGroupChart(ip, key, [key], 24);
        }
    }
}

// Range picker
document.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-range-picker] button');
    if (!btn) return;
    e.stopPropagation();

    const picker = btn.closest('[data-range-picker]');
    const groupId = picker.dataset.groupId;
    const ip = picker.dataset.deviceIp;
    const keys = (picker.dataset.metrics || '').split(',').filter(Boolean);
    const hours = Number(btn.dataset.hours);

    picker.querySelectorAll('button').forEach(b => b.classList.remove('is-active'));
    btn.classList.add('is-active');

    await buildGroupChart(ip, groupId, keys, hours);
});

// Реагируем на событие из device-cards.js
window.addEventListener('cerber:modal-charts', (e) => {
    const ip = e.detail?.ip;
    if (ip) {
        requestAnimationFrame(() => initAll(ip));
    }
});

// Fallback — если DOMContentLoaded наступил раньше
document.addEventListener('DOMContentLoaded', () => {
    const info = window.__MODAL_METRICS__;
    if (info?.ip && document.getElementById(`modal-chart-${info.ip}-${(info.groups?.[0]?.id) || info.keys?.[0]}`)) {
        initAll(info.ip);
    }
});
