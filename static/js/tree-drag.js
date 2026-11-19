/* Перетаскивание в дереве устройств /local/ (Sortable.js).
 *
 *  - устройство: менять порядок, переносить в другую категорию / подкатегорию / «Без категории»;
 *  - подкатегория: менять порядок, переносить в другую категорию (вместе с устройствами);
 *  - категория: менять порядок (тянуть за заголовок).
 *  Свёрнутая категория/подкатегория раскрывается, если задержать над ней перетаскиваемый элемент.
 *  Только с правом «Устройства: изменение» и только мышью — на телефоне долгое нажатие
 *  открывает контекстное меню (tree-menu.js), там есть «Переместить в…».
 *  Сохраняет: POST /local/devices/tree-order/ (apps/devices/views/actions.py:tree_order).
 */
(function () {
    "use strict";

    const tree = document.getElementById("devices-tree");
    if (!tree || !window.Sortable || !window.Cerber || !Cerber.can("devices")) return;
    if (window.matchMedia("(pointer: coarse)").matches) return;

    const URL = "/local/devices/tree-order/";
    const UNCATEGORIZED = "_none";

    async function save(body) {
        try {
            const d = await Cerber.fetchJSON(URL, { json: body });
            if (d && d.ok === false) throw new Error(d.error);
            return true;
        } catch (err) {
            Cerber.toast("Не сохранилось: " + (err.status === 403 ? "недостаточно прав" : err.message), "error", 5000);
            setTimeout(() => location.reload(), 1500);   // вернуть дерево к тому, что в базе
            return false;
        }
    }

    // Куда относится контейнер: {category, subcategory, label}
    function target(container) {
        const sub = container.closest(".tree-subcategory");
        const cat = container.closest(".tree-category");
        if (container.classList.contains("tree-subcategory__devices") && sub) {
            return {
                category: cat.dataset.category,
                subcategory: sub.dataset.subcategory.split("/")[1],
                label: sub.querySelector(".tree-subcategory__title > span:not([class])")?.textContent.trim(),
            };
        }
        return {
            category: cat.dataset.category, subcategory: null,
            label: cat.querySelector(".tree-category__title > span:not([class])")?.textContent.trim(),
        };
    }

    function itemsOf(container) {
        return [...container.children].map((el) => {
            if (el.matches(".tree-device[data-slug]")) return { type: "device", id: el.dataset.slug };
            if (el.matches(".tree-subcategory")) return { type: "subcategory", id: el.dataset.subcategory.split("/")[1] };
            return null;
        }).filter(Boolean);
    }

    function recount() {
        tree.querySelectorAll(".tree-subcategory").forEach((s) => {
            const c = s.querySelector(".tree-subcategory__title [data-count]");
            if (c) c.textContent = s.querySelectorAll(".tree-device").length;
        });
        tree.querySelectorAll(".tree-category").forEach((c) => {
            const n = c.querySelector(".tree-category__title [data-count]");
            if (n) n.textContent = c.querySelectorAll(".tree-device").length;
        });
    }

    async function onDrop(evt) {
        document.body.classList.remove("tree-dragging");
        stopSpring();
        const moved = evt.from !== evt.to;
        if (!moved && evt.oldIndex === evt.newIndex) return;
        const to = target(evt.to);
        const el = evt.item;
        if (el.matches(".tree-subcategory") && moved) {
            el.dataset.subcategory = `${to.category}/${el.dataset.subcategory.split("/")[1]}`;
        }
        recount();
        const ok = await save({ category: to.category, subcategory: to.subcategory, items: itemsOf(evt.to) });
        if (ok && moved) {
            const name = el.dataset.name || el.querySelector(".tree-subcategory__title > span:not([class])")?.textContent.trim();
            Cerber.toast(`«${name}» → «${to.label}»`, "success");
        }
    }

    let draggingCategory = false;   // категории двигаем свёрнутыми — не раскрывать узлы по наведению
    const common = {
        animation: 150,
        forceFallback: true,          // одинаково во всех браузерах + раскрытие свёрнутых по наведению
        fallbackTolerance: 4,         // обычный клик по устройству не превращается в перетаскивание
        fallbackOnBody: true,
        swapThreshold: 0.65,
        emptyInsertThreshold: 10,
        ghostClass: "tree-drag-ghost",
        chosenClass: "tree-drag-chosen",
        filter: "input, select, button",
        preventOnFilter: false,
        onStart: (evt) => {
            document.body.classList.add("tree-dragging");
            draggingCategory = evt.item.matches(".tree-category");
        },
        onEnd: onDrop,
    };

    // Уровень категории: устройства + подкатегории (подкатегории — не в «Без категории»)
    tree.querySelectorAll(".tree-category > .tree-devices").forEach((box) => {
        const isNone = box.closest(".tree-category").dataset.category === UNCATEGORIZED;
        Sortable.create(box, {
            ...common,
            group: {
                name: "tree",
                // put-функция отменяет проверку имени группы — тип элемента проверяем сами
                put: (to, from, dragEl) => dragEl.matches(".tree-device") || (dragEl.matches(".tree-subcategory") && !isNone),
            },
            draggable: ".tree-device, .tree-subcategory",
            handle: ".tree-device, .tree-subcategory__title",
        });
    });
    // Уровень подкатегории: только устройства
    tree.querySelectorAll(".tree-subcategory__devices").forEach((box) => {
        Sortable.create(box, {
            ...common,
            group: { name: "tree", put: (to, from, dragEl) => dragEl.matches(".tree-device") },
            draggable: ".tree-device",
        });
    });
    // Порядок категорий
    Sortable.create(tree, {
        ...common,
        group: "tree-cats",
        draggable: ".tree-category",
        handle: ".tree-category__title",
        onEnd: async (evt) => {
            document.body.classList.remove("tree-dragging");
            stopSpring();
            if (evt.oldIndex === evt.newIndex) return;
            const cats = [...tree.querySelectorAll(":scope > .tree-category")]
                .map((c) => c.dataset.category).filter((s) => s !== UNCATEGORIZED);
            if (await save({ categories: cats })) Cerber.toast("Порядок категорий сохранён", "success");
        },
    });

    // Раскрытие свёрнутого узла, если подержать над ним перетаскиваемое
    let springEl = null, springTimer = null;
    function stopSpring() { clearTimeout(springTimer); springTimer = null; springEl = null; }
    document.addEventListener("mousemove", (e) => {
        if (!document.body.classList.contains("tree-dragging") || draggingCategory) return;
        const under = document.elementFromPoint(e.clientX, e.clientY);
        const title = under && under.closest(".tree-category__title, .tree-subcategory__title");
        const node = title && title.parentElement;
        const closed = node && !node.classList.contains("is-open") ? node : null;
        if (closed === springEl) return;
        stopSpring();
        if (!closed) return;
        springEl = closed;
        springTimer = setTimeout(() => { closed.classList.add("is-open"); stopSpring(); }, 600);
    });
})();
