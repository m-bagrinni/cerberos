/**
 * Tab 2 «Метрики» — графики всех устройств с фильтрами.
 * Поддерживает псевдо-группу «traffic» — суммарный трафик устройства.
 */

const API_BASE = '/api/v1';
const GROUP_PALETTE = ['#ff780a','#0abfbc','#5794f2','#73bf69','#f2cc0c','#f2495c','#b877d9'];
const TRAFFIC_COLORS = { in: '#73bf69', out: '#f2495c' };

let currentHours = 24;
const charts = {};

function $(sel) { return document.querySelector(sel); }

async function apiGet(path) {
    const r = await fetch(API_BASE + path, { headers: { Accept: 'application/json' } });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
}

function fmtTime(iso, hours) {
    const d = new Date(iso);
    if (hours <= 24) return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function fmtBps(v) {
    if (v === null || v === undefined) return '—';
    const n = Number(v);
    if (n >= 1e9) return (n / 1e9).toFixed(1) + ' Gb/s';
    if (n >= 1e6) return (n / 1e6).toFixed(1) + ' Mb/s';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + ' Kb/s';
    return n.toFixed(0) + ' b/s';
}

async function buildMetricsChart(card, hours = 24) {
    const ip = card.dataset.deviceIp;
    const groupId = card.dataset.groupId;
    const canvas = card.querySelector('canvas');
    if (!canvas) return;

    const keys = (canvas.dataset.metrics || '').split(',').filter(Boolean);
    if (!keys.length) return;

    const datasetsData = [];
    let commonLabels = null;

    for (let i = 0; i < keys.length; i++) {
        const key = keys[i];
        let data = [];
        try {
            data = await apiGet(`/devices/${encodeURIComponent(ip)}/metric-history/?key=${encodeURIComponent(key)}&hours=${hours}`);
        } catch (e) { console.error(`[tab-metrics] ${ip} ${key}:`, e); continue; }

        const labels = data.map(r => fmtTime(r.ts, hours));
        const values = data.map(r => r.value);
        if (!commonLabels) commonLabels = labels;

        datasetsData.push({ label: key, data: values, color: GROUP_PALETTE[i % GROUP_PALETTE.length] });
    }

    if (!datasetsData.length) return;

    const datasets = datasetsData.map(d => ({
        label: d.label, data: d.data,
        borderColor: d.color, backgroundColor: d.color + '22',
        borderWidth: 2, pointRadius: 0, pointHoverRadius: 3,
        tension: 0.35, fill: false,
    }));

    drawChart(canvas, commonLabels, datasets, groupId, false);
}

async function buildTrafficChart(card, hours = 24) {
    const ip = card.dataset.deviceIp;
    const canvas = card.querySelector('canvas');
    if (!canvas) return;

    let data = [];
    try {
        data = await apiGet(`/devices/${encodeURIComponent(ip)}/traffic-summary/?hours=${hours}`);
    } catch (e) {
        console.error(`[tab-metrics] traffic ${ip}:`, e);
        return;
    }

    const labels = data.map(r => fmtTime(r.ts, hours));
    const inBps = data.map(r => r.in_bps);
    const outBps = data.map(r => r.out_bps);

    const datasets = [
        {
            label: 'In', data: inBps,
            borderColor: TRAFFIC_COLORS.in, backgroundColor: TRAFFIC_COLORS.in + '22',
            borderWidth: 2, pointRadius: 0, pointHoverRadius: 3, tension: 0.3, fill: true,
        },
        {
            label: 'Out', data: outBps,
            borderColor: TRAFFIC_COLORS.out, backgroundColor: TRAFFIC_COLORS.out + '22',
            borderWidth: 2, pointRadius: 0, pointHoverRadius: 3, tension: 0.3, fill: true,
        },
    ];

    drawChart(canvas, labels, datasets, 'traffic', true);
}

function drawChart(canvas, labels, datasets, groupId, isTraffic) {
    const canvasId = canvas.id;

    if (charts[canvasId] && charts[canvasId].canvas === canvas) {
        charts[canvasId].data.labels = labels;
        charts[canvasId].data.datasets = datasets;
        charts[canvasId].update('none');
        return;
    }

    if (charts[canvasId]) { try { charts[canvasId].destroy(); } catch(e){} delete charts[canvasId]; }
    if (window.Chart && Chart.getChart) {
        const existing = Chart.getChart(canvas);
        if (existing) { try { existing.destroy(); } catch(e){} }
    }

    charts[canvasId] = new Chart(canvas, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true, maintainAspectRatio: false, animation: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: { label: (ctx) => {
                    const v = ctx.parsed.y;
                    const val = isTraffic
                        ? fmtBps(v)
                        : (v !== null && v !== undefined ? Number(v).toFixed(2) : '—');
                    return `${ctx.dataset.label}: ${val}`;
                }}},
            },
            scales: {
                x: { type: 'category', ticks: { maxTicksLimit: 4, font: { size: 9 }, autoSkip: true }, grid: { display: false }},
                y: {
                    beginAtZero: groupId === 'cpu' || groupId === 'mem' || isTraffic,
                    ticks: {
                        font: { size: 9 },
                        callback: isTraffic ? (v) => fmtBps(v) : undefined,
                    },
                    grid: { color: 'rgba(255,255,255,0.06)' },
                },
            },
        },
    });
}

async function buildAll(hours) {
    const cards = document.querySelectorAll('.metrics-chart-card');
    for (const card of cards) {
        if (card.style.display === 'none') continue;
        if (card.dataset.isTraffic === '1') {
            await buildTrafficChart(card, hours);
        } else {
            await buildMetricsChart(card, hours);
        }
    }
}

function applyFilters() {
    const devFilter = $('#filter-device').value;
    const grpFilter = $('#filter-group').value;
    document.querySelectorAll('.metrics-chart-card').forEach(card => {
        const show = (!devFilter || card.dataset.deviceIp === devFilter)
                  && (!grpFilter || card.dataset.groupId === grpFilter);
        card.style.display = show ? '' : 'none';
    });
    requestAnimationFrame(() => buildAll(currentHours));
}

document.addEventListener('DOMContentLoaded', () => {
    const hoursSel = $('#filter-hours');
    const devSel = $('#filter-device');
    const grpSel = $('#filter-group');

    if (hoursSel) hoursSel.addEventListener('change', () => { currentHours = Number(hoursSel.value); applyFilters(); });
    if (devSel) devSel.addEventListener('change', applyFilters);
    if (grpSel) grpSel.addEventListener('change', applyFilters);

    buildAll(currentHours);
});
