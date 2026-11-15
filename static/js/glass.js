/* Glassmorphism UI API — Cerberus */

// 1. ТЕМА
(function () {
  const KEY = 'glass-theme';
  const GlassTheme = {
    current() { return document.documentElement.classList.contains('dark') ? 'dark' : 'light'; },
    set(mode) {
      document.documentElement.classList.toggle('dark', mode === 'dark');
      localStorage.setItem(KEY, mode);
    },
    toggle() {
      this.set(this.current() === 'dark' ? 'light' : 'dark');
      return this.current();
    },
    init() {
      const saved = localStorage.getItem(KEY);
      if (saved) this.set(saved);
      else if (window.matchMedia('(prefers-color-scheme: dark)').matches) this.set('dark');
    }
  };
  window.GlassTheme = GlassTheme;
})();

// 2. ТОСТЫ
(function () {
  const GlassToast = {
    show(msg, type = 'info', ms = 3500) {
      const el = document.createElement('div');
      el.className = 'glass toast-item';
      el.style.cssText = ['position:fixed','right:20px','bottom:20px','z-index:9999','padding:14px 18px','max-width:360px','transform:translateY(20px)','opacity:0','transition:transform .25s, opacity .25s'].join(';');
      el.innerHTML = `<strong style="text-transform:capitalize">${Cerber.escapeHtml(type)}</strong><br><span>${Cerber.escapeHtml(msg)}</span>`;
      document.body.appendChild(el);
      requestAnimationFrame(() => { el.style.transform = 'translateY(0)'; el.style.opacity = '1'; });
      setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateY(20px)'; setTimeout(() => el.remove(), 300); }, ms);
    }
  };
  window.GlassToast = GlassToast;
})();

// 3. МОДАЛКИ
(function () {
  const GlassModal = {
    open(id) {
      const m = document.getElementById(id);
      if (!m) return;
      m.classList.add('open');
      m.style.display = 'block';
      document.body.classList.add('glass-modal-open');
    },
    close(id) {
      const m = document.getElementById(id);
      if (!m) return;
      m.classList.remove('open');
      m.style.display = 'none';
      document.body.classList.remove('glass-modal-open');
    }
  };
  window.GlassModal = GlassModal;
})();

// 4. ПОКРАСКА МЕТРИК SFP
(function() {
    function colorizeAll() {
        document.querySelectorAll('.sfp-metric, .sfp-card__row').forEach(metric => {
            const labelEl = metric.querySelector('.sfp-metric__label, .sfp-card__row-label');
            const valueEl = metric.querySelector('.sfp-metric__value, .sfp-card__row-value');
            if (!labelEl || !valueEl) return;
            const label = labelEl.innerText.trim().toUpperCase();
            const match = valueEl.innerText.match(/(-?\d+[,.]?\d*)/);
            if (!match) return;
            const val = parseFloat(match[1].replace(',', '.'));
            let color = '#ffffff';
            if (label === 'RX') {
                if (val < -25) color = '#f87171';
                else if (val < -20) color = '#fbbf24';
                else color = '#34d399';
            } else if (label === 'TX') {
                if (val > 0 || val < -10) color = '#f87171';
                else color = '#34d399';
            } else if (label === 'TEMP') {
                if (val > 65) color = '#f87171';
                else if (val >= 60) color = '#fbbf24';
                else color = '#34d399';
            } else if (label === 'VCC') {
                if (val < 3.1 || val > 3.5) color = '#f87171';
                else color = '#34d399';
            } else if (label === 'BIAS') {
                if (val > 40) color = '#f87171';
                else if (val > 30) color = '#fbbf24';
                else color = '#34d399';
            }
            valueEl.style.setProperty('color', color, 'important');
        });
    }
    colorizeAll();
    // Перекрашиваем только когда DOM реально изменился (HTMX/AJAX-вставки),
    // не чаще раза в 150 мс. Раньше был ещё setInterval(1000) — полный обход DOM каждую секунду.
    const obs = new MutationObserver(Cerber.debounce(colorizeAll, 150));
    obs.observe(document.body, { childList: true, subtree: true, characterData: true });
})();

// 5. ИНИЦИАЛИЗАЦИЯ ПРИ ЗАГРУЗКЕ
(function() {
  function ready(fn) {
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }
  ready(() => window.GlassTheme.init());
})();

// ============================================================

// ============================================================

// ============================================================

// ============================================================

// ============================================================
// Модалка IP с кнопками действий (с обновлением ячейки)
// ============================================================

// Функция для обновления класса ячейки (по data-id)
function updateCellClass(cellId, data) {
    const cell = document.querySelector(`.ip-cell[data-id="${cellId}"]`);
    if (!cell) return;

    // Убираем старые классы
    cell.classList.remove('ip-cell--alive', 'ip-cell--dead', 'ip-cell--reserved', 'ip-cell--alive-mon', 'ip-cell--free', 'ip-cell--unknown');

    // Добавляем новый класс
    if (data.is_reserved) {
        cell.classList.add('ip-cell--reserved');
    } else if (data.is_monitored && !data.is_alive) {
        cell.classList.add('ip-cell--dead');
    } else if (data.is_monitored && data.is_alive) {
        cell.classList.add('ip-cell--alive-mon');
    } else if (data.is_alive) {
        cell.classList.add('ip-cell--alive');
    } else {
        cell.classList.add('ip-cell--free');
    }

    // Обновляем dataset
    cell.dataset.alive = data.is_alive ? '1' : '0';
    cell.dataset.monitored = data.is_monitored ? '1' : '0';
    cell.dataset.state = data.state;
    cell.dataset.hostname = data.hostname || '';
}

document.addEventListener('click', async function(e) {
    const cell = e.target.closest('.ip-cell');
    if (!cell) return;

    const ip = cell.dataset.ip;
    const id = cell.dataset.id;
    if (!ip || !id) return;

    const modal = document.getElementById('ip-modal');
    const title = document.getElementById('modal-title');
    const body = document.getElementById('modal-body');
    if (!modal || !body) return;

    if (title) title.innerText = 'IP: ' + ip;
    body.innerHTML = '<div style="padding: 10px; color: #888;">Загрузка...</div>';
    modal.hidden = false;
    modal.style.display = 'flex';

    // Функция для перерисовки модалки (используется после действий)
    async function refreshModal() {
        try {
            const resp = await fetch(`/api/v1/ip-addresses/${id}/`, { credentials: 'same-origin' });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();

            // Если hostname пустой — пробуем получить через nmap (resolve-ip)
            if (!data.hostname) {
                try {
                    const r2 = await fetch(`/api/v1/subnets/resolve-ip/?ip=${encodeURIComponent(data.ip)}`, { credentials: 'same-origin' });
                    if (r2.ok) {
                        const j2 = await r2.json();
                        if (j2.ok && j2.hostname) {
                            data.hostname = j2.hostname;
                            data.mac = j2.mac || data.mac;
                            data.vendor = j2.vendor || '';
                        }
                    }
                } catch (e) {
                    console.warn('[Glass] resolve-ip failed:', e);
                }
            }

            // Обновляем ячейку в сетке
            updateCellClass(id, data);

            // Рендер данных
            let html = `<div style="padding: 10px;">`;
            // MAC
            if (data.mac) {
                html += `<div class="ip-modal__row"><span class="ip-modal__row-label"><i class="bi bi-hdd-network"></i> MAC</span><span class="ip-modal__row-value">${Cerber.escapeHtml(data.mac)}</span></div>`;
            }
            // IP
            html += `<div class="ip-modal__row"><span class="ip-modal__row-label"><i class="bi bi-globe"></i> IP</span><span class="ip-modal__row-value">${Cerber.escapeHtml(data.ip)}</span></div>`;
            // Hostname
            if (data.hostname) {
                html += `<div class="ip-modal__row"><span class="ip-modal__row-label"><i class="bi bi-pc-display"></i> Hostname</span><span class="ip-modal__row-value">${Cerber.escapeHtml(data.hostname)}</span></div>`;
            }
            // Станция
            if (data.station_name) {
                html += `<div class="ip-modal__row"><span class="ip-modal__row-label"><i class="bi bi-geo-alt"></i> Станция</span><span class="ip-modal__row-value">${Cerber.escapeHtml(data.station_name)}</span></div>`;
            }
            // Производитель
            if (data.vendor) {
                html += `<div class="ip-modal__row"><span class="ip-modal__row-label"><i class="bi bi-building"></i> Производитель</span><span class="ip-modal__row-value">${Cerber.escapeHtml(data.vendor)}</span></div>`;
            }
            // Живой
            const aliveIcon = data.is_alive ? '<i class="bi bi-check-circle-fill" style="color:#34d399;"></i>' : '<i class="bi bi-x-circle-fill" style="color:#f87171;"></i>';
            const aliveText = data.is_alive ? 'да' : 'нет';
            html += `<div class="ip-modal__row"><span class="ip-modal__row-label"><i class="bi bi-heart-pulse"></i> Живой</span><span class="ip-modal__row-value">${aliveIcon} ${aliveText}</span></div>`;
            // На мониторинге
            const monIcon = data.is_monitored ? '<i class="bi bi-eye-fill" style="color:#34d399;"></i>' : '<i class="bi bi-eye-slash-fill" style="color:#f87171;"></i>';
            const monText = data.is_monitored ? 'да' : 'нет';
            html += `<div class="ip-modal__row"><span class="ip-modal__row-label"><i class="bi bi-activity"></i> На мониторинге</span><span class="ip-modal__row-value">${monIcon} ${monText}</span></div>`;
            // Reserved
            const resStatusIcon = data.is_reserved ? '<i class="bi bi-check-circle-fill" style="color:#fbbf24;"></i>' : '<i class="bi bi-dash-circle" style="color:#9ca3af;"></i>';
            const resStatusText = data.is_reserved ? 'да' : 'нет';
            html += `<div class="ip-modal__row"><span class="ip-modal__row-label"><i class="bi bi-bookmark-star"></i> Reserved</span><span class="ip-modal__row-value">${resStatusIcon} ${resStatusText}</span></div>`;
            // RTT
            if (data.last_rtt_ms != null) {
                html += `<div class="ip-modal__row"><span class="ip-modal__row-label"><i class="bi bi-speedometer2"></i> RTT</span><span class="ip-modal__row-value">${Cerber.escapeHtml(data.last_rtt_ms)} ms</span></div>`;
            }

            // Если hostname пустой — поле для ручного ввода
            if (!data.hostname) {
                html += `<div style="margin-top:12px; padding-top:12px; border-top:1px solid rgba(255,255,255,0.06);">
                    <label style="color:#9ca3af; font-size:12px; display:block; margin-bottom:4px;">Указать имя вручную:</label>
                    <div style="display:flex; gap:8px;">
                        <input type="text" id="manual-hostname" placeholder="Например, SW-Core" style="flex:1; background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.1); color:#fff; border-radius:6px; padding:6px 10px; font-size:13px; outline:none;">
                        <button id="save-hostname" style="background:#ff780a; color:#fff; border:none; border-radius:6px; padding:6px 12px; font-size:13px; cursor:pointer;">Сохранить</button>
                    </div>
                </div>`;
            }

            // Станция (выпадающий список)
            html += `<div style="margin-top:12px; padding-top:12px; border-top:1px solid rgba(255,255,255,0.06);">
                <label style="color:#9ca3af; font-size:12px; display:block; margin-bottom:4px;">Прикрепить к станции:</label>
                <div style="display:flex; gap:8px;">
                    <select id="station-select" style="flex:1; background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.1); color:#fff; border-radius:6px; padding:6px 10px; font-size:13px; outline:none;">
                        <option value="">— не прикреплён —</option>
                    </select>
                    <button id="save-station" style="background:#ff780a; color:#fff; border:none; border-radius:6px; padding:6px 12px; font-size:13px; cursor:pointer;">OK</button>
                </div>
            </div>`;

            html += `<div class="ip-modal__actions" style="display:flex; gap:8px; margin-top:16px;">`;
            html += `<button class="btn btn-ping" data-id="${Cerber.escapeHtml(data.id)}" style="background:#3b82f6;color:#fff;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;">Пинговать</button>`;
            const monitorText = data.is_monitored ? 'Снять с мониторинга' : 'На мониторинг';
            const monitorColor = data.is_monitored ? '#ef4444' : '#10b981';
            html += `<button class="btn btn-monitor" data-id="${Cerber.escapeHtml(data.id)}" style="background:${monitorColor};color:#fff;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;">${monitorText}</button>`;
            const resText = data.is_reserved ? 'Снять reserved' : 'Reserved';
            html += `<button class="btn btn-reserve" data-id="${Cerber.escapeHtml(data.id)}" style="background:#f59e0b;color:#fff;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;">${resText}</button>`;
            html += `</div></div>`;

            body.innerHTML = html;

            // Обработчики кнопок
            body.querySelector('.btn-ping')?.addEventListener('click', async (ev) => {
                ev.target.disabled = true;
                ev.target.textContent = 'Пингую...';
                await fetch(`/api/v1/ip-addresses/${id}/ping-now/`, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': Cerber.csrfToken() },
                    credentials: 'same-origin'
                });
                await refreshModal();
            });

            body.querySelector('.btn-monitor')?.addEventListener('click', async (ev) => {
                ev.target.disabled = true;
                await fetch(`/api/v1/ip-addresses/${id}/toggle-monitor/`, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': Cerber.csrfToken() },
                    credentials: 'same-origin'
                });
                await refreshModal();
            });

            body.querySelector('.btn-reserve')?.addEventListener('click', async (ev) => {
                ev.target.disabled = true;
                await fetch(`/api/v1/ip-addresses/${id}/set-reserved/`, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': Cerber.csrfToken() },
                    credentials: 'same-origin'
                });
                await refreshModal();
            });

        } catch (err) {
            body.innerHTML = `<div style="padding: 10px; color: #f87171;">Ошибка: ${Cerber.escapeHtml(err.message)}</div>`;
        }
    }

    await refreshModal();
}, true);

