/* Проверка SNMP из формы устройства (кнопка «Проверить связь»). */
(function () {
  'use strict';

  const btn = document.getElementById('btn-test-snmp');
  const result = document.getElementById('snmp-test-result');
  if (!btn || !result) return;

  function csrf() {
    const m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : '';
  }

  btn.addEventListener('click', async () => {
    const ip = document.getElementById('id_ip').value.trim();
    const community = document.getElementById('id_snmp_community').value.trim();
    const version = document.getElementById('id_snmp_version').value;
    const port = document.getElementById('id_snmp_port').value || 161;

    if (!ip || !community) {
      result.style.display = 'block';
      result.className = 'aui-snmp-test aui-snmp-test--error';
      result.textContent = 'Заполните IP и Community';
      return;
    }

    result.style.display = 'block';
    result.className = 'aui-snmp-test aui-snmp-test--loading';
    result.textContent = 'Проверка… (до 3 сек)';

    try {
      const resp = await fetch(window.CERBER_TEST_SNMP_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrf(),
        },
        body: JSON.stringify({ ip, community, version, port: parseInt(port) }),
      });
      const data = await resp.json();

      if (data.ok) {
        result.className = 'aui-snmp-test aui-snmp-test--ok';
        result.innerHTML =
          '<div><i class="bi bi-check-circle"></i> Устройство отвечает</div>' +
          '<div class="aui-snmp-test__row">sysDescr: ' + escapeHtml(data.sys_descr || '') + '</div>' +
          '<div class="aui-snmp-test__row">sysObjectID: ' + escapeHtml(data.sys_object_id || '') + '</div>';
      } else {
        result.className = 'aui-snmp-test aui-snmp-test--error';
        result.innerHTML =
          '<div><i class="bi bi-x-circle"></i> Ошибка: ' + escapeHtml(data.error || 'unknown') + '</div>';
      }
    } catch (e) {
      result.className = 'aui-snmp-test aui-snmp-test--error';
      result.textContent = 'Ошибка сети: ' + e.message;
    }
  });

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }
})();
