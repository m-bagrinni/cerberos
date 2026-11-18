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

// Модалка IP — static/js/subnets.js (openIpModal); захват трафика — static/js/capture.js

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