// Закрытие модалки
document.addEventListener('click', function(e) {
    const closeBtn = e.target.closest('.modal-close');
    const backdrop = e.target.closest('#ip-modal');
    if (closeBtn || (backdrop && e.target === backdrop)) {
        const modal = document.getElementById('ip-modal');
        if (modal) { modal.hidden = true; modal.style.display = 'none'; }
    }
});

// ============================================================

// ============================================================
// TCPDUMP — делегированные обработчики (работают с AJAX)
// ============================================================
document.addEventListener('click', function(e) {
    // Открытие/закрытие панели
    if (e.target.closest('#subnet-tcpdump-btn')) {
        e.preventDefault();
        const panel = document.getElementById('tcpdump-panel');
        if (panel) {
            panel.classList.toggle('is-open');
        } else {
            console.warn('[Tcpdump] Панель #tcpdump-panel не найдена');
        }
        return;
    }

    // Закрытие панели
    if (e.target.closest('#tcpdump-close')) {
        const panel = document.getElementById('tcpdump-panel');
        if (panel) panel.classList.remove('is-open');
        return;
    }

    // Очистка лога
    if (e.target.closest('#tcpdump-clear')) {
        const log = document.getElementById('tcpdump-log');
        if (log) log.textContent = '';
        return;
    }

    // Запуск tcpdump
    if (e.target.closest('#tcpdump-start')) {
        const btn = e.target.closest('#tcpdump-start');
        const log = document.getElementById('tcpdump-log');
        const bpf = document.getElementById('tcpdump-bpf')?.value.trim() || 'icmp';
        const iface = document.getElementById('tcpdump-iface')?.value || 'any';
        const duration = parseInt(document.getElementById('tcpdump-duration')?.value) || 10;
        const packets = parseInt(document.getElementById('tcpdump-packets')?.value) || 50;

        btn.disabled = true;
        btn.textContent = '⌛ Запуск...';
        if (log) log.textContent += `[${new Date().toLocaleTimeString()}] Запуск: bpf="${bpf}", iface=${iface}, duration=${duration}s\n`;

        fetch('/api/v1/subnets/tcpdump/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': Cerber.csrfToken()
            },
            credentials: 'same-origin',
            body: JSON.stringify({ bpf, iface, duration, packets })
        })
        .then(r => r.json())
        .then(data => {
            if (log) {
                if (data.ok === false || data.error) {
                    log.textContent += `❌ Ошибка: ${data.error || 'неизвестно'}\n`;
                } else {
                    log.textContent += `✅ Готово. Пакетов: ${data.count || data.packets || 0}\n`;
                    if (data.output) log.textContent += data.output + '\n';
                }
                log.scrollTop = log.scrollHeight;
            }
        })
        .catch(err => {
            if (log) log.textContent += `❌ Ошибка сети: ${err.message}\n`;
        })
        .finally(() => {
            btn.disabled = false;
            btn.textContent = '▶ Запустить';
        });
        return;
    }

    // Скачать pcap
    if (e.target.closest('#tcpdump-pcap')) {
        const btn = e.target.closest('#tcpdump-pcap');
        const log = document.getElementById('tcpdump-log');
        const bpf = document.getElementById('tcpdump-bpf')?.value.trim() || '';
        const iface = document.getElementById('tcpdump-iface')?.value || 'any';
        const duration = parseInt(document.getElementById('tcpdump-duration')?.value) || 10;
        const packets = parseInt(document.getElementById('tcpdump-packets')?.value) || 50;

        btn.disabled = true;
        btn.textContent = '⌛...';
        if (log) log.textContent += `[${new Date().toLocaleTimeString()}] 💾 Запрос pcap...\n`;

        fetch('/api/v1/subnets/tcpdump/pcap/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': Cerber.csrfToken()
            },
            credentials: 'same-origin',
            body: JSON.stringify({ bpf, iface, duration, packets })
        })
        .then(r => r.json())
        .then(data => {
            if (data.download_url) {
                if (log) log.textContent += `✅ Файл готов: ${data.download_url}\n`;
                window.open(data.download_url, '_blank');
            } else {
                if (log) log.textContent += `❌ Ошибка: ${data.error || 'нет download_url'}\n`;
            }
        })
        .catch(err => {
            if (log) log.textContent += `❌ Ошибка: ${err.message}\n`;
        })
        .finally(() => {
            btn.disabled = false;
            btn.textContent = '💾 Скачать .pcap';
        });
        return;
    }
});

// ============================================================

// ============================================================
// Загрузка интерфейсов для tcpdump (правильный URL: ifaces/)
// ============================================================
function loadTcpdumpIfaces() {
    const select = document.getElementById('tcpdump-iface');
    if (!select) return;

    // Используем /ifaces/ вместо /interfaces/
    fetch('/api/v1/subnets/tcpdump/ifaces/', {
        credentials: 'same-origin'
    })
    .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
    })
    .then(data => {
        
        // API может вернуть массив строк, массив объектов или {ifaces: [...]}
        let ifaces = [];
        if (Array.isArray(data)) {
            ifaces = data;
        } else if (data.ifaces && Array.isArray(data.ifaces)) {
            ifaces = data.ifaces;
        } else if (data.interfaces && Array.isArray(data.interfaces)) {
            ifaces = data.interfaces;
        } else if (typeof data === 'object') {
            // Если пришёл объект вида {"eth0": "192.168.1.1", ...}
            ifaces = Object.entries(data).map(([name, ip]) => ({ name, ip }));
        }

        if (!ifaces.length) {
            console.warn('[Tcpdump] API вернул пустой список интерфейсов');
            return;
        }

        select.innerHTML = '';
        ifaces.forEach(item => {
            const name = typeof item === 'string' ? item : (item.name || item.iface || '?');
            const ip = typeof item === 'string' ? '' : (item.ip || item.address || '');
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = ip ? `${name} (${ip})` : name;
            select.appendChild(opt);
        });

        // Если есть eth0 — выбираем
        if (Array.from(select.options).some(o => o.value === 'eth0')) {
            select.value = 'eth0';
        }
    })
    .catch(err => {
        console.error('[Tcpdump] Ошибка загрузки интерфейсов:', err);
    });
}

// Вызываем при клике на кнопку "Захват трафика" (когда панель появляется)
document.addEventListener('click', function(e) {
    if (e.target.closest('#subnet-tcpdump-btn')) {
        // Небольшая задержка, чтобы панель успела вставиться в DOM
        setTimeout(loadTcpdumpIfaces, 200);
    }
});

