/**
 * Кнопки управления портами на странице интерфейсов.
 */

function getCookie(name) {
    const cookies = document.cookie.split(';');
    for (let c of cookies) {
        c = c.trim();
        if (c.startsWith(name + '=')) {
            return decodeURIComponent(c.substring(name.length + 1));
        }
    }
    return null;
}

document.addEventListener('click', async (e) => {
    const btn = e.target.closest('.port-toggle-btn');
    if (!btn) return;

    const ifaceId = btn.dataset.ifaceId;
    const ifaceName = btn.dataset.ifaceName;
    const currentAdmin = parseInt(btn.dataset.currentAdmin);
    const targetState = currentAdmin === 1 ? 'down' : 'up';
    const actionLabel = targetState === 'down' ? 'ВЫКЛЮЧИТЬ' : 'ВКЛЮЧИТЬ';

    if (!confirm(`${actionLabel} порт ${ifaceName}?\n\nПорт перестанет передавать трафик.`)) {
        return;
    }

    btn.disabled = true;
    btn.style.opacity = '0.5';

    try {
        const resp = await fetch(`/api/v1/interfaces/${ifaceId}/set-admin-status/`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken'),
                'Accept': 'application/json',
            },
            credentials: 'same-origin',
            body: JSON.stringify({ state: targetState }),
        });

        const data = await resp.json();

        if (!resp.ok) {
            alert(`Ошибка: ${data.error || data.detail || 'неизвестная'}`);
            btn.disabled = false;
            btn.style.opacity = '1';
            return;
        }

        // Успех — обновляем UI
        const row = btn.closest('[data-iface-row]');
        if (row) {
            const statusSpan = row.querySelector('.c-status');
            if (statusSpan) {
                statusSpan.textContent = targetState;
                statusSpan.className = 'c-status c-status--' + (targetState === 'up' ? 'ok' : 'crit');
                statusSpan.style.fontSize = '11px';
            }
        }

        // Обновляем кнопку
        btn.dataset.currentAdmin = targetState === 'up' ? '1' : '2';
        btn.title = targetState === 'up' ? 'Выключить порт' : 'Включить порт';
        const icon = btn.querySelector('i');
        if (icon) {
            icon.style.color = targetState === 'up' ? '#6b7280' : '#10b981';
        }

        // Показываем ненавязчивое уведомление
        showNotification(`Порт ${ifaceName} → ${targetState}`, 'success');

    } catch (e) {
        alert(`Ошибка сети: ${e.message}`);
    } finally {
        btn.disabled = false;
        btn.style.opacity = '1';
    }
});

function showNotification(message, type = 'info') {
    const div = document.createElement('div');
    div.textContent = message;
    div.style.cssText = `
        position: fixed;
        top: 80px;
        right: 20px;
        background: ${type === 'success' ? '#10b981' : '#ef4444'};
        color: white;
        padding: 10px 20px;
        border-radius: 6px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        z-index: 9999;
        font-size: 14px;
        animation: slideIn 200ms;
    `;
    document.body.appendChild(div);
    setTimeout(() => div.remove(), 3000);
}
