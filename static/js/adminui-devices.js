/* Кнопка «Poll now» (список устройств, детальная). */
(function () {
  'use strict';

  function csrf() {
    const m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : '';
  }

  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('.aui-icon-btn--poll');
    if (!btn || btn.dataset.busy === '1') return;
    btn.dataset.busy = '1';

    const pk = btn.dataset.pk;
    if (!pk) {
      alert('Не задан data-pk для кнопки Poll now');
      btn.dataset.busy = '';
      return;
    }

    const orig = btn.innerHTML;
    btn.innerHTML = '<i class="bi bi-hourglass-split"></i>';

    try {
      const resp = await fetch(`/admin-ui/devices/${pk}/poll-now/`, {
        method: 'POST',
        headers: { 'X-CSRFToken': csrf() },
      });
      const ct = resp.headers.get('content-type') || '';
      if (!ct.includes('application/json')) {
        throw new Error(`HTTP ${resp.status} (не JSON, вероятно редирект на login)`);
      }
      const data = await resp.json();
      if (data.ok) {
        btn.innerHTML = '<i class="bi bi-check-lg"></i>';
        setTimeout(() => location.reload(), 1200);
      } else {
        btn.innerHTML = '<i class="bi bi-x-lg"></i>';
        alert('Ошибка: ' + (data.error || 'unknown'));
        setTimeout(() => { btn.innerHTML = orig; }, 2500);
      }
    } catch (err) {
      btn.innerHTML = '<i class="bi bi-x-lg"></i>';
      alert('Ошибка: ' + err.message);
      setTimeout(() => { btn.innerHTML = orig; }, 2500);
    } finally {
      btn.dataset.busy = '';
    }
  });
})();
