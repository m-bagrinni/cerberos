/* Drag & drop для карточек NE на странице «Мультиплексоры». */
(function() {
    'use strict';

    function getCookie(name) {
        var m = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
        return m ? decodeURIComponent(m[2]) : '';
    }

    function initSortable() {
        var grid = document.querySelector('.device-grid');
        if (!grid || typeof Sortable === 'undefined') return;

        // Только карточки с data-ne-id
        Sortable.create(grid, {
            animation: 150,
            ghostClass: 'device-card--ghost',
            dragClass: 'device-card--dragging',
            handle: '.device-card__header',  // тянуть за шапку (чтобы не мешать клику по карточке)
            filter: '.device-card__edit',     // игнорировать клики по карандашу
            preventOnFilter: false,
            onEnd: function(evt) {
                if (evt.oldIndex === evt.newIndex) return;

                // Собираем новый порядок
                var cards = Array.from(grid.querySelectorAll('.mux-card[data-ne-id]'));
                var order = cards.map(function(card, idx) {
                    return {
                        id: parseInt(card.dataset.neId, 10),
                        sort_order: (idx + 1) * 10,
                    };
                });

                // Отправляем на сервер
                fetch('/api/v1/ne/reorder-bulk/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCookie('csrftoken'),
                    },
                    body: JSON.stringify({order: order}),
                })
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    if (!data.ok) {
                        console.error('reorder failed', data);
                        alert('Не удалось сохранить порядок');
                    }
                })
                .catch(function(e) {
                    console.error('reorder error', e);
                });
            },
        });
    }

    // Инициализация после загрузки DOM
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initSortable);
    } else {
        initSortable();
    }
})();


// ============================================================
// Кнопка «Опросить все»
// ============================================================

window.pollAllGateways = function() {
    var btn = event && event.target ? event.target.closest('button') : null;
    var origHtml = btn ? btn.innerHTML : '';
    var gatewayIp = window.MUX_GATEWAY_IP || '';

    if (!gatewayIp) {
        alert('Не найден IP шлюза');
        return;
    }

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Опрос…';
    }

    fetch('/api/v1/gateway/' + gatewayIp + '/poll-now/', {
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
            if (window.reloadNeCards) window.reloadNeCards();
            setTimeout(function() {
                if (btn) {
                    btn.innerHTML = origHtml;
                    btn.style.background = '';
                    btn.disabled = false;
                }
            }, 2000);
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


window.reloadNeCards = function() {
    var grid = document.querySelector('.device-grid');
    if (!grid) return;

    grid.style.transition = 'opacity 0.2s';
    grid.style.opacity = '0.5';

    fetch(window.location.pathname + '?partial=1')
        .then(function(r) { return r.text(); })
        .then(function(html) {
            var parser = new DOMParser();
            var doc = parser.parseFromString(html, 'text/html');
            var newGrid = doc.querySelector('.device-grid');
            if (newGrid) {
                grid.innerHTML = newGrid.innerHTML;
                grid.style.opacity = '1';
                if (window.Sortable) {
                    if (grid._sortable) { try { grid._sortable.destroy(); } catch(e) {} }
                    grid._sortable = Sortable.create(grid, {
                        animation: 150,
                        ghostClass: 'device-card--ghost',
                        dragClass: 'device-card--dragging',
                        handle: '.device-card__header',
                        filter: '.device-card__edit',
                        preventOnFilter: false,
                    });
                }
            } else {
                grid.style.opacity = '1';
                console.warn('reloadNeCards: device-grid не найден');
            }
        })
        .catch(function(e) {
            console.error('reloadNeCards error', e);
            grid.style.opacity = '1';
        });
};
