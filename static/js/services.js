/* ═══════════════════════════════════════════════════════════
   Службы — панель управления.
   ═══════════════════════════════════════════════════════════ */
(function() {
    'use strict';

    var API = '/api/v1/services';
    var refreshInterval = 10000;  // 10 сек
    var timer = null;

    function fmtBytes(b) {
        if (b === null || b === undefined) return '—';
        if (b < 1024) return b + ' B';
        if (b < 1024*1024) return (b/1024).toFixed(1) + ' KB';
        if (b < 1024*1024*1024) return (b/1024/1024).toFixed(1) + ' MB';
        return (b/1024/1024/1024).toFixed(2) + ' GB';
    }

    function fmtTime(iso) {
        if (!iso) return '—';
        var d = new Date(iso);
        return d.toLocaleString('ru-RU', {hour12: false});
    }

    function fmtAge(sec) {
        if (sec === null || sec === undefined) return '—';
        if (sec < 60) return Math.round(sec) + ' сек';
        if (sec < 3600) return Math.round(sec/60) + ' мин';
        if (sec < 86400) return Math.round(sec/3600) + ' ч';
        return Math.round(sec/86400) + ' д';
    }

    function csrf() {
        var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return m ? m[1] : '';
    }

    // ═══ Health ═══
    async function loadHealth() {
        try {
            var r = await fetch(API + '/health/');
            var d = await r.json();
            
            document.getElementById('h-db').textContent = fmtBytes(d.db_size_bytes);
            document.getElementById('h-redis').textContent = fmtBytes(d.redis_used_bytes);
            document.getElementById('h-queue').textContent = d.queue_len !== null ? d.queue_len : '—';
            document.getElementById('h-alerts').textContent = 
                d.alerts_firing + ' (' + d.alerts_crit + ' crit, ' + d.alerts_warn + ' warn)';
            document.getElementById('h-mv').textContent = 
                d.metric_value_count.toLocaleString('ru-RU');
            document.getElementById('h-pg').textContent = d.pg_connections;
            
            // Status badge
            var badge = document.getElementById('health-badge');
            if (d.alerts_crit > 0) {
                badge.textContent = '⚠️ ' + d.alerts_crit + ' crit';
                badge.parentElement.querySelector('.dot').className = 'dot dot--crit';
            } else if (d.alerts_warn > 0) {
                badge.textContent = '⚠️ ' + d.alerts_warn + ' warn';
                badge.parentElement.querySelector('.dot').className = 'dot dot--warn';
            } else {
                badge.textContent = 'OK';
                badge.parentElement.querySelector('.dot').className = 'dot dot--ok';
            }
        } catch (e) {
            console.error('[health]', e);
        }
    }

    // ═══ Containers ═══
    async function loadContainers() {
        try {
            var r = await fetch(API + '/containers/');
            var d = await r.json();
            var grid = document.getElementById('containers-grid');
            var count = document.getElementById('containers-count');
            
            if (!d.containers || d.containers.length === 0) {
                grid.innerHTML = '<div class="services-loading">Контейнеры не найдены</div>';
                count.textContent = '';
                return;
            }
            
            count.textContent = '(' + d.containers.length + ')';
            grid.innerHTML = d.containers.map(function(c) {
                var cls = c.is_up ? '' : 'is-stopped';
                return '<div class="services-container-card ' + cls + '" data-name="' + c.name + '">' +
                    '<div class="services-container-card__head">' +
                        '<span class="services-container-card__name">' + c.short_name + '</span>' +
                        '<span class="services-container-card__status">' + (c.is_up ? '✅' : '⏹') + '</span>' +
                    '</div>' +
                    '<div class="services-container-card__status" style="font-size:11px;">' + c.status + '</div>' +
                    '<div class="services-container-card__actions">' +
                        '<button data-action="restart" ' + (c.is_up ? '' : 'disabled') + '>↻ Restart</button>' +
                        '<button data-action="start" ' + (c.is_up ? 'disabled' : '') + '>▶ Start</button>' +
                        '<button data-action="stop" ' + (c.is_up ? '' : 'disabled') + '>⏹ Stop</button>' +
                    '</div>' +
                '</div>';
            }).join('');
            
            // Bind actions
            grid.querySelectorAll('button[data-action]').forEach(function(btn) {
                btn.addEventListener('click', async function() {
                    var card = btn.closest('.services-container-card');
                    var name = card.dataset.name;
                    var action = btn.dataset.action;
                    
                    if (action === 'stop' && !(await CerberConfirm('Остановить ' + name + '?'))) return;
                    
                    btn.disabled = true;
                    btn.textContent = '...';
                    try {
                        var r = await fetch(API + '/containers/' + name + '/' + action + '/', {
                            method: 'POST',
                            headers: { 'X-CSRFToken': csrf() },
                        });
                        var result = await r.json();
                        setTimeout(loadAll, 1500);
                    } catch (e) {
                        console.error(e);
                        alert('Ошибка: ' + e.message);
                    }
                });
            });
        } catch (e) {
            console.error('[containers]', e);
        }
    }

    // ═══ Tasks ═══
    async function loadTasks() {
        try {
            var r = await fetch(API + '/tasks/');
            var d = await r.json();
            var tbody = document.getElementById('tasks-tbody');
            var count = document.getElementById('tasks-count');
            
            count.textContent = '(' + d.tasks.length + ')';
            
            tbody.innerHTML = d.tasks.map(function(t) {
                // Динамический порог: 2× interval, если есть interval_sec
                var intervalSec = t.interval_sec || null;
                var staleThreshold = intervalSec ? intervalSec * 2 : 1800;
                
                var lastCls = 'task-last-run';
                if (t.last_age_sec === null) lastCls += ' is-dead';
                else if (t.last_age_sec > staleThreshold) lastCls += ' is-stale';
                
                var stateCls = t.enabled ? 'is-enabled' : 'is-disabled';
                var stateText = t.enabled ? 'вкл' : 'выкл';
                
                return '<tr>' +
                    '<td class="task-name">' + t.name + '</td>' +
                    '<td class="task-target">' + t.task + '</td>' +
                    '<td class="task-target">' + (t.interval || '—') + '</td>' +
                    '<td class="' + lastCls + '">' + (t.last_run_at ? fmtAge(t.last_age_sec) + ' назад' : 'никогда') + '</td>' +
                    '<td>' + t.total_run + '</td>' +
                    '<td>' +
                        '<span class="task-state ' + stateCls + '">' + stateText + '</span> ' +
                        '<button class="btn-action" data-action="toggle" data-id="' + t.id + '" data-enabled="' + t.enabled + '">' + (t.enabled ? '⏸' : '▶') + '</button> ' +
                        '<button class="btn-action" data-action="run" data-id="' + t.id + '">⟳</button>' +
                    '</td>' +
                '</tr>';
            }).join('');
            
            tbody.querySelectorAll('button[data-action]').forEach(function(btn) {
                btn.addEventListener('click', async function() {
                    var id = btn.dataset.id;
                    var action = btn.dataset.action;
                    var endpoint;
                    if (action === 'toggle') {
                        endpoint = btn.dataset.enabled === 'true' ? 'disable' : 'enable';
                    } else {
                        endpoint = 'run_now';
                    }
                    try {
                        var r = await fetch(API + '/tasks/' + id + '/' + endpoint + '/', {
                            method: 'POST',
                            headers: { 'X-CSRFToken': csrf() },
                        });
                        var result = await r.json();
                        setTimeout(loadAll, 500);
                    } catch (e) {
                        console.error(e);
                        alert('Ошибка: ' + e.message);
                    }
                });
            });
        } catch (e) {
            console.error('[tasks]', e);
        }
    }

    // ═══ Events ═══
    async function loadEvents() {
        try {
            var r = await fetch(API + '/events/');
            var d = await r.json();
            var el = document.getElementById('events-list');
            
            if (!d.events || d.events.length === 0) {
                el.innerHTML = '<div class="services-loading">Событий нет</div>';
                return;
            }
            
            el.innerHTML = d.events.map(function(e) {
                var t = new Date(e.created_at);
                var time = t.toLocaleTimeString('ru-RU');
                var icon = e.success ? '✅' : '❌';
                return '<div class="services-event">' +
                    '<span class="services-event__time">' + time + '</span>' +
                    icon +
                    '<span class="services-event__action services-event__action--' + e.action + '">' + e.action + '</span>' +
                    (e.is_system ? '<span class="badge" style="background: var(--bg-elevated); color: var(--text-muted); font-size: 9px; margin-right: 5px;">СИСТЕМА</span>' : '') +
                    '<span class="services-event__target">' + (e.is_system ? e.target_name : e.target_type + ': ' + e.target_name) + '</span>' +
                '</div>';
            }).join('');
        } catch (e) {
            console.error('[events]', e);
        }
    }

    // ═══ Load All ═══
    async function loadAll() {
        await Promise.all([
            loadHealth(),
            loadContainers(),
            loadTasks(),
            loadEvents(),
        ]);
    }

    // ═══ Start ═══
    function start() {
        timer = Cerber.poll(loadAll, refreshInterval, { immediate: true });  // пауза в фоне
    }

    document.addEventListener('DOMContentLoaded', start);
})();