// И при загрузке страницы (на случай, если панель уже открыта)
document.addEventListener('DOMContentLoaded', function() {
    if (document.getElementById('tcpdump-panel')) {
        setTimeout(loadTcpdumpIfaces, 300);
    }
});

// ============================================================
// TCPDUMP — Live (polling вместо SSE)
// ============================================================
let livePollTimer = null;
let livePollActive = false;

document.addEventListener('click', function(e) {
    const liveBtn = e.target.closest('#tcpdump-live');
    if (!liveBtn) return;

    const log = document.getElementById('tcpdump-log');
    if (!log) return;

    if (livePollActive) {
        // Останавливаем
        livePollActive = false;
        if (livePollTimer) {
            clearTimeout(livePollTimer);
            livePollTimer = null;
        }
        liveBtn.textContent = '🔴 Live';
        liveBtn.style.background = '';
        log.textContent += `[${new Date().toLocaleTimeString()}] ⏹ Live остановлен\n`;
        return;
    }

    // Запускаем
    livePollActive = true;
    liveBtn.textContent = '⏹ Остановить';
    liveBtn.style.background = '#ef4444';
    liveBtn.style.color = '#fff';

    const bpf = document.getElementById('tcpdump-bpf')?.value.trim() || 'ip';
    const iface = document.getElementById('tcpdump-iface')?.value || 'any';
    const duration = parseInt(document.getElementById('tcpdump-duration')?.value) || 10;
    const packets = parseInt(document.getElementById('tcpdump-packets')?.value) || 50;

    log.textContent += `[${new Date().toLocaleTimeString()}] 🔴 Live (polling): bpf="${bpf}", iface=${iface}, duration=${duration}s\n`;

    async function poll() {
        if (!livePollActive) return;
        try {
            const resp = await fetch('/api/v1/subnets/tcpdump/poll/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': Cerber.csrfToken()
                },
                credentials: 'same-origin',
                body: JSON.stringify({
                    bpf: bpf,
                    iface: iface,
                    duration: 2,          // каждый poll — 2 секунды
                    packets: 30
                })
            });
            const data = await resp.json();

            if (data.ok === false || data.error) {
                log.textContent += `❌ ${data.error || 'ошибка'}\n`;
            } else if (data.packets && data.packets.length) {
                renderTcpdumpPackets(data.packets, log);
            } else {
                log.textContent += `[${new Date().toLocaleTimeString()}] (пакетов нет)\n`;
            }
            log.scrollTop = log.scrollHeight;
        } catch (err) {
            log.textContent += `❌ Ошибка сети: ${err.message}\n`;
        }

        if (livePollActive) {
            livePollTimer = setTimeout(poll, 500); // пауза между опросами
        }
    }

    poll();
});

// ============================================================
// Красивый вывод пакетов tcpdump
// ============================================================
function renderTcpdumpPackets(packets, logEl) {
    if (!logEl || !packets || !packets.length) return;

    function detectCategory(pkt) {
        const info = (pkt.info || '').toLowerCase();
        const macDst = (pkt.mac_dst || '').toLowerCase();

        if (info.includes('who-has') || info.includes('is-at') || info.includes('arp')) return 'ARP';
        if (macDst === 'ff:ff:ff:ff:ff:ff') return 'Broadcast';
        if (macDst.startsWith('01:00:5e') || macDst.startsWith('33:33')) return 'Multicast';
        if (info.includes('icmp')) return 'ICMP';
        if (info.includes('udp')) return 'UDP';
        if (info.includes('tcp')) return 'TCP';
        if (pkt.src && pkt.src.includes('.')) return 'IP';
        if (pkt.dst && pkt.dst.includes('.')) return 'IP';
        return (pkt.category || 'OTHER').toUpperCase();
    }

    const logLines = [];
    packets.forEach(pkt => {
        let time = '—';
        if (pkt.timestamp) {
            const t = pkt.timestamp.split(' ')[1] || pkt.timestamp;
            time = t.split('.')[0];
        }

        const cat = detectCategory(pkt);
        const src = pkt.mac_src || pkt.src || '?';
        const dst = pkt.mac_dst || pkt.dst || '?';
        const info = pkt.info || '';

        // Делаем MAC кликабельными (только если это MAC, а не IP)
        function macSpan(mac) {
            const m = Cerber.escapeHtml(mac);
            if (!mac || !String(mac).includes(':')) return m;
            return `<span class="mac-link" data-mac="${m}" style="color:#60a5fa; cursor:pointer; text-decoration:underline;">${m}</span>`;
        }

        const srcHtml = macSpan(src);
        const dstHtml = macSpan(dst);

        logLines.push(`[${Cerber.escapeHtml(time)}] ${Cerber.escapeHtml(cat)} | ${srcHtml} → ${dstHtml} | ${Cerber.escapeHtml(info)}\n`);
    });
    logEl.insertAdjacentHTML('beforeend', logLines.join(''));
    logEl.scrollTop = logEl.scrollHeight;
}



// Пресеты BPF
document.addEventListener('click', function(e) {
    const preset = e.target.closest('.bpf-preset');
    if (!preset) return;
    const bpfInput = document.getElementById('tcpdump-bpf');
    if (bpfInput) {
        bpfInput.value = preset.dataset.bpf;
        document.querySelectorAll('.bpf-preset').forEach(b => b.classList.remove('is-active'));
        preset.classList.add('is-active');
    }
});

