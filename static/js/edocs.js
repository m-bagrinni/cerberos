/* СЭД (/edocs/): списки, карточка документа, форма создания.
   Без зависимостей; всё навешивается по data-атрибутам из шаблонов apps/edocs/templates/edocs/. */
(function () {
    "use strict";
    const $ = (s, r) => (r || document).querySelector(s);
    const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
    let submitting = false;

    function csrf() {
        const i = $("[name=csrfmiddlewaretoken]");
        if (i) return i.value;
        const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : "";
    }

    function toast(text, bad) {
        let t = $(".ed-toast");
        if (!t) {
            t = document.createElement("div");
            t.className = "ed-live ed-toast";
            t.setAttribute("role", "status");
            document.body.appendChild(t);
        }
        t.textContent = text;
        t.style.borderColor = bad ? "var(--crit, #ff5d6c)" : "";
        t.hidden = false;
        clearTimeout(t._h);
        t._h = setTimeout(() => { t.hidden = true; }, 3500);
    }

    // ── Списки: клик по строке открывает документ ──
    document.addEventListener("click", (e) => {
        const tr = e.target.closest("tr[data-href]");
        if (!tr || e.target.closest("a, button, input, label, select")) return;
        if (e.ctrlKey || e.metaKey || e.button === 1) window.open(tr.dataset.href, "_blank");
        else location.href = tr.dataset.href;
    });

    // ── Фильтры: применяются сразу ──
    $$("[data-ed-autosubmit]").forEach((form) => {
        form.addEventListener("change", (e) => {
            if (e.target.type !== "search") form.requestSubmit();
        });
        const q = $("input[type=search]", form);
        if (q) {
            let h;
            q.addEventListener("input", () => {
                clearTimeout(h);
                h = setTimeout(() => form.requestSubmit(), 500);
            });
            // курсор в конец поля после перезагрузки
            if (q.value && document.activeElement === document.body && new URLSearchParams(location.search).has("q")) {
                q.focus();
                q.setSelectionRange(q.value.length, q.value.length);
            }
        }
    });

    // ── Диалоги ──
    document.addEventListener("click", (e) => {
        const open = e.target.closest("[data-ed-open]");
        if (open) {
            const d = document.getElementById(open.dataset.edOpen);
            if (d && d.showModal) {
                d.showModal();
                const first = $("textarea, select, input:not([type=hidden])", d);
                if (first) first.focus();
            }
            return;
        }
        if (e.target.closest("[data-ed-close]")) {
            const d = e.target.closest("dialog");
            if (d) d.close();
            return;
        }
        if (e.target.tagName === "DIALOG") e.target.close();  // клик по подложке
    });

    // ── Отправка форм: подтверждение и защита от двойного нажатия ──
    document.addEventListener("submit", (e) => {
        const form = e.target;
        if (form.matches("[data-ed-autosubmit]") || form.method.toLowerCase() === "get") return;
        if (form.dataset.edConfirm && !confirm(form.dataset.edConfirm)) {
            e.preventDefault();
            return;
        }
        if (form.dataset.busy) {
            e.preventDefault();
            return;
        }
        form.dataset.busy = "1";
        submitting = true;
        const btn = e.submitter;
        // имя/значение нажатой кнопки сохраняются (e.submitter), блокируем после отправки
        setTimeout(() => {
            $$("button[type=submit]", form).forEach((b) => { b.disabled = true; });
            if (btn && btn.form !== form) btn.disabled = true;
        }, 0);
    });

    // ── Карточка: просмотр файлов ──
    const viewer = $("[data-ed-viewer]");
    function preview(el) {
        if (!viewer) { window.open(el.dataset.edPreview, "_blank"); return; }
        $$("[data-ed-preview]").forEach((x) => x.classList.toggle("is-active", x === el));
        viewer.src = el.dataset.edPreview;
    }
    $$("[data-ed-preview]").forEach((el) => {
        el.addEventListener("click", (e) => { if (!e.target.closest("a")) preview(el); });
        el.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); preview(el); }
        });
    });

    // ── Карточка: Telegram, ссылка ──
    $$("[data-ed-tg]").forEach((b) => b.addEventListener("click", async () => {
        b.disabled = true;
        try {
            const r = await fetch(b.dataset.edTg, {
                method: "POST", headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" },
            });
            const d = await r.json().catch(() => ({}));
            toast(d.ok ? (d.message || "Отправлено в Telegram") : (d.error || "Не удалось отправить"), !d.ok);
        } catch (err) {
            toast("Нет связи с сервером", true);
        } finally {
            b.disabled = false;
        }
    }));
    $$("[data-ed-copy]").forEach((b) => b.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(b.dataset.edCopy); toast("Ссылка скопирована"); }
        catch (err) { prompt("Ссылка на документ:", b.dataset.edCopy); }
    }));

    // ── Карточка: хронология и комментарий ──
    const tl = $("[data-ed-timeline]");
    if (tl) tl.scrollTop = tl.scrollHeight;
    $$("[data-ed-composer] textarea").forEach((ta) => {
        const fit = () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight + 2, 160) + "px"; };
        ta.addEventListener("input", fit);
        ta.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); ta.form.requestSubmit(); }
        });
    });

    // ── Карточка: живое обновление (кто-то другой изменил документ) ──
    const docEl = $("[data-ed-doc]");
    if (docEl && "WebSocket" in window) {
        const live = $("[data-ed-live]");
        let tries = 0;
        const connect = () => {
            const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/edocs/document/${docEl.dataset.edDoc}/`);
            ws.onopen = () => { tries = 0; };
            ws.onmessage = (m) => {
                let d = {};
                try { d = JSON.parse(m.data); } catch (err) { return; }
                if (d.type === "doc_changed" && !submitting && live) live.hidden = false;
            };
            ws.onclose = (e) => {
                if (e.code === 1000 || tries > 5) return;
                tries += 1;
                setTimeout(connect, 2000 * tries);
            };
        };
        connect();
    }

    // ── Форма создания ──
    const form = $("#ed-doc-form");
    if (form) {
        // срок: быстрые кнопки
        const due = $("[name=due_date]", form);
        $$("[data-ed-due-quick] button", form).forEach((b) => b.addEventListener("click", () => {
            if (!b.dataset.days) { due.value = ""; return; }
            const d = new Date();
            d.setDate(d.getDate() + Number(b.dataset.days));
            due.value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
        }));

        // адресаты: ответственный не может быть соисполнителем
        const main = $("[name=main]", form);
        const coCount = $("[data-ed-co-count]", form);
        const syncPicker = () => {
            let n = 0;
            $$(".ed-pick", form).forEach((p) => {
                const cb = $("input", p);
                const isMain = p.dataset.id === main.value;
                p.classList.toggle("is-main", isMain);
                if (isMain) cb.checked = false;
                if (cb.checked) n += 1;
            });
            if (coCount) coCount.textContent = n ? `· выбрано ${n}` : "";
        };
        main.addEventListener("change", syncPicker);
        form.addEventListener("change", (e) => { if (e.target.name === "co") syncPicker(); });
        syncPicker();
        const search = $("[data-ed-picker-search]", form);
        if (search) search.addEventListener("input", () => {
            const q = search.value.trim().toLowerCase();
            $$(".ed-pick", form).forEach((p) => { p.hidden = q && !p.dataset.name.includes(q); });
        });
        if (search) search.addEventListener("keydown", (e) => { if (e.key === "Enter") e.preventDefault(); });

        // файлы: перетаскивание и накопление нескольких выборов
        const drop = $("[data-ed-drop]", form);
        const input = drop && $("input[type=file]", drop);
        const list = $("[data-ed-upl]", form);
        const store = new DataTransfer();
        const fmt = (n) => n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} КБ` : `${(n / 1048576).toFixed(1)} МБ`;
        const render = () => {
            list.innerHTML = "";
            Array.from(store.files).forEach((f, i) => {
                const row = document.createElement("div");
                row.className = "ed-upl__item";
                row.innerHTML = '<i class="bi bi-file-earmark ed-file__icon"></i><span class="ed-file__name"></span>'
                    + '<span class="ed-file__size"></span><button type="button" class="ed-btn ed-btn--sm ed-btn--ghost ed-btn--icon" aria-label="Убрать"><i class="bi bi-x"></i></button>';
                row.querySelector(".ed-file__name").textContent = f.name;
                row.querySelector(".ed-file__size").textContent = fmt(f.size);
                row.querySelector("button").addEventListener("click", () => {
                    const next = new DataTransfer();
                    Array.from(store.files).forEach((x, j) => { if (j !== i) next.items.add(x); });
                    store.items.clear();
                    Array.from(next.files).forEach((x) => store.items.add(x));
                    input.files = store.files;
                    render();
                });
                list.appendChild(row);
            });
        };
        const add = (files) => {
            Array.from(files).forEach((f) => {
                if (!Array.from(store.files).some((x) => x.name === f.name && x.size === f.size)) store.items.add(f);
            });
            input.files = store.files;
            render();
        };
        if (input) {
            input.addEventListener("change", () => { if (input.files !== store.files) add(input.files); });
            ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("is-over"); }));
            ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, () => drop.classList.remove("is-over")));
            drop.addEventListener("drop", (e) => { e.preventDefault(); add(e.dataTransfer.files); });
        }
        $$("[data-ed-remove] input", form).forEach((cb) => cb.addEventListener("change", () => {
            cb.closest("[data-ed-remove]").classList.toggle("is-removed", cb.checked);
        }));

        // отправка: понятные ошибки до запроса (черновик сохраняется без проверок)
        form.addEventListener("submit", (e) => {
            if (e.submitter && e.submitter.value === "draft") {
                if (!$("[name=title]", form).value.trim()) {
                    e.preventDefault(); e.stopImmediatePropagation();
                    toast("Для черновика укажите хотя бы тему", true);
                    delete form.dataset.busy;
                }
                return;
            }
            const err = !$("[name=title]", form).value.trim() ? "Укажите тему документа"
                : !main.value ? "Выберите ответственного" : "";
            if (err) {
                e.preventDefault(); e.stopImmediatePropagation();
                toast(err, true);
                ($("[name=title]", form).value.trim() ? main : $("[name=title]", form)).focus();
            }
        }, true);
    }
})();
