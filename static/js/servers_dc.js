// ===== Общие хелперы (static/js/core.js) =====
function getCsrfToken() { return Cerber.csrfToken(); }
var __dcEsc = Cerber.escapeHtml;  // var: файл может выполниться повторно при подгрузке панели

async function apiPost(url, body) {
    const resp = await fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrfToken(),
        },
        body: JSON.stringify(body || {}),
    });
    if (resp.redirected) {
        window.location.href = resp.url;
        return null;
    }
    return resp;
}


// 1. Инициализация страницы серверов (клики по дереву)
window.initServersPage = function() {
    const panel = document.getElementById('server-detail-panel');
    if (!panel) return;

    async function loadServerDetail(serverId) {
        panel.innerHTML = `<div class="g-empty"><div class="spinner-border text-primary" role="status" style="margin-bottom: 16px;"></div><div>Загрузка...</div></div>`;
        try {
            const resp = await fetch(`/servers/${serverId}/detail/`);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const html = await resp.text();
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');
            const content = doc.querySelector('.g-wrapper');
            if (content) {
                panel.innerHTML = content.outerHTML;
                const scripts = panel.querySelectorAll('script');
                scripts.forEach(oldScript => {
                    const newScript = document.createElement('script');
                    Array.from(oldScript.attributes).forEach(attr => newScript.setAttribute(attr.name, attr.value));
                    newScript.appendChild(document.createTextNode(oldScript.innerHTML));
                    panel.appendChild(newScript);
                });
                // Запускаем инициализацию вкладок и первой загрузки данных
                setTimeout(() => {
                    if (typeof window.initDcTabs === 'function') window.initDcTabs();
                    if (typeof window.initDcButtons === 'function') window.initDcButtons();
                    if (typeof window.loadServerMetrics === 'function') window.loadServerMetrics();
                }, 100);
            } else {
                panel.innerHTML = '<div class="g-empty" style="color: var(--red);">Ошибка загрузки</div>';
            }
        } catch (e) {
            panel.innerHTML = `<div class="g-empty" style="color: var(--red);">Ошибка: ${__dcEsc(e.message)}</div>`;
        }
    }

    document.querySelectorAll('.g-server-item').forEach(item => {
        item.addEventListener('click', () => {
            document.querySelectorAll('.g-server-item').forEach(i => i.classList.remove('active'));
            item.classList.add('active');
            loadServerDetail(item.dataset.id);
        });
    });

    const first = document.querySelector('.g-server-item');
    if (first) setTimeout(() => first.click(), 100);
};

// 2. Инициализация вкладок
window.initDcTabs = function() {
    const tabs = document.querySelectorAll('.g-tab');
    const panes = document.querySelectorAll('.tab-pane');
    tabs.forEach(tab => {
        tab.addEventListener('click', (e) => {
            e.preventDefault();
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            panes.forEach(p => p.style.display = 'none');
            const target = document.getElementById('tab-' + tab.dataset.tab);
            if (target) target.style.display = 'block';
            
            if (tab.dataset.tab === 'users') {
                window.loadAdUsers();
            } else if (tab.dataset.tab === 'groups') {
                window.loadAdGroups();
            } else if (tab.dataset.tab === 'services') {
                window.loadDcServices();
            } else if (tab.dataset.tab === 'overview') {
                window.loadServerMetrics();
            }
        });
    });
};