// ============================================================
// Пресеты BPF (создаются динамически при открытии панели)
// ============================================================
document.addEventListener('click', function(e) {
    if (e.target.closest('#subnet-tcpdump-btn')) {
        // Ждём, пока панель откроется
        setTimeout(() => {
            const bpfInput = document.getElementById('tcpdump-bpf');
            if (!bpfInput) return;
            
            // Проверяем, есть ли уже контейнер с пресетами
            if (bpfInput.parentElement.querySelector('.tcpdump-presets')) return;

            // Создаём контейнер
            const presets = document.createElement('div');
            presets.className = 'tcpdump-presets';
            presets.style.cssText = 'display:flex; flex-wrap:wrap; gap:6px; margin-top:8px;';

            const list = [
                { label: 'IP', bpf: 'ip' },
                { label: 'ARP', bpf: 'arp' },
                { label: 'ICMP', bpf: 'icmp' },
                { label: 'TCP', bpf: 'tcp' },
                { label: 'UDP', bpf: 'udp' },
                { label: 'Broadcast', bpf: 'ether broadcast and not arp' },
                { label: 'Multicast', bpf: 'ether multicast' },
                { label: 'BOOTP/DHCP', bpf: 'udp port 67 or udp port 68' },
                { label: 'ICMP+ARP', bpf: 'icmp or arp' },
                { label: 'Всё', bpf: '' },
            ];

            list.forEach(item => {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'bpf-preset';
                btn.dataset.bpf = item.bpf;
                btn.textContent = item.label;
                btn.style.cssText = 'background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1); color:#d1d5db; padding:4px 10px; border-radius:6px; font-size:12px; cursor:pointer;';
                
                btn.addEventListener('mouseenter', () => {
                    btn.style.background = 'rgba(255,120,10,0.2)';
                    btn.style.borderColor = '#ff780a';
                    btn.style.color = '#fff';

                    // Если Live активен — перезапускаем с новым BPF
                    if (typeof livePollActive !== 'undefined' && livePollActive) {
                        const liveBtnEl = document.getElementById('tcpdump-live');
                        if (liveBtnEl) {
                            liveBtnEl.click(); // Останавливаем
                            setTimeout(() => {
                                const newLiveBtn = document.getElementById('tcpdump-live');
                                if (newLiveBtn) newLiveBtn.click(); // Запускаем
                            }, 200);
                        }
                    }
                });
                btn.addEventListener('mouseleave', () => {
                    if (!btn.classList.contains('is-active')) {
                        btn.style.background = 'rgba(255,255,255,0.05)';
                        btn.style.borderColor = 'rgba(255,255,255,0.1)';
                        btn.style.color = '#d1d5db';
                    }
                });
                
                btn.addEventListener('click', () => {
                    bpfInput.value = item.bpf;
                    presets.querySelectorAll('.bpf-preset').forEach(b => {
                        b.classList.remove('is-active');
                        b.style.background = 'rgba(255,255,255,0.05)';
                        b.style.borderColor = 'rgba(255,255,255,0.1)';
                        b.style.color = '#d1d5db';
                    });
                    btn.classList.add('is-active');
                    btn.style.background = '#ff780a';
                    btn.style.borderColor = '#ff780a';
                    btn.style.color = '#fff';
                });

                presets.appendChild(btn);
            });

            // Вставляем после input
            bpfInput.parentElement.appendChild(presets);
        }, 150);
    }
});

// ============================================================
// Красивая сетка для панели tcpdump
// ============================================================
document.addEventListener('click', function(e) {
    if (e.target.closest('#subnet-tcpdump-btn')) {
        setTimeout(() => {
            const form = document.querySelector('.tcpdump-form');
            if (!form || form.dataset.gridFixed) return;
            form.dataset.gridFixed = '1';

            // Находим поля
            const bpfWrap = form.children[0]; // BPF
            const ifaceWrap = form.children[1]; // Интерфейс
            const durWrap = form.children[2]; // Длительность
            const pktWrap = form.children[3]; // Пакеты

            // Создаём контейнеры
            const bpfContainer = document.createElement('div');
            bpfContainer.style.gridColumn = '1 / -1'; // на всю ширину
            bpfContainer.style.marginBottom = '12px';
            
            const rowContainer = document.createElement('div');
            rowContainer.style.display = 'grid';
            rowContainer.style.gridTemplateColumns = 'repeat(3, 1fr)';
            rowContainer.style.gap = '12px';

            // Перемещаем поля
            bpfContainer.appendChild(bpfWrap);
            rowContainer.appendChild(ifaceWrap);
            rowContainer.appendChild(durWrap);
            rowContainer.appendChild(pktWrap);

            // Очищаем форму и вставляем контейнеры
            form.innerHTML = '';
            form.appendChild(bpfContainer);
            form.appendChild(rowContainer);

            // Делаем форму блочной, а не grid
            form.style.display = 'block';

            // Настраиваем пресеты (они находятся внутри bpfWrap)
            const presets = bpfWrap.querySelector('.tcpdump-presets');
            if (presets) {
                presets.style.display = 'grid';
                presets.style.gridTemplateColumns = 'repeat(auto-fill, minmax(80px, 1fr))';
                presets.style.gap = '8px';
                presets.style.marginTop = '12px';
            }

        }, 200);
    }
});

// Остановка Live при закрытии панели
document.addEventListener('click', function(e) {
    if (e.target.closest('#tcpdump-close')) {
            const liveBtn = document.getElementById('tcpdump-live');
            if (liveBtn) {
                liveBtn.textContent = '🔴 Live';
                liveBtn.style.background = '';
                liveBtn.style.color = '';
            }
        }
});

// ============================================================
// Автоопределение интерфейса для tcpdump по CIDR подсети
// ============================================================
function autoSelectTcpdumpIface() {
    const page = document.querySelector('.subnet-detail-page');
    const select = document.getElementById('tcpdump-iface');
    if (!page || !select) return;

    const cidr = page.dataset.cidr;
    if (!cidr) {
        console.warn('[Tcpdump] У .subnet-detail-page нет data-cidr');
        return;
    }

    // Если уже выбрали вручную — не перебиваем
    if (select.dataset.autoSelected === '1') return;

    fetch(`/api/v1/subnets/tcpdump/iface-for/?cidr=${encodeURIComponent(cidr)}`, {
        credentials: 'same-origin'
    })
    .then(r => r.json())
    .then(data => {
        if (!data.ok || !data.iface) {
            console.warn('[Tcpdump] Автоопределение не удалось:', data.error);
            return;
        }

        // Проверяем, есть ли такой интерфейс в select
        const exists = Array.from(select.options).some(o => o.value === data.iface);
        if (exists) {
            select.value = data.iface;
            select.dataset.autoSelected = '1';
            console.log(`[Tcpdump] Автовыбор интерфейса: ${data.iface} (${data.ip}) для ${cidr}`);
        } else {
            // Если в списке нет — добавляем
            const opt = document.createElement('option');
            opt.value = data.iface;
            opt.textContent = `${data.iface} (${data.ip})`;
            select.appendChild(opt);
            select.value = data.iface;
            select.dataset.autoSelected = '1';
        }
    })
    .catch(err => {
        console.warn('[Tcpdump] Ошибка автоопределения:', err.message);
    });
}

// Вызываем при открытии панели tcpdump
document.addEventListener('click', function(e) {
    if (e.target.closest('#subnet-tcpdump-btn')) {
        setTimeout(() => {
            // Сначала грузим список всех интерфейсов (чтобы select был заполнен)
            // (loadTcpdumpIfaces уже делает это, но на всякий случай)
            if (typeof loadTcpdumpIfaces === 'function') {
                loadTcpdumpIfaces();
            }
            // Затем автоопределение
            setTimeout(autoSelectTcpdumpIface, 500);
        }, 200);
    }
});

