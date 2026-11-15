document.addEventListener('DOMContentLoaded', function() {
    const periodSelect = document.getElementById('period-select');
    let trafficChart = null;
    let alertsChart = null;
    let currentPeriodDays = 7;

    async function fetchData(days = 7) {
        currentPeriodDays = days;
        try {
            const resp = await fetch(`/analytics/api/data/?days=${days}`);
            if (!resp.ok) throw new Error('Ошибка загрузки данных');
            const data = await resp.json();
            updateSummary(data);
            updateCharts(data);
            updateProblemDevices(data);
            updateDdmDegradation(data);
        } catch (err) {
            console.error('Analytics error:', err);
        }
    }

    function updateSummary(data) {
        // Устройства
        const dev = data.devices;
        document.getElementById('val-devices').className = ''; document.getElementById('val-devices').textContent = dev.total;
        document.getElementById('val-devices-sub').className = ''; document.getElementById('val-devices-sub').textContent = `Онлайн: ${dev.online} / Офлайн: ${dev.offline}`;
        
        // Алерты (последний день)
        const lastAlert = data.alerts[data.alerts.length - 1] || {crit: 0, warn: 0};
        document.getElementById('val-alerts').className = ''; document.getElementById('val-alerts').textContent = lastAlert.crit + lastAlert.warn;
        document.getElementById('val-alerts-sub').className = ''; document.getElementById('val-alerts-sub').textContent = `Crit: ${lastAlert.crit} / Warn: ${lastAlert.warn}`;

        // Оптика
        const problemOptics = data.optics.degraded + data.optics.crit + data.optics.no_signal;
        document.getElementById('val-optics').className = ''; document.getElementById('val-optics').textContent = problemOptics;
        document.getElementById('val-optics-sub').className = ''; document.getElementById('val-optics-sub').textContent = `OK: ${data.optics.ok} / Деградация: ${data.optics.degraded}`;
    }

    function updateCharts(data) {
        if (trafficChart) trafficChart.destroy();
        if (alertsChart) alertsChart.destroy();

        // График трафика
        const trafficSkel = document.getElementById('trafficSkeleton');
        if (trafficSkel) trafficSkel.style.display = 'none';
        document.getElementById('trafficChart').style.display = 'block';
        const trafficCtx = document.getElementById('trafficChart').getContext('2d');
        const trafficLabels = data.traffic.map(t => t.day);
        const trafficIn = data.traffic.map(t => (t.total_in || 0) / 1000000);
        const trafficOut = data.traffic.map(t => (t.total_out || 0) / 1000000);

        trafficChart = new Chart(trafficCtx, {
            type: 'line',
            data: {
                labels: trafficLabels,
                datasets: [
                    { label: 'In (Mbps)', data: trafficIn, borderColor: '#00aaff', backgroundColor: 'rgba(0,170,255,0.1)', fill: true, tension: 0.3 },
                    { label: 'Out (Mbps)', data: trafficOut, borderColor: '#ff8800', backgroundColor: 'rgba(255,136,0,0.1)', fill: true, tension: 0.3 }
                ]
            },
            options: { responsive: true, maintainAspectRatio: false }
        });

        // График алертов
        const alertsSkel = document.getElementById('alertsSkeleton');
        if (alertsSkel) alertsSkel.style.display = 'none';
        document.getElementById('alertsChart').style.display = 'block';
        const alertsCtx = document.getElementById('alertsChart').getContext('2d');
        const alertLabels = data.alerts.map(a => a.day);
        const critData = data.alerts.map(a => a.crit);
        const warnData = data.alerts.map(a => a.warn);

        alertsChart = new Chart(alertsCtx, {
            type: 'bar',
            data: {
                labels: alertLabels,
                datasets: [
                    { label: 'Crit', data: critData, backgroundColor: '#f2495c' },
                    { label: 'Warn', data: warnData, backgroundColor: '#ffcc00' }
                ]
            },
            options: { responsive: true, maintainAspectRatio: false, scales: { x: { stacked: true }, y: { stacked: true } } }
        });
    }

    function updateProblemDevices(data) {
        const list = document.getElementById('problem-devices-list');
        if (!list) return;
        list.innerHTML = '';
        if (!data.problem_devices || data.problem_devices.length === 0) {
            list.innerHTML = '<div style="color:#888;padding:12px;">Все устройства работают нормально.</div>';
            return;
        }
        data.problem_devices.forEach(dev => {
            const item = document.createElement('div');
            item.className = 'problem-item';
            item.innerHTML = `
                <div><strong>${dev.name}</strong> <span class="ip">${dev.ip}</span></div>
                <div class="status status-${dev.status === 'unreachable' ? 'crit' : 'warn'}">${dev.status.toUpperCase()}</div>
            `;
            list.appendChild(item);
        });
    }

    function updateDdmDegradation(data) {
        const rxCanvas = document.getElementById('ddmRxChart');
        const tempCanvas = document.getElementById('ddmTempChart');
        
        if (!data.ddm_history || data.ddm_history.length === 0) {
            if (rxCanvas) rxCanvas.style.display = 'none';
            if (tempCanvas) tempCanvas.style.display = 'none';
            return;
        } else {
            if (rxCanvas) { rxCanvas.style.display = 'block'; const skel = document.getElementById('rxSkeleton'); if(skel) skel.style.display = 'none'; }
            if (tempCanvas) tempCanvas.style.display = 'block';
            // Увеличиваем высоту контейнера
            if (rxCanvas.parentElement) rxCanvas.parentElement.style.height = '400px';
            if (tempCanvas.parentElement) tempCanvas.parentElement.style.height = '400px';
        }

        const rxDevices = data.ddm_history.filter(d => !d.is_temp);
        const tempDevices = data.ddm_history.filter(d => d.is_temp);

        if (window.ddmRxInstance) window.ddmRxInstance.destroy();
        if (window.ddmTempInstance) window.ddmTempInstance.destroy();

        const colors = ['#00e676', '#ff5252', '#00b0ff', '#ffb300', '#e040fb', '#ff4081', '#536dfe', '#69f0ae', '#ff6e40', '#18ffff'];

        const formatLabel = (label) => {
            const parts = label.split(' ');
            if (parts.length < 2) return label;
            return currentPeriodDays <= 1 ? parts[1] : parts[0].substring(5);
        };

        const chartOptions = {
            responsive: true, 
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: { 
                legend: { 
                    position: 'left',
                    labels: { 
                        color: '#e0e0e0', 
                        boxWidth: 10, 
                        font: { size: 10 },
                        padding: 12,
                        // Скрываем линии по умолчанию (оставляем только 3)
                        // В Chart.js это делается через dataset.hidden
                    }
                },
                tooltip: {
                    backgroundColor: 'rgba(30, 34, 39, 0.95)',
                    titleColor: '#fff',
                    bodyColor: '#ddd',
                    borderColor: '#444',
                    borderWidth: 1,
                    padding: 10
                }
            },
            scales: {
                x: { 
                    grid: { display: false },
                    ticks: { 
                        color: '#888', font: { size: 10 }, maxTicksLimit: 8,
                        callback: function(val) { return formatLabel(this.getLabelForValue(val)); }
                    } 
                },
                y: { 
                    position: 'right',
                    grid: { color: 'rgba(255,255,255,0.05)', drawBorder: false },
                    ticks: { color: '#888', font: { size: 10 } },
                    beginAtZero: false
                }
            },
            elements: {
                line: { 
                    tension: 0.4,
                    borderWidth: 2
                },
                point: { 
                    radius: 0, 
                    hoverRadius: 5,
                    hitRadius: 10
                }
            }
        };

        // График 1: RX
        if (rxDevices.length > 0 && rxCanvas) {
            const dates = new Set();
            rxDevices.forEach(dev => dev.history.forEach(p => dates.add(p.t)));
            const labels = Array.from(dates).sort();

            const datasets = rxDevices.map((dev, idx) => {
                const dataPoints = labels.map(label => {
                    const p = dev.history.find(x => x.t === label);
                    return p ? p.v : null;
                });
                return {
                    label: dev.name.replace(' (sfp_rx_dbm)', ''),
                    data: dataPoints,
                    borderColor: colors[idx % colors.length],
                    backgroundColor: colors[idx % colors.length],
                    fill: false,
                    spanGaps: true,
                    // Скрываем все, кроме первых 3-х, чтобы не засорять график
                    hidden: idx >= 3
                };
            });

            const opts = JSON.parse(JSON.stringify(chartOptions));
            opts.scales.y.title = { display: true, text: 'dBm', color: '#888', font: {size: 10} };
            opts.scales.y.min = -30;
            opts.scales.y.max = 0;

            window.ddmRxInstance = new Chart(rxCanvas.getContext('2d'), {
                type: 'line',
                data: { labels, datasets },
                options: opts
            });
        }

        // График 2: Температура
        if (tempDevices.length > 0 && tempCanvas) {
            const dates = new Set();
            tempDevices.forEach(dev => dev.history.forEach(p => dates.add(p.t)));
            const labels = Array.from(dates).sort();

            const datasets = tempDevices.map((dev, idx) => {
                const dataPoints = labels.map(label => {
                    const p = dev.history.find(x => x.t === label);
                    return p ? p.v : null;
                });
                return {
                    label: dev.name.replace(' (sfp_temp)', ''),
                    data: dataPoints,
                    borderColor: colors[idx % colors.length],
                    backgroundColor: colors[idx % colors.length],
                    fill: false,
                    spanGaps: true,
                    hidden: idx >= 3
                };
            });

            const opts = JSON.parse(JSON.stringify(chartOptions));
            opts.scales.y.title = { display: true, text: '°C', color: '#888', font: {size: 10} };
            opts.scales.y.min = 20;
            opts.scales.y.max = 80;

            window.ddmTempInstance = new Chart(tempCanvas.getContext('2d'), {
                type: 'line',
                data: { labels, datasets },
                options: opts
            });
        }
    }

    periodSelect.addEventListener('change', (e) => {
        fetchData(parseInt(e.target.value));
    });

    fetchData(7);
});