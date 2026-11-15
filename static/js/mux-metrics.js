(function() {
    'use strict';

    var bigCharts = {};
    var currentPort = "1";
    var currentHours = 1;
    var loadedOnce = false;
    var allNeIds = [];

    var COLOR_OK = '#73bf69';
    var COLOR_WARN = '#f2cc0c';
    var COLOR_CRIT = '#f2495c';
    var COLOR_NEUTRAL = '#0abfbc';
    var COLOR_TRAFFIC = '#3b82f6';

    function fetchBulk(key) {
        var gw = window.MUX_GATEWAY_IP || '';
        if (!gw) return Promise.reject(new Error('MUX_GATEWAY_IP not set'));
        var url = '/api/v1/ne/metrics-bulk/?gateway_ip=' + gw + '&key=' + key + '&instance=' + currentPort + '&hours=' + currentHours + '&limit=500';
        return fetch(url).then(function(r) { return r.json(); });
    }

    // Получение истории для конкретного NE (для трафика)
    function fetchNeTraffic(neId, key, port) {
        var p = port || currentPort;
        var url = '/api/v1/ne/' + neId + '/metric-history/?key=' + key + '&instance=' + p + '&hours=1&limit=100';
        return fetch(url).then(function(r) { return r.json(); });
    }

    function load() {
        var gridEl = document.getElementById('mux-ne-grid');
        if (gridEl) gridEl.innerHTML = '<div style="padding:20px; text-align:center;">Загрузка...</div>';

        var keys = ['sfp_rx_dbm', 'sfp_tx_dbm', 'sfp_temp', 'sfp_bias'];
        Promise.all(keys.map(fetchBulk)).then(function(results) {
            var map = {};
            keys.forEach(function(k, i) { map[k] = results[i]; });
            
            if (!map['sfp_rx_dbm'] || !map['sfp_rx_dbm'].ok) return;

            // SFP графики
            drawBarChart('mux-chart-rx', 'sfp_rx_dbm', map, function(v) {
                if (v < -25) return COLOR_CRIT;
                if (v < -20) return COLOR_WARN;
                return COLOR_OK;
            }, -30, 5, true);

            drawBarChart('mux-chart-tx', 'sfp_tx_dbm', map, function(v) { return COLOR_NEUTRAL; }, -5, 5, true);
            drawBarChart('mux-chart-temp', 'sfp_temp', map, function(v) {
                if (v > 70) return COLOR_CRIT;
                if (v > 65) return COLOR_WARN;
                return COLOR_OK;
            }, 20, 80, false);
            drawBarChart('mux-chart-bias', 'sfp_bias', map, function(v) { return COLOR_NEUTRAL; }, 0, 45, true);

            renderNeGrid(map);
            
            // Трафик
            allNeIds = map['sfp_rx_dbm'].nes.map(function(ne){ return { id: ne.id, name: ne.name }; });
            loadTraffic(allNeIds);

            loadedOnce = true;
        }).catch(function(e) {
            console.error('Ошибка загрузки SFP:', e);
        });
    }

    function loadTraffic(neList) {
        var PORTS = ['1', '2', '3', '4', '5', '6'];
        
        // Для каждого NE: 6 запросов (по портам)
        var promisesIn = [];
        neList.forEach(function(ne) {
            PORTS.forEach(function(port) {
                promisesIn.push(
                    fetchNeTraffic(ne.id, 'eth_rx_bytes', port).then(function(r) {
                        r._neId = ne.id;
                        r._port = port;
                        return r;
                    })
                );
            });
        });

        Promise.all(promisesIn).then(function(results) {
            // Группируем по NE: {id, name, total_kbps}
            var byNe = {};
            neList.forEach(function(ne) {
                byNe[ne.id] = { id: ne.id, name: ne.name, total: 0 };
            });
            
            results.forEach(function(r) {
                var points = r.points || [];
                if (points.length < 2) return;
                
                // Последние две точки (интервал 30-900 сек)
                var p2 = points[points.length - 1];
                var t2 = new Date(p2.ts).getTime();
                var val2 = p2.value || 0;
                
                for (var j = points.length - 2; j >= 0; j--) {
                    var p1 = points[j];
                    var t1 = new Date(p1.ts).getTime();
                    var dt = (t2 - t1) / 1000;
                    if (dt >= 30 && dt <= 900) {
                        var delta = val2 - (p1.value || 0);
                        if (delta >= 0) {
                            var kbps = (delta * 8) / dt / 1000;  // Кбит/с
                            if (byNe[r._neId]) {
                                byNe[r._neId].total += kbps;
                            }
                        }
                        break;
                    }
                }
            });
            
            // Формируем массив для графика (только NE с трафиком)
            var trafficData = Object.values(byNe).filter(function(t) { return t.total > 0.001; });
            
            drawTrafficChart('mux-chart-lan-in', trafficData, neList, COLOR_TRAFFIC);
            
            // LAN Out скрыт (нет eth_tx_bytes)
            var outCanvas = document.getElementById('mux-chart-lan-out');
            if (outCanvas) {
                var outCard = outCanvas.closest('.mux-metrics-chart-card');
                if (outCard) outCard.style.display = 'none';
            }
        }).catch(function(e) {
            console.error('[traffic] error:', e);
        });
    }

    function drawTrafficChart(canvasId, trafficData, neList, color) {
        var canvas = document.getElementById(canvasId);
        if (!canvas) return;
        
        if (bigCharts[canvasId]) { try { bigCharts[canvasId].destroy(); } catch(e){} }
        
        if (trafficData.length === 0) {
            var parent = canvas.parentElement;
            parent.innerHTML = '<div style="text-align:center; color:var(--text-muted); padding:40px; font-size:13px;">Нет трафика за последний час (по всем портам).</div>';
            return;
        }
        
        // Сортируем по убыванию
        trafficData.sort(function(a, b) { return b.total - a.total; });
        
        var labels = trafficData.map(function(t) { return t.name; });
        var values = trafficData.map(function(t) { return t.total; });
        
        bigCharts[canvasId] = new Chart(canvas, {
            type: 'bar',
            data: { 
                labels: labels, 
                datasets: [{ 
                    label: 'Кбит/с', 
                    data: values, 
                    backgroundColor: color, 
                    borderRadius: 3 
                }] 
            },
            options: {
                responsive: true, 
                maintainAspectRatio: false, 
                animation: false, 
                indexAxis: 'y',
                plugins: { 
                    legend: { display: false }, 
                    tooltip: { 
                        callbacks: { 
                            label: function(ctx){ 
                                return ctx.parsed.x.toFixed(1) + ' Кбит/с'; 
                            } 
                        } 
                    } 
                },
                scales: {
                    x: { ticks: { color: '#8e8e8e' }, grid: { color: 'rgba(255,255,255,0.05)' } },
                    y: { ticks: { color: '#ccc', font: {size: 10} }, grid: { display: false } }
                }
            }
        });
    }

    function drawBarChart(canvasId, key, map, colorFn, yMin, yMax, horizontal) {
        var canvas = document.getElementById(canvasId);
        if (!canvas || !map[key] || !map[key].ok) return;
        if (bigCharts[canvasId]) { try { bigCharts[canvasId].destroy(); } catch(e){} }

        var data = map[key];
        var items = data.nes.filter(function(ne) { return ne.points && ne.points.length > 0; }).map(function(ne) {
            return { name: ne.name, neId: ne.ne_id, value: ne.points[ne.points.length - 1].value };
        });

        if (key === 'sfp_rx_dbm') items.sort(function(a,b) { return a.value - b.value; });
        else items.sort(function(a,b) { return b.value - a.value; });

        var labels = items.map(function(it) { return it.name; });
        var values = items.map(function(it) { return it.value; });
        var colors = items.map(function(it) { return colorFn(it.value); });

        bigCharts[canvasId] = new Chart(canvas, {
            type: 'bar',
            data: { labels: labels, datasets: [{ data: values, backgroundColor: colors, borderRadius: 3 }] },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                indexAxis: horizontal ? 'y' : 'x',
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: { label: function(ctx) { var v = horizontal ? ctx.parsed.x : ctx.parsed.y; return v.toFixed(2); } } }
                },
                scales: horizontal ? {
                    x: { suggestedMin: yMin, suggestedMax: yMax, ticks: { color: '#8e8e8e' }, grid: { color: 'rgba(255,255,255,0.05)' } },
                    y: { ticks: { color: '#ccc', font: {size: 10} }, grid: { display: false } }
                } : {
                    x: { ticks: { color: '#ccc', font: {size: 10}, autoSkip: false, maxRotation: 60 }, grid: { display: false } },
                    y: { suggestedMin: yMin, suggestedMax: yMax, ticks: { color: '#8e8e8e' }, grid: { color: 'rgba(255,255,255,0.05)' } }
                }
            }
        });
    }

    function renderNeGrid(map) {
        var grid = document.getElementById('mux-ne-grid');
        if (!grid) return;
        grid.innerHTML = '';
        var rxData = map['sfp_rx_dbm'];
        var tempData = map['sfp_temp'];
        
        rxData.nes.forEach(function(ne) {
            var lastRx = (ne.points && ne.points.length) ? ne.points[ne.points.length-1].value : null;
            var tempNe = tempData.nes.find(function(n){ return n.ne_id === ne.ne_id; });
            var lastTemp = (tempNe && tempNe.points && tempNe.points.length) ? tempNe.points[tempNe.points.length-1].value : null;

            var rxStr = lastRx !== null ? lastRx.toFixed(1) + ' dBm' : '—';
            var tempStr = lastTemp !== null ? lastTemp.toFixed(1) + ' °C' : '—';
            var rxClass = lastRx < -25 ? 'val-crit' : (lastRx < -20 ? 'val-warn' : 'val-ok');
            var tempClass = lastTemp > 70 ? 'val-crit' : (lastTemp > 65 ? 'val-warn' : 'val-ok');

            var card = document.createElement('div');
            card.className = 'mux-ne-card';
            card.innerHTML = '<div class="mux-ne-card__head"><span class="mux-ne-card__name">' + Cerber.escapeHtml(ne.name) + '</span><span class="mux-ne-card__id">NE-' + Cerber.escapeHtml(ne.ne_id) + '</span></div>' +
                             '<div class="mux-ne-card__stats"><div class="mux-ne-stat"><div class="mux-ne-stat__label">RX</div><div class="mux-ne-stat__value ' + rxClass + '">' + rxStr + '</div></div>' +
                             '<div class="mux-ne-stat"><div class="mux-ne-stat__label">Temp</div><div class="mux-ne-stat__value ' + tempClass + '">' + tempStr + '</div></div></div>';
            grid.appendChild(card);
        });
    }

    document.addEventListener('DOMContentLoaded', function() {
        var sw = document.getElementById('mux-port-switch');
        if (sw) sw.addEventListener('click', function(e) {
            var b = e.target.closest('button[data-port]'); if(!b) return;
            sw.querySelectorAll('button').forEach(x=>x.classList.remove('is-active')); b.classList.add('is-active');
            currentPort = b.dataset.port; load();
        });
        
        var pw = document.getElementById('mux-period-switch');
        if (pw) pw.addEventListener('click', function(e) {
            var b = e.target.closest('button[data-hours]'); if(!b) return;
            pw.querySelectorAll('button').forEach(x=>x.classList.remove('is-active')); b.classList.add('is-active');
            currentHours = parseInt(b.dataset.hours); load();
        });

        var tab = document.querySelector('[data-mux-tab="metrics"]');
        if (tab && tab.classList.contains('is-active')) setTimeout(function(){ if(!loadedOnce) load(); }, 100);
    });
    
    document.addEventListener('click', function(e) { 
        var tab = e.target.closest('[data-mux-tab]'); 
        if (tab && tab.dataset.muxTab === 'metrics' && !loadedOnce) setTimeout(load, 50); 
    });

    window.MuxMetrics = { reload: function() { loadedOnce = false; load(); }, load: load };
})();