// ============================================================
// Клик по MAC в логе tcpdump → поиск IP
// ============================================================
document.addEventListener('click', async function(e) {
    const macEl = e.target.closest('.mac-link');
    if (!macEl) return;

    const mac = macEl.dataset.mac;
    if (!mac) return;


    try {
        // Берём интерфейс из выпадающего списка панели tcpdump
        const ifaceSelect = document.getElementById('tcpdump-iface');
        const iface = ifaceSelect ? ifaceSelect.value : 'enp4s0.50';
        const resp = await fetch(`/api/v1/subnets/resolve-mac/?mac=${encodeURIComponent(mac)}&iface=${encodeURIComponent(iface)}`, {
            credentials: 'same-origin'
        });
        const data = await resp.json();

        if (data.ok) {
            const ipData = data;
            // Открываем модалку с деталями IP
            const modal = document.getElementById('ip-modal');
            const title = document.getElementById('modal-title');
            const body = document.getElementById('modal-body');
            if (modal && body) {
                if (title) title.innerText = ipData.ip ? `IP: ${ipData.ip}` : `MAC: ${mac}`;
                let html = `<div style="padding:8px 0;">`;
                // MAC
                html += `<div class="ip-modal__row"><span class="ip-modal__row-label"><i class="bi bi-hdd-network"></i> MAC</span><span class="ip-modal__row-value">${mac}</span></div>`;
                // Hostname
                if (ipData.hostname) {
                    html += `<div class="ip-modal__row"><span class="ip-modal__row-label"><i class="bi bi-pc-display"></i> Hostname</span><span class="ip-modal__row-value">${Cerber.escapeHtml(ipData.hostname)}</span></div>`;
                }
                // IP
                if (ipData.ip) {
                    html += `<div class="ip-modal__row"><span class="ip-modal__row-label"><i class="bi bi-globe"></i> IP</span><span class="ip-modal__row-value">${Cerber.escapeHtml(ipData.ip)}</span></div>`;
                } else if (ipData.source === 'dhcp') {
                    html += `<div class="ip-modal__row"><span class="ip-modal__row-label"><i class="bi bi-exclamation-triangle" style="color:#fbbf24;"></i> IP</span><span class="ip-modal__row-value" style="color:#fbbf24;">не получен (DHCP)</span></div>`;
                }
                // Живой
                if (ipData.is_alive !== undefined) {
                    const aliveIcon = ipData.is_alive ? '<i class="bi bi-check-circle-fill" style="color:#34d399;"></i>' : '<i class="bi bi-x-circle-fill" style="color:#f87171;"></i>';
                    const aliveText = ipData.is_alive ? 'да' : 'нет';
                    html += `<div class="ip-modal__row"><span class="ip-modal__row-label"><i class="bi bi-heart-pulse"></i> Живой</span><span class="ip-modal__row-value">${aliveIcon} ${aliveText}</span></div>`;
                }
                // На мониторинге
                if (ipData.is_monitored !== undefined) {
                    const monIcon = ipData.is_monitored ? '<i class="bi bi-eye-fill" style="color:#34d399;"></i>' : '<i class="bi bi-eye-slash-fill" style="color:#f87171;"></i>';
                    const monText = ipData.is_monitored ? 'да' : 'нет';
                    html += `<div class="ip-modal__row"><span class="ip-modal__row-label"><i class="bi bi-activity"></i> На мониторинге</span><span class="ip-modal__row-value">${monIcon} ${monText}</span></div>`;
                }
                // RTT
                if (ipData.last_rtt_ms != null) {
                    html += `<div class="ip-modal__row"><span class="ip-modal__row-label"><i class="bi bi-speedometer2"></i> RTT</span><span class="ip-modal__row-value">${Cerber.escapeHtml(ipData.last_rtt_ms)} ms</span></div>`;
                }
                // Note
                if (ipData.note) {
                    html += `<div style="margin-top:12px; padding-top:12px; border-top:1px solid rgba(255,255,255,0.06); color:#9ca3af; font-size:12px;"><i class="bi bi-info-circle"></i> ${Cerber.escapeHtml(ipData.note)}</div>`;
                }
                html += `</div>`;
                body.innerHTML = html;
                modal.hidden = false;
                modal.style.display = 'flex';
            }
        } else {
            // Показываем модалку с сообщением, что MAC не найден
            const modal = document.getElementById('ip-modal');
            const title = document.getElementById('modal-title');
            const body = document.getElementById('modal-body');
            if (modal && body) {
                if (title) title.innerText = 'MAC: ' + mac;
                body.innerHTML = `
                    <div style="padding:10px;">
                        <p><strong>MAC:</strong> ${Cerber.escapeHtml(mac)}</p>
                        <div class="ip-modal__row"><span class="ip-modal__row-label"><i class="bi bi-x-circle-fill" style="color:#f87171;"></i> MAC</span><span class="ip-modal__row-value">${Cerber.escapeHtml(mac)}</span></div>
                        <div class="ip-modal__row"><span class="ip-modal__row-label"><i class="bi bi-exclamation-triangle-fill" style="color:#fbbf24;"></i> Статус</span><span class="ip-modal__row-value" style="color:#fbbf24;">не найден</span></div>
                        <div class="ip-modal__row"><span class="ip-modal__row-label"><i class="bi bi-wifi-off" style="color:#9ca3af;"></i> IP</span><span class="ip-modal__row-value" style="color:#9ca3af;">не получен (DHCP)</span></div>
                        <div style="margin-top:12px; padding-top:12px; border-top:1px solid rgba(255,255,255,0.06); color:#9ca3af; font-size:12px;"><i class="bi bi-info-circle"></i> Устройство активно (шлёт DHCP-запросы), но ещё не получило IP. IP появится в ARP после того, как DHCP-сервер выдаст адрес.</div>
                    </div>
                `;
                modal.hidden = false;
                modal.style.display = 'flex';
            } else {
                alert(`MAC ${mac} не найден в базе`);
            }
        }
    } catch (err) {
        console.error('[Tcpdump] Ошибка поиска MAC:', err);
        alert('Ошибка поиска: ' + err.message);
    }
});

// Обработчик кнопки "Сохранить" для ручного hostname
document.addEventListener('click', async function(e) {
    if (e.target.id === 'save-hostname') {
        const input = document.getElementById('manual-hostname');
        if (!input) return;
        const hostname = input.value.trim();
        if (!hostname) return;

        // Получаем IP из модалки (заголовок)
        const title = document.getElementById('modal-title');
        const ip = title ? title.innerText.replace('IP: ', '').trim() : '';
        if (!ip) return;

        try {
            // Находим id IP по адресу (через API)
            const resp = await fetch(`/api/v1/ip-addresses/?ip=${encodeURIComponent(ip)}`, { credentials: 'same-origin' });
            const data = await resp.json();
            if (data.results && data.results.length > 0) {
                const id = data.results[0].id;
                const r = await fetch(`/api/v1/ip-addresses/${id}/set-hostname/`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': Cerber.csrfToken()
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify({ hostname })
                });
                if (r.ok) {
                    alert('Имя сохранено!');
                    // Перерисовываем модалку (кликаем по ячейке заново)
                    const cell = document.querySelector(`.ip-cell[data-ip="${ip}"]`);
                    if (cell) cell.click();
                } else {
                    alert('Ошибка сохранения');
                }
            }
        } catch (err) {
            alert('Ошибка: ' + err.message);
        }
    }
});

