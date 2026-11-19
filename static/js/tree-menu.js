/* Контекстное меню дерева устройств на /local/ (ПКМ, на телефоне — долгое нажатие).
 *
 *  Устройство:    опросить, опросить интерфейсы, веб-интерфейс, новая вкладка, админка,
 *                 переименовать, переместить в категорию/подкатегорию, копировать IP/имя.
 *  Категория:     создать подкатегорию, переименовать, настройки, свернуть/развернуть, удалить.
 *  Подкатегория:  переименовать, переместить в другую категорию, удалить.
 *  Пустое место:  обновить, опросить все, добавить устройство/категорию, свернуть/развернуть.
 *
 *  Пункты, меняющие данные, показываются только с правом «Устройства: изменение» (Cerber.can).
 *  Данные для подменю «Переместить…» — <script id="tree-menu-data"> (apps/devices/views/pages.py).
 */
(function () {
    "use strict";

    const tree = document.getElementById("devices-tree");
    if (!tree || !window.Cerber) return;

    const UNCATEGORIZED = "_none";
    const DATA = (() => {
        try { return JSON.parse(document.getElementById("tree-menu-data").textContent); }
        catch (e) { return { categories: [], subcategories: [] }; }
    })();
    const canEdit = () => Cerber.can("devices");
    const esc = Cerber.escapeHtml;

    // ── Запросы ─────────────────────────────────────────────────────────
    async function call(url, json, method) {
        try {
            const data = await Cerber.fetchJSON(url, { method: method || "POST", json: json || {} });
            if (data && data.ok === false) throw new Error(data.error || "ошибка");
            return data || {};
        } catch (err) {
            const msg = err.status === 403 ? "Недостаточно прав" : err.message;
            Cerber.toast("Не получилось: " + msg, "error", 5000);
            throw err;
        }
    }
    const reloadSoon = () => setTimeout(() => location.reload(), 500);

    // ── Диалог (ввод текста / подтверждение) ────────────────────────────
    function ask({ title, text = "", value = null, ok = "Сохранить", danger = false }) {
        return new Promise((resolve) => {
            const dlg = document.createElement("dialog");
            dlg.className = "tm-dialog";
            dlg.innerHTML = `
                <form method="dialog">
                    <h3 class="tm-dialog__title">${esc(title)}</h3>
                    ${text ? `<p class="tm-dialog__text">${esc(text)}</p>` : ""}
                    ${value !== null ? `<input class="tm-dialog__input" name="v" maxlength="128" autocomplete="off">` : ""}
                    <div class="tm-dialog__actions">
                        <button value="cancel" class="tm-btn">Отмена</button>
                        <button value="ok" class="tm-btn ${danger ? "tm-btn--danger" : "tm-btn--primary"}">${esc(ok)}</button>
                    </div>
                </form>`;
            document.body.appendChild(dlg);
            const input = dlg.querySelector("input");
            if (input) input.value = value;
            dlg.addEventListener("close", () => {
                const res = dlg.returnValue === "ok" ? (input ? input.value.trim() : true) : null;
                dlg.remove();
                resolve(res === "" ? null : res);
            });
            dlg.showModal();
            if (input) { input.focus(); input.select(); }
        });
    }

    // ── Меню ────────────────────────────────────────────────────────────
    const menu = document.createElement("div");
    menu.className = "tm-menu";
    menu.setAttribute("role", "menu");
    menu.hidden = true;
    document.body.appendChild(menu);
    let actions = [];

    function render(items) {
        actions = [];
        const html = (list) => list.filter(Boolean).map((it) => {
            if (it === "-") return '<div class="tm-sep" role="separator"></div>';
            if (it.header) return `<div class="tm-head">${esc(it.header)}</div>`;
            const i = actions.push(it) - 1;
            const icon = `<i class="bi ${esc(it.icon || "bi-dot")}"></i>`;
            if (it.sub) {
                const inner = it.sub.length ? html(it.sub) : '<div class="tm-empty">Пусто</div>';
                return `<div class="tm-item tm-item--sub" role="menuitem" tabindex="-1" data-i="${i}">${icon}<span>${esc(it.label)}</span><i class="bi bi-chevron-right tm-arrow"></i><div class="tm-submenu" role="menu">${inner}</div></div>`;
            }
            return `<div class="tm-item${it.danger ? " tm-item--danger" : ""}${it.current ? " is-current" : ""}" role="menuitem" tabindex="-1" data-i="${i}">${icon}<span>${esc(it.label)}</span>${it.hint ? `<small>${esc(it.hint)}</small>` : ""}</div>`;
        }).join("");
        // убрать разделители по краям и двойные (когда пункты скрыты по правам)
        const clean = items.filter(Boolean).filter((it, k, arr) => it !== "-" || (k > 0 && k < arr.length - 1 && arr[k - 1] !== "-"));
        menu.innerHTML = html(clean);
    }

    function openAt(items, x, y) {
        render(items);
        menu.hidden = false;
        menu.style.left = "0px"; menu.style.top = "0px";
        const r = menu.getBoundingClientRect();
        const left = Math.min(x, window.innerWidth - r.width - 8);
        const top = Math.min(y, window.innerHeight - r.height - 8);
        menu.style.left = Math.max(8, left) + "px";
        menu.style.top = Math.max(8, top) + "px";
        menu.classList.toggle("tm-menu--flip", left + r.width + 220 > window.innerWidth);
        const first = menu.querySelector(".tm-item");
        if (first) first.focus({ preventScroll: true });
    }
    function close() { menu.hidden = true; menu.innerHTML = ""; }

    menu.addEventListener("click", (e) => {
        const el = e.target.closest(".tm-item");
        if (!el) return;
        const it = actions[+el.dataset.i];
        if (!it || it.sub) { if (it && it.sub) el.classList.toggle("is-open"); return; }
        close();
        if (it.run) it.run();
    });
    document.addEventListener("click", (e) => { if (!menu.contains(e.target)) close(); });
    document.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    document.addEventListener("keydown", (e) => {
        if (menu.hidden) return;
        const items = [...menu.querySelectorAll(":scope > .tm-item")];
        const k = items.indexOf(document.activeElement);
        if (e.key === "Escape") { close(); }
        else if (e.key === "ArrowDown") { e.preventDefault(); (items[k + 1] || items[0]).focus(); }
        else if (e.key === "ArrowUp") { e.preventDefault(); (items[k - 1] || items[items.length - 1]).focus(); }
        else if (e.key === "Enter" && k >= 0) { e.preventDefault(); items[k].click(); }
    });

    // ── Наборы пунктов ──────────────────────────────────────────────────
    function deviceItems(el) {
        const ip = el.dataset.slug, name = el.dataset.name;
        const catSlug = el.closest(".tree-category")?.dataset.category;
        const subEl = el.closest(".tree-subcategory");
        const subSlug = subEl ? subEl.dataset.subcategory.split("/")[1] : null;
        const edit = canEdit();
        const apiIp = encodeURIComponent(ip);
        return [
            { header: name },
            edit && { icon: "bi-arrow-repeat", label: "Опросить устройство", run: async () => {
                await call(`/api/v1/devices/${apiIp}/poll-now/`); Cerber.toast(`Опрос ${name} запущен`, "success");
            } },
            edit && { icon: "bi-ethernet", label: "Опросить интерфейсы", run: async () => {
                await call(`/api/v1/devices/${apiIp}/poll-interfaces-now/`); Cerber.toast(`Опрос интерфейсов ${name} запущен`, "success");
            } },
            "-",
            { icon: "bi-globe2", label: "Открыть веб-интерфейс", hint: ip, run: () => window.open(`http://${ip}/`, "_blank", "noopener") },
            { icon: "bi-window-plus", label: "Открыть в новой вкладке", run: () => window.open(`/local/#${ip}`, "_blank") },
            edit && { icon: "bi-shield-lock", label: "Настройки в админ-панели", run: () => window.open(`/admin-ui/devices/?q=${apiIp}`, "_blank") },
            "-",
            edit && { icon: "bi-pencil", label: "Переименовать…", run: async () => {
                const v = await ask({ title: "Переименовать устройство", text: ip, value: name });
                if (!v || v === name) return;
                await call(`/api/v1/devices/${apiIp}/`, { name: v }, "PATCH");
                el.dataset.name = v;
                const n = el.querySelector(".tree-device__name"); if (n) n.textContent = v;
                Cerber.toast("Переименовано", "success");
            } },
            edit && { icon: "bi-folder-symlink", label: "Переместить в категорию", sub: DATA.categories.map((c) => ({
                icon: c.icon, label: c.name, current: c.slug === catSlug && !subSlug,
                run: async () => { await call("/local/devices/move-category/", { ip, category_slug: c.slug }); reloadSoon(); },
            })) },
            edit && { icon: "bi-folder2-open", label: "Переместить в подкатегорию", sub: DATA.categories.flatMap((c) => {
                const subs = DATA.subcategories.filter((s) => s.category === c.slug);
                return subs.length ? [{ header: c.name }, ...subs.map((s) => ({
                    icon: "bi-folder", label: s.name, current: s.slug === subSlug,
                    run: async () => { await call("/local/devices/move-subcategory/", { ip, subcategory_slug: s.slug }); reloadSoon(); },
                }))] : [];
            }) },
            "-",
            { icon: "bi-clipboard", label: "Копировать IP", run: () => copy(ip) },
            { icon: "bi-clipboard-check", label: "Копировать имя", run: () => copy(name) },
        ];
    }

    function toggleAll(open) {
        tree.querySelectorAll(".tree-category, .tree-subcategory").forEach((x) => x.classList.toggle("is-open", open));
    }
    const foldItems = () => [
        { icon: "bi-arrows-expand", label: "Развернуть всё", run: () => toggleAll(true) },
        { icon: "bi-arrows-collapse", label: "Свернуть всё", run: () => toggleAll(false) },
    ];

    function categoryItems(catEl) {
        const slug = catEl.dataset.category;
        const label = catEl.querySelector(".tree-category__title > span:not([class])")?.textContent.trim() || slug;
        const edit = canEdit() && slug !== UNCATEGORIZED;
        return [
            { header: label },
            edit && { icon: "bi-folder-plus", label: "Создать подкатегорию…", run: async () => {
                const v = await ask({ title: "Новая подкатегория", text: `В категории «${label}»`, value: "", ok: "Создать" });
                if (!v) return;
                await call("/admin-ui/subcategories/create-api/", { category_slug: slug, name: v });
                Cerber.toast("Подкатегория создана", "success"); reloadSoon();
            } },
            edit && { icon: "bi-pencil", label: "Переименовать…", run: async () => {
                const v = await ask({ title: "Переименовать категорию", value: label });
                if (!v || v === label) return;
                await call("/admin-ui/categories/rename-api/", { slug, name: v });
                const s = catEl.querySelector(".tree-category__title > span:not([class])"); if (s) s.textContent = v;
                Cerber.toast("Переименовано", "success");
            } },
            edit && { icon: "bi-gear", label: "Настройки категории", run: () => window.open("/admin-ui/categories/", "_blank") },
            "-",
            ...foldItems(),
            "-",
            edit && { icon: "bi-trash3", label: "Удалить категорию…", danger: true, run: async () => {
                const ok = await ask({ title: `Удалить «${label}»?`, text: "Устройства не удалятся — они перейдут в группу «Без категории».", ok: "Удалить", danger: true });
                if (!ok) return;
                await call("/admin-ui/categories/delete-api/", { slug });
                Cerber.toast("Категория удалена", "success"); reloadSoon();
            } },
        ];
    }

    function subcategoryItems(subEl) {
        const [catSlug, slug] = subEl.dataset.subcategory.split("/");
        const label = subEl.querySelector(".tree-subcategory__title > span:not([class])")?.textContent.trim() || slug;
        const edit = canEdit();
        return [
            { header: label },
            edit && { icon: "bi-pencil", label: "Переименовать…", run: async () => {
                const v = await ask({ title: "Переименовать подкатегорию", value: label });
                if (!v || v === label) return;
                await call("/admin-ui/subcategories/rename-api/", { slug, name: v });
                const s = subEl.querySelector(".tree-subcategory__title > span:not([class])"); if (s) s.textContent = v;
                Cerber.toast("Переименовано", "success");
            } },
            edit && { icon: "bi-folder-symlink", label: "Переместить в категорию", sub: DATA.categories.map((c) => ({
                icon: c.icon, label: c.name, current: c.slug === catSlug,
                run: async () => { await call("/local/subcategories/move-category/", { slug, category_slug: c.slug }); reloadSoon(); },
            })) },
            "-",
            ...foldItems(),
            "-",
            edit && { icon: "bi-trash3", label: "Удалить подкатегорию…", danger: true, run: async () => {
                const ok = await ask({ title: `Удалить «${label}»?`, text: "Устройства останутся в категории, просто без подкатегории.", ok: "Удалить", danger: true });
                if (!ok) return;
                await call("/admin-ui/subcategories/delete-api/", { slug });
                Cerber.toast("Подкатегория удалена", "success"); reloadSoon();
            } },
        ];
    }

    function backgroundItems() {
        const edit = canEdit();
        return [
            { icon: "bi-arrow-clockwise", label: "Обновить", run: () => location.reload() },
            edit && { icon: "bi-lightning-charge", label: "Опросить все устройства", run: async () => {
                const ok = await ask({ title: "Опросить все устройства?", text: "Опрос запустится в фоне, это может занять несколько минут.", ok: "Запустить" });
                if (!ok) return;
                await call("/api/v1/devices/poll-all-now/"); Cerber.toast("Опрос всех устройств запущен", "success");
            } },
            "-",
            edit && { icon: "bi-plus-square", label: "Добавить устройство", run: () => { location.href = "/admin-ui/devices/new/"; } },
            edit && { icon: "bi-folder-plus", label: "Добавить категорию", run: () => { location.href = "/admin-ui/categories/new/"; } },
            "-",
            ...foldItems(),
        ];
    }

    function copy(text) {
        const done = () => Cerber.toast("Скопировано: " + text, "success");
        if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done);
        else {   // http без TLS: старый способ
            const t = document.createElement("textarea"); t.value = text; document.body.appendChild(t);
            t.select(); try { document.execCommand("copy"); done(); } finally { t.remove(); }
        }
    }

    function itemsFor(target) {
        const dev = target.closest(".tree-device[data-slug]");
        if (dev) return deviceItems(dev);
        const subTitle = target.closest(".tree-subcategory__title");
        if (subTitle) return subcategoryItems(subTitle.closest(".tree-subcategory"));
        const catTitle = target.closest(".tree-category__title");
        if (catTitle) return categoryItems(catTitle.closest(".tree-category"));
        if (target.closest("input, select, textarea, .tree-toolbar")) return null;   // в поле поиска — обычное меню
        return backgroundItems();
    }

    // ПКМ
    tree.addEventListener("contextmenu", (e) => {
        const items = itemsFor(e.target);
        if (!items) return;
        e.preventDefault();
        openAt(items, e.clientX, e.clientY);
    });

    // Долгое нажатие (телефон, Telegram WebApp)
    let timer = null, longPressed = false;
    tree.addEventListener("touchstart", (e) => {
        longPressed = false;
        const t = e.touches[0], target = e.target;
        clearTimeout(timer);
        timer = setTimeout(() => {
            const items = itemsFor(target);
            if (!items) return;
            longPressed = true;
            if (navigator.vibrate) navigator.vibrate(30);
            openAt(items, t.clientX, t.clientY);
        }, 500);
    }, { passive: true });
    ["touchend", "touchmove", "touchcancel"].forEach((ev) => tree.addEventListener(ev, () => clearTimeout(timer), { passive: true }));
    // после долгого нажатия не «кликать» по устройству
    tree.addEventListener("click", (e) => { if (longPressed) { e.stopPropagation(); e.preventDefault(); longPressed = false; } }, true);
})();
