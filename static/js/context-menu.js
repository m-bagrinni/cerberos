document.addEventListener('DOMContentLoaded', function() {
    const menu = document.getElementById('map-context-menu');
    
    // Перемещаем меню в body (если оно есть)
    if (menu && menu.parentElement !== document.body) {
        document.body.appendChild(menu);
    }

    let currentTarget = null;

    // Перехватываем правую кнопку мыши на карточках устройств
    // (замените селектор .device-card на свой, если карточки называются иначе)
    // Обработчик долгого нажатия (для мобильных/Telegram)
    let touchTimer = null;
    document.addEventListener('touchstart', function(e) {
        const card = e.target.closest('.device-card');
        if (!card) return;
        
        touchTimer = setTimeout(function() {
            // Вибрация (если поддерживается)
            if (navigator.vibrate) navigator.vibrate(50);
            
            currentTarget = card;
            const touch = e.touches[0];
            menu.style.display = 'block';
            const menuWidth = 220;
            const menuHeight = 200;
            let left = touch.clientX;
            let top = touch.clientY;
            
            if (left + menuWidth > window.innerWidth) {
                left = window.innerWidth - menuWidth - 10;
            }
            if (top + menuHeight > window.innerHeight) {
                top = window.innerHeight - menuHeight - 10;
            }
            
            menu.style.left = left + 'px';
            menu.style.top = top + 'px';
        }, 500); // 500 мс — долгое нажатие
    });
    
    document.addEventListener('touchend', function() {
        if (touchTimer) {
            clearTimeout(touchTimer);
            touchTimer = null;
        }
    });
    
    document.addEventListener('touchmove', function() {
        if (touchTimer) {
            clearTimeout(touchTimer);
            touchTimer = null;
        }
    });

    // Обработчик для карты (правый клик)
    document.getElementById('stationsMap')?.addEventListener('contextmenu', function(e) {
        e.preventDefault();  // ← блокируем стандартное меню
        
        const menu = document.getElementById('map-context-menu');
        if (!menu) return;
        
        // Позиция с учётом границ окна
        const menuWidth = 220;
        const menuHeight = 200;
        let left = e.clientX;
        let top = e.clientY;
        
        if (left + menuWidth > window.innerWidth) {
            left = window.innerWidth - menuWidth - 10;
        }
        if (top + menuHeight > window.innerHeight) {
            top = window.innerHeight - menuHeight - 10;
        }
        
        menu.style.display = 'block';
        menu.style.left = left + 'px';
        menu.style.top = top + 'px';
    });

    document.addEventListener('contextmenu', function(e) {
        // Если это NE (мультиплексор) — пропускаем, но блокируем стандартное меню
        if (e.target.closest('.mux-ne')) {
            e.preventDefault();
            return;
        }
        
        const card = e.target.closest('.device-card');
        if (!card) return; // Если кликнули не по карточке — показываем стандартное меню браузера
        
        e.preventDefault();
        currentTarget = card;
        
        // Позиционируем меню
        menu.style.display = 'block';
        // Позиция с учётом границ окна
        const menuWidth = 220;
        const menuHeight = 200;
        let left = e.clientX;
        let top = e.clientY;
        
        if (left + menuWidth > window.innerWidth) {
            left = window.innerWidth - menuWidth - 10;
        }
        if (top + menuHeight > window.innerHeight) {
            top = window.innerHeight - menuHeight - 10;
        }
        
        menu.style.left = left + 'px';
        menu.style.top = top + 'px';
    });

    // Скрываем меню при клике вне его
    document.addEventListener('click', function(e) {
        if (menu && !menu.contains(e.target)) {
            menu.style.display = 'none';
        }
    });

    // Обработка кликов по пунктам меню
    if (menu) menu.addEventListener('click', function(e) {
        const item = e.target.closest('.map-ctx-item');
        if (!item) return;
        
        const action = item.dataset.action;
        const ip = currentTarget ? currentTarget.dataset.deviceIp : null;
        
        if (action === 'poll') {
            // CSRF-токен
            // CSRF и прочие общие хелперы — static/js/core.js
            function getCookie(name) { return name === 'csrftoken' ? Cerber.csrfToken() : null; }
            const csrftoken = getCookie('csrftoken');
            
            if (ip) {
                // Карточка устройства → опрос конкретного устройства
                fetch('/api/v1/devices/' + ip + '/poll-now/', {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': csrftoken,
                        'Content-Type': 'application/json'
                    },
                    credentials: 'same-origin'
                })
                .then(res => {
                    if (res.ok) showToast('Опрос запущен для ' + ip, 'success');
                    else showToast('Ошибка при опросе (HTTP ' + res.status + ')', 'error');
                })
                .catch(err => showToast('Ошибка сети: ' + err, 'error'));
            } else {
                // Карта → опрос ВСЕХ оптических линий
                fetch('/api/v1/optical-links/poll-all-now/', {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': csrftoken,
                        'Content-Type': 'application/json'
                    },
                    credentials: 'same-origin'
                })
                .then(res => {
                    if (res.ok) {
                        showToast('Опрос всех линий запущен', 'success');
                    } else {
                        showToast('Ошибка при опросе линий (HTTP ' + res.status + ')', 'error');
                    }
                })
                .catch(err => showToast('Ошибка сети: ' + err, 'error'));
            }
        } else if (action === 'web') {
            // Открываем веб-интерфейс
            window.open('http://' + ip, '_blank');
        } else if (action === 'copy') {
            // Копируем IP в буфер обмена
            // Копирование с fallback (для Telegram WebApp и HTTP)
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(ip).then(() => {
                    showToast('IP скопирован: ' + ip, 'success');
                }).catch(() => {
                    fallbackCopy(ip);
                });
            } else {
                fallbackCopy(ip);
            }
            
            function fallbackCopy(text) {
                const textarea = document.createElement('textarea');
                textarea.value = text;
                textarea.style.position = 'fixed';
                textarea.style.opacity = '0';
                document.body.appendChild(textarea);
                textarea.select();
                try {
                    document.execCommand('copy');
                    showToast('IP скопирован: ' + text, 'success');
                } catch (e) {
                    showToast('Не удалось скопировать IP: ' + text, 'error');
                }
                document.body.removeChild(textarea);
            }
        }
        
        menu.style.display = 'none';
    });
});