// 3. Инициализация кнопок
window.initDcButtons = function() {

    // Обновить пользователей
    const btnRefresh = document.getElementById('btn-refresh-users');
    if (btnRefresh) btnRefresh.addEventListener('click', window.loadAdUsers);

    // Кнопки "Добавить..."
    const btnAdd = document.getElementById('btn-add-user');
    if (btnAdd) btnAdd.addEventListener('click', () => {
        document.getElementById('add-login').value = '';
        document.getElementById('add-name').value = '';
        document.getElementById('add-email').value = '';
        document.getElementById('add-password').value = '';
        document.getElementById('add-user-modal').style.display = 'flex';
    });

    const btnAddGroup = document.getElementById('btn-add-group');
    if (btnAddGroup) btnAddGroup.addEventListener('click', () => {
        document.getElementById('create-group-name').value = '';
        document.getElementById('create-group-desc').value = '';
        document.getElementById('create-group-type').value = 'global';
        document.getElementById('create-group-modal').style.display = 'flex';
    });

    // user-modal: Сохранить
    const modalSave = document.getElementById('modal-save');
    if (modalSave) modalSave.addEventListener('click', async () => {
        const login = window.currentLoginGlobal;
        if (!login) return;
        const name = document.getElementById('modal-name').value.trim();
        const email = document.getElementById('modal-email').value.trim();
        const password = document.getElementById('modal-password').value;
        try {
            if (name || email) {
                const r = await apiPost(`/servers/api/ad-users/${encodeURIComponent(login)}/update/`, { name, email });
                if (r.status === 403) { alert('Нет прав'); return; }
                const d = await r.json();
                if (!d.ok) { alert(d.error || 'Ошибка'); return; }
            }
            if (password) {
                const r = await apiPost(`/servers/api/ad-users/${encodeURIComponent(login)}/reset-password/`, { password });
                if (r.status === 403) { alert('Нет прав'); return; }
                const d = await r.json();
                if (!d.ok) { alert(d.error || 'Ошибка сброса пароля'); return; }
            }
            document.getElementById('user-modal').style.display = 'none';
            window.loadAdUsers();
        } catch (e) { alert(e.message); }
    });

    // user-modal: Отмена
    const modalCancel = document.getElementById('modal-cancel');
    if (modalCancel) modalCancel.addEventListener('click', () => {
        document.getElementById('user-modal').style.display = 'none';
    });

    // add-user-modal: Создать
    const addSave = document.getElementById('add-modal-save');
    if (addSave) addSave.addEventListener('click', async () => {
        const login = document.getElementById('add-login').value.trim();
        const name = document.getElementById('add-name').value.trim();
        const email = document.getElementById('add-email').value.trim();
        const password = document.getElementById('add-password').value;
        if (!login || !name || !password) { alert('Заполните Логин, ФИО и Пароль'); return; }
        try {
            const r = await apiPost('/servers/api/ad-users/create/', { login, name, email, password });
            if (r.status === 403) { alert('Нет прав'); return; }
            const d = await r.json();
            if (d.ok) {
                document.getElementById('add-user-modal').style.display = 'none';
                window.loadAdUsers();
            } else alert(d.error || 'Ошибка');
        } catch (e) { alert(e.message); }
    });

    // add-user-modal: Отмена
    const addCancel = document.getElementById('add-modal-cancel');
    if (addCancel) addCancel.addEventListener('click', () => {
        document.getElementById('add-user-modal').style.display = 'none';
    });

    // create-group-modal: Создать
    const createGroupSave = document.getElementById('create-group-save');
    if (createGroupSave) createGroupSave.addEventListener('click', async () => {
        const name = document.getElementById('create-group-name').value.trim();
        const description = document.getElementById('create-group-desc').value.trim();
        const group_type = document.getElementById('create-group-type').value;
        if (!name) { alert('Введите название группы'); return; }
        try {
            const r = await apiPost('/servers/api/ad-groups/create/', { name, description, group_type });
            if (r.status === 403) { alert('Нет прав'); return; }
            const d = await r.json();
            if (d.ok) {
                document.getElementById('create-group-modal').style.display = 'none';
                window.loadAdGroups();
            } else alert(d.error || 'Ошибка');
        } catch (e) { alert(e.message); }
    });

    // create-group-modal: Отмена
    const createGroupCancel = document.getElementById('create-group-cancel');
    if (createGroupCancel) createGroupCancel.addEventListener('click', () => {
        document.getElementById('create-group-modal').style.display = 'none';
    });

    // group-modal: Добавить в группу
    const groupAddBtn = document.getElementById('group-add-btn');
    if (groupAddBtn) groupAddBtn.addEventListener('click', async () => {
        const login = document.getElementById('group-add-login').value.trim();
        if (!login || !window.currentGroupName) return;
        try {
            const r = await apiPost(`/servers/api/ad-groups/${encodeURIComponent(window.currentGroupName)}/add-user/`, { login });
            if (r.status === 403) { alert('Нет прав'); return; }
            const d = await r.json();
            if (d.ok) {
                document.getElementById('group-add-login').value = '';
                window.loadGroupMembers(window.currentGroupName);
            } else alert(d.error || 'Ошибка');
        } catch (e) { alert(e.message); }
    });

    // group-modal: Закрыть
    const groupClose = document.getElementById('group-modal-close');
    if (groupClose) groupClose.addEventListener('click', () => {
        document.getElementById('group-modal').style.display = 'none';
    });
};


