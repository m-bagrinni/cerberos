/**
 * Telegram WebApp: оформление и вход по initData.
 *
 * Вход выполняется ТОЛЬКО если сервер пометил страницу как «не вошёл»
 * (<body data-auth="0">). Раньше состояние угадывалось по document.cookie,
 * но sessionid — HttpOnly и из JS не виден: скрипт логинился и делал
 * location.reload() на каждой странице, а при медленной загрузке — по кругу.
 */
(function () {
    "use strict";

    const tg = window.Telegram && window.Telegram.WebApp;
    if (!tg || !tg.initData) return;          // открыто не из Telegram

    const LOOP_KEY = "tg_auth_attempts";
    const LOOP_WINDOW_MS = 60000;
    const LOOP_MAX = 2;

    function ready(fn) {
        if (document.readyState !== "loading") fn();
        else document.addEventListener("DOMContentLoaded", fn);
    }

    // ── 1. Оформление: один раз, без повторных вызовов (они дёргали экран) ──
    try { tg.ready(); } catch (e) {}
    try { if (!tg.isExpanded) tg.expand(); } catch (e) {}
    try {
        if (tg.requestFullscreen && tg.isVersionAtLeast && tg.isVersionAtLeast("8.0") && !tg.isFullscreen) {
            tg.requestFullscreen();
        }
    } catch (e) {}
    try {
        tg.setHeaderColor && tg.setHeaderColor("#0e1116");
        tg.setBackgroundColor && tg.setBackgroundColor("#0e1116");
        tg.setBottomBarColor && tg.setBottomBarColor("#0e1116");
    } catch (e) {}
    try { tg.enableClosingConfirmation && tg.enableClosingConfirmation(); } catch (e) {}

    ready(function () {
        document.body.classList.add("tg-webapp");

        // Тема Telegram → CSS-переменные
        try {
            const tp = tg.themeParams || {};
            const root = document.documentElement;
            if (tp.bg_color) root.style.setProperty("--tg-bg", tp.bg_color);
            if (tp.text_color) root.style.setProperty("--tg-text", tp.text_color);
            if (tp.hint_color) root.style.setProperty("--tg-hint", tp.hint_color);
            if (tp.button_color) root.style.setProperty("--tg-button", tp.button_color);
            root.classList.add(tg.colorScheme === "light" ? "tg-light" : "tg-dark");
        } catch (e) {}

        // Кнопка «Назад» Telegram
        try {
            if (tg.BackButton) {
                tg.BackButton.onClick(() => history.back());
                if (history.length > 1) tg.BackButton.show(); else tg.BackButton.hide();
            }
        } catch (e) {}

        // ── 2. Вход — только если сервер сказал, что сессии нет ──
        if (document.body.dataset.auth !== "0") {
            try { sessionStorage.removeItem(LOOP_KEY); } catch (e) {}
            return;
        }
        login();
    });

    function attempts() {
        try {
            const now = Date.now();
            return JSON.parse(sessionStorage.getItem(LOOP_KEY) || "[]").filter((t) => now - t < LOOP_WINDOW_MS);
        } catch (e) { return []; }
    }

    function nextUrl() {
        const next = new URLSearchParams(location.search).get("next");
        // только свои пути: «/…», но не «//host»
        return next && next.startsWith("/") && !next.startsWith("//") ? next : "/local/stations/";
    }

    function showProblem(text) {
        const box = document.createElement("div");
        box.className = "tg-auth-problem";
        box.textContent = text;
        box.style.cssText = "margin:16px;padding:12px 14px;border-radius:8px;font-size:14px;line-height:1.45;" +
            "background:rgba(245,197,66,.12);border:1px solid rgba(245,197,66,.4);color:#f5c542;";
        (document.querySelector("main, .auth-card, .auth-page") || document.body).prepend(box);
    }

    function login() {
        const tries = attempts();
        if (tries.length >= LOOP_MAX) {
            // Вход проходит, но сессия не сохраняется (веб-версия Telegram в iframe не отдаёт cookie)
            showProblem("Не удаётся сохранить вход внутри этого клиента Telegram. " +
                        "Откройте бота в приложении Telegram на телефоне или компьютере, либо войдите по логину и паролю.");
            return;
        }
        tries.push(Date.now());
        try { sessionStorage.setItem(LOOP_KEY, JSON.stringify(tries)); } catch (e) {}

        fetch("/accounts/telegram-webapp-login/", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
            body: JSON.stringify({ initData: tg.initData }),
        })
            .then((r) => {
                if (r.ok) {
                    location.replace(location.pathname.startsWith("/accounts/login") ? nextUrl() : location.href);
                } else if (r.status === 401 && !location.pathname.startsWith("/accounts/login")) {
                    location.replace("/accounts/login/?tg=not_linked");
                }
            })
            .catch(() => {});
    }
})();
