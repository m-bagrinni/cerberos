(function initAlertsStorm() {
    const tabs = document.querySelectorAll(".alerts-tab");
    if (!tabs.length) return;

    const networkTabContent = document.getElementById("networkTabContent");
    const devicesTabWrapper = document.getElementById("devicesTabWrapper");
    const activeEl = document.getElementById("networkActive");
    const historyEl = document.getElementById("networkHistory");
    const infoEl = document.getElementById("networkAlertsInfo");
    const badge = document.getElementById("networkTabBadge");

    tabs.forEach(tab => {
        tab.addEventListener("click", () => {
            const name = tab.getAttribute("data-tab");
            tabs.forEach(t => t.classList.remove("alerts-tab--active"));
            tab.classList.add("alerts-tab--active");

            if (name === "devices") {
                if (devicesTabWrapper) devicesTabWrapper.style.display = "";
                if (networkTabContent) networkTabContent.style.display = "none";
            } else {
                if (devicesTabWrapper) devicesTabWrapper.style.display = "none";
                if (networkTabContent) networkTabContent.style.display = "";
                loadNetworkAlerts();
            }
        });
    });

    async function loadNetworkAlerts() {
        try {
            const resp = await fetch("/api/v1/alerts/storm/");
            const data = await resp.json();
            if (!data.ok) {
                if (activeEl) activeEl.innerHTML = '<div class="empty-state">Ошибка загрузки</div>';
                return;
            }

            if (badge) {
                if (data.counts.total_active > 0) {
                    badge.textContent = data.counts.total_active;
                    badge.style.display = "";
                } else {
                    badge.style.display = "none";
                }
            }

            if (infoEl) {
                infoEl.textContent =
                    `Активных: ${data.counts.total_active} · CRIT: ${data.counts.active_crit} · WARN: ${data.counts.active_warn}`;
            }

            if (activeEl) {
                if (data.active.length === 0) {
                    activeEl.innerHTML = `
                        <div class="empty-state">
                            <div class="empty-state__icon">✅</div>
                            Активных аномалий трафика нет
                        </div>`;
                } else {
                    activeEl.innerHTML = data.active.map(renderCard).join("");
                }
            }

            if (historyEl) {
                if (data.history.length > 0) {
                    historyEl.innerHTML = `
                        <h3 style="margin: 24px 0 12px; font-size: 14px; color: #e6e9ef;">История (последние ${data.history.length})</h3>
                        <table class="storm-history-table">
                            <thead><tr>
                                <th>Уровень</th><th>Подсеть</th><th>Broadcast peak</th>
                                <th>Начало</th><th>Разрешён</th><th>Топ MAC</th>
                            </tr></thead>
                            <tbody>${data.history.map(renderRow).join("")}</tbody>
                        </table>`;
                } else {
                    historyEl.innerHTML = `
                        <h3 style="margin: 24px 0 12px; font-size: 14px; color: #e6e9ef;">История</h3>
                        <div class="empty-state">
                            <div class="empty-state__icon">📋</div>
                            История пуста (storm ещё не разрешался)
                        </div>`;
                }
            }
        } catch (e) {
            if (activeEl) activeEl.innerHTML = `<div class="empty-state">Ошибка: ${escapeHtml(e.message)}</div>`;
        }
    }

    function escapeHtml(s) {
        return String(s || "").replace(/[&<>"']/g, c => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
        }[c]));
    }

    function renderCard(e) {
        const sev = e.severity.toUpperCase();
        const macs = (e.top_macs || []).slice(0, 5).map(m =>
            `<span class="storm-mac">${escapeHtml(m.mac)} <span class="storm-mac__count">${m.count}</span></span>`
        ).join("");

        const started = new Date(e.started_at);
        const timeAgo = timeAgoStr(started);

        return `
            <div class="storm-alert-card storm-alert-card--${e.severity}">
                <div class="storm-alert-card__head">
                    <div class="storm-alert-card__head-left">
                        <span class="storm-sev storm-sev--${e.severity}">${sev}</span>
                        <span class="storm-alert-card__title">Broadcast Storm</span>
                    </div>
                    <span class="storm-alert-card__time">${timeAgo}</span>
                </div>
                <div class="storm-alert-card__body">
                    <div style="display:flex; justify-content:space-between; margin-bottom:10px;">
                        <span class="storm-alert-card__subnet">${escapeHtml(e.subnet_cidr)}</span>
                        <span style="font-size:11px; color:#8a94a6;">${escapeHtml(e.iface)}</span>
                    </div>

                    <div class="storm-metric">
                        <span class="storm-metric__value">${e.bcast_pct_now.toFixed(1)}%</span>
                        <span class="storm-metric__label">Broadcast</span>
                        <span class="storm-metric__peak">peak <b>${e.bcast_pct_peak.toFixed(1)}%</b></span>
                    </div>

                    <div class="storm-reason">
                        ⚠️ ${escapeHtml(e.reason)}
                    </div>

                    <div style="font-size:11px; color:#8a94a6; margin-bottom:12px;">
                        📊 Multicast: ${e.mcast_pct_now.toFixed(1)}% · Всего пакетов: ${e.total_now}
                    </div>

                    ${macs ? `
                        <div class="storm-macs">
                            <div class="storm-macs__label">🎯 Топ-источники (MAC)</div>
                            <div class="storm-macs__list">${macs}</div>
                        </div>
                    ` : ""}
                </div>
                <div class="storm-alert-card__actions">
                    <a href="/local/subnets/?subnet=${e.subnet_id}" class="storm-btn storm-btn--primary">
                        🔍 Диагностика
                    </a>
                    <a href="/local/subnets/?subnet=${e.subnet_id}&tcpdump=1" class="storm-btn">
                        📊 Захват трафика
                    </a>
                </div>
            </div>
        `;
    }

    function renderRow(e) {
        const macs = (e.top_macs || []).slice(0, 3).map(m =>
            `<span class="storm-mac">${escapeHtml(m.mac)}</span>`
        ).join(" ");
        const resolved = e.resolved_at
            ? new Date(e.resolved_at).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })
            : "—";
        const started = new Date(e.started_at).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
        return `<tr>
            <td><span class="storm-sev storm-sev--${e.severity}">${e.severity}</span></td>
            <td>${escapeHtml(e.subnet_cidr)}</td>
            <td>${e.bcast_pct_peak.toFixed(1)}%</td>
            <td>${started}</td>
            <td>${resolved}</td>
            <td>${macs || "—"}</td>
        </tr>`;
    }

    function timeAgoStr(date) {
        const sec = Math.floor((Date.now() - date.getTime()) / 1000);
        if (sec < 60) return `${sec} сек назад`;
        if (sec < 3600) return `${Math.floor(sec/60)} мин назад`;
        if (sec < 86400) return `${Math.floor(sec/3600)} ч назад`;
        return `${Math.floor(sec/86400)} дн назад`;
    }

    fetch("/api/v1/alerts/storm/").then(r => r.json()).then(d => {
        if (d.ok && d.counts.total_active > 0 && badge) {
            badge.textContent = d.counts.total_active;
            badge.style.display = "";
        }
    }).catch(() => {});
})();