// 4. Загрузка метрик (графики)
window.loadServerMetrics = async function() {
    const wrapper = document.querySelector('.g-wrapper[data-server-id]');
    const serverId = wrapper ? wrapper.getAttribute('data-server-id') : null;
    if (!serverId) return;
    try {
        const resp = await fetch(`/servers/api/server/${serverId}/metrics/`);
        const data = await resp.json();
        if (data.ok) {
            if (data.metrics.cpu_load) document.getElementById('metric-cpu').textContent = data.metrics.cpu_load.value.toFixed(2);
            window.loadUserStats();
            if (data.metrics.ram_used_pct) document.getElementById('metric-ram').textContent = data.metrics.ram_used_pct.value.toFixed(1) + '%';
            if (data.metrics.disk_used_pct) document.getElementById('metric-disk').textContent = data.metrics.disk_used_pct.value.toFixed(1) + '%';
            
            const ctx = document.getElementById('metrics-chart');
            if (ctx && data.history && data.history.cpu_load) {
                if (window.metricsChart) window.metricsChart.destroy();
                window.metricsChart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: data.history.cpu_load.map(p => p.time),
                        datasets: [{
                            label: 'CPU Load',
                            data: data.history.cpu_load.map(p => p.value),
                            borderColor: '#ff780a',
                            backgroundColor: 'rgba(255, 120, 10, 0.1)',
                            fill: true,
                            tension: 0.3,
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: { legend: { labels: { color: '#8e8e8e' } } },
                        scales: {
                            x: { ticks: { color: '#8e8e8e' }, grid: { color: '#2c3235' } },
                            y: { ticks: { color: '#8e8e8e' }, grid: { color: '#2c3235' } }
                        }
                    }
                });
            }
        }
    } catch (e) { console.error(e); }
};

// 5. Загрузка пользователей
window.loadAdUsers = async function() {
    const tbody = document.getElementById('ad-users-body');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; padding: 20px; color: var(--text-muted);">Загрузка...</td></tr>';
    try {
        const resp = await fetch('/servers/api/ad-users/');
        const data = await resp.json();
        if (data.ok && data.users.length > 0) {
            tbody.innerHTML = data.users.map(u => {
                const statusColor = u.is_active ? 'var(--green)' : 'var(--red)';
                const statusText = u.is_active ? 'Активен' : 'Заблокирован';
                const actionBtn = u.is_active 
                    ? `<button class="g-btn" data-need-edit="servers" style="color: var(--red); border-color: var(--red);" data-login="${__dcEsc(u.login)}" onclick="event.stopPropagation(); window.toggleUser(this.dataset.login, true)">Блок</button>`
                    : `<button class="g-btn" data-need-edit="servers" style="color: var(--green); border-color: var(--green);" data-login="${__dcEsc(u.login)}" onclick="event.stopPropagation(); window.toggleUser(this.dataset.login, false)">Разблок</button>`;
                return `<tr style="cursor: pointer;" data-login="${__dcEsc(u.login)}" data-name="${__dcEsc(u.name || '')}" data-email="${__dcEsc(u.email || '')}"
                    onclick="window.openUserModal(this.dataset.login, this.dataset.name, this.dataset.email)">
                    <td style="font-family: monospace;">${__dcEsc(u.login)}</td>
                    <td>${__dcEsc(u.name || '—')}</td>
                    <td><span style="color: ${statusColor}; font-weight: 500;">${statusText}</span></td>
                    <td style="text-align: right;">${actionBtn}</td>
                </tr>`;
            }).join('');
        } else {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; padding: 20px; color: var(--text-muted);">Пользователи не найдены</td></tr>';
        }
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; padding: 20px; color: var(--red);">Ошибка: ${__dcEsc(e.message)}</td></tr>`;
    }
};

