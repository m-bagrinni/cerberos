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

  // subnets.js перезапускается списком при каждом выборе подсети — не дублируем обработчики
  if (!window.__cerberSubnetsListBound) {
  window.__cerberSubnetsListBound = true;

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

  } // __cerberSubnetsListBound

  // ─── Страница деталей подсети ─────────────────────────────

  // Предыдущий экземпляр (прошлая выбранная подсеть) — остановить таймеры и слушатели
  if (typeof window.__cerberSubnetDetailTeardown === "function") {
    try { window.__cerberSubnetDetailTeardown(); } catch (_) {}
    window.__cerberSubnetDetailTeardown = null;
  }

  const detailPage = document.querySelector(".subnet-detail-page");
  if (!detailPage) return;

  const subnetId = detailPage.dataset.subnetId;
  const cidr = detailPage.dataset.cidr;
  const gridEl = document.getElementById("ip-grid");
  const filterInput = document.getElementById("ip-filter");
  const onlyAlive = document.getElementById("only-alive");
  const onlyMonitored = document.getElementById("only-monitored");
  const typeFilter = document.getElementById("ip-type-filter");

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
  typeFilter?.addEventListener("change", applyFilters);

  function applyFilters() {
    const q = (filterInput?.value || "").trim().toLowerCase();
    const a = onlyAlive?.checked || false;
    const m = onlyMonitored?.checked || false;
    const ty = typeFilter?.value || "";

    document.querySelectorAll(".ip-cell").forEach(el => {
      const ip = el.dataset.ip || "";
      const host = (el.dataset.hostname || "").toLowerCase();
      const alive = el.dataset.alive === "1";
      const monitored = el.dataset.monitored === "1";

      let show = true;
      const vendor = (el.dataset.vendor || "").toLowerCase();
      if (q && !ip.toLowerCase().includes(q) && !host.includes(q) && !vendor.includes(q)) show = false;
      if (a && !alive) show = false;
      if (m && !monitored) show = false;
      if (ty && (el.dataset.dtype || "none") !== ty) show = false;

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
    if (ip.dtype) {
      var conf = ip.dconf === "medium" ? " (вероятно)" : "";
      parts.push('<div class="t-type"><i class="bi ' + esc(ip.dicon) + '"></i> ' + esc(ip.dlabel) + esc(conf) + '</div>');
      parts.push('<div class="t-why">' + esc(ip.dwhy) + '</div>');
    } else if (ip.dcand) {
      parts.push('<div class="t-type t-type--unsure">Тип неясен: ' + esc(ip.dcand) + '</div>');
      parts.push('<div class="t-why">' + esc(ip.dwhy) + '</div>');
    }
    if (ip.fp && (ip.fp.ttl || ip.fp.nb)) {
      var fpParts = [];
      if (ip.fp.ttl) fpParts.push("TTL " + ip.fp.ttl);
      if (ip.fp.nb) fpParts.push("NetBIOS " + ip.fp.nb + (ip.fp.domain ? " / " + ip.fp.domain : ""));
      parts.push('<div class="t-why">' + esc(fpParts.join(" · ")) + '</div>');
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
    var th = 170;
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
    gridEl.classList.toggle("has-icons", cellPx >= 28);

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
      // Тип устройства «плюс-минус» (сервер: devtype.py) — иконка в углу
      el.dataset.vendor = ip.vendor || "";
      if (ip.dtype) {
        // плитка: значок типа + номер под ним
        el.dataset.dtype = ip.dtype;
        el.classList.add("has-type", "has-type--" + (ip.dconf || "medium"));
        el.textContent = "";
        const ico = document.createElement("i");
        ico.className = "ip-cell__ico bi " + ip.dicon;
        ico.setAttribute("aria-hidden", "true");
        const num = document.createElement("span");
        num.className = "ip-cell__num";
        num.textContent = ip.ip.split(".").pop();
        el.append(ico, num);
      }

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
    updateTypeFilter(data.ips);
  }

  function updateTypeFilter(ips) {
    if (!typeFilter) return;
    const counts = new Map();
    let none = 0;
    for (const ip of ips) {
      if (ip.dtype) {
        const c = counts.get(ip.dtype) || { label: ip.dlabel, n: 0 };
        c.n++; counts.set(ip.dtype, c);
      } else if (ip.is_alive) {
        none++;     // живой, но тип не определён или спорный
      }
    }
    const cur = typeFilter.value;
    const opts = ['<option value="">Все типы</option>'];
    [...counts.entries()].sort((a, b) => b[1].n - a[1].n).forEach(([k, v]) => {
      opts.push(`<option value="${esc(k)}">${esc(v.label)} — ${v.n}</option>`);
    });
    if (none) opts.push(`<option value="none">Не определён — ${none}</option>`);
    typeFilter.innerHTML = opts.join("");
    if ([...typeFilter.options].some(o => o.value === cur)) typeFilter.value = cur;

    // Легенда значков: тип и количество, клик — фильтр
    const legend = document.getElementById("ip-type-legend");
    if (legend) {
      const icons = {};
      ips.forEach(ip => { if (ip.dtype) icons[ip.dtype] = ip.dicon; });
      legend.innerHTML = [...counts.entries()].sort((a, b) => b[1].n - a[1].n).map(([k, v]) =>
        `<button type="button" class="type-chip${typeFilter.value === k ? " is-active" : ""}" data-dtype="${esc(k)}">` +
        `<i class="bi ${esc(icons[k])}"></i> ${esc(v.label)} <span>${v.n}</span></button>`
      ).join("") + (counts.size ? `<span class="type-legend__note" title="По производителю MAC, имени хоста и данным Cerber. Бледный значок — догадка.">тип определён автоматически</span>` : "");
    }
  }

  document.getElementById("ip-type-legend")?.addEventListener("click", (e) => {
    const chip = e.target.closest(".type-chip");
    if (!chip || !typeFilter) return;
    typeFilter.value = typeFilter.value === chip.dataset.dtype ? "" : chip.dataset.dtype;
    document.querySelectorAll("#ip-type-legend .type-chip").forEach(c => c.classList.toggle("is-active", c.dataset.dtype === typeFilter.value));
    applyFilters();
  });
  typeFilter?.addEventListener("change", () => {
    document.querySelectorAll("#ip-type-legend .type-chip").forEach(c => c.classList.toggle("is-active", c.dataset.dtype === typeFilter.value));
  });

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

  // ─── Карточка IP ───────────────────────────────────────────
  // Шапка (IP, имя, тип, статус) → «Устройство» → «Доступность» (+ график RTT) →
  // правка (имя, станция, подпись) по кнопке → действия. Только непустые поля.

  const modal = document.getElementById("ip-modal");
  const backdrop = document.getElementById("ip-modal-backdrop");
  let modalIp = null;          // строка сетки (id, ip, тип …)
  let modalData = null;        // ответ /api/v1/ip-addresses/<id>/
  let modalEditing = false;
  let stationsCache = null;

  backdrop?.addEventListener("click", closeModal);
  function onModalKey(e) { if (e.key === "Escape" && modal && !modal.hidden) closeModal(); }
  document.addEventListener("keydown", onModalKey);

  function closeModal() {
    if (modal) modal.hidden = true;
    if (backdrop) backdrop.hidden = true;
    if (currentChart) { try { currentChart.destroy(); } catch (_) {} currentChart = null; }
    modalIp = modalData = null;
    modalEditing = false;
  }

  async function openIpModal(ip) {
    if (!modal || !ip || !ip.id) return;
    modalIp = ip;
    modalEditing = false;
    modal.hidden = false;
    backdrop.hidden = false;
    renderModal(null, "loading");
    try {
      const r = await fetch(`/api/v1/ip-addresses/${ip.id}/`);
      if (!r.ok) throw new Error("HTTP " + r.status);
      const data = await r.json();
      if (modalIp !== ip) return;               // успели открыть другой IP
      modalData = data;
      renderModal(data);
    } catch (e) {
      if (modalIp === ip) renderModal(null, "error", e.message);
    }
  }

  function relTime(iso) {
    if (!iso) return null;
    const sec = (Date.now() - new Date(iso).getTime()) / 1000;
    if (sec < 0) return "только что";
    if (sec < 60) return Math.round(sec) + " с назад";
    if (sec < 3600) return Math.round(sec / 60) + " мин назад";
    if (sec < 86400) return Math.round(sec / 3600) + " ч назад";
    const d = Math.round(sec / 86400);
    return d + (d === 1 ? " день" : d < 5 ? " дня" : " дней") + " назад";
  }

  function statusOf(d) {
    if (d.is_alive) return { cls: "ok", text: "В сети" + (d.last_rtt_ms != null ? " · " + fmtMs(d.last_rtt_ms) : "") };
    if (d.is_monitored) return { cls: "crit", text: "Не отвечает" };
    if (d.last_seen_at) return { cls: "muted", text: "Не в сети" };
    return { cls: "muted", text: "Свободен" };
  }
  function fmtMs(v) { return (v < 10 ? Number(v).toFixed(2) : Math.round(v)) + " мс"; }

  function renderModal(d, state, err) {
    const ip = modalIp || {};
    const canEdit = !!(window.Cerber && Cerber.can("subnets"));
    const icon = ip.dtype ? ip.dicon : "bi-question-lg";
    const st = d ? statusOf(d) : null;
    const name = (d && d.hostname) || ip.hostname || "";
    const typeLine = ip.dtype ? ip.dlabel : (ip.vendor || "");

    const h = [];
    h.push(
      `<header class="ipm__head">` +
        `<div class="ipm__ico ipm__ico--${st ? st.cls : "muted"}"><i class="bi ${esc(icon)}"></i></div>` +
        `<div class="ipm__titles">` +
          `<h3 id="ip-modal-title">${esc(ip.ip || "")}</h3>` +
          `<div class="ipm__sub">${[name ? `<b>${esc(name)}</b>` : "", esc(typeLine)].filter(Boolean).join(" · ") || '<span class="ipm__muted">нет данных об устройстве</span>'}</div>` +
        `</div>` +
        `<div class="ipm__badges">` +
          (st ? `<span class="ipm-pill ipm-pill--${st.cls}">${esc(st.text)}</span>` : "") +
          (d && d.is_reserved ? `<span class="ipm-pill ipm-pill--warn">Резерв</span>` : "") +
        `</div>` +
        `<button type="button" class="ipm__close" data-ipm="close" title="Закрыть (Esc)"><i class="bi bi-x-lg"></i></button>` +
      `</header>`
    );

    h.push(`<div class="ipm__body">`);
    if (state === "loading") h.push(`<div class="ipm__wait"><span class="ipm-spin"></span> Загрузка…</div>`);
    if (state === "error") h.push(`<div class="ipm__err">Не удалось загрузить: ${esc(err || "")}</div>`);

    if (d) {
      // ── Устройство ──
      const rows = [];
      const row = (label, html) => rows.push(`<div class="ipm-row"><dt>${esc(label)}</dt><dd>${html}</dd></div>`);
      if (ip.dtype) {
        row("Тип", `${esc(ip.dlabel)}${ip.dconf === "medium" ? ' <span class="ipm__muted">(вероятно)</span>' : ""}` +
                   `<div class="ipm__hint">${esc(ip.dwhy || "")}</div>`);
      } else if (ip.dcand) {
        row("Тип", `<span class="ipm__muted">${esc(ip.dcand)}</span><div class="ipm__hint">${esc(ip.dwhy || "")}</div>`);
      }
      const mac = d.mac || ip.mac;
      if (mac) {
        row("MAC", `<code>${esc(mac)}</code>` +
                   `<button type="button" class="ipm-copy" data-ipm="copy" data-v="${esc(mac)}" title="Копировать"><i class="bi bi-copy"></i></button>` +
                   (ip.vendor ? `<div class="ipm__hint">${esc(ip.vendor)}</div>` : ""));
      }
      if (d.fdb_device) row("Подключён", `${esc(d.fdb_device)}${d.fdb_port ? ` · ${/^порт/i.test(d.fdb_port) ? "" : "порт "}<b>${esc(d.fdb_port)}</b>` : ""}` +
        (d.fdb_sure === false ? `<div class="ipm__hint">виден только через аплинк — его коммутатор доступа не опрашивается по SNMP</div>` : ""));
      if (d.is_device_ip && d.device_name) {
        row("В Cerber", `<a href="/local/devices/${encodeURIComponent(d.ip)}/">${esc(d.device_name)} <i class="bi bi-arrow-up-right"></i></a>`);
      }
      if (d.station_name) row("Станция", esc(d.station_name));
      if (d.description) row("Подпись", esc(d.description));
      if (ip.fp && ip.fp.nb && ip.fp.domain) row("Домен", esc(ip.fp.domain));
      if (rows.length) h.push(`<section class="ipm-sec"><h4>Устройство</h4><dl class="ipm-dl">${rows.join("")}</dl></section>`);

      // ── Доступность ──
      const tiles = [];
      const tile = (label, value, cls, title) =>
        tiles.push(`<div class="ipm-tile"${title ? ` title="${esc(title)}"` : ""}><span>${esc(label)}</span><b class="${cls || ""}">${esc(value)}</b></div>`);
      tile("Отклик", d.is_alive && d.last_rtt_ms != null ? fmtMs(d.last_rtt_ms) : "—");
      tile("Потери", d.packet_loss_pct != null ? Math.round(d.packet_loss_pct) + "%" : "—",
           d.packet_loss_pct > 0 ? (d.packet_loss_pct >= 50 ? "is-crit" : "is-warn") : "");
      tile("Последний ответ", relTime(d.last_seen_at) || "никогда", "", d.last_seen_at ? fmtTime(d.last_seen_at) : "");
      h.push(`<section class="ipm-sec"><h4>Доступность</h4><div class="ipm-tiles">${tiles.join("")}</div>` +
             (d.is_monitored
               ? `<div class="ipm-chart"><div class="ipm-chart__head"><span>Отклик за 24 часа</span><span id="ip-chart-info"></span></div>` +
                 `<div class="ipm-chart__box"><canvas id="ip-rtt-chart"></canvas></div></div>`
               : "") +
             `</section>`);

      // ── Правка ──
      if (canEdit && modalEditing) {
        h.push(
          `<section class="ipm-sec ipm-edit"><h4>Изменить</h4>` +
            `<label>Имя<input type="text" id="ipm-hostname" maxlength="255" value="${esc(d.hostname || "")}" placeholder="например, PC-067"></label>` +
            `<label>Станция<select id="ipm-station"><option value="">— не прикреплён —</option></select></label>` +
            `<label>Подпись<input type="text" id="ipm-description" maxlength="255" value="${esc(d.description || "")}" placeholder="кабинет, назначение…"></label>` +
            `<div class="ipm-edit__btns"><button type="button" class="ipm-btn ipm-btn--primary" data-ipm="save">Сохранить</button>` +
            `<button type="button" class="ipm-btn" data-ipm="cancel">Отмена</button></div>` +
          `</section>`
        );
      }
    }
    h.push(`</div>`);

    // ── Действия ──
    if (d && canEdit && !modalEditing) {
      h.push(
        `<footer class="ipm__foot">` +
          `<button type="button" class="ipm-btn" data-ipm="ping"><i class="bi bi-broadcast"></i> Пинг</button>` +
          `<button type="button" class="ipm-btn${d.is_monitored ? " is-on" : ""}" data-ipm="monitor" title="${d.is_monitored ? "Снять с мониторинга" : "Поставить на мониторинг"}">` +
            `<i class="bi ${d.is_monitored ? "bi-eye-fill" : "bi-eye"}"></i> ${d.is_monitored ? "На мониторинге" : "Мониторинг"}</button>` +
          `<button type="button" class="ipm-btn${d.is_reserved ? " is-warn" : ""}" data-ipm="reserve" title="${d.is_reserved ? "Снять резерв" : "Зарезервировать адрес"}">` +
            `<i class="bi bi-bookmark${d.is_reserved ? "-fill" : ""}"></i> ${d.is_reserved ? "В резерве" : "Резерв"}</button>` +
          `<button type="button" class="ipm-btn ipm-btn--ghost" data-ipm="edit"><i class="bi bi-pencil"></i> Изменить</button>` +
        `</footer>`
      );
    }
    modal.innerHTML = h.join("");

    if (d && d.is_monitored) setTimeout(() => drawRttChart(d.id), 30);
    if (d && modalEditing) fillStations(d.station);
  }

  async function fillStations(current) {
    const sel = document.getElementById("ipm-station");
    if (!sel) return;
    try {
      if (!stationsCache) {
        const r = await fetch("/api/v1/stations/?light=1");
        const j = await r.json();
        stationsCache = (j.results || j || []).map((s) => ({ id: s.id, name: s.name }))
          .sort((a, b) => a.name.localeCompare(b.name, "ru"));
      }
      sel.insertAdjacentHTML("beforeend", stationsCache.map((s) =>
        `<option value="${esc(s.id)}"${String(s.id) === String(current) ? " selected" : ""}>${esc(s.name)}</option>`).join(""));
    } catch (e) {
      sel.insertAdjacentHTML("beforeend", `<option disabled>станции не загрузились</option>`);
    }
  }

  async function modalAction(btn, url, body) {
    btn.disabled = true;
    btn.classList.add("is-busy");
    try {
      const r = await csrfFetch(url, body
        ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
        : { method: "POST" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      return await r.json();
    } finally {
      btn.disabled = false;
      btn.classList.remove("is-busy");
    }
  }

  modal?.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-ipm]");
    if (!btn || !modalData) {
      if (btn && btn.dataset.ipm === "close") closeModal();
      return;
    }
    const d = modalData;
    const act = btn.dataset.ipm;
    try {
      if (act === "close") return closeModal();
      if (act === "copy") {
        await navigator.clipboard?.writeText(btn.dataset.v);
        Cerber.toast("MAC скопирован", "success", 1500);
        return;
      }
      if (act === "edit") { modalEditing = true; renderModal(d); document.getElementById("ipm-hostname")?.focus(); return; }
      if (act === "cancel") { modalEditing = false; renderModal(d); return; }
      if (act === "ping") {
        const j = await modalAction(btn, `/api/v1/ip-addresses/${d.id}/ping-now/`);
        Object.assign(d, j);
        Cerber.toast(j.is_alive ? `Отвечает · ${fmtMs(j.last_rtt_ms)}` : "Не отвечает", j.is_alive ? "success" : "error", 2500);
      } else if (act === "monitor") {
        const j = await modalAction(btn, `/api/v1/ip-addresses/${d.id}/toggle-monitor/`);
        d.is_monitored = j.is_monitored;
      } else if (act === "reserve") {
        const j = await modalAction(btn, `/api/v1/ip-addresses/${d.id}/set-reserved/`);
        d.is_reserved = j.is_reserved; d.state = j.state;
      } else if (act === "save") {
        const hostname = document.getElementById("ipm-hostname").value.trim();
        const sel = document.getElementById("ipm-station");
        const description = document.getElementById("ipm-description").value.trim();
        if (hostname !== (d.hostname || "")) {
          await modalAction(btn, `/api/v1/ip-addresses/${d.id}/set-hostname/`, { hostname });
        }
        // update-station пишет и станцию, и подпись — отправляем обе, чтобы не стереть подпись
        const j = await modalAction(btn, `/api/v1/ip-addresses/${d.id}/update-station/`,
                                    { station: sel.value ? parseInt(sel.value, 10) : null, description });
        d.hostname = hostname; d.description = j.description; d.station = j.station;
        d.station_name = j.station ? (sel.selectedOptions[0] || {}).textContent : null;
        if (modalIp) modalIp.hostname = hostname;
        modalEditing = false;
        Cerber.toast("Сохранено", "success", 1500);
      } else {
        return;
      }
      renderModal(d);
      loadGrid();
    } catch (err) {
      Cerber.toast("Ошибка: " + err.message, "error", 4000);
    }
  });

  // ─── График RTT (Chart.js) ───
  var currentChart = null;

  async function drawRttChart(ipId) {
    var canvas = document.getElementById("ip-rtt-chart");
    if (!canvas) return;
    var box = canvas.parentElement;
    if (typeof Chart === "undefined") { box.innerHTML = '<div class="ipm-chart__empty">График недоступен</div>'; return; }
    try {
      var r = await fetch("/api/v1/ip-addresses/" + ipId + "/ping-history/?hours=24");
      if (!r.ok) throw new Error("HTTP " + r.status);
      var data = await r.json();
      var samples = data.samples || [];
      if (!document.body.contains(canvas)) return;
      if (!samples.length) {
        box.innerHTML = '<div class="ipm-chart__empty">Данных пока нет — опрос идёт раз в 30 секунд</div>';
        return;
      }
      var lost = samples.filter(function (s) { return !s.is_alive; }).length;
      var info = document.getElementById("ip-chart-info");
      if (info) info.textContent = lost ? ("без ответа: " + lost + " из " + samples.length) : (samples.length + " замеров, потерь нет");

      var css = getComputedStyle(document.body);
      var ok = (css.getPropertyValue("--ok") || "#6fd08c").trim();
      var crit = (css.getPropertyValue("--crit") || "#ff5d6c").trim();
      var muted = (css.getPropertyValue("--text-3") || "#7d889b").trim();

      if (currentChart) { try { currentChart.destroy(); } catch (_) {} }
      currentChart = new Chart(canvas.getContext("2d"), {
        type: "line",
        data: {
          labels: samples.map(function (s) {
            return new Date(s.ts).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
          }),
          datasets: [{
            data: samples.map(function (s) { return s.is_alive && s.rtt_ms != null ? s.rtt_ms : null; }),
            borderColor: ok, backgroundColor: ok + "22", borderWidth: 1.5,
            pointRadius: samples.map(function (s) { return s.is_alive ? 0 : 2.5; }),
            pointBackgroundColor: crit, tension: 0.25, spanGaps: true, fill: true,
          }],
        },
        options: {
          responsive: true, maintainAspectRatio: false, animation: false,
          interaction: { mode: "index", intersect: false },
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: function (ctx) {
              var s = samples[ctx.dataIndex];
              return s.is_alive ? ("отклик " + (s.rtt_ms != null ? fmtMs(s.rtt_ms) : "?")) : "не ответил";
            } } },
          },
          scales: {
            x: { ticks: { color: muted, maxTicksLimit: 6, font: { size: 10 } }, grid: { display: false } },
            y: { beginAtZero: true, ticks: { color: muted, font: { size: 10 }, maxTicksLimit: 4,
                 callback: function (v) { return v + " мс"; } }, grid: { color: "rgba(255,255,255,.05)" } },
          },
        },
      });
    } catch (e) {
      box.innerHTML = '<div class="ipm-chart__empty">Ошибка графика: ' + esc(e.message) + '</div>';
    }
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
  function onResize() {
    if (!currentIps.length) return;
    clearTimeout(_resizeTimer);
    _resizeTimer = setTimeout(function () {
      renderGrid({
        cols: Math.ceil(Math.sqrt(currentIps.length)),
        ips: currentIps,
      });
      applyFilters();
    }, 150);
  }
  window.addEventListener("resize", onResize);

  window.__cerberSubnetDetailTeardown = function () {
    stopLive();
    clearTimeout(_resizeTimer);
    clearTimeout(tooltipTimer);
    window.removeEventListener("resize", onResize);
    document.removeEventListener("keydown", onModalKey);
    if (tooltipEl) tooltipEl.remove();
  };

  loadGrid();
  startLive();

})();
