/**
 * Редактирование ifAlias (SNMP SET) — двойной клик по ячейке.
 * С красивым модальным окном и защитой от дубликатов.
 */
(function () {
    "use strict";

    // Защита от повторной инициализации
    if (window.__interfaceEditInitialized) return;
    window.__interfaceEditInitialized = true;

    // CSRF и прочие общие хелперы — static/js/core.js
    function getCookie(name) { return name === 'csrftoken' ? Cerber.csrfToken() : null; }

    // Создаём модальное окно
    function createModal(currentValue) {
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
                <div style="font-size: 16px; font-weight: 600; color: #fff; margin-bottom: 16px;">
                    Описание интерфейса (ifAlias)
                </div>
                <input type="text" id="ifalias-input" value="${currentValue}"
                    style="width: 100%; box-sizing: border-box; padding: 10px 12px;
                           background: #0b0c0e; border: 1px solid #3d444b; border-radius: 4px;
                           color: #ccccdc; font-size: 14px; font-family: monospace;
                           outline: none; transition: border-color 0.2s;"
                    placeholder="Введите описание..." />
                <div style="display: flex; gap: 8px; justify-content: flex-end; margin-top: 20px;">
                    <button id="ifalias-cancel" style="padding: 8px 16px; background: transparent;
                        border: 1px solid #3d444b; border-radius: 4px; color: #8e8e8e;
                        font-size: 13px; cursor: pointer;">Отмена</button>
                    <button id="ifalias-save" style="padding: 8px 16px; background: #ff780a;
                        border: none; border-radius: 4px; color: #fff; font-size: 13px;
                        font-weight: 600; cursor: pointer;">Сохранить</button>
                </div>
            `;

            overlay.appendChild(modal);
            document.body.appendChild(overlay);

            const input = modal.querySelector('#ifalias-input');
            input.focus();
            input.select();

            const close = (result) => {
                document.body.removeChild(overlay);
                resolve(result);
            };

            modal.querySelector('#ifalias-save').addEventListener('click', () => {
                close(input.value);
            });
            modal.querySelector('#ifalias-cancel').addEventListener('click', () => {
                close(null);
            });
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) close(null);
            });
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') close(input.value);
                if (e.key === 'Escape') close(null);
            });
        });
    }

    // Обработчик двойного клика
    function handleDoubleClick(e) {
        const cell = e.target.closest('.iface-alias');
        if (!cell) return;

        const pk = cell.dataset.ifaceAliasPk;
        const currentValue = cell.dataset.ifaceAliasValue || '';

        if (!pk) return;

        e.preventDefault();
        e.stopPropagation();

        createModal(currentValue).then(async (newValue) => {
            if (newValue === null) return;  // отмена
            if (newValue === currentValue) return;  // без изменений

            try {
                const resp = await fetch(`/api/v1/interfaces/${pk}/set-alias/`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCookie('csrftoken'),
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify({ alias: newValue }),
                });

                if (resp.ok) {
                    cell.textContent = newValue || '—';
                    cell.dataset.ifaceAliasValue = newValue;
                    if (typeof showToast === 'function') {
                        showToast('Описание обновлено', 'success');
                    }
                } else {
                    const err = await resp.text();
                    if (typeof showToast === 'function') {
                        showToast('Ошибка: ' + resp.status, 'error');
                    }
                    console.error('[ifalias]', err);
                }
            } catch (err) {
                if (typeof showToast === 'function') {
                    showToast('Ошибка сети: ' + err.message, 'error');
                }
                console.error('[ifalias]', err);
            }
        });
    }

    // Вешаем обработчик (делегирование, чтобы работало и для AJAX-контента)
    document.addEventListener('dblclick', handleDoubleClick);

})();
