/* Выбор порта для конца оптической линии (adminui/links/_end.html).
 *
 *  Коммутатор → реальные порты из последнего опроса интерфейсов:
 *      GET /admin-ui/links/device-ports/<pk>/?link=<id>
 *      номер, имя (gi1/0/3), description (alias), up/down, скорость, SFP и RX, «занят линией …».
 *      По умолчанию — только активные (up) + выбранный; флажок «все порты» показывает остальные.
 *      Выбор порта с SFP-модулем сам ставит тип «SFP», без — «Ethernet LAN».
 *  NE (мультиплексор) → порты 1–3, как раньше (интерфейсы NE не опрашиваются).
 */
(function () {
    "use strict";
    const form = document.getElementById("link-form");
    if (!form) return;
    const urlTmpl = form.dataset.portsUrl;              // …/device-ports/0/
    const linkId = form.dataset.linkId || "";
    const esc = (s) => (window.Cerber ? Cerber.escapeHtml(s) : String(s ?? ""));
    const NE_PORTS = 3;

    form.querySelectorAll("[data-link-end]").forEach((end) => {
        const box = end.querySelector("[data-ports]");
        const $ = (id) => document.getElementById(id);
        const portInput = $(box.dataset.portInput);
        const typeInput = $(box.dataset.typeInput);
        const devInput = $(box.dataset.deviceInput);
        const neInput = $(box.dataset.neInput);
        const allToggle = end.querySelector("[data-ports-all]");
        const picked = end.querySelector("[data-ports-picked]");
        let ports = [];      // последний ответ сервера
        let seq = 0;         // защита от гонки при быстрой смене устройства

        function setValue(num, port) {
            portInput.value = num;
            if (port && typeInput) typeInput.value = port.sfp ? "sfp" : "lan";
            portInput.dispatchEvent(new Event("change", { bubbles: true }));   // для savebar
            render();
        }

        function card(p, current) {
            const cls = ["aui-port", p.up ? "is-up" : "is-down", current ? "is-active" : "", p.used_by && !current ? "is-used" : ""].join(" ");
            const speed = p.speed ? (p.speed >= 1000 ? `${p.speed / 1000}G` : `${p.speed}M`) : "";
            const rx = p.rx != null ? `<span class="aui-port__rx${p.rx < -20 ? " is-warn" : ""}">RX ${p.rx.toFixed(1)}</span>` : "";
            const title = [p.name, p.alias, p.up ? "линк есть" : (p.admin_up ? "линка нет" : "порт выключен"),
                           p.used_by ? `занят линией «${p.used_by}»` : ""].filter(Boolean).join(" · ");
            return `<button type="button" class="${cls}" data-num="${p.num}" title="${esc(title)}">
                <span class="aui-port__top"><span class="aui-port__dot"></span><b>${p.num}</b>
                  <span class="aui-port__name">${esc(p.name)}</span>${p.sfp ? '<span class="aui-port__sfp">SFP</span>' : ""}</span>
                <span class="aui-port__alias">${p.alias ? esc(p.alias) : '<i>без описания</i>'}</span>
                <span class="aui-port__meta">${speed}${rx}${p.used_by ? `<span class="aui-port__used"><i class="bi bi-link-45deg"></i>${esc(p.used_by)}</span>` : ""}</span>
            </button>`;
        }

        function render() {
            const cur = parseInt(portInput.value, 10);
            if (neInput && neInput.value) {
                box.innerHTML = Array.from({ length: NE_PORTS }, (_, i) => i + 1).map((n) =>
                    `<button type="button" class="aui-port aui-port--ne${n === cur ? " is-active" : ""}" data-num="${n}">
                       <span class="aui-port__top"><b>${n}</b><span class="aui-port__name">порт NE</span></span></button>`).join("");
                picked.textContent = cur ? `NE, порт ${cur}` : "";
                allToggle.closest("label").hidden = true;
                return;
            }
            allToggle.closest("label").hidden = !ports.length;
            if (!devInput || !devInput.value) {
                box.innerHTML = '<div class="aui-ports__empty">Выберите коммутатор или NE</div>';
                picked.textContent = "";
                return;
            }
            if (!ports.length) {
                box.innerHTML = '<div class="aui-ports__empty">Интерфейсы этого устройства ещё не опрашивались — порт можно указать номером: ' +
                    `<input type="number" min="1" max="999" class="aui-input aui-ports__manual" value="${cur || ""}"></div>`;
                box.querySelector(".aui-ports__manual").addEventListener("input", (e) => {
                    portInput.value = e.target.value;
                    portInput.dispatchEvent(new Event("change", { bubbles: true }));
                });
                picked.textContent = "";
                return;
            }
            const shown = ports.filter((p) => allToggle.checked || p.up || p.num === cur);
            const hidden = ports.length - shown.length;
            box.innerHTML = shown.map((p) => card(p, p.num === cur)).join("") +
                (hidden ? `<button type="button" class="aui-ports__more" data-show-all>+ ещё ${hidden} без линка</button>` : "");
            const p = ports.find((x) => x.num === cur);
            picked.innerHTML = p ? `выбран <b>${esc(p.name)}</b>${p.alias ? " · " + esc(p.alias) : ""}`
                                 : (cur ? `<span class="text-warn">порт ${cur} не найден на устройстве</span>` : "");
        }

        async function load() {
            ports = [];
            const dev = devInput && devInput.value;
            if (!dev || (neInput && neInput.value)) { render(); return; }
            const my = ++seq;
            box.innerHTML = '<div class="aui-ports__empty">Загружаю порты…</div>';
            try {
                const r = await fetch(urlTmpl.replace(/\/0\/$/, `/${encodeURIComponent(dev)}/`) + (linkId ? `?link=${linkId}` : ""),
                                      { credentials: "same-origin" });
                const d = await r.json();
                if (my !== seq) return;
                ports = d.ports || [];
            } catch (e) {
                if (my !== seq) return;
                if (window.Cerber) Cerber.toast("Не удалось загрузить порты: " + e.message, "error");
            }
            render();
        }

        box.addEventListener("click", (e) => {
            if (e.target.closest("[data-show-all]")) { allToggle.checked = true; render(); return; }
            const b = e.target.closest(".aui-port");
            if (!b) return;
            const num = parseInt(b.dataset.num, 10);
            setValue(num, ports.find((p) => p.num === num));
        });
        allToggle.addEventListener("change", render);
        // коммутатор и NE на одном конце взаимоисключающие
        devInput && devInput.addEventListener("change", () => { if (devInput.value && neInput) neInput.value = ""; load(); });
        neInput && neInput.addEventListener("change", () => { if (neInput.value && devInput) devInput.value = ""; load(); });
        load();
    });
})();
