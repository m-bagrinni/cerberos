/* Вкладка «Метрики»: графики всех NE (категориальная ось X, без time-adapter). */
(function() {
    'use strict';

    var charts = {};
    var currentHours = 1;
    var loadedOnce = false;

    var PALETTE = [
        '#ff780a', '#0abfbc', '#73bf69', '#f2cc0c', '#f2495c',
        '#8e44ad', '#3498db', '#e67e22', '#1abc9c', '#e74c3c',
        '#9b59b6', '#2ecc71', '#f39c12', '#16a085', '#c0392b',
        '#d35400', '#27ae60', '#2980b9', '#9b59b6', '#2c3e50'
    ];
    function colorFor(i) { return PALETTE[i % PALETTE.length]; }

    function fmtTs(iso) {
        var d = new Date(iso);
        return d.toLocaleTimeString('ru-RU', {hour:'2-digit', minute:'2-digit'});
    }

    function load() {
        var gatewayIp = window.MUX_GATEWAY_IP || '';
        var key = document.getElementById('mux-metric-key').value;
        var instance = document.getElementById('mux-metric-port').value;
        if (!gatewayIp) { console.warn('MUX_GATEWAY_IP not set'); return; }

        var url = '/api/v1/ne/metrics-bulk/?gateway_ip=' + gatewayIp +
                  '&key=' + key + '&instance=' + instance +
                  '&hours=' + currentHours + '&limit=500';

        fetch(url)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data.ok) { console.error(data); return; }
                console.log('[MuxMetrics] loaded:', data.nes.length, 'NE');
                renderComparison(data);
                renderPerNe(data);
            })
            .catch(function(e) { console.error('mux-metrics error', e); });
    }

    function buildLabels(nes) {
        var longest = null;
        nes.forEach(function(ne) {
            if (!longest || ne.points.length > longest.points.length) longest = ne;
        });
        if (!longest) return [];
        return longest.points.map(function(p) { return fmtTs(p.ts); });
    }

    function baseOptions(showLegend) {
        return {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                legend: showLegend ? {
                    display: true,
                    position: 'bottom',
                    labels: {
                        color: '#8e8e8e',
                        boxWidth: 8,
                        boxHeight: 8,
                        padding: 6,
                        font: { size: 10 },
                        usePointStyle: true,
                    }
                } : { display: false },
                tooltip: {
                    backgroundColor: '#181b1f',
                    borderColor: '#2c3235',
                    borderWidth: 1,
                    titleColor: '#ccc',
                    bodyColor: '#ccc',
                    padding: 8,
                }
            },
            scales: {
                x: {
                    grid: { color: '#1f2226', drawTicks: false },
                    border: { color: '#2c3235' },
                    ticks: {
                        color: '#6c6c6c',
                        font: { size: 10 },
                        maxTicksLimit: 6,
                        padding: 6,
                        autoSkip: true,
                    }
                },
                y: {
                    grid: { color: '#1f2226' },
                    border: { color: '#2c3235' },
                    ticks: {
                        color: '#6c6c6c',
                        font: { size: 10 },
                        maxTicksLimit: 5,
                        padding: 4,
                        callback: function(v) { return typeof v === 'number' ? v.toFixed(1) : v; }
                    }
                }
            }
        };
    }

    function renderComparison(data) {
        var canvas = document.getElementById('mux-chart-all');
        if (!canvas) return;

        var labels = buildLabels(data.nes);
        var datasets = data.nes.map(function(ne, i) {
            var color = colorFor(i);
            return {
                label: ne.name,
                data: ne.points.map(function(p) { return p.value; }),
                borderColor: color,
                backgroundColor: color + '22',
                borderWidth: 1.5,
                pointRadius: 0,
                pointHoverRadius: 3,
                tension: 0.25,
                spanGaps: true,
            };
        });

        if (charts.all) {
            charts.all.data.labels = labels;
            charts.all.data.datasets = datasets;
            charts.all.update('none');
        } else {
            charts.all = new Chart(canvas, {
                type: 'line',
                data: { labels: labels, datasets: datasets },
                options: baseOptions(true),
            });
        }
    }

    function renderPerNe(data) {
        var grid = document.getElementById('mux-metrics-grid');
        if (!grid) return;

        if (!loadedOnce) {
            grid.innerHTML = '';
            window.MUX_NE_LIST.forEach(function(ne) {
                var div = document.createElement('div');
                div.className = 'ne-chart-card';
                div.innerHTML =
                    '<h4 class="ne-chart-card__title">' + ne.name +
                        ' <span class="text-muted">· NE-' + ne.ne_id + '</span></h4>' +
                    '<div class="ne-chart-card__canvas"><canvas id="mux-chart-ne-' + ne.id + '"></canvas></div>';
                grid.appendChild(div);
            });
            loadedOnce = true;
        }

        data.nes.forEach(function(ne, i) {
            var meta = window.MUX_NE_LIST.find(function(x) { return x.ne_id === ne.ne_id; });
            if (!meta) return;
            var canvas = document.getElementById('mux-chart-ne-' + meta.id);
            if (!canvas) return;

            var color = colorFor(i);
            var labels = ne.points.map(function(p) { return fmtTs(p.ts); });
            var values = ne.points.map(function(p) { return p.value; });

            if (charts['ne-' + meta.id]) {
                charts['ne-' + meta.id].data.labels = labels;
                charts['ne-' + meta.id].data.datasets[0].data = values;
                charts['ne-' + meta.id].update('none');
            } else {
                charts['ne-' + meta.id] = new Chart(canvas, {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: ne.name,
                            data: values,
                            borderColor: color,
                            backgroundColor: color + '22',
                            borderWidth: 1.5,
                            pointRadius: 0,
                            pointHoverRadius: 3,
                            tension: 0.25,
                            fill: true,
                            spanGaps: true,
                        }]
                    },
                    options: baseOptions(false),
                });
            }
        });
    }

    document.querySelectorAll('.ne-range').forEach(function(btn) {
        btn.addEventListener('click', function() {
            document.querySelectorAll('.ne-range').forEach(function(b){ b.classList.remove('is-active'); });
            btn.classList.add('is-active');
            currentHours = parseInt(btn.dataset.hours, 10);
            load();
        });
    });
    var keySelect = document.getElementById('mux-metric-key');
    var portSelect = document.getElementById('mux-metric-port');
    if (keySelect) keySelect.addEventListener('change', load);
    if (portSelect) portSelect.addEventListener('change', load);

    window.MuxMetrics = { load: load, reload: load };
})();
