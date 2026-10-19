/* Inline-переименование NE (кнопка ✏ рядом с именем). */
(function () {
  'use strict';

  function csrf() {
    const m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : '';
  }

  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('.aui-icon-btn--rename');
    if (!btn) return;

    const pk = btn.dataset.pk;
    const oldName = btn.dataset.name;
    const newName = prompt('Новое имя NE:', oldName);
    if (!newName || newName === oldName) return;

    const url = window.CERBER_NE_RENAME_URL.replace('__PK__', pk);
    try {
      const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
        body: JSON.stringify({ name: newName }),
      });
      const data = await resp.json();
      if (data.ok) {
        // Обновляем текст ссылки
        const link = btn.closest('td').querySelector('.ne-name-link');
        if (link) link.textContent = data.name;
        btn.dataset.name = data.name;
      } else {
        alert('Ошибка: ' + (data.error || 'unknown'));
      }
    } catch (err) {
      alert('Ошибка: ' + err.message);
    }
  });
})();
