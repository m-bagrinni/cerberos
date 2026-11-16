/**
 * Cerber core — общие утилиты для всех страниц.
 * Подключается в <head> до остальных скриптов и даёт window.Cerber:
 *
 *   Cerber.escapeHtml(s)          — экранирование для вставки в innerHTML
 *   Cerber.csrfToken()            — CSRF-токен из cookie
 *   Cerber.fetch(url, opts)       — fetch c same-origin cookies и X-CSRFToken для POST/PUT/PATCH/DELETE
 *   Cerber.fetchJSON(url, opts)   — то же + JSON-тело (opts.json) и разбор ответа; кидает Cerber.HttpError
 *   Cerber.poll(fn, ms, opts)     — setInterval, который не тикает в фоновой вкладке; возвращает {stop()}
 *   Cerber.toast(msg, type, ms)   — всплывающее уведомление (текст, НЕ HTML)
 *   Cerber.debounce(fn, ms)
 *
 * Для совместимости также определяет window.showToast.
 */
(function () {
    "use strict";
    if (window.Cerber) return;

    const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

    function escapeHtml(value) {
        return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);
    }

    function csrfToken() {
        const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        if (m) return decodeURIComponent(m[1]);
        const input = document.querySelector("input[name=csrfmiddlewaretoken]");
        return input ? input.value : "";
    }

    const SAFE_METHODS = /^(GET|HEAD|OPTIONS|TRACE)$/i;

    function cerberFetch(url, opts = {}) {
        const method = (opts.method || "GET").toUpperCase();
        const headers = new Headers(opts.headers || {});
        if (!SAFE_METHODS.test(method) && !headers.has("X-CSRFToken")) {
            headers.set("X-CSRFToken", csrfToken());
        }
        return fetch(url, { credentials: "same-origin", ...opts, method, headers });
    }

    class HttpError extends Error {
        constructor(status, payload) {
            super((payload && (payload.error || payload.detail)) || "HTTP " + status);
            this.status = status;
            this.payload = payload;
        }
    }

    async function fetchJSON(url, opts = {}) {
        const { json, ...rest } = opts;
        const headers = new Headers(rest.headers || {});
        headers.set("Accept", "application/json");
        if (json !== undefined) {
            headers.set("Content-Type", "application/json");
            rest.body = JSON.stringify(json);
            if (!rest.method) rest.method = "POST";
        }
        const resp = await cerberFetch(url, { ...rest, headers });
        let payload = null;
        try { payload = await resp.json(); } catch (e) { /* пустой/не-JSON ответ */ }
        if (!resp.ok) throw new HttpError(resp.status, payload);
        return payload;
    }

    /**
     * Периодический вызов fn, который приостанавливается, пока вкладка скрыта.
     * При возврате на вкладку fn вызывается сразу, затем снова по таймеру.
     * opts.immediate — вызвать fn сразу при старте.
     */
    function poll(fn, ms, opts = {}) {
        let timer = null;
        let active = true;
        let running = false;

        async function tick() {
            if (running || document.hidden) return;   // не накладываем запросы друг на друга
            running = true;
            try { await fn(); } catch (e) { console.error("[poll]", e); }
            finally { running = false; }
        }
        function start() { if (!timer) timer = setInterval(tick, ms); }
        function pause() { if (timer) { clearInterval(timer); timer = null; } }
        function onVisibility() {
            if (!active) return;
            if (document.hidden) pause();
            else { tick(); start(); }
        }

        document.addEventListener("visibilitychange", onVisibility);
        if (!document.hidden) start();
        if (opts.immediate) tick();

        return {
            stop() {
                active = false;
                pause();
                document.removeEventListener("visibilitychange", onVisibility);
            },
        };
    }

    function toastContainer() {
        let c = document.getElementById("toast-container");
        if (!c) {
            c = document.createElement("div");
            c.id = "toast-container";
            c.style.cssText = "position:fixed;bottom:20px;right:20px;z-index:100000;" +
                "display:flex;flex-direction:column;gap:10px;pointer-events:none;";
            document.body.appendChild(c);
        }
        return c;
    }

    function toast(message, type = "info", duration = 3000) {
        if (!document.body) return;
        const el = document.createElement("div");
        el.className = "cerber-toast cerber-toast--" + type;
        el.setAttribute("role", type === "error" ? "alert" : "status");
        el.textContent = message == null ? "" : String(message);
        toastContainer().appendChild(el);
        setTimeout(() => {
            el.style.transition = "opacity 0.3s ease";
            el.style.opacity = "0";
            setTimeout(() => el.remove(), 300);
        }, duration);
    }

    function debounce(fn, ms) {
        let t = null;
        return function (...args) {
            clearTimeout(t);
            t = setTimeout(() => fn.apply(this, args), ms);
        };
    }

    // Права текущего пользователя (из <body data-acc-e / data-acc-c>): Cerber.can("subnets"),
    // Cerber.can("port_control", "cap"). Только для интерфейса — сервер проверяет сам.
    function can(key, kind) {
        const b = document.body;
        if (!b) return false;
        const list = (b.getAttribute(kind === "cap" ? "data-acc-c" : "data-acc-e") || "").split(/\s+/);
        return list.indexOf(key) !== -1;
    }

    window.Cerber = {
        escapeHtml, csrfToken, fetch: cerberFetch, fetchJSON, HttpError, poll, toast, debounce, can,
    };
    window.showToast = toast;
})();
