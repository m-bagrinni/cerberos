/* Профиль: отвязка Telegram, загрузка аватара, выбор станций. */
(function () {
    "use strict";

    // ── Отвязка Telegram: POST на telegram_unlink, затем перезагрузка ──
    const card = document.getElementById("tg-card");
    const btn = document.getElementById("tg-unlink-btn");
    if (card && btn) {
        btn.addEventListener("click", async function () {
            const ok = window.CerberConfirm
                ? await window.CerberConfirm("Отвязать Telegram от аккаунта?")
                : window.confirm("Отвязать Telegram от аккаунта?");
            if (!ok) return;
            btn.disabled = true;
            try {
                const r = await fetch(card.dataset.unlinkUrl, {
                    method: "POST",
                    headers: { "X-CSRFToken": card.dataset.csrfToken, "Accept": "application/json" },
                    credentials: "same-origin",
                });
                if (!r.ok) throw new Error("HTTP " + r.status);
                window.location.reload();
            } catch (e) {
                btn.disabled = false;
                alert("Не удалось отвязать: " + e.message);
            }
        });
    }

    // ── Аватар: отправка сразу после выбора файла ──
    const avatarInput = document.getElementById("avatar-input");
    if (avatarInput) {
        avatarInput.addEventListener("change", function () {
            if (!avatarInput.files.length) return;
            if (avatarInput.files[0].size > 3 * 1024 * 1024) {
                alert("Файл больше 3 МБ.");
                avatarInput.value = "";
                return;
            }
            avatarInput.form.submit();
        });
    }

    // ── Выбор станций: поиск, счётчик, сброс ──
    document.querySelectorAll("[data-picker]").forEach(function (picker) {
        const search = picker.querySelector("[data-picker-search]");
        const count = picker.querySelector("[data-picker-count]");
        const items = Array.from(picker.querySelectorAll(".pf-picker__item"));
        const boxes = items.map(function (i) { return i.querySelector("input"); });
        function recount() { count.textContent = boxes.filter(function (b) { return b.checked; }).length; }
        boxes.forEach(function (b) { b.addEventListener("change", recount); });
        search && search.addEventListener("input", function () {
            const q = search.value.trim().toLowerCase();
            items.forEach(function (i) { i.hidden = q && !i.dataset.name.includes(q); });
        });
        picker.querySelector("[data-picker-clear]")?.addEventListener("click", function () {
            boxes.forEach(function (b) { b.checked = false; });
            recount();
        });
    });
})();
