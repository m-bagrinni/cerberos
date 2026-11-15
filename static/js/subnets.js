/* ═══════════════════════════════════════════════════════════
   Подсети — логика карты IP, модалки, live-refresh.
   ═══════════════════════════════════════════════════════════ */

(function () {
  "use strict";

  // ─── Общие переменные IIFE ───


  
  // ─── Общие хелперы (static/js/core.js) ───
  const esc = Cerber.escapeHtml;

  function csrfFetch(url, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ Accept: "application/json" }, opts.headers || {});
    return Cerber.fetch(url, opts);
  }
// ─── Страница списка подсетей ─────────────────────────────

  const scanAllBtn = document.getElementById("subnets-scan-all");
  if (scanAllBtn) {
    scanAllBtn.addEventListener("click", async () => {
      scanAllBtn.disabled = true;
      scanAllBtn.innerHTML = '<i class="bi bi-arrow-repeat"></i> Запущено';
      try {
        const r = await csrfFetch("/api/v1/subnets/scan-all-now/", { method: "POST" });
        const d = await r.json();
      } catch (e) {
        console.error(e);
      }
      setTimeout(() => {
        scanAllBtn.disabled = false;
        scanAllBtn.innerHTML = '<i class="bi bi-arrow-clockwise"></i> Сканировать все';
      }, 3000);
    });
  }

  // ─── Модалка «Добавить подсеть» ───────────────────────────

  const addBtn = document.getElementById("subnets-add-btn");
  const subnetModal = document.getElementById("subnet-modal");
  const subnetBackdrop = document.getElementById("subnet-modal-backdrop");
  const subnetForm = document.getElementById("subnet-form");
  const subnetError = document.getElementById("subnet-form-error");

  function openSubnetModal() {
    if (!subnetModal) return;
    if (subnetForm) subnetForm.reset();
    if (subnetError) { subnetError.hidden = true; subnetError.textContent = ""; }
    if (subnetModal.parentElement !== document.body) {
      document.body.appendChild(subnetBackdrop);
      document.body.appendChild(subnetModal);
    }
    subnetModal.hidden = false;
    subnetBackdrop.hidden = false;
    const cidrInput = document.getElementById("subnet-cidr");
    if (cidrInput) cidrInput.focus();
  }

  function closeSubnetModal() {
    if (!subnetModal) return;
    subnetModal.hidden = true;
    subnetBackdrop.hidden = true;
  }

  if (addBtn) {
    addBtn.addEventListener("click", function (e) {
      e.preventDefault();
      openSubnetModal();
    });
  }
  document.getElementById("subnet-modal-close")?.addEventListener("click", closeSubnetModal);
  document.getElementById("subnet-modal-cancel")?.addEventListener("click", closeSubnetModal);
  subnetBackdrop?.addEventListener("click", closeSubnetModal);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && subnetModal && !subnetModal.hidden) closeSubnetModal();
  });

  // ─── Отправка формы создания подсети ───
  if (subnetForm) {
    subnetForm.addEventListener("submit", async function (e) {
      e.preventDefault();

      const fd = new FormData(subnetForm);
      const payload = {
        cidr: (fd.get("cidr") || "").trim(),
        name: (fd.get("name") || "").trim(),
        description: (fd.get("description") || "").trim(),
        gateway_ip: (fd.get("gateway_ip") || "").trim() || null,
        scan_interval_min: parseInt(fd.get("scan_interval_min") || "15", 10) || 15,
        populate_ips: !!fd.get("populate_ips"),
        is_active: !!fd.get("is_active"),
      };

      if (!payload.cidr) {
        subnetError.hidden = false;
        subnetError.textContent = "CIDR обязателен";
        return;
      }

      const submitBtn = document.getElementById("subnet-form-submit");
      submitBtn.disabled = true;
      submitBtn.innerHTML = "Создаю...";
      subnetError.hidden = true;

      try {
        const r = await csrfFetch("/api/v1/subnets/", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await r.json();

        if (!r.ok) {
          let msgs = [];
          for (const [k, v] of Object.entries(data)) {
            const text = Array.isArray(v) ? v.join("; ") : String(v);
            msgs.push((k === "non_field_errors" ? "" : k + ": ") + text);
          }
          throw new Error(msgs.join(" • ") || "Ошибка создания");
        }

        // Запускаем сканирование
        if (data.id) {
          try {
            await csrfFetch("/api/v1/subnets/" + data.id + "/scan-now/", { method: "POST" });
          } catch (err) { /* игнор */ }
        }

        setTimeout(function () { window.location.reload(); }, 700);
      } catch (err) {
        subnetError.hidden = false;
        subnetError.textContent = err.message;
        console.error("[add subnet] error:", err);
      } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = "Создать";
      }
    });
  }

  // ─── Страница деталей подсети ─────────────────────────────

  const detailPage = document.querySelector(".subnet-detail-page");
  if (!detailPage) return;

  const subnetId = detailPage.dataset.subnetId;
  const cidr = detailPage.dataset.cidr;
  const gridEl = document.getElementById("ip-grid");
  const filterInput = document.getElementById("ip-filter");
  const onlyAlive = document.getElementById("only-alive");
  const onlyMonitored = document.getElementById("only-monitored");

  let liveTimer = null;
  let liveOn = true;
  let currentIps = [];      // массив объектов {id, ip, ...}
  let gridSize = (typeof localStorage !== "undefined")
    ? (localStorage.getItem("subnets_grid_size") || "normal")
    : "normal";

  // ─── Кнопки ───────────────────────────────────────────────

  document.getElementById("subnet-scan-now")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.innerHTML = '<i class="bi bi-arrow-repeat"></i> Сканирую...';
    try {
      await csrfFetch(`/api/v1/subnets/${subnetId}/scan-now/`, { method: "POST" });
      // Через 3 секунды — перезагрузить сетку
      setTimeout(() => { loadGrid(); }, 3000);
    } catch (err) {
      console.error(err);
    }
    setTimeout(() => {
      btn.disabled = false;
      btn.innerHTML = '<i class="bi bi-arrow-clockwise"></i> Сканировать';
    }, 4000);
  });

  document.getElementById("subnet-toggle-auto")?.addEventListener("click", (e) => {
    liveOn = !liveOn;
    const btn = e.currentTarget;
    if (liveOn) {
      btn.innerHTML = '<i class="bi bi-toggle-on"></i> LIVE';
      btn.classList.add("btn--primary");
      startLive();
    } else {
      btn.innerHTML = '<i class="bi bi-toggle-off"></i> LIVE off';
      btn.classList.remove("btn--primary");
      stopLive();
    }
  });

  // ─── Фильтры ─────────────────────────────────────────────

  filterInput?.addEventListener("input", applyFilters);
  onlyAlive?.addEventListener("change", applyFilters);
  onlyMonitored?.addEventListener("change", applyFilters);

  function applyFilters() {
    const q = (filterInput?.value || "").trim().toLowerCase();
    const a = onlyAlive?.checked || false;
    const m = onlyMonitored?.checked || false;

    document.querySelectorAll(".ip-cell").forEach(el => {
      const ip = el.dataset.ip || "";
      const host = (el.dataset.hostname || "").toLowerCase();
      const alive = el.dataset.alive === "1";
      const monitored = el.dataset.monitored === "1";

      let show = true;
      if (q && !ip.toLowerCase().includes(q) && !host.includes(q)) show = false;
      if (a && !alive) show = false;
      if (m && !monitored) show = false;

      el.classList.toggle("ip-cell--hidden", !show);
    });
  }

  // ─── Загрузка и рендер ─────────────────────────────────────

  async function loadGrid() {
    try {
      const r = await fetch(`/api/v1/subnets/${subnetId}/ip-grid/`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      currentIps = data.ips || [];
      renderGrid(data);
      updateSummary();
      applyFilters();
    } catch (e) {
      console.error("loadGrid:", e);
      gridEl.innerHTML = `<div class="ip-grid__loading">Ошибка загрузки: ${esc(e.message)}</div>`;
    }
  }

  // ─── Кастомный тултип ───
  var tooltipEl = null;
  var tooltipTimer = null;

  function buildTooltipHtml(ip) {
    var parts = [];
    parts.push('<div class="t-ip">' + esc(ip.ip) + '</div>');
    if (ip.hostname) {
      parts.push('<div class="t-host">' + esc(ip.hostname) + '</div>');
    }

    var state = ip.state || "unknown";
    var stateLabel = {
      free: "свободен", used: "занят",
      reserved: "reserved", unknown: "неизвестен",
    }[state] || state;
    var stateClass = "t-state";
    if (state === "reserved") stateClass += " t-reserved";
    parts.push('<div class="' + stateClass + '">состояние: ' + esc(stateLabel) + '</div>');

    if (ip.is_alive) {
      var rtt = (ip.last_rtt_ms != null) ? (ip.last_rtt_ms + " ms") : "?";
      parts.push('<div class="t-rtt">RTT: ' + esc(rtt) + '</div>');
    } else if (ip.is_monitored) {
      parts.push('<div class="t-bad">❌ не отвечает</div>');
    }
    if (ip.is_monitored) {
      parts.push('<div class="t-mon">◉ на мониторинге</div>');
    }
    if (ip.is_reserved) {
      parts.push('<div class="t-reserved">🔒 reserved</div>');
    }
    return parts.join("");
  }

  function showTooltip(ip, cellEl) {
    if (!tooltipEl) {
      tooltipEl = document.createElement("div");
      tooltipEl.className = "ip-cell__tooltip";
      document.body.appendChild(tooltipEl);
    }
    tooltipEl.innerHTML = buildTooltipHtml(ip);

    var rect = cellEl.getBoundingClientRect();
    var tw = 260;
    var th = 130;
    var spaceRight = window.innerWidth - rect.right;
    var spaceBelow = window.innerHeight - rect.bottom;

    var left = (spaceRight > tw + 20) ? (rect.right + 10) : Math.max(4, rect.left - tw - 10);
    var top  = (spaceBelow > th + 20) ? rect.top : Math.max(4, window.innerHeight - th - 10);

    tooltipEl.style.left = left + "px";
    tooltipEl.style.top = top + "px";

    clearTimeout(tooltipTimer);
    tooltipTimer = setTimeout(function () { tooltipEl.classList.add("is-visible"); }, 120);
  }

  function hideTooltip() {
    clearTimeout(tooltipTimer);
    if (tooltipEl) tooltipEl.classList.remove("is-visible");
  }

  function highlightRowCol(cells, index, cols) {
    if (!cells || index >= cells.length) return;
    var targetRow = cells[index].row;
    var targetCol = cells[index].col;
    for (var i = 0; i < cells.length; i++) {
      var c = cells[i];
      c.el.classList.remove("is-hover-row", "is-hover-col");
      if (c.row === targetRow && c.col === targetCol) continue;
      if (c.row === targetRow) c.el.classList.add("is-hover-row");
      else if (c.col === targetCol) c.el.classList.add("is-hover-col");
    }
  }

  function clearHighlight(cells) {
    if (!cells) return;
    for (var i = 0; i < cells.length; i++) {
      cells[i].el.classList.remove("is-hover-row", "is-hover-col");
    }
  }

  // Текущий размер ячейки (пересчитывается автоматически)
  var _cellSize = { px: 22, gap: 2 };

  function computeCellSize(cols) {
    // gridSize: "normal" | "dense" | "tiny"
    // Всегда подгоняем под ширину wrap — режим влияет только на padding
    var wrap = gridEl.parentElement;
    var wrapW = wrap ? wrap.clientWidth : 1200;

    // Отступы вокруг сетки (см. .ip-grid-wrap padding)
    var outerPad = 28;      // слева + справа
    var availW = Math.max(200, wrapW - outerPad);

    // Gap зависит от размера: чем больше ячейка — тем больше отступ
    // Начнём с 2 и подберём
    var gap = 2;
    var cell = Math.floor((availW - (cols - 1) * gap) / cols);

    // Если ячейка получается слишком крупная (>90) — увеличим gap для красоты
    if (cell > 70) gap = 6;
    else if (cell > 50) gap = 4;
    else if (cell > 30) gap = 3;

    // Пересчёт с учётом нового gap
    cell = Math.floor((availW - (cols - 1) * gap) / cols);

    // Ограничения разумности
    cell = Math.max(14, Math.min(90, cell));

    return { px: cell, gap: gap };
  }

  function renderGrid(data) {
    const cols = data.cols || 16;

    _cellSize = computeCellSize(cols);
    const cellPx = _cellSize.px;
    const gapPx = _cellSize.gap;

    gridEl.style.gridTemplateColumns = `repeat(${cols}, ${cellPx}px)`;
    gridEl.style.gap = gapPx + "px";
    gridEl.style.setProperty("--cell-px", cellPx + "px");
    gridEl.style.setProperty("--cell-gap", gapPx + "px");

    gridEl.classList.toggle("is-large", cellPx >= 36);
    gridEl.classList.toggle("is-mini", cellPx <= 18);

    const frag = document.createDocumentFragment();
    const cells = [];

    data.ips.forEach((ip, i) => {
      const el = document.createElement("div");
      el.className = "ip-cell " + ipCellClass(ip);
      el.dataset.ip = ip.ip;
      el.dataset.id = ip.id;
      el.dataset.alive = ip.is_alive ? "1" : "0";
      el.dataset.monitored = ip.is_monitored ? "1" : "0";
      el.dataset.state = ip.state;
      el.dataset.hostname = ip.hostname || "";

      // Всегда рисуем цифры — CSS сам скроет для is-mini
      el.textContent = ip.ip.split(".").pop();

      el.addEventListener("click", () => openIpModal(ip));

      // Hover: кастомный тултип + подсветка строки/столбца
      el.addEventListener("mouseenter", (e) => {
        showTooltip(ip, e.currentTarget);
      });
      el.addEventListener("mouseleave", () => {
        hideTooltip();
      });

      cells.push({
        el: el,
        row: Math.floor(i / cols),
        col: i % cols,
      });

      frag.appendChild(el);
    });

    gridEl.innerHTML = "";
    gridEl.appendChild(frag);
  }

  function ipCellClass(ip) {
    // Приоритет: reserved > monitored+dead > monitored+alive > alive > free > unknown
    if (ip.is_reserved || ip.state === "reserved") return "ip-cell--reserved";
    if (ip.is_monitored && !ip.is_alive) return "ip-cell--dead";
    if (ip.is_monitored && ip.is_alive) return "ip-cell--alive-mon";
    if (ip.is_alive) return "ip-cell--alive";
    if (ip.state === "free") return "ip-cell--free";
    return "ip-cell--unknown";
  }

  function buildTooltip(ip) {
    let t = ip.ip;
    if (ip.hostname) t += ` (${ip.hostname})`;
    t += `\nstate: ${ip.state}`;
    if (ip.is_alive) t += `\nRTT: ${ip.last_rtt_ms ?? "?"} ms`;
    if (ip.is_monitored) t += `\n[на мониторинге]`;
    if (ip.is_reserved) t += `\n[reserved]`;
    return t;
  }

  // ─── Сводка ───────────────────────────────────────────────

  function updateSummary() {
    const total = currentIps.length;
    const alive = currentIps.filter(i => i.is_alive).length;
    const reserved = currentIps.filter(i => i.is_reserved).length;
    const used = currentIps.filter(i => i.state === "used" && !i.is_reserved).length;
    // Свободен = total - used - reserved (не полагаемся на state="free")
    const free = total - used - reserved;
    const monitored = currentIps.filter(i => i.is_monitored).length;

    setText("stat-total", total);
    setText("stat-alive", alive);
    setText("stat-used", used);
    setText("stat-free", free);
    setText("stat-monitored", monitored);
  }

  function setText(id, v) {
    const el = document.getElementById(id);
    if (el) el.textContent = v;
  }

  // ─── Live-refresh ─────────────────────────────────────────

  function startLive() {
    stopLive();
    liveTimer = Cerber.poll(loadGrid, 15000);   // пауза в фоновой вкладке
  }

  function stopLive() {
    if (liveTimer) liveTimer.stop();
    liveTimer = null;
  }

  // ─── Модалка IP (упрощённая, гарантированно рабочая) ───

  const modal = document.getElementById("ip-modal");
  const backdrop = document.getElementById("ip-modal-backdrop");
  const modalTitle = document.getElementById("ip-modal-title");
  const modalBody = document.getElementById("ip-modal-body");

  document.getElementById("ip-modal-close")?.addEventListener("click", closeModal);
  backdrop?.addEventListener("click", closeModal);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

  function closeModal() {
    if (modal) modal.hidden = true;
    if (backdrop) backdrop.hidden = true;
  }

  async function openIpModal(ip) {
    if (!modal || !modalBody) return;
    modalTitle.textContent = ip.ip || "?";
    modalBody.innerHTML = "Загрузка...";
    modal.hidden = false;
    backdrop.hidden = false;

    const id = ip.id;
    if (!id) {
      modalBody.innerHTML = '<div style="color:var(--crit)">Ошибка: у IP нет id</div>';
      return;
    }

    try {
      const url = `/api/v1/ip-addresses/${id}/`;
      const resp = await fetch(url);
      if (!resp.ok) {
        modalBody.innerHTML = `<div style="color:var(--crit)">HTTP ${esc(resp.status)} — ${esc(url)}</div>`;
        return;
      }
      const data = await resp.json();
      modalBody.innerHTML = buildModalHtml(data);
      bindModalButtons(data);
      if (data.is_monitored) {
        // Отложенный вызов — canvas уже в DOM
        setTimeout(function () { drawRttChart(data.id); }, 50);
      }
    } catch (e) {
      modalBody.innerHTML = `<div style="color:var(--crit)">Ошибка: ${esc(e.message)}</div>`;
    }
  }

  // ─── График RTT (Chart.js) ───
  var currentChart = null;

  async function drawRttChart(ipId) {
    var canvas = document.getElementById("ip-rtt-chart");
    if (!canvas) return;
    if (typeof Chart === "undefined") {
      canvas.parentElement.innerHTML =
        '<div class="ip-modal__no-chart">Chart.js не загружен</div>';
      return;
    }

    try {
      var r = await fetch("/api/v1/ip-addresses/" + ipId + "/ping-history/?hours=24");
      if (!r.ok) throw new Error("HTTP " + r.status);
      var data = await r.json();
      var samples = data.samples || [];

      var info = document.getElementById("ip-chart-info");
      if (info) info.textContent = samples.length + " точек";

      if (samples.length === 0) {
        canvas.parentElement.innerHTML =
          '<div class="ip-modal__no-chart">Нет данных за 24 часа.<br>' +
          'Воркер пингует каждые 30 сек — подожди минуту.</div>';
        return;
      }

      var labels = samples.map(function (s) {
        var d = new Date(s.ts);
        return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
      });
      var rtts = samples.map(function (s) {
        return (s.is_alive && s.rtt_ms != null) ? s.rtt_ms : null;
      });

      if (currentChart) { try { currentChart.destroy(); } catch (_) {} }
      currentChart = new Chart(canvas.getContext("2d"), {
        type: "line",
        data: {
          labels: labels,
          datasets: [{
            label: "RTT (ms)",
            data: rtts,
            borderColor: "#73bf69",
            backgroundColor: "rgba(115,191,105,.15)",
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0.25,
            spanGaps: true,
            fill: true,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: function (ctx) {
                  var s = samples[ctx.dataIndex];
                  return s.is_alive
                    ? ("RTT: " + (s.rtt_ms != null ? s.rtt_ms : "?") + " ms")
                    : "❌ не отвечал";
                },
              },
            },
          },
          scales: {
            x: {
              ticks: { color: "#8e8e8e", maxTicksLimit: 6, font: { size: 10 } },
              grid: { color: "rgba(44,50,53,.5)" },
            },
            y: {
              beginAtZero: true,
              ticks: { color: "#8e8e8e", font: { size: 10 } },
              grid: { color: "rgba(44,50,53,.5)" },
            },
          },
        },
      });
    } catch (e) {
      console.error("[chart] error:", e);
      canvas.parentElement.innerHTML =
        '<div class="ip-modal__no-chart">Ошибка: ' + esc(e.message) + '</div>';
    }
  }

  function buildModalHtml(d) {
    const rows = [
      ["IP", d.ip],
      ["Подсеть", d.subnet_cidr],
      ["Hostname", d.hostname || "—"],
      ["MAC", d.mac || "—"],
      ["Состояние", d.state],
      ["Живой", d.is_alive ? "✅ да" : "❌ нет"],
      ["На мониторинге", d.is_monitored ? "✅ да" : "❌ нет"],
      ["Reserved", d.is_reserved ? "✅ да" : "❌ нет"],
      ["RTT", d.last_rtt_ms != null ? d.last_rtt_ms + " ms" : "—"],
      ["Потери", d.packet_loss_pct != null ? d.packet_loss_pct + "%" : "—"],
      ["Последний ping", fmtTime(d.last_ping_at)],
      ["Последний ответ", fmtTime(d.last_seen_at)],
    ];
    let html = rows.map(r =>
      '<div class="ip-modal__row"><span class="ip-modal__row-label">' + esc(r[0]) +
      '</span><span class="ip-modal__row-value">' + esc(r[1]) + '</span></div>'
    ).join("");
    // График RTT для monitored IP
    if (d.is_monitored) {
      html += '<div class="ip-modal__chart">' +
              '<div class="ip-modal__chart-title"><span>RTT за 24 часа</span>' +
              '<span id="ip-chart-info"></span></div>' +
              '<canvas id="ip-rtt-chart"></canvas>' +
              '</div>';
    }

    html += '<div class="ip-modal__actions" id="ip-modal-actions"></div>';
    return html;
  }

  function bindModalButtons(d) {
    const cont = document.getElementById("ip-modal-actions");
    if (!cont) return;

    const toggle = document.createElement("button");
    toggle.className = "btn " + (d.is_monitored ? "btn--crit" : "btn--ok");
    toggle.textContent = d.is_monitored ? "Снять с мониторинга" : "На мониторинг";
    toggle.addEventListener("click", async () => {
      const r = await csrfFetch(`/api/v1/ip-addresses/${d.id}/toggle-monitor/`, { method: "POST" });
      const j = await r.json();
      d.is_monitored = j.is_monitored;
      await loadGrid();
      openIpModal(d);
    });

    const ping = document.createElement("button");
    ping.className = "btn";
    ping.textContent = "Пинговать";
    ping.addEventListener("click", async () => {
      ping.disabled = true;
      ping.textContent = "Пингую...";
      try {
        const r = await csrfFetch(`/api/v1/ip-addresses/${d.id}/ping-now/`, { method: "POST" });
        const j = await r.json();
        await loadGrid();
        openIpModal(j);
      } catch (e) {
        ping.textContent = "Ошибка";
      }
    });

    const res = document.createElement("button");
    res.className = "btn";
    res.textContent = d.is_reserved ? "Снять reserved" : "Reserved";
    res.addEventListener("click", async () => {
      const r = await csrfFetch(`/api/v1/ip-addresses/${d.id}/set-reserved/`, { method: "POST" });
      const j = await r.json();
      d.is_reserved = j.is_reserved;
      d.state = j.state;
      await loadGrid();
      openIpModal(d);
    });

    cont.appendChild(toggle);
    cont.appendChild(ping);
    cont.appendChild(res);
  }

  function fmtTime(iso) {
    if (!iso) return "—";
    try { return new Date(iso).toLocaleString("ru-RU", { hour12: false }); }
    catch (_) { return iso; }
  }

  // ─── Инициализация ────────────────────────────────────────

  // ─── Переключатель размера сетки ───
  var sizeButtons = document.querySelectorAll("#grid-size-switch button");
  sizeButtons.forEach(function (btn) {
    if (btn.dataset.size === gridSize) {
      btn.classList.add("is-active");
    } else {
      btn.classList.remove("is-active");
    }
    btn.addEventListener("click", function () {
      gridSize = btn.dataset.size || "normal";
      localStorage.setItem("subnets_grid_size", gridSize);
      // Перерисовываем с новым размером
      if (currentIps.length) {
        renderGrid({
          cols: Math.ceil(Math.sqrt(currentIps.length)),
          ips: currentIps,
        });
        applyFilters();
      }
      sizeButtons.forEach(function (b) { b.classList.remove("is-active"); });
      btn.classList.add("is-active");
      // Перерисовываем сетку с новым размером
      if (currentIps.length) {
        // Сохраняем данные, рендерим заново
        renderGrid({
          cols: Math.ceil(Math.sqrt(currentIps.length)),
          ips: currentIps,
        });
        applyFilters();
      }
    });
  });

  // При изменении размера окна — перерисовать сетку
  var _resizeTimer = null;
  window.addEventListener("resize", function () {
    if (!currentIps.length) return;
    clearTimeout(_resizeTimer);
    _resizeTimer = setTimeout(function () {
      renderGrid({
        cols: Math.ceil(Math.sqrt(currentIps.length)),
        ips: currentIps,
      });
      applyFilters();
    }, 150);
  });

  loadGrid();
  startLive();

})();
