/* Модалка NE: вкладки + графики DDM. Всё — ТОЛЬКО внутри root-элемента модалки. */
(function() {
    'use strict';

    var charts = {};
    var currentHours = 1;
    var currentPort = "1";
    var neId = null;
    var rootEl = null;

    function cssVar(name) {
        return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';
    }

    function $(sel) { return rootEl ? rootEl.querySelector(sel) : null; }
    function $$(sel) { return rootEl ? Array.from(rootEl.querySelectorAll(sel)) : []; }

    function loadAllCharts() {
        loadChart('ne-chart-temp',    'sfp_temp',    '°C',  '--primary');
        loadChart('ne-chart-rx',      'sfp_rx_dbm',  'dBm', '--tiffany');
        loadChart('ne-chart-tx',      'sfp_tx_dbm',  'dBm', '--ok');
        loadChart('ne-chart-bias',    'sfp_bias',    'mA',  '--warn');
        loadChart('ne-chart-voltage', 'sfp_voltage', 'V',   '--text-strong');
    }

    function loadChart(canvasId, key, unit, colorVar) {
        var canvas = $('#' + canvasId);
        if (!canvas) return;

        var url = '/api/v1/ne/' + neId + '/metric-history/?key=' + key +
                  '&instance=' + currentPort + '&hours=' + currentHours + '&limit=500';

        fetch(url)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var points = data.points || [];
                if (!points.length) {
                    if (charts[canvasId]) { charts[canvasId].destroy(); delete charts[canvasId]; }
                    return;
                }
                var labels = points.map(function(p) {
                    var d = new Date(p.ts);
                    return d.toLocaleString('ru-RU', {hour:'2-digit', minute:'2-digit', day:'2-digit', month:'2-digit'});
                });
                var values = points.map(function(p) { return p.value; });

                if (charts[canvasId]) {
                    charts[canvasId].data.labels = labels;
                    charts[canvasId].data.datasets[0].data = values;
                    charts[canvasId].update('none');
                } else {
                    if (typeof Chart === 'undefined') { console.error('Chart.js not loaded'); return; }
                    var color = cssVar(colorVar);
                    charts[canvasId] = new Chart(canvas, {
                        type: 'line',
                        data: {
                            labels: labels,
                            datasets: [{
                                label: unit,
                                data: values,
                                borderColor: color,
                                backgroundColor: color + '22',
                                borderWidth: 2,
                                pointRadius: 0,
                                tension: 0.3,
                                fill: true,
                            }]
                        },
                        options: {
                            responsive: true,
                            maintainAspectRatio: false,
                            animation: false,
                            plugins: { legend: { display: false } },
                            scales: {
                                x: { grid: { color: '#2c3235' }, ticks: { color: '#8e8e8e', maxTicksLimit: 6 } },
                                y: { grid: { color: '#2c3235' }, ticks: { color: '#8e8e8e' } }
                            }
                        }
                    });
                }
            })
            .catch(function(e) { console.error('chart error', key, e); });
    }

    function bind(neIdValue, root) {
        // Уничтожить старые графики
        Object.keys(charts).forEach(function(k) {
            try { charts[k].destroy(); } catch (e) {}
        });
        charts = {};

        neId = neIdValue;
        rootEl = root || document.getElementById('ne-modal-body') || document;

        currentHours = 1;
        currentPort = "1";

        // Сброс вкладок
        $$('.ne-tab').forEach(function(t) {
            t.classList.toggle('is-active', t.dataset.neTab === 'overview');
        });
        $$('.ne-tab-content').forEach(function(c) {
            c.classList.toggle('is-active', c.dataset.neTabContent === 'overview');
        });
        $$('.ne-range').forEach(function(b) {
            b.classList.toggle('is-active', b.dataset.hours === '1');
        });
        $$('.ne-sfp-sel').forEach(function(b) {
            b.classList.toggle('is-active', b.dataset.instance === '1');
        });

        // Вкладки
        $$('.ne-tab').forEach(function(tab) {
            tab.onclick = function(e) {
                e.preventDefault();
                var target = tab.dataset.neTab;
                $$('.ne-tab').forEach(function(t) { t.classList.toggle('is-active', t === tab); });
                $$('.ne-tab-content').forEach(function(c) {
                    c.classList.toggle('is-active', c.dataset.neTabContent === target);
                });
                if (target === 'charts') loadAllCharts();
            };
        });

        // Range
        $$('.ne-range').forEach(function(btn) {
            btn.onclick = function(e) {
                e.preventDefault();
                $$('.ne-range').forEach(function(b) { b.classList.remove('is-active'); });
                btn.classList.add('is-active');
                currentHours = parseInt(btn.dataset.hours, 10);
                loadAllCharts();
            };
        });

        // SFP port
        $$('.ne-sfp-sel').forEach(function(btn) {
            btn.onclick = function(e) {
                e.preventDefault();
                $$('.ne-sfp-sel').forEach(function(b) { b.classList.remove('is-active'); });
                btn.classList.add('is-active');
                currentPort = btn.dataset.instance;
                loadAllCharts();
            };
        });
    }

    function cleanup() {
        Object.keys(charts).forEach(function(k) {
            try { charts[k].destroy(); } catch (e) {}
        });
        charts = {};
        rootEl = null;
        neId = null;
    }

    window.NeModal = {
        bind: bind,
        cleanup: cleanup,
        reload: loadAllCharts,
    };

    // ============================================================
    // График трафика LAN (eth_rx_bytes для портов 1..4 = LAN1-LAN4)
    // ============================================================

    var trafficChart = null;
    var trafficHours = 1;
    var trafficMode = "speed";  // "speed" | "total"

    function loadTrafficChart() {
        var canvas = document.getElementById('ne-chart-traffic');
        if (!canvas) return;

        var ports = ['1', '2', '3', '4'];
        var promises = ports.map(function(port) {
            var url = '/api/v1/ne/' + neId + '/metric-history/?key=eth_rx_bytes' +
                      '&instance=' + port + '&hours=' + trafficHours + '&limit=500';
            return fetch(url).then(function(r) { return r.json(); })
                .then(function(d) { return {port: port, points: d.points || []}; });
        });

        Promise.all(promises).then(function(results) {
            renderTrafficChart(results);
        });
    }

    function renderTrafficChart(results) {
        var canvas = document.getElementById('ne-chart-traffic');
        if (!canvas) return;

        // Общие labels
        var longest = results.reduce(function(a, b) {
            return (a.points && a.points.length > (b.points ? b.points.length : 0)) ? a : b;
        }, {points: []});
        var labels = (longest.points || []).map(function(p) {
            var d = new Date(p.ts);
            return d.toLocaleString('ru-RU', {hour:'2-digit', minute:'2-digit', day:'2-digit', month:'2-digit'});
        });

        var colors = ['#ff780a', '#0abfbc', '#73bf69', '#f2cc0c'];

        var datasets = results.map(function(r, i) {
            var color = colors[i % colors.length];
            var pts = r.points || [];
            var values;
            if (trafficMode === 'total') {
                values = pts.map(function(p) { return p.value / 1_000_000; });
            } else {
                values = [];
                for (var j = 0; j < pts.length; j++) {
                    if (j === 0) { values.push(null); continue; }
                    var dt = (new Date(pts[j].ts) - new Date(pts[j-1].ts)) / 1000;
                    var db = pts[j].value - pts[j-1].value;
                    if (dt > 0 && db >= 0) {
                        values.push((db * 8) / dt / 1_000_000);  // Мбит/с
                    } else {
                        values.push(null);
                    }
                }
            }
            return {
                label: 'LAN' + r.port,
                data: values,
                borderColor: color,
                backgroundColor: color + '22',
                borderWidth: 2,
                pointRadius: 0,
                pointHoverRadius: 4,
                tension: 0.3,
                fill: false,
                spanGaps: true,
            };
        });

        if (trafficChart) {
            trafficChart.data.labels = labels;
            trafficChart.data.datasets = datasets;
            trafficChart.update('none');
        } else {
            trafficChart = new Chart(canvas, {
                type: 'line',
                data: { labels: labels, datasets: datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    interaction: { intersect: false, mode: 'index' },
                    plugins: {
                        legend: {
                            display: true,
                            position: 'bottom',
                            labels: { color: '#8e8e8e', boxWidth: 10, usePointStyle: true, font: { size: 11 } },
                        },
                    },
                    scales: {
                        x: {
                            grid: { color: '#1f2226' },
                            ticks: { color: '#8e8e8e', maxTicksLimit: 8 },
                        },
                        y: {
                            grid: { color: '#1f2226' },
                            ticks: { color: '#8e8e8e' },
                            title: {
                                display: true,
                                text: trafficMode === 'speed' ? 'Мбит/с' : 'МБ',
                                color: '#8e8e8e',
                            },
                        },
                    },
                },
            });
        }
    }

    // Обработчики трафика
    document.addEventListener('click', function(e) {
        if (e.target.classList && e.target.classList.contains('ne-range-traffic')) {
            document.querySelectorAll('.ne-range-traffic').forEach(function(b) { b.classList.remove('is-active'); });
            e.target.classList.add('is-active');
            trafficHours = parseInt(e.target.dataset.hours, 10);
            loadTrafficChart();
        }
        if (e.target.classList && e.target.classList.contains('ne-traffic-mode')) {
            document.querySelectorAll('.ne-traffic-mode').forEach(function(b) { b.classList.remove('is-active'); });
            e.target.classList.add('is-active');
            trafficMode = e.target.dataset.mode;
            loadTrafficChart();
        }
    });


    // ============================================================
    // Ручной запуск poll для NE
    // ============================================================

    window.pollNeNow = function(neIdValue) {
        var btn = event && event.target ? event.target.closest('.ne-poll-btn') : null;
        var origHtml = btn ? btn.innerHTML : '';

        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Опрос…';
        }

        fetch('/api/v1/ne/' + neIdValue + '/poll-now/', {
            method: 'POST',
            headers: {
                'X-CSRFToken': (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '',
            },
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.ok) {
                if (btn) {
                    btn.innerHTML = '<i class="bi bi-check-lg"></i> Готово';
                    btn.style.background = 'var(--ok)';
                }
                setTimeout(function() {
                    if (window.reloadNeModal) window.reloadNeModal();
                    if (btn) {
                        btn.innerHTML = origHtml;
                        btn.style.background = '';
                        btn.disabled = false;
                    }
                }, 1500);
            } else {
                alert('Ошибка: ' + (data.error || 'unknown'));
                if (btn) { btn.innerHTML = origHtml; btn.disabled = false; }
            }
        })
        .catch(function(e) {
            alert('Ошибка сети: ' + e);
            if (btn) { btn.innerHTML = origHtml; btn.disabled = false; }
        });
    };


    // ============================================================
    // Перезагрузка содержимого модалки (SFP + LAN + графики)
    // ============================================================

    window.reloadNeModal = function() {
        var neIdValue = neId;
        if (!neIdValue) return;

        var body = document.getElementById('ne-modal-body');
        if (!body) return;

        body.style.transition = 'opacity 0.15s';
        body.style.opacity = '0.5';

        fetch('/modal/ne/' + neIdValue + '/')
            .then(function(r) { return r.text(); })
            .then(function(html) {
                body.innerHTML = html;
                body.style.opacity = '1';
                // Пере-биндим вкладки и графики
                if (window.NeModal && window.NeModal.bind) {
                    window.NeModal.bind(neIdValue, body);
                }
            })
            .catch(function(e) {
                console.error('reloadNeModal error', e);
                body.style.opacity = '1';
            });
    };


    // ─────────────────────────────────────────────────────────
    // openNeModal — открывает модалку NE по ID
    // Вызывается из onclick на карточке
    // ─────────────────────────────────────────────────────────
    window.openNeModal = function(neIdValue) {
        if (!neIdValue) return;

        var modal = document.getElementById('ne-modal');
        var body = document.getElementById('ne-modal-body');
        var title = document.getElementById('ne-modal-title');
        if (!modal || !body) {
            console.error('[openNeModal] не найдены #ne-modal или #ne-modal-body');
            return;
        }

        // Показываем модалку
        modal.style.display = 'flex';
        document.body.style.overflow = 'hidden';

        // Показываем «Загрузка...»
        body.innerHTML = '<div class="ne-modal__loading">Загрузка…</div>';
        if (title) title.textContent = 'NE-' + neIdValue;

        // Загружаем контент
        fetch('/modal/ne/' + neIdValue + '/')
            .then(function(r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.text();
            })
            .then(function(html) {
                body.innerHTML = html;
                // Привязываем вкладки и графики
                if (window.NeModal && window.NeModal.bind) {
                    window.NeModal.bind(neIdValue, body);
                }
            })
            .catch(function(e) {
                console.error('[openNeModal] error:', e);
                body.innerHTML = '<div style="color:var(--crit);padding:20px">Ошибка загрузки: ' + Cerber.escapeHtml(e.message) + '</div>';
            });
    };

    // ─────────────────────────────────────────────────────────
    // closeNeModal — закрывает модалку
    // ─────────────────────────────────────────────────────────
    window.closeNeModal = function() {
        var modal = document.getElementById('ne-modal');
        if (modal) modal.style.display = 'none';
        document.body.style.overflow = '';
    };

    // ─────────────────────────────────────────────────────────
    // openNeRename / closeNeRename — модалка переименования
    // ─────────────────────────────────────────────────────────
    window.openNeRename = function(neIdValue, currentName) {
        var modal = document.getElementById('ne-rename');
        var input = document.getElementById('ne-rename-input');
        if (!modal || !input) return;
        window._renameNeId = neIdValue;
        input.value = currentName || '';
        modal.style.display = 'flex';
        setTimeout(function() { input.focus(); input.select(); }, 50);
    };

    window.closeNeRename = function() {
        var modal = document.getElementById('ne-rename');
        if (modal) modal.style.display = 'none';
        window._renameNeId = null;
    };

    window.submitNeRename = function() {
        var neIdValue = window._renameNeId;
        var input = document.getElementById('ne-rename-input');
        if (!neIdValue || !input) return;

        var newName = input.value.trim();
        if (!newName) {
            alert('Имя не может быть пустым');
            return;
        }

        fetch('/api/v1/ne/' + neIdValue + '/rename/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': (function() {
                    var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
                    return m ? m[1] : '';
                })(),
            },
            body: JSON.stringify({ name: newName }),
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.ok) {
                window.closeNeRename();
                // Обновим заголовок карточки
                var card = document.querySelector('[data-ne-id="' + neIdValue + '"]');
                if (card) {
                    var nameEl = card.querySelector('.ne-card__name, .ne-name, h3');
                    if (nameEl) nameEl.textContent = newName;
                }
                if (window.reloadNeCards) window.reloadNeCards();
            } else {
                alert('Ошибка: ' + (data.error || 'не удалось'));
            }
        })
        .catch(function(e) {
            console.error('[submitNeRename] error:', e);
            alert('Ошибка: ' + e.message);
        });
    };

    // Esc закрывает модалки
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            if (window.closeNeModal) window.closeNeModal();
            if (window.closeNeRename) window.closeNeRename();
        }
    });

})();
