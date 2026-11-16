/* Главная: колокольчик, объявления, поиск сотрудников, карточка сотрудника. */
(function () {
    'use strict';

    const $ = (sel, root = document) => root.querySelector(sel);
    const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

    function store(key, value) {
        try {
            if (value === undefined) return JSON.parse(localStorage.getItem(key) || 'null');
            localStorage.setItem(key, JSON.stringify(value));
        } catch (e) { return null; }
    }

    // ── Колокольчик ──
    const bell = $('#notif-bell-btn');
    const pop = $('#notif-popover');
    if (bell && pop) {
        const setOpen = (open) => { pop.hidden = !open; bell.setAttribute('aria-expanded', String(open)); };
        bell.addEventListener('click', (e) => { e.stopPropagation(); setOpen(pop.hidden); });
        document.addEventListener('click', (e) => { if (!$('#notif-wrapper').contains(e.target)) setOpen(false); });
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setOpen(false); });
    }

    // ── Объявления: скрытые запоминаются в браузере (ключ = id + время правки,
    //    поэтому отредактированное объявление покажется снова) ──
    const DISMISS_KEY = 'home.dismissedAnnouncements';
    const dismissed = new Set(store(DISMISS_KEY) || []);
    $$('.hp-ann[data-dismissible]').forEach((el) => {
        const id = el.dataset.annId;
        if (dismissed.has(id)) { el.remove(); return; }
        const btn = $('.hp-ann__close', el);
        btn && btn.addEventListener('click', () => {
            dismissed.add(id);
            store(DISMISS_KEY, Array.from(dismissed).slice(-100));
            el.classList.add('is-leaving');
            setTimeout(() => {
                el.remove();
                const box = $('.hp-announcements');
                if (box && !box.children.length) box.remove();
            }, 200);
        });
    });
    const annBox = $('.hp-announcements');
    if (annBox && !annBox.children.length) annBox.remove();

    // ── Поиск сотрудников ──
    const search = $('#people-search');
    if (search) {
        const items = $$('#people-list .user-item');
        const groups = $$('#people-list .hp-people__group');
        const none = $('#people-none');
        search.addEventListener('input', () => {
            const q = search.value.trim().toLowerCase();
            items.forEach((el) => {
                const hay = (el.dataset.userName + ' ' + el.dataset.userFull + ' ' + el.dataset.userRole).toLowerCase();
                el.hidden = q && !hay.includes(q);
            });
            let any = false;
            groups.forEach((g) => {
                const visible = $$('.user-item', g).some((el) => !el.hidden);
                g.hidden = !visible;
                any = any || visible;
            });
            if (none) none.hidden = any || !groups.length;
        });
    }

    // ── Карточка сотрудника ──
    const overlay = $('#user-menu-overlay');
    let lastFocus = null;

    function openUser(el) {
        const d = el.dataset;
        $('#user-menu-name').textContent = d.userFull || d.userName;
        $('#user-menu-role').textContent = d.userRole || '';

        const av = $('#user-menu-avatar');
        av.textContent = '';
        if (d.userAvatar) {
            const img = document.createElement('img');
            img.src = d.userAvatar; img.alt = '';
            av.appendChild(img);
        } else {
            av.textContent = (d.userName || '?').charAt(0).toUpperCase();
        }

        const docBtn = $('#user-menu-doc');
        docBtn.hidden = d.self === '1';
        docBtn.href = '/edocs/create/?to=' + encodeURIComponent(d.userId);
        $('#user-menu-docs').href = '/edocs/employees/' + encodeURIComponent(d.userId) + '/';

        const tg = $('#user-menu-tg');
        const tgName = (d.userTg || '').replace(/^@/, '');
        tg.hidden = !tgName || d.self === '1';
        if (tgName) tg.href = 'https://t.me/' + encodeURIComponent(tgName);

        lastFocus = el;
        overlay.hidden = false;
        (docBtn.hidden ? $('[data-close]', overlay) : docBtn).focus();
    }

    function closeUser() {
        overlay.hidden = true;
        lastFocus && lastFocus.focus();
    }

    $$('.user-item').forEach((el) => el.addEventListener('click', () => openUser(el)));
    if (overlay) {
        overlay.addEventListener('click', (e) => { if (e.target === overlay || e.target.closest('[data-close]')) closeUser(); });
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !overlay.hidden) closeUser(); });
    }
})();
