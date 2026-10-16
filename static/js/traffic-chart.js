/**
 * График трафика порта — раскрывается вниз при клике на карточку.
 * Двойной клик по карточке или кнопке «Трафик» → toggle.
 */

const API_BASE = '/api/v1';

const COLORS = {
    in:  '#73bf69',   // зелёный — входящий
    out: '#f2495c',   // красный — исходящий
};

let currentHours = 24;
const charts = {};   // { ifaceId: Chart }

function fmtBps(v) {
    if (v === null || v === undefined) return '—';
    const n = Number(v);
    if (n >= 1e9) return (n / 1e9).toFixed(2) + ' Gbit/s';
    if (n >= 1e6) return (n / 1e6).toFixed(2) + ' Mbit/s';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + ' Kbit/s';
    return n.toFixed(0) + ' bit/s';
}

function fmtTime(iso, hours) {
    const d = new Date(iso);
    if (hours <= 24) {
        return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    }
    return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

async function apiGet(path) {
    const r = await fetch(API_BASE + path, { headers: { Accept: 'application/json' } });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
}

async function buildTrafficChart(ifaceId, hours, canvas) {
    const key = `${ifaceId}`;

    let data = [];
    try {
        data = await apiGet(`/interfaces/${ifaceId}/traffic-history/?hours=${hours}`);
    } catch (e) {
        console.error(`[traffic] iface ${ifaceId}:`, e);
        return;
    }

    const labels = data.map(r => fmtTime(r.ts, hours));
    const inBps = data.map(r => r.in_bps);
    const outBps = data.map(r => r.out_bps);

    // Уничтожаем предыдущий chart на этом canvas (если был)
    if (charts[key] && charts[key].canvas === canvas) {
        charts[key].data.labels = labels;
        charts[key].data.datasets[0].data = inBps;
        charts[key].data.datasets[1].data = outBps;
        charts[key].update('none');
        return;
    }

    if (charts[key]) {
        try { charts[key].destroy(); } catch (e) {}
        delete charts[key];
    }

    if (window.Chart && Chart.getChart) {
        const existing = Chart.getChart(canvas);
        if (existing) { try { existing.destroy(); } catch (e) {} }
    }

    charts[key] = new Chart(canvas, {
        type: 'line',
        data: {
            labels,
            datasets: [
                {
                    label: 'In',
                    data: inBps,
                    borderColor: COLORS.in,
                    backgroundColor: COLORS.in + '22',
                    borderWidth: 2,
                    pointRadius: 0,
                    pointHoverRadius: 3,
                    tension: 0.3,
                    fill: true,
                },
                {
                    label: 'Out',
                    data: outBps,
                    borderColor: COLORS.out,
                    backgroundColor: COLORS.out + '22',
                    borderWidth: 2,
                    pointRadius: 0,
                    pointHoverRadius: 3,
                    tension: 0.3,
                    fill: true,
                },
            ],
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
                            return `${ctx.dataset.label}: ${fmtBps(ctx.parsed.y)}`;
                        },
                    },
                },
            },
            scales: {
                x: {
                    type: 'category',
                    ticks: { maxTicksLimit: 6, font: { size: 9 }, autoSkip: true },
                    grid: { display: false },
                },
                y: {
                    beginAtZero: true,
                    ticks: {
                        font: { size: 9 },
                        callback: (v) => fmtBps(v),
                    },
                    grid: { color: 'rgba(255,255,255,0.06)' },
                },
            },
        },
    });
}

// Клик по «шапке» карточки — раскрыть/закрыть график
document.addEventListener('click', async (e) => {
    // Игнорируем клики по кнопке ⏻
    if (e.target.closest('.port-toggle-btn')) return;

    // Клик по карточке порта
    const card = e.target.closest('.iface-card');
    if (!card) return;
    // Не реагируем на клик внутри раскрытого блока (range picker)
    if (e.target.closest('.iface-card__chart-wrap')) return;

    // Toggle
    const isExpanded = card.classList.toggle('iface-card--expanded');
    const ifaceId = card.dataset.ifaceId;

    if (!isExpanded) return;

    // Ленивая отрисовка — если canvas уже есть, просто сбрасываем на 24ч
    let wrap = card.querySelector('.iface-card__chart-wrap');
    if (!wrap) {
        wrap = document.createElement('div');
        wrap.className = 'iface-card__chart-wrap';
        wrap.innerHTML = `
            <div class="iface-card__chart-header">
                <span class="iface-card__chart-title">
                    <i class="bi bi-activity"></i> Трафик
                </span>
                <div class="iface-card__chart-range" data-traffic-range>
                    <button data-hours="1">1ч</button>
                    <button data-hours="6">6ч</button>
                    <button data-hours="24" class="is-active">24ч</button>
                    <button data-hours="168">7д</button>
                </div>
            </div>
            <div class="iface-card__chart-body">
                <canvas id="traffic-chart-${ifaceId}"></canvas>
            </div>
            <div class="iface-card__chart-legend">
                <span class="iface-card__chart-legend-item">
                    <span class="iface-card__chart-dot" style="background:${COLORS.in}"></span> In
                </span>
                <span class="iface-card__chart-legend-item">
                    <span class="iface-card__chart-dot" style="background:${COLORS.out}"></span> Out
                </span>
            </div>
        `;
        card.appendChild(wrap);
    }

    const canvas = wrap.querySelector('canvas');
    currentHours = 24;
    await buildTrafficChart(ifaceId, currentHours, canvas);
});

// Range picker внутри раскрытой карточки
document.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-traffic-range] button');
    if (!btn) return;
    e.stopPropagation();

    const wrap = btn.closest('[data-traffic-range]');
    wrap.querySelectorAll('button').forEach(b => b.classList.remove('is-active'));
    btn.classList.add('is-active');

    const hours = Number(btn.dataset.hours);
    const card = btn.closest('.iface-card');
    const ifaceId = card.dataset.ifaceId;
    const canvas = card.querySelector('.iface-card__chart-body canvas');

    if (canvas) {
        await buildTrafficChart(ifaceId, hours, canvas);
    }
});
