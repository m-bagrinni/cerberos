/* Bulk resolve для алертов. */
(function () {
  'use strict';

  const btn = document.getElementById('btn-bulk-resolve');
  const counter = document.getElementById('bulk-count');
  const checkAll = document.getElementById('check-all');
  const checks = () => Array.from(document.querySelectorAll('.alert-check:not([disabled])'));

  function csrf() {
    const m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : '';
  }

  function updateUI() {
    const selected = checks().filter(c => c.checked);
    if (counter) counter.textContent = selected.length;
    if (btn) btn.style.display = selected.length > 0 ? 'inline-flex' : 'none';
  }

  document.addEventListener('change', (e) => {
    if (e.target.classList.contains('alert-check')) {
      updateUI();
    }
    if (e.target.id === 'check-all') {
      checks().forEach(c => c.checked = e.target.checked);
      updateUI();
    }
  });

  if (btn) {
    btn.addEventListener('click', async () => {
      const ids = checks().filter(c => c.checked).map(c => parseInt(c.dataset.id));
      if (!ids.length) return;
      btn.disabled = true;
      btn.innerHTML = '<i class="bi bi-hourglass-split"></i> ...';

      try {
        const resp = await fetch(window.CERBER_BULK_RESOLVE_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
          body: JSON.stringify({ ids }),
        });
        const data = await resp.json();
        if (data.ok) {
          btn.innerHTML = '<i class="bi bi-check-lg"></i> Resolved ' + data.resolved;
          setTimeout(() => location.reload(), 800);
        } else {
          btn.innerHTML = '<i class="bi bi-x-lg"></i> ' + (data.error || 'error');
          setTimeout(() => { btn.disabled = false; updateUI(); }, 2000);
        }
      } catch (err) {
        btn.innerHTML = '<i class="bi bi-x-lg"></i> ' + err.message;
        setTimeout(() => { btn.disabled = false; updateUI(); }, 2000);
      }
    });
  }
})();