// ============================================================
// Загрузка станций и прикрепление IP к станции
// ============================================================
document.addEventListener('click', async function(e) {
    // Кнопка "Сохранить" для станции
    if (e.target.id === 'save-station') {
        const select = document.getElementById('station-select');
        if (!select) return;
        const stationId = select.value || null;
        const title = document.getElementById('modal-title');
        const ip = title ? title.innerText.replace('IP: ', '').trim() : '';
        if (!ip) return;

        try {
            // Находим id IP по адресу
            const resp = await fetch(`/api/v1/ip-addresses/?ip=${encodeURIComponent(ip)}`, { credentials: 'same-origin' });
            const data = await resp.json();
            if (data.results && data.results.length > 0) {
                const id = data.results[0].id;
                const r = await fetch(`/api/v1/ip-addresses/${id}/update-station/`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': Cerber.csrfToken()
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify({ station: stationId ? parseInt(stationId) : null })
                });
                if (r.ok) {
                    alert('Станция обновлена!');
                    const cell = document.querySelector(`.ip-cell[data-ip="${ip}"]`);
                    if (cell) cell.click();
                } else {
                    alert('Ошибка сохранения');
                }
            }
        } catch (err) {
            alert('Ошибка: ' + err.message);
        }
    }
});


// ============================================================
// Автоматическая загрузка станций при появлении #station-select
// ============================================================
const stationObserver = new MutationObserver(function(mutations) {
    const select = document.getElementById('station-select');
    if (select && !select.dataset.loaded) {
        select.dataset.loaded = '1';
        fetch('/api/v1/stations/?light=1', { credentials: 'same-origin' })
            .then(r => r.json())
            .then(d => {
                const stations = d.results || d;
                if (Array.isArray(stations)) {
                    stations.forEach(s => {
                        const opt = document.createElement('option');
                        opt.value = s.id;
                        opt.textContent = s.name;
                        select.appendChild(opt);
                    });
                }
            })
            .catch(e => console.warn('[Glass] stations load failed:', e));
    }
});

// Наблюдаем за изменениями в body (модалка появляется динамически)
stationObserver.observe(document.body, { childList: true, subtree: true });

