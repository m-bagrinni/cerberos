/**
 * Управление портами (SNMP SET ifAdminStatus).
 * С красивым модальным окном и защитой от дубликатов.
 */
(function () {
    "use strict";

    // Защита от повторной инициализации
    if (window.__portControlInitialized) return;
    window.__portControlInitialized = true;

    // CSRF и прочие общие хелперы — static/js/core.js
    function getCookie(name) { return name === 'csrftoken' ? Cerber.csrfToken() : null; }

    // Создаём модальное окно подтверждения
    function confirmModal(title, message) {
        return new Promise((resolve) => {
            const overlay = document.createElement('div');
            overlay.style.cssText = `
                position: fixed; inset: 0; z-index: 100000;
                background: rgba(0,0,0,0.7);
                display: flex; align-items: center; justify-content: center;
            `;

            const modal = document.createElement('div');
            modal.style.cssText = `
                background: #181b1f;
                border: 1px solid #2c3235;
                border-radius: 8px;
                padding: 24px;
                min-width: 400px;
                max-width: 500px;
                box-shadow: 0 12px 48px rgba(0,0,0,0.6);
            `;

            modal.innerHTML = `
                <div style="font-size: 16px; font-weight: 600; color: #fff; margin-bottom: 12px;">
                    ${title}
                </div>
                <div style="font-size: 13px; color: #8e8e8e; margin-bottom: 20px;">
                    ${message}
                </div>
                <div style="display: flex; gap: 8px; justify-content: flex-end;">
                    <button id="pc-cancel" style="padding: 8px 16px; background: transparent;
                        border: 1px solid #3d444b; border-radius: 4px; color: #8e8e8e;
                        font-size: 13px; cursor: pointer;">Отмена</button>
                    <button id="pc-confirm" style="padding: 8px 16px; background: #f2495c;
                        border: none; border-radius: 4px; color: #fff; font-size: 13px;
                        font-weight: 600; cursor: pointer;">Выключить</button>
                </div>
            `;

            overlay.appendChild(modal);
            document.body.appendChild(overlay);

            modal.querySelector('#pc-confirm').addEventListener('click', () => {
                document.body.removeChild(overlay);
                resolve(true);
            });
            modal.querySelector('#pc-cancel').addEventListener('click', () => {
                document.body.removeChild(overlay);
                resolve(false);
            });
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) {
                    document.body.removeChild(overlay);
                    resolve(false);
                }
            });
        });
    }

    // Обработчик клика по кнопке (делегирование)
    async function handleClick(e) {
        const btn = e.target.closest('.port-toggle-btn');
        if (!btn) return;

        e.preventDefault();
        e.stopPropagation();

        const ifaceId = btn.dataset.ifaceId;
        const ifaceName = btn.dataset.ifaceName;
        const currentAdmin = btn.dataset.currentAdmin;
        const newState = currentAdmin == 1 ? 'down' : 'up';
        const actionText = newState === 'down' ? 'Выключить' : 'Включить';

        // Показываем модальное окно
        const ok = await confirmModal(
            `${actionText} порт ${ifaceName}?`,
            newState === 'down'
                ? 'Порт перестанет передавать трафик.'
                : 'Порт начнёт передавать трафик.'
        );
        if (!ok) return;

        btn.disabled = true;
        btn.style.opacity = '0.5';

        try {
            const resp = await fetch(`/api/v1/interfaces/${ifaceId}/set-admin-status/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken'),
                },
                credentials: 'same-origin',
                body: JSON.stringify({ state: newState }),
            });

            if (resp.ok) {
                if (typeof showToast === 'function') {
                    showToast(`Порт ${ifaceName} ${newState === 'down' ? 'выключен' : 'включён'}`, 'success');
                }
                // Перезагружаем вкладку «Интерфейсы» (без перезагрузки всей страницы)
                setTimeout(() => {
                    if (window._currentDeviceIp && window.loadDeviceTab) {
                        const activeTab = document.querySelector('.detail-tab.is-active');
                        if (activeTab) {
                            window.loadDeviceTab(window._currentDeviceIp, 'interfaces', activeTab);
                        }
                    } else {
                        // Fallback — перезагрузка страницы
                        location.reload();
                    }
                }, 500);
            } else {
                const err = await resp.text();
                if (typeof showToast === 'function') {
                    showToast('Ошибка: HTTP ' + resp.status, 'error');
                }
                console.error('[port-control]', err);
            }
        } catch (err) {
            if (typeof showToast === 'function') {
                showToast('Ошибка сети: ' + err.message, 'error');
            }
        } finally {
            btn.disabled = false;
            btn.style.opacity = '1';
        }
    }

    // Делегирование (работает и для AJAX-контента)
    document.addEventListener('click', handleClick);

})();
