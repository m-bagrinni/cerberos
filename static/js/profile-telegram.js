(function () {
    "use strict";
    // Обработка кнопки "Отвязать"
    const $unlink = document.getElementById("tg-unlink-btn");
    if ($unlink) {
        $unlink.addEventListener("click", async function (e) {
            if (!(await CerberConfirm("Отвязать Telegram от аккаунта?"))) return;
            // В данном простом варианте оставим только генерацию через форму, 
            // для отвязки можно сделать аналогичную форму, но пока оставим так.
        });
    }
})();
