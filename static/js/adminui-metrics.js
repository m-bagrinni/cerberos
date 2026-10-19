/* Кнопка «Тест OID» в карточке профиля. */
(function () {
  'use strict';

  const ip = window.CERBER_TEST_DEVICE_IP;
  const community = window.CERBER_TEST_DEVICE_COMMUNITY;
  const version = window.CERBER_TEST_DEVICE_VERSION;
  const url = window.CERBER_TEST_OID_URL;
  if (!ip || !url) return;

  function csrf() {
    const m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : '';
  }

  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('.aui-icon-btn--test');
    if (!btn || btn.dataset.busy === '1') return;
    btn.dataset.busy = '1';

    const row = btn.closest('tr');
    const key = row ? row.querySelector('td.mono strong')?.textContent : '?';
    const oid = btn.dataset.oid;

    btn.innerHTML = '<i class="bi bi-hourglass-split"></i>';

    try {
      const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
        body: JSON.stringify({ ip, community, version, oid }),
      });
      const data = await resp.json();

      if (data.ok) {
        alert(`✓ ${key} (${oid})\n\nЗначение: ${data.value}`);
      } else {
        alert(`✗ ${key} (${oid})\n\nОшибка: ${data.error}`);
      }
    } catch (err) {
      alert('Ошибка сети: ' + err.message);
    } finally {
      btn.innerHTML = '<i class="bi bi-plug"></i>';
      btn.dataset.busy = '';
    }
  });
})();