// 6. Загрузка служб (из БД)
window.loadDcServices = async function() {
    const tbody = document.getElementById('dc-services-body');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="3" style="text-align: center; padding: 20px; color: var(--text-muted);">Загрузка служб...</td></tr>';
    const wrapper = document.querySelector('.g-wrapper[data-server-id]');
    const serverId = wrapper ? wrapper.getAttribute('data-server-id') : null;
    if (!serverId) return;
    try {
        const resp = await fetch(`/servers/api/server/${serverId}/services/`);
        const data = await resp.json();
        if (data.ok && data.services.length > 0) {
            tbody.innerHTML = data.services.map(s => {
                const stateColor = s.state === 'Running' ? 'var(--green)' : 'var(--red)';
                const stateText = s.state === 'Running' ? 'Работает' : 'Остановлена';
                return `<tr>
                    <td style="font-family: monospace;">${__dcEsc(s.name)}</td>
                    <td><span style="color: ${stateColor}; font-weight: 500;">${stateText}</span></td>
                    <td style="text-align: right;"><button class="g-btn" data-need-edit="servers" style="padding: 2px 8px; font-size: 11px;">Управление</button></td>
                </tr>`;
            }).join('');
        } else {
            tbody.innerHTML = '<tr><td colspan="3" style="text-align: center; padding: 20px; color: var(--text-muted);">Службы не найдены</td></tr>';
        }
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="3" style="text-align: center; padding: 20px; color: var(--red);">Ошибка: ${__dcEsc(e.message)}</td></tr>`;
    }
};

// 7. Загрузка групп
window.loadAdGroups = async function() {
    const tbody = document.getElementById('ad-groups-body');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; padding: 20px; color: var(--text-muted);">Загрузка...</td></tr>';
    try {
        const resp = await fetch('/servers/api/ad-groups/');
        const data = await resp.json();
        if (data.ok && data.groups.length > 0) {
            const filterCheckbox = document.getElementById('filter-custom-groups');
            const showOnlyCustom = filterCheckbox ? filterCheckbox.checked : true;
            const filtered = data.groups.filter(g => !showOnlyCustom || g.name.startsWith('гр_'));
            tbody.innerHTML = filtered.map(g => `
                <tr style="cursor: pointer;">
                    <td style="font-family: monospace;">${__dcEsc(g.name)}</td>
                    <td>${__dcEsc(g.description || '—')}</td>
                    <td>${__dcEsc(g.members_count)}</td>
                    <td style="text-align: right;"><button class="g-btn" data-need-edit="servers" data-name="${__dcEsc(g.name)}" onclick="window.openGroupModal(this.dataset.name)">Управление</button></td>
                </tr>
            `).join('');
        } else {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; padding: 20px; color: var(--text-muted);">Группы не найдены</td></tr>';
        }
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; padding: 20px; color: var(--red);">Ошибка: ${__dcEsc(e.message)}</td></tr>`;
    }
};

// 8. Функции для модалок
window.openUserModal = function(login, name, email) {
    window.currentLoginGlobal = login;
    document.getElementById('modal-login').value = login;
    document.getElementById('modal-name').value = name;
    document.getElementById('modal-email').value = email;
    document.getElementById('modal-password').value = '';
    document.getElementById('user-modal').style.display = 'flex';
};

window.toggleUser = async function(login, block) {
    if (!(await CerberConfirm(block ? `Заблокировать ${login}?` : `Разблокировать ${login}?`))) return;
    try {
        const resp = await apiPost(`/servers/api/ad-users/${encodeURIComponent(login)}/toggle/`, { block });
        if (!resp) return;
        if (resp.status === 403) { alert('Нет прав. Только staff может менять AD.'); return; }
        const data = await resp.json();
        if (data.ok) window.loadAdUsers();
        else alert(data.error || `Ошибка ${resp.status}`);
    } catch (e) { alert(e.message); }
};

// 9. Автозапуск при прямом открытии страницы детали
document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('server-detail-panel')) {
        window.initServersPage();
    }
});

window.loadUserStats = async function() {
    const wrapper = document.querySelector('.g-wrapper[data-server-id]');
    const serverId = wrapper ? wrapper.getAttribute('data-server-id') : null;
    if (!serverId) return;
    try {
        const resp = await fetch(`/servers/api/server/${serverId}/user-stats/`);
        const data = await resp.json();
        
        if (data.ok) {
            // Онлайн
            const onlineEl = document.getElementById('online-users-list');
            if (onlineEl) {
                if (data.online_users.length > 0) {
                    onlineEl.innerHTML = data.online_users.map(u => `
                        <div style="padding: 6px 0; border-bottom: 1px solid var(--border);">
                            <span style="font-family: monospace; color: var(--green);">${__dcEsc(u.login)}</span>
                        </div>
                    `).join('');
                } else {
                    onlineEl.innerHTML = '<div style="color: var(--text-muted);">Нет активных сессий</div>';
                }
            }
            
            // Последние входы
            const lastEl = document.getElementById('last-logins-list');
            if (lastEl) {
                if (data.last_logins.length > 0) {
                    lastEl.innerHTML = data.last_logins.map(u => `
                        <div style="padding: 6px 0; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between;">
                            <span style="font-family: monospace;">${__dcEsc(u.login)}</span>
                            <span style="color: var(--text-muted); font-size: 12px;">${__dcEsc(u.last_logon)}</span>
                        </div>
                    `).join('');
                } else {
                    lastEl.innerHTML = '<div style="color: var(--text-muted);">Нет данных о входах</div>';
                }
            }
        }
    } catch (e) { console.error(e); }
};
