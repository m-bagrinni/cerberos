/* Графики метрик на карточке устройства (админка) — с диагностикой */
(function () {
  'use strict';

  // Видимый лог прямо на странице — чтобы не открывать F12
  function debugLog(msg, isError) { if (true) return; // отключено
    var box = document.getElementById('cerber-debug');
    if (!box) {
      box = document.createElement('div');
      box.id = 'cerber-debug';
      box.style.cssText = 'position:fixed;bottom:0;left:0;right:0;background:#1f2226;color:#ccc;padding:8px 12px;font-family:monospace;font-size:12px;border-top:1px solid #ff780a;z-index:99999;max-height:200px;overflow:auto;';
      document.body.appendChild(box);
    }
    var line = document.createElement('div');
    line.style.color = isError ? '#f2495c' : '#73bf69';
    line.textContent = (isError ? '✗ ' : '✓ ') + msg;
    box.appendChild(line);
  }

  try {
    debugLog('script loaded');

    if (typeof Chart === 'undefined') {
      debugLog('Chart is UNDEFINED — Chart.js не загружен!', true);
      return;
    }
    debugLog('Chart OK (v' + (Chart.version || '?') + ')');

    var charts = window.CERBER_CHARTS || {};
    var meta = window.CERBER_METRICS_META || {};
    var keys = Object.keys(charts);
    debugLog('metrics: ' + keys.length);
    debugLog('canvas на странице: ' + document.querySelectorAll('canvas').length);

    if (!keys.length) {
      debugLog('Нет данных в CERBER_CHARTS', true);
      return;
    }

    var PALETTE = [
      '#ff780a', '#0abfbc', '#73bf69', '#f2cc0c',
      '#f2495c', '#b877d9', '#8ab8ff', '#ff9f40',
      '#54d6c2', '#e6b450', '#e05a78', '#a5d6a7',
    ];

    function fmtTs(ms) {
      var d = new Date(ms);
      return String(d.getHours()).padStart(2, '0') + ':' +
             String(d.getMinutes()).padStart(2, '0');
    }

    function renderChart(key, idx) {
      var canvas = document.getElementById('chart-' + key);
      if (!canvas) {
        debugLog('canvas не найден: chart-' + key, true);
        return;
      }
      var points = charts[key] || [];
      var info = meta[key] || { name: key, unit: '' };
      if (!points.length) {
        canvas.parentElement.innerHTML =
          '<div class="empty-state"><i class="bi bi-graph-up"></i>' +
          '<p class="text-muted">Нет данных за 24ч</p></div>';
        return;
      }

      try {
        new Chart(canvas.getContext('2d'), {
          type: 'line',
          data: {
            labels: points.map(function (p) { return fmtTs(p.ts); }),
            datasets: [{
              label: info.name,
              data: points.map(function (p) { return p.value; }),
              borderColor: PALETTE[idx % PALETTE.length],
              backgroundColor: PALETTE[idx % PALETTE.length] + '22',
              borderWidth: 2,
              pointRadius: 0,
              pointHoverRadius: 4,
              tension: 0.25,
              fill: true,
              spanGaps: true,
            }],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
              legend: { display: false },
              tooltip: {
                backgroundColor: 'rgba(17,18,23,.95)',
                borderColor: '#2c3235',
                borderWidth: 1,
              },
            },
            scales: {
              x: {
                ticks: { color: '#8e8e8e', maxTicksLimit: 6, font: { size: 10 } },
                grid: { color: 'rgba(44,50,53,.4)' },
              },
              y: {
                ticks: { color: '#8e8e8e', font: { size: 10 } },
                grid: { color: 'rgba(44,50,53,.4)' },
              },
            },
          },
        });
        debugLog('нарисован: ' + key + ' (' + points.length + ' точек)');
      } catch (e) {
        debugLog('ОШИБКА ' + key + ': ' + e.message, true);
      }
    }

    keys.forEach(renderChart);
    debugLog('=== готово ===');
  } catch (e) {
    debugLog('КРИТИЧНАЯ ОШИБКА: ' + e.message, true);
    console.error(e);
  }
})();

// Перерисовка графиков при открытии details (иначе Chart.js сжимается в 0)
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('details.aui-details').forEach(d => {
    d.addEventListener('toggle', () => {
      if (d.open) {
        // Все canvas внутри — перерисовать
        setTimeout(() => {
          document.querySelectorAll('canvas').forEach(c => {
            const chart = Chart.getChart(c);
            if (chart) chart.resize();
          });
        }, 50);
      }
    });
  });
});