// ============================================================
// Массовое прикрепление активных IP к станции
// ============================================================
document.addEventListener('click', async function(e) {
    const btn = e.target.closest('#bulk-attach-station-btn');
    if (!btn) return;

    // Получаем subnet_id из .subnet-detail-page
    const page = document.querySelector('.subnet-detail-page');
    const subnetId = page ? page.dataset.subnetId : null;
    if (!subnetId) {
        alert('Не удалось определить ID подсети');
        return;
    }

    // Создаём модалку (если нет)
    let modal = document.getElementById('bulk-station-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'bulk-station-modal';
        modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center;';
        modal.innerHTML = `
            <div style="background:rgba(45,50,56,0.95);backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,0.15);border-radius:16px;padding:24px;width:400px;max-width:90vw;color:#fff;">
                <h3 style="margin-top:0;color:#fff;">Прикрепить активные IP к станции</h3>
                <p style="color:#9ca3af;font-size:13px;">Все активные IP этой подсети будут привязаны к выбранной станции.</p>
                <div style="margin:16px 0;">
                    <label style="display:block;color:#9ca3af;font-size:13px;margin-bottom:4px;">Станция:</label>
                    <select id="bulk-station-select" style="width:100%;background:rgba(0,0,0,0.3);border:1px solid rgba(255,255,255,0.1);color:#fff;border-radius:8px;padding:8px 12px;font-size:14px;outline:none;">
                        <option value="">— выберите станцию —</option>
                    </select>
                </div>
                <div style="display:flex;gap:8px;justify-content:flex-end;">
                    <button id="bulk-station-cancel" style="background:transparent;border:1px solid rgba(255,255,255,0.15);color:#fff;padding:8px 16px;border-radius:8px;cursor:pointer;">Отмена</button>
                    <button id="bulk-station-ok" style="background:#ff780a;border:none;color:#fff;padding:8px 16px;border-radius:8px;cursor:pointer;">Прикрепить</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    }
    modal.style.display = 'flex';

    // Загружаем станции
    const select = document.getElementById('bulk-station-select');
    if (select && !select.dataset.loaded) {
        select.dataset.loaded = '1';
        try {
            const r = await fetch('/api/v1/stations/?light=1', { credentials: 'same-origin' });
            const d = await r.json();
            const stations = d.results || d;
            stations.forEach(s => {
                const opt = document.createElement('option');
                opt.value = s.id;
                opt.textContent = s.name;
                select.appendChild(opt);
            });
        } catch (err) {
            console.warn('Ошибка загрузки станций:', err);
        }
    }

    // Отмена
    document.getElementById('bulk-station-cancel').onclick = () => {
        modal.style.display = 'none';
    };

    // Прикрепить
    document.getElementById('bulk-station-ok').onclick = async () => {
        const stationId = select.value;
        if (!stationId) {
            alert('Выберите станцию');
            return;
        }
        try {
            const resp = await fetch('/api/v1/subnets/bulk-attach-station/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': Cerber.csrfToken()
                },
                credentials: 'same-origin',
                body: JSON.stringify({ subnet_id: parseInt(subnetId), station_id: parseInt(stationId) })
            });
            const data = await resp.json();
            if (data.ok) {
                alert(`Прикреплено ${data.updated} IP к станции "${data.station}"`);
                modal.style.display = 'none';
                // Перезагружаем сетку (клик по подсети заново)
                const treeItem = document.querySelector(`.tree-subnet[data-id="${subnetId}"]`);
                if (treeItem) treeItem.click();
            } else {
                alert('Ошибка: ' + (data.error || 'неизвестно'));
            }
        } catch (err) {
            alert('Ошибка: ' + err.message);
        }
    };
});

// ── Кастомные всплывающие окна (Cerber-style) ──────────────────────────
window.CerberAlert = function(message, title = 'Уведомление') {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.style.cssText = `
            position: fixed; top:0; left:0; right:0; bottom:0;
            background: rgba(0,0,0,0.3); backdrop-filter: blur(8px);
            display: flex; align-items: center; justify-content: center; z-index: 99999;
        `;
        const card = document.createElement('div');
        card.style.cssText = `
            background: rgba(255,255,255,0.4); backdrop-filter: blur(25px) saturate(180%);
            border-radius: 20px; padding: 32px; max-width: 400px; width: 90%;
            box-shadow: 0 20px 40px rgba(0,0,0,0.15); text-align: center;
            font-family: inherit; color: #1f2937;
            border: 1px solid rgba(255,255,255,0.7);
        `;
        card.innerHTML = `
            <h3 style="margin-top:0; font-size: 1.3rem; font-weight: 700;">${Cerber.escapeHtml(title)}</h3>
            <p style="color: #374151; margin-bottom: 24px; white-space: pre-wrap; font-size: 15px;">${Cerber.escapeHtml(message)}</p>
            <button class="btn-ok" style="width:100%; padding: 14px; border-radius: 10px; border: none; background: #ff7300; color: #fff; font-weight: 600; font-size: 15px; cursor: pointer; transition: 0.2s;">ОК</button>
        `;
        overlay.appendChild(card);
        document.body.appendChild(overlay);

        const btn = card.querySelector('.btn-ok');
        btn.onmouseover = () => btn.style.background = '#e06b00';
        btn.onmouseout = () => btn.style.background = '#ff7300';
        btn.onclick = () => { document.body.removeChild(overlay); resolve(); };
        overlay.onclick = (e) => { if (e.target === overlay) { document.body.removeChild(overlay); resolve(); } };
    });
};

window.CerberConfirm = function(message, title = 'Подтверждение') {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.style.cssText = `
            position: fixed; top:0; left:0; right:0; bottom:0;
            background: rgba(0,0,0,0.3); backdrop-filter: blur(8px);
            display: flex; align-items: center; justify-content: center; z-index: 99999;
        `;
        const card = document.createElement('div');
        card.style.cssText = `
            background: rgba(255,255,255,0.4); backdrop-filter: blur(25px) saturate(180%);
            border-radius: 20px; padding: 32px; max-width: 400px; width: 90%;
            box-shadow: 0 20px 40px rgba(0,0,0,0.15); text-align: center;
            font-family: inherit; color: #1f2937;
            border: 1px solid rgba(255,255,255,0.7);
        `;
        card.innerHTML = `
            <h3 style="margin-top:0; font-size: 1.3rem; font-weight: 700;">${Cerber.escapeHtml(title)}</h3>
            <p style="color: #374151; margin-bottom: 24px; font-size: 15px;">${Cerber.escapeHtml(message)}</p>
            <div style="display: flex; gap: 12px; justify-content: center;">
                <button class="btn-cancel" style="flex:1; padding: 14px; border-radius: 10px; border: 1px solid rgba(0,0,0,0.1); background: rgba(255,255,255,0.6); color: #4b5563; font-weight: 600; font-size: 15px; cursor: pointer; transition: 0.2s;">Отмена</button>
                <button class="btn-ok" style="flex:1; padding: 14px; border-radius: 10px; border: none; background: #ff7300; color: #fff; font-weight: 600; font-size: 15px; cursor: pointer; transition: 0.2s;">ОК</button>
            </div>
        `;
        overlay.appendChild(card);
        document.body.appendChild(overlay);

        const btnOk = card.querySelector('.btn-ok');
        const btnCancel = card.querySelector('.btn-cancel');
        
        btnOk.onmouseover = () => btnOk.style.background = '#e06b00';
        btnOk.onmouseout = () => btnOk.style.background = '#ff7300';
        btnCancel.onmouseover = () => btnCancel.style.background = 'rgba(255,255,255,0.9)';
        btnCancel.onmouseout = () => btnCancel.style.background = 'rgba(255,255,255,0.6)';

        btnOk.onclick = () => { document.body.removeChild(overlay); resolve(true); };
        btnCancel.onclick = () => { document.body.removeChild(overlay); resolve(false); };
        overlay.onclick = (e) => { if (e.target === overlay) { document.body.removeChild(overlay); resolve(false); } };
    });
};

window.CerberPrompt = function(message, defaultValue = '', title = 'Ввод') {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.style.cssText = `
            position: fixed; top:0; left:0; right:0; bottom:0;
            background: rgba(0,0,0,0.3); backdrop-filter: blur(8px);
            display: flex; align-items: center; justify-content: center; z-index: 99999;
        `;
        const card = document.createElement('div');
        card.style.cssText = `
            background: rgba(255,255,255,0.4); backdrop-filter: blur(25px) saturate(180%);
            border-radius: 20px; padding: 32px; max-width: 400px; width: 90%;
            box-shadow: 0 20px 40px rgba(0,0,0,0.15); text-align: center;
            font-family: inherit; color: #1f2937;
            border: 1px solid rgba(255,255,255,0.7);
        `;
        card.innerHTML = `
            <h3 style="margin-top:0; font-size: 1.3rem; font-weight: 700;">${Cerber.escapeHtml(title)}</h3>
            <p style="color: #374151; margin-bottom: 15px; font-size: 15px;">${Cerber.escapeHtml(message)}</p>
            <input type="text" class="cerber-prompt-input" value="${Cerber.escapeHtml(defaultValue)}" style="width: 100%; padding: 12px; border-radius: 10px; border: 1px solid rgba(0,0,0,0.1); margin-bottom: 20px; box-sizing: border-box; font-size: 15px; background: rgba(255,255,255,0.8); color: #111827;">
            <div style="display: flex; gap: 12px; justify-content: center;">
                <button class="btn-cancel" style="flex:1; padding: 14px; border-radius: 10px; border: 1px solid rgba(0,0,0,0.1); background: rgba(255,255,255,0.6); color: #4b5563; font-weight: 600; font-size: 15px; cursor: pointer; transition: 0.2s;">Отмена</button>
                <button class="btn-ok" style="flex:1; padding: 14px; border-radius: 10px; border: none; background: #ff7300; color: #fff; font-weight: 600; font-size: 15px; cursor: pointer; transition: 0.2s;">ОК</button>
            </div>
        `;
        overlay.appendChild(card);
        document.body.appendChild(overlay);

        const input = card.querySelector('.cerber-prompt-input');
        const btnOk = card.querySelector('.btn-ok');
        const btnCancel = card.querySelector('.btn-cancel');
        
        input.focus();
        input.select();

        btnOk.onmouseover = () => btnOk.style.background = '#e06b00';
        btnOk.onmouseout = () => btnOk.style.background = '#ff7300';
        btnCancel.onmouseover = () => btnCancel.style.background = 'rgba(255,255,255,0.9)';
        btnCancel.onmouseout = () => btnCancel.style.background = 'rgba(255,255,255,0.6)';

        const submit = () => { 
            const val = input.value;
            document.body.removeChild(overlay); 
            resolve(val); 
        };

        btnOk.onclick = submit;
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
        btnCancel.onclick = () => { document.body.removeChild(overlay); resolve(null); };
        overlay.onclick = (e) => { if (e.target === overlay) { document.body.removeChild(overlay); resolve(null); } };
    });
};

// ── Глобальное переопределение alert (только после загрузки DOM) ───────
document.addEventListener('DOMContentLoaded', function() {
    window.alert = function(message) {
        window.CerberAlert(message).catch(function(err) {
            console.error('CerberAlert error:', err);
        });
    };
});
