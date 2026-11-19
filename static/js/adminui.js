/* Админ-панель: общие мелочи интерфейса (подключается в adminui/base_admin.html).
 *  - строки таблиц с data-href открываются кликом (кроме кликов по ссылкам/кнопкам/полям);
 *  - кнопки [data-scan-url] — запуск сканирования подсети (POST);
 *  - форма с .aui-savebar: «Есть несохранённые изменения», Ctrl+S — сохранить,
 *    предупреждение при уходе со страницы с несохранёнными правками.
 */
(function () {
  'use strict';

  // ── Кликабельные строки ────────────────────────────────────────────
  document.addEventListener('click', function (e) {
    const row = e.target.closest('tr[data-href]');
    if (!row || e.target.closest('a, button, input, select, textarea, label, form')) return;
    if (e.ctrlKey || e.metaKey || e.button === 1) window.open(row.dataset.href, '_blank');
    else location.href = row.dataset.href;
  });

  // ── Сканирование подсети ───────────────────────────────────────────
  document.addEventListener('click', async function (e) {
    const btn = e.target.closest('[data-scan-url]');
    if (!btn || btn.dataset.busy === '1') return;
    e.preventDefault();
    btn.dataset.busy = '1';
    const orig = btn.innerHTML;
    btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Запуск…';
    try {
      const resp = await fetch(btn.dataset.scanUrl, { method: 'POST', headers: { 'X-CSRFToken': Cerber.csrfToken() } });
      const data = await resp.json().catch(() => ({}));
      if (resp.ok && data.ok) {
        Cerber.toast('Сканирование запущено — результаты появятся через минуту-две', 'success');
        btn.innerHTML = '<i class="bi bi-check-lg"></i> Запущено';
      } else {
        Cerber.toast('Не удалось запустить: ' + (data.error || ('HTTP ' + resp.status)), 'error');
        btn.innerHTML = orig;
      }
    } catch (err) {
      Cerber.toast('Ошибка сети: ' + err.message, 'error');
      btn.innerHTML = orig;
    } finally {
      setTimeout(function () { btn.innerHTML = orig; btn.dataset.busy = ''; }, 4000);
    }
  });

  // ── Панель сохранения формы ────────────────────────────────────────
  document.querySelectorAll('form').forEach(function (form) {
    const bar = form.querySelector('.aui-savebar');
    if (!bar) return;
    const state = bar.querySelector('.aui-savebar__state');
    let dirty = false, submitting = false;
    function markDirty() {
      if (dirty) return;
      dirty = true;
      bar.classList.add('is-dirty');
      if (state) state.textContent = 'Есть несохранённые изменения';
    }
    form.addEventListener('input', markDirty);
    form.addEventListener('change', markDirty);
    form.addEventListener('submit', function () { submitting = true; });
    window.addEventListener('beforeunload', function (e) {
      if (dirty && !submitting) { e.preventDefault(); e.returnValue = ''; }
    });
    document.addEventListener('keydown', function (e) {
      if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'ы')) {
        e.preventDefault();
        submitting = true;
        if (form.requestSubmit) form.requestSubmit(); else form.submit();
      }
    });
  });
})();
