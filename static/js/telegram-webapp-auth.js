(function () {
    "use strict";

    const tg = window.Telegram && window.Telegram.WebApp;
    if (!tg) return;

    // ── 1. Раскрыть на весь экран (несколько попыток) ──
    function expand() {
        try { tg.ready(); } catch (e) {}
        try { tg.expand(); } catch (e) {}
        try { tg.requestFullscreen && tg.requestFullscreen(); } catch (e) {}
        document.body.classList.add("tg-webapp");
    }
    expand();
    setTimeout(expand, 100);
    setTimeout(expand, 500);
    setTimeout(expand, 1500);

    // ── 2. Тёмная тема (header/background/footer) ──
    try {
        if (tg.setHeaderColor) tg.setHeaderColor("#0e1116");
        if (tg.setBackgroundColor) tg.setBackgroundColor("#0e1116");
        if (tg.setBottomBarColor) tg.setBottomBarColor("#0e1116");
    } catch (e) {}

    // ── 3. Защита от случайного закрытия ──
    try { tg.enableClosingConfirmation && tg.enableClosingConfirmation(); } catch (e) {}

    // ── 4. Кнопка «Назад» внутри Telegram ──
    try {
        if (tg.BackButton) {
            tg.BackButton.onClick(() => history.back());
            if (history.length > 1) tg.BackButton.show();
            else tg.BackButton.hide();
        }
    } catch (e) {}

    // ── 5. Тема Telegram → CSS-переменные (safe) ──
    try {
        const tp = tg.themeParams || {};
        const root = document.documentElement;
        if (tp.bg_color) root.style.setProperty("--tg-bg", tp.bg_color);
        if (tp.text_color) root.style.setProperty("--tg-text", tp.text_color);
        if (tp.hint_color) root.style.setProperty("--tg-hint", tp.hint_color);
        if (tp.button_color) root.style.setProperty("--tg-button", tp.button_color);
        if (tg.colorScheme === "light") {
            root.classList.add("tg-light");
        } else {
            root.classList.add("tg-dark");
        }
    } catch (e) {}

    // ── 6. Авторизация через initData ──
    function tryLogin(attemptsLeft) {
        // Если пользователь уже авторизован в браузере (есть sessionid), 
        // и это НЕ WebApp внутри Telegram (нет initDataUnsafe), не перелогиниваем.
        const isInsideTelegram = tg.initDataUnsafe && tg.initDataUnsafe.user;
        if (document.cookie.includes('sessionid') && !isInsideTelegram) {
            return;
        }

        if (!tg.initData) {
            if (attemptsLeft > 0) {
                setTimeout(() => tryLogin(attemptsLeft - 1), 500);
            }
            return;
        }

        const lastTry = sessionStorage.getItem("tg_auth_try");
        if (lastTry && Date.now() - parseInt(lastTry) < 3000) {
            return;
        }

        fetch("/accounts/telegram-webapp-login/", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
            body: JSON.stringify({ initData: tg.initData }),
        })
        .then((r) => {
            if (r.ok) {
                if (location.pathname.startsWith("/accounts/login")) {
                    location.replace("/local/stations/");
                } else {
                    sessionStorage.setItem("tg_auth_try", Date.now().toString());
                    location.reload();
                }
            } else if (r.status === 401) {
                if (!location.pathname.startsWith("/accounts/login")) {
                    location.replace("/accounts/login/?tg=not_linked");
                }
            }
        })
        .catch(() => {});
    }

    tryLogin(10);
})();
