/**
 * Захват трафика на странице подсети.
 *
 * Панель (#cap-panel) приходит в разметке подсети, в том числе AJAX-ом в список
 * подсетей, поэтому всё — через делегирование и ленивую инициализацию при открытии.
 *
 * Сервер: /api/v1/subnets/tcpdump/
 *   ifaces/ · iface-for/?cidr= · validate/ · live/ · live/<sid>/?cursor= · live/<sid>/stop/ · pcap/
 */
(function () {
    "use strict";
    if (window.__cerberCapture) return;
    window.__cerberCapture = true;

    const API = "/api/v1/subnets/tcpdump/";
    const ROW_H = 26;                 // высота строки таблицы (px) — синхронно с capture.css
    const MAX_KEEP = 100000;          // пакетов в памяти браузера
    const POLL_MS = 700;
    const esc = (s) => Cerber.escapeHtml(s);

    const STOP_REASONS = {
        user: "Остановлен",
        timeout: "Время вышло",
        limit: "Достигнут лимит пакетов",
        client_gone: "Остановлен: страница не опрашивала сервер",
        lost: "Связь с захватом потеряна (перезапуск сервера?)",
        ended: "tcpdump завершился",
    };

    // Пресеты фильтра захвата. {cidr} подставляется из подсети.
    const PRESETS = [
        { label: "Эта подсеть", bpf: "net {cidr}", title: "Только трафик с адресами этой подсети" },
        { label: "ARP", bpf: "arp" },
        { label: "ICMP", bpf: "icmp or icmp6", title: "ping, unreachable, TTL exceeded" },
        { label: "DHCP", bpf: "udp port 67 or udp port 68" },
        { label: "DNS", bpf: "port 53" },
        { label: "SNMP", bpf: "udp port 161 or udp port 162", title: "Опросы и трапы" },
        { label: "Syslog", bpf: "udp port 514" },
        { label: "NTP", bpf: "udp port 123" },
        { label: "STP / LLDP / CDP", bpf: "stp or ether proto 0x88cc or ether dst 01:00:0c:cc:cc:cc", title: "Служебные L2-протоколы коммутаторов" },
        { label: "Broadcast", bpf: "ether broadcast and not arp", title: "Широковещательный трафик кроме ARP" },
        { label: "Multicast", bpf: "ether multicast and not ether broadcast" },
        { label: "TCP SYN", bpf: "tcp[tcpflags] & (tcp-syn|tcp-ack) = tcp-syn", title: "Новые TCP-соединения" },
        { label: "TCP RST", bpf: "tcp[tcpflags] & tcp-rst != 0", title: "Сброшенные соединения" },
        { label: "ICMP unreachable", bpf: "icmp[icmptype] = icmp-unreach" },
        { label: "SCADA", bpf: "tcp port 502 or tcp port 2404 or tcp port 20000", title: "Modbus/TCP, IEC 60870-5-104, DNP3" },
    ];

    const DURATIONS = [[60, "1 мин"], [300, "5 мин"], [900, "15 мин"], [1800, "30 мин"]];
    const LIMITS = [[0, "без лимита"], [100, "100"], [1000, "1 000"], [10000, "10 000"], [100000, "100 000"]];

    // ── состояние ────────────────────────────────────────────────────────────
    const S = {
        root: null, cidr: "", ifaces: [],
        sid: null, cursor: 0, status: "idle", pollTimer: null, polling: false,
        packets: [], view: [], selected: null, lastTsUs: null,
        filter: { text: "", tokens: [] }, hiddenCats: new Set(),
        autoscroll: true, frozen: false,
        stats: null, rate: { t: 0, count: 0, bytes: 0, pps: 0, bps: 0 },
        meta: null, bpfValid: true, validateSeq: 0, renderQueued: false, sideQueued: false,
        labels: new Map(), labelPending: new Set(), whoCache: new Map(), whoTarget: null,
    };

    function resetStats() {
        S.stats = { cats: new Map(), hosts: new Map(), pairs: new Map(), senders: new Map(), count: 0, bytes: 0 };
    }
    resetStats();

    const $ = (id) => document.getElementById(id);

    // ── форматирование ───────────────────────────────────────────────────────
    function fmtBytes(n) {
        if (n == null) return "—";
        const u = ["Б", "КБ", "МБ", "ГБ"];
        let i = 0;
        while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
        return (i ? n.toFixed(n < 10 ? 1 : 0) : n) + " " + u[i];
    }
    function fmtBits(bps) {
        const u = ["бит/с", "Кбит/с", "Мбит/с", "Гбит/с"];
        let i = 0;
        while (bps >= 1000 && i < u.length - 1) { bps /= 1000; i++; }
        return bps.toFixed(bps < 10 && i ? 1 : 0) + " " + u[i];
    }
    function fmtNum(n) { return (n || 0).toLocaleString("ru-RU"); }
    function fmtDur(sec) {
        sec = Math.max(0, Math.floor(sec));
        const m = Math.floor(sec / 60), s = sec % 60;
        return `${m}:${String(s).padStart(2, "0")}`;
    }
    function tsUs(ts) {
        // "2026-11-17 15:57:43.159988" → микросекунды
        const [d, t] = ts.split(" ");
        const [hms, frac = "0"] = t.split(".");
        const ms = Date.parse(`${d}T${hms}`);
        return ms * 1000 + parseInt((frac + "000000").slice(0, 6), 10);
    }
    function fmtTime(ts) {
        const t = (ts || "").split(" ")[1] || "";
        const [hms, frac = ""] = t.split(".");
        return `${hms}.${frac.slice(0, 3)}`;
    }
    function fmtDelta(us) {
        if (us == null) return "";
        if (us < 1000) return `${us} мкс`;
        if (us < 1e6) return `${(us / 1000).toFixed(us < 1e4 ? 2 : 1)} мс`;
        return `${(us / 1e6).toFixed(2)} с`;
    }
    function endpoint(addr, port, mac) {
        if (addr) {
            const a = addr.includes(":") && port != null ? `[${addr}]` : addr;
            return port != null ? `${a}:${port}` : a;
        }
        return mac || "—";
    }

    // ── фильтр отображения ───────────────────────────────────────────────────
    // Токены через пробел, все должны совпасть (И). Префикс ! — отрицание.
    //   ip:10.0.0.1  port:161  proto:snmp  mac:aa:bb  vlan:50  iface:enp4s0  len>100  текст
    function parseDisplayFilter(text) {
        const tokens = [];
        for (const raw of text.trim().split(/\s+/).filter(Boolean)) {
            let neg = false, t = raw;
            if (t.startsWith("!") || t.startsWith("-")) { neg = true; t = t.slice(1); }
            if (!t) continue;
            let m = t.match(/^len([<>]=?|=)(\d+)$/i);
            if (m) { tokens.push({ neg, kind: "len", op: m[1], v: +m[2] }); continue; }
            m = t.match(/^(ip|host|port|proto|mac|vlan|iface):(.+)$/i);
            if (m) { tokens.push({ neg, kind: m[1].toLowerCase() === "host" ? "ip" : m[1].toLowerCase(), v: m[2].toLowerCase() }); continue; }
            tokens.push({ neg, kind: "text", v: t.toLowerCase() });
        }
        return tokens;
    }
    function tokenMatch(p, tk) {
        switch (tk.kind) {
            case "ip": return p.src === tk.v || p.dst === tk.v;
            case "port": return String(p.sport) === tk.v || String(p.dport) === tk.v;
            case "proto": return (p.proto || "").toLowerCase() === tk.v || (p.l4 || "").toLowerCase() === tk.v || p.category === tk.v;
            case "mac": return (p.mac_src || "").includes(tk.v) || (p.mac_dst || "").includes(tk.v);
            case "vlan": return String(p.vlan) === tk.v;
            case "iface": return (p.iface || "") === tk.v;
            case "len": {
                const L = p.length || 0;
                return tk.op === ">" ? L > tk.v : tk.op === "<" ? L < tk.v : tk.op === ">=" ? L >= tk.v : tk.op === "<=" ? L <= tk.v : L === tk.v;
            }
            default: {
                if (p._hay === undefined) {
                    p._hay = [p.src, p.dst, p.sport, p.dport, p.proto, p.info, p.mac_src, p.mac_dst, p.vlan, p.iface]
                        .filter((x) => x != null && x !== "").join(" ").toLowerCase();
                }
                return p._hay.includes(tk.v);
            }
        }
    }
    function matches(p) {
        if (S.hiddenCats.has(catOf(p))) return false;
        for (const tk of S.filter.tokens) {
            if (tokenMatch(p, tk) === tk.neg) return false;
        }
        return true;
    }
    function catOf(p) { return p.category || "other"; }
    function isMac(s) { return /^[0-9a-f]{2}(:[0-9a-f]{2}){5}$/i.test(s || ""); }
    function isIp(s) { return !!s && !isMac(s) && (/^\d+\.\d+\.\d+\.\d+$/.test(s) || s.includes(":")); }

    // ── данные ───────────────────────────────────────────────────────────────
    function ingest(list) {
        if (!list.length) return;
        for (const p of list) {
            const us = tsUs(p.timestamp);
            p._us = us;
            p._delta = S.lastTsUs == null ? null : Math.max(0, us - S.lastTsUs);
            S.lastTsUs = us;
            S.packets.push(p);
            accountStats(p);
            if (matches(p)) S.view.push(S.packets.length - 1);
        }
        if (S.packets.length > MAX_KEEP) {
            const drop = Math.floor(MAX_KEEP / 10);
            S.packets.splice(0, drop);
            if (S.selected != null) S.selected = S.selected >= drop ? S.selected - drop : null;
            rebuildView();
        }
        queueRender();
        queueSide();
    }
    function accountStats(p) {
        const st = S.stats;
        const len = p.length || 0;
        st.count++; st.bytes += len;
        const c = catOf(p);
        const label = p.proto && p.proto !== "?" ? p.proto : c.toUpperCase();
        const ce = st.cats.get(label) || { n: 0, b: 0, cat: c };
        ce.n++; ce.b += len; st.cats.set(label, ce);
        for (const h of [p.src || p.mac_src, p.dst || p.mac_dst]) {
            if (!h) continue;
            const he = st.hosts.get(h) || { n: 0, b: 0 };
            he.n++; he.b += len; st.hosts.set(h, he);
        }
        if (p.mac_src) {
            const se = st.senders.get(p.mac_src) || { n: 0, b: 0, bc: 0, mc: 0, ips: new Set(), arpIps: new Set() };
            se.n++; se.b += len;
            if (p.bm === "broadcast") se.bc++; else if (p.bm === "multicast") se.mc++;
            if (p.src && p.src !== "0.0.0.0" && isIp(p.src) && se.ips.size < 50) se.ips.add(p.src);
            if (p.proto === "ARP" && p.src && se.arpIps.size < 5) se.arpIps.add(p.src);
            st.senders.set(p.mac_src, se);
        }
        const a = p.src || p.mac_src, b = p.dst || p.mac_dst;
        if (a && b) {
            const key = a < b ? `${a}↔${b}` : `${b}↔${a}`;
            const pe = st.pairs.get(key) || { n: 0, b: 0, a: a < b ? a : b, z: a < b ? b : a };
            pe.n++; pe.b += len; st.pairs.set(key, pe);
        }
    }
    function rebuildView() {
        S.view = [];
        for (let i = 0; i < S.packets.length; i++) if (matches(S.packets[i])) S.view.push(i);
        queueRender();
    }
    function clearData() {
        S.packets = []; S.view = []; S.selected = null; S.lastTsUs = null;
        resetStats();
        renderDetail();
        queueRender(); queueSide();
    }

    // ── таблица (виртуальная прокрутка) ──────────────────────────────────────
    function queueRender() {
        if (S.renderQueued) return;
        S.renderQueued = true;
        requestAnimationFrame(() => { S.renderQueued = false; renderTable(); });
    }
    function renderTable() {
        const body = $("cap-body"), spacer = $("cap-spacer"), rows = $("cap-rows");
        if (!body) return;
        const total = S.view.length;
        spacer.style.height = total * ROW_H + "px";
        if (S.autoscroll && !S.frozen) body.scrollTop = body.scrollHeight;

        const first = Math.max(0, Math.floor(body.scrollTop / ROW_H) - 10);
        const last = Math.min(total, Math.ceil((body.scrollTop + body.clientHeight) / ROW_H) + 10);
        const html = [];
        for (let vi = first; vi < last; vi++) {
            const idx = S.view[vi];
            const p = S.packets[idx];
            if (!p) continue;
            const cat = catOf(p);
            const flags = p.flags ? ` <span class="cap-flags">[${esc(p.flags)}]</span>` : "";
            const bm = p.bm ? ` cap-row--${p.bm}` : "";
            html.push(
                `<div class="cap-row${bm}${idx === S.selected ? " is-selected" : ""}" style="top:${vi * ROW_H}px" data-idx="${idx}">` +
                `<span class="c-n">${p.n}</span>` +
                `<span class="c-t">${esc(fmtTime(p.timestamp))}</span>` +
                `<span class="c-d">${esc(fmtDelta(p._delta))}</span>` +
                `<span class="c-if">${esc(p.iface || "")}${p.direction ? ` <i class="cap-dir cap-dir--${esc(p.direction)}">${p.direction === "Out" ? "↑" : p.direction === "In" ? "↓" : esc(p.direction)}</i>` : ""}</span>` +
                whoCell("c-src", p.src, p.sport, p.mac_src) +
                whoCell("c-dst", p.dst, p.dport, p.mac_dst) +
                `<span class="c-p"><b class="cap-proto cap-proto--${esc(cat)}">${esc(p.proto)}</b>${p.vlan != null ? `<i class="cap-vlan">v${p.vlan}</i>` : ""}</span>` +
                `<span class="c-l">${p.length || ""}</span>` +
                `<span class="c-i">${esc(p.info)}${flags}</span>` +
                `</div>`
            );
        }
        rows.innerHTML = html.join("");
        const empty = $("cap-empty");
        if (empty) {
            empty.hidden = total > 0;
            empty.textContent = S.packets.length
                ? "Нет пакетов, подходящих под фильтр отображения"
                : S.status === "running" || S.status === "starting" ? "Ждём пакеты…" : "Задайте фильтр и нажмите «Старт»";
        }
        const shown = $("cap-shown");
        if (shown) {
            shown.textContent = S.packets.length === total
                ? `${fmtNum(total)} пакетов`
                : `показано ${fmtNum(total)} из ${fmtNum(S.packets.length)}`;
        }
    }

    function whoCell(cls, ip, port, mac) {
        const text = endpoint(ip, port, mac);
        const byIp = isIp(ip);
        if (!byIp && !isMac(mac)) return `<span class="${cls}">${esc(text)}</span>`;
        const label = mac ? S.labels.get(mac) : null;
        const tip = [mac ? `MAC ${mac}` : null, label, "Клик — кто это"].filter(Boolean).join("\n");
        return `<span class="${cls} cap-who-link" data-ip="${byIp ? esc(ip) : ""}" data-mac="${esc(mac || "")}" title="${esc(tip)}">${esc(text)}</span>`;
    }

    // ── детали пакета ────────────────────────────────────────────────────────
    function renderDetail() {
        const box = $("cap-detail");
        if (!box) return;
        const p = S.selected != null ? S.packets[S.selected] : null;
        if (!p) { box.hidden = true; return; }
        const rows = [
            ["№", p.n], ["Время", p.timestamp], ["Интерфейс", p.iface ? `${p.iface} (${p.direction || "?"})` : null],
            ["Источник", endpoint(p.src, p.sport, null)], ["MAC источника", p.mac_src],
            ["Назначение", endpoint(p.dst, p.dport, null)], ["MAC назначения", p.mac_dst],
            ["VLAN", p.vlan], ["EtherType", p.ethertype], ["Транспорт", p.l4],
            ["Протокол", p.proto], ["Флаги TCP", p.flags], ["Длина кадра", p.length ? `${p.length} байт` : null],
            ["Тип доставки", p.bm === "broadcast" ? "широковещательный" : p.bm === "multicast" ? "групповой" : "одноадресный"],
        ].filter(([, v]) => v != null && v !== "");
        const actions = [];
        for (const [label, ip] of [["источник", p.src], ["назначение", p.dst]]) {
            if (!ip) continue;
            actions.push(`<button type="button" class="cap-link" data-act="show" data-v="ip:${esc(ip)}">Показать только ${esc(ip)}</button>`);
            actions.push(`<button type="button" class="cap-link" data-act="hide" data-v="!ip:${esc(ip)}">Скрыть ${esc(ip)}</button>`);
            actions.push(`<button type="button" class="cap-link" data-act="bpf" data-v="host ${esc(ip)}">Захватывать только ${esc(label)}</button>`);
        }
        for (const ip of [p.src, p.dst]) {
            if (isIp(ip) && ip !== "0.0.0.0" && ip !== "255.255.255.255") {
                actions.push(`<button type="button" class="cap-link cap-who-link" data-ip="${esc(ip)}" data-mac="">Кто это: ${esc(ip)}</button>`);
            }
        }
        for (const mac of [p.mac_src, p.mac_dst]) {
            if (mac && mac !== "ff:ff:ff:ff:ff:ff") {
                actions.push(`<button type="button" class="cap-link cap-who-link" data-ip="" data-mac="${esc(mac)}">Кто это: ${esc(mac)}</button>`);
            }
        }
        if (p.sport != null || p.dport != null) {
            const port = p.dport != null && (p.sport == null || p.dport < p.sport) ? p.dport : p.sport;
            actions.push(`<button type="button" class="cap-link" data-act="show" data-v="port:${port}">Только порт ${port}</button>`);
        }
        box.innerHTML =
            `<div class="cap-detail__head"><span>Пакет №${p.n}</span><button type="button" class="cap-icon-btn" data-act="close-detail" title="Закрыть (Esc)"><i class="bi bi-x-lg"></i></button></div>` +
            `<dl class="cap-detail__grid">${rows.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>` +
            `<div class="cap-detail__info">${esc(p.info)}</div>` +
            `<div class="cap-detail__actions">${actions.join("")}</div>`;
        box.hidden = false;
    }

    // ── боковая статистика ───────────────────────────────────────────────────
    function queueSide() {
        if (S.sideQueued) return;
        S.sideQueued = true;
        setTimeout(() => { S.sideQueued = false; renderSide(); }, 800);
    }
    function renderSide() {
        const box = $("cap-side-body");
        if (!box) return;
        const tab = (S.root && S.root.querySelector(".cap-tab.is-active")?.dataset.tab) || "senders";
        const st = S.stats;
        if (!st.count) { box.innerHTML = `<div class="cap-side__empty">Статистика появится во время захвата</div>`; renderCatChips(); return; }
        let items = [];
        if (tab === "proto") {
            items = [...st.cats.entries()].map(([k, v]) => ({ key: k, n: v.n, b: v.b, cat: v.cat, f: `proto:${k.toLowerCase()}` }));
        } else if (tab === "senders") {
            items = [...st.senders.entries()].map(([mac, v]) => ({
                key: S.labels.get(mac) || mac, sub: S.labels.get(mac) ? mac : null, n: v.n, b: v.b,
                bc: v.bc, who: mac, f: `mac:${mac}`,
            }));
        } else if (tab === "hosts") {
            items = [...st.hosts.entries()].map(([k, v]) => ({ key: k, n: v.n, b: v.b, f: k.includes(":") && k.length === 17 ? `mac:${k}` : `ip:${k}` }));
        } else {
            items = [...st.pairs.values()].map((v) => ({ key: `${v.a} ↔ ${v.z}`, n: v.n, b: v.b, f: v.a.length === 17 && v.a.includes(":") ? `mac:${v.a} mac:${v.z}` : `ip:${v.a} ip:${v.z}` }));
        }
        items.sort((a, b) => b.n - a.n);
        const top = items.slice(0, 15);
        const max = top[0]?.n || 1;
        box.innerHTML = (tab === "senders" ? `<div class="cap-side__hint">Кто больше всех шлёт (по MAC отправителя). Клик — кто это.</div>` : "") +
            top.map((it) => {
                const open = it.who
                    ? `<button type="button" class="cap-bar cap-who-link" data-mac="${esc(it.who)}" data-ip="" title="Кто это?">`
                    : `<button type="button" class="cap-bar" data-act="show" data-v="${esc(it.f)}" title="Показать в таблице">`;
                return open +
                    `<span class="cap-bar__fill${it.cat ? ` cap-fill--${esc(it.cat)}` : ""}" style="width:${(it.n / max * 100).toFixed(1)}%"></span>` +
                    `<span class="cap-bar__key">${esc(it.key)}${it.sub ? `<small>${esc(it.sub)}</small>` : ""}</span>` +
                    `<span class="cap-bar__val">${fmtNum(it.n)} · ${(it.n / st.count * 100).toFixed(1)}%` +
                    `${it.bc ? `<small class="cap-bc" title="широковещательных">bc ${fmtNum(it.bc)}</small>` : ""}</span>` +
                    `</button>`;
            }).join("") + (items.length > top.length ? `<div class="cap-side__more">и ещё ${items.length - top.length}</div>` : "");
        if (tab === "senders") labelTopSenders(top.map((it) => it.who));
        renderCatChips();
    }

    // Подписи «кто это» для самых шумных MAC (лениво, по одному запросу на MAC)
    function labelTopSenders(macs) {
        for (const mac of macs.slice(0, 10)) {
            if (!mac || S.labels.has(mac) || S.labelPending.has(mac)) continue;
            S.labelPending.add(mac);
            const se = S.stats.senders.get(mac);
            fetchWho({ mac, ips: hintIps(mac, se) }).then((d) => {
                S.labels.set(mac, whoLabel(d) || mac);
                queueSide(); queueRender();
            }).catch(() => S.labels.set(mac, mac));
        }
    }
    function renderCatChips() {
        const box = $("cap-cats");
        if (!box) return;
        const counts = new Map();
        for (const p of S.packets) counts.set(catOf(p), (counts.get(catOf(p)) || 0) + 1);
        const cats = [...counts.entries()].sort((a, b) => b[1] - a[1]);
        box.innerHTML = cats.map(([c, n]) =>
            `<button type="button" class="cap-cat cap-proto--${esc(c)}${S.hiddenCats.has(c) ? " is-off" : ""}" data-cat="${esc(c)}" title="Скрыть/показать">${esc(c.toUpperCase())} <span>${fmtNum(n)}</span></button>`
        ).join("");
    }

    // ── статус и счётчики ────────────────────────────────────────────────────
    function setStatus(state, text) {
        S.status = state;
        const el = $("cap-status");
        if (el) { el.dataset.state = state; el.textContent = text; }
        const running = state === "running" || state === "starting";
        const start = $("cap-start"), stop = $("cap-stop");
        if (start) start.hidden = running;
        if (stop) { stop.hidden = !running; stop.disabled = state === "stopping"; }
        for (const id of ["cap-iface", "cap-duration", "cap-limit", "cap-dedup"]) {
            const e = $(id); if (e) e.disabled = running;
        }
        if (S.root) S.root.classList.toggle("is-running", running);
        updateStartEnabled();
    }
    function updateStartEnabled() {
        const start = $("cap-start");
        if (start) start.disabled = !S.bpfValid || !$("cap-iface")?.value;
    }
    function renderCounters() {
        const m = S.meta;
        const set = (id, v) => { const e = $(id); if (e) e.textContent = v; };
        if (!m) {
            set("cap-c-count", "0"); set("cap-c-bytes", "—"); set("cap-c-rate", "—"); set("cap-c-time", "—"); set("cap-c-loss", "—");
            return;
        }
        set("cap-c-count", fmtNum(m.count));
        set("cap-c-bytes", fmtBytes(m.bytes));
        set("cap-c-rate", S.status === "running" ? `${fmtNum(Math.round(S.rate.pps))} п/с · ${fmtBits(S.rate.bps)}` : "—");
        const elapsed = ((m.ended || Date.now() / 1000) - m.started);
        set("cap-c-time", m.duration ? `${fmtDur(elapsed)} / ${fmtDur(m.duration)}` : fmtDur(elapsed));
        const loss = [];
        if (m.kernel_dropped) loss.push(`ядро: ${fmtNum(m.kernel_dropped)}`);
        if (S.skippedTotal) loss.push(`вытеснено: ${fmtNum(S.skippedTotal)}`);
        if (m.dup_dropped) loss.push(`скрыто дублей/Docker: ${fmtNum(m.dup_dropped)}`);
        set("cap-c-loss", loss.join(" · ") || "нет");
    }
    function updateRate(m) {
        const now = performance.now();
        if (S.rate.t) {
            const dt = (now - S.rate.t) / 1000;
            if (dt > 0.3) {
                const pps = (m.count - S.rate.count) / dt, bps = (m.bytes - S.rate.bytes) * 8 / dt;
                S.rate.pps = S.rate.pps ? S.rate.pps * 0.5 + pps * 0.5 : pps;
                S.rate.bps = S.rate.bps ? S.rate.bps * 0.5 + bps * 0.5 : bps;
                S.rate.t = now; S.rate.count = m.count; S.rate.bytes = m.bytes;
            }
        } else {
            S.rate.t = now; S.rate.count = m.count; S.rate.bytes = m.bytes;
        }
    }

    // ── live-сессия ──────────────────────────────────────────────────────────
    async function startCapture() {
        if (!S.bpfValid) return;
        const body = {
            iface: $("cap-iface").value,
            bpf: $("cap-bpf").value.trim(),
            duration: +$("cap-duration").value,
            max_packets: +$("cap-limit").value,
            dedup: $("cap-dedup").checked,
        };
        saveSettings();
        clearData();
        S.meta = null; S.cursor = 0; S.skippedTotal = 0;
        S.rate = { t: 0, count: 0, bytes: 0, pps: 0, bps: 0 };
        S.root.classList.toggle("is-any", body.iface === "any");
        setStatus("starting", "Запуск…");
        try {
            const r = await Cerber.fetchJSON(API + "live/", { json: body });
            S.sid = r.sid;
            setStatus("running", "Идёт захват");
            schedulePoll(0);
        } catch (e) {
            setStatus("error", "Ошибка");
            showMessage(e.message, "crit");
        }
    }
    async function stopCapture() {
        if (!S.sid) return;
        setStatus("stopping", "Останавливается…");
        try {
            await Cerber.fetchJSON(`${API}live/${S.sid}/stop/`, { method: "POST" });
        } catch (e) {
            showMessage(e.message, "crit");
        }
        schedulePoll(0);
    }
    function schedulePoll(ms) {
        clearTimeout(S.pollTimer);
        S.pollTimer = setTimeout(pollOnce, ms);
    }
    async function pollOnce() {
        if (!S.sid || S.polling) return;
        if (S.root && !S.root.isConnected) { abandon(); return; }   // панель заменили (другая подсеть)
        S.polling = true;
        let next = POLL_MS;
        try {
            const r = await Cerber.fetchJSON(`${API}live/${S.sid}/?cursor=${S.cursor}`);
            S.cursor = r.cursor;
            if (r.skipped) S.skippedTotal = (S.skippedTotal || 0) + r.skipped;
            S.meta = r;
            updateRate(r);
            if (!S.frozen) ingest(r.packets);
            else S.frozenBuf = (S.frozenBuf || []).concat(r.packets);
            if (r.error) showMessage(r.error, "crit");
            if (r.more) next = 0;
            if (r.status === "stopped" && !r.more) {
                const reason = STOP_REASONS[r.stop_reason] || "Остановлен";
                setStatus(r.error && !r.count ? "error" : "stopped", reason);
                S.sid = null;
            } else if (r.status === "running" && S.status !== "stopping") {
                setStatus("running", "Идёт захват");
            }
        } catch (e) {
            if (e.status === 404) {
                setStatus("stopped", "Сессия завершена");
                S.sid = null;
            } else {
                showMessage(`Ошибка опроса: ${e.message}`, "warn");
                next = 2000;
            }
        } finally {
            S.polling = false;
            renderCounters();
        }
        if (S.sid) schedulePoll(next);
    }
    function abandon() {
        // Панель исчезла со страницы — останавливаем серверную сессию и забываем её
        const sid = S.sid;
        S.sid = null;
        clearTimeout(S.pollTimer);
        if (sid) stopBeacon(sid);
    }
    function stopBeacon(sid) {
        try {
            fetch(`${API}live/${sid}/stop/`, {
                method: "POST", keepalive: true, credentials: "same-origin",
                headers: { "X-CSRFToken": Cerber.csrfToken() },
            });
        } catch (e) { /* страница закрывается */ }
    }
    window.addEventListener("pagehide", () => { if (S.sid) stopBeacon(S.sid); });

    // ── PCAP ─────────────────────────────────────────────────────────────────
    async function recordPcap() {
        if (!S.bpfValid) return;
        const btn = $("cap-pcap");
        const duration = Math.min(+$("cap-duration").value, 300);
        const limit = +$("cap-limit").value || 100000;
        const body = { iface: $("cap-iface").value, bpf: $("cap-bpf").value.trim(), duration, packets: limit };
        btn.disabled = true;
        const started = Date.now();
        const tick = setInterval(() => {
            btn.innerHTML = `<i class="bi bi-record-circle"></i> ${fmtDur((Date.now() - started) / 1000)} / ${fmtDur(duration)}`;
        }, 500);
        showMessage(`Запись .pcap: до ${fmtDur(duration)} или ${fmtNum(limit)} пакетов — файл скачается автоматически`, "info");
        try {
            const r = await Cerber.fetchJSON(API + "pcap/", { json: body });
            if (r.download_url) {
                showMessage(`PCAP готов: ${fmtNum(r.packets_captured)} пакетов, ${fmtBytes(r.file_size)}. Ссылка действует 1 час.`, "ok");
                const a = document.createElement("a");
                a.href = r.download_url; a.download = "";
                document.body.appendChild(a); a.click(); a.remove();
            } else {
                showMessage(r.error || "Файл не создан", "warn");
            }
        } catch (e) {
            showMessage(e.message, "crit");
        } finally {
            clearInterval(tick);
            btn.disabled = false;
            btn.innerHTML = `<i class="bi bi-download"></i> PCAP`;
        }
    }

    // ── CSV ──────────────────────────────────────────────────────────────────
    function exportCsv() {
        const cols = ["n", "timestamp", "iface", "src", "sport", "dst", "dport", "proto", "l4", "flags", "length", "vlan", "mac_src", "mac_dst", "info"];
        const q = (v) => {
            const s = v == null ? "" : String(v);
            return /[",;\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
        };
        const lines = [cols.join(";")];
        for (const idx of S.view) lines.push(cols.map((c) => q(S.packets[idx][c])).join(";"));
        const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `capture_${(S.cidr || "net").replace(/[/.]/g, "_")}_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.csv`;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    }

    // ── сообщения ────────────────────────────────────────────────────────────
    function showMessage(text, kind) {
        const el = $("cap-msg");
        if (!el) return;
        el.textContent = text || "";
        el.dataset.kind = kind || "info";
        el.hidden = !text;
    }

    // ── фильтр захвата: проверка ─────────────────────────────────────────────
    const validateBpf = Cerber.debounce(async function () {
        const input = $("cap-bpf");
        if (!input) return;
        const seq = ++S.validateSeq;
        const expr = input.value.trim();
        const state = $("cap-bpf-state"), msg = $("cap-bpf-msg");
        const set = (st, text) => {
            if (seq !== S.validateSeq) return;
            input.parentElement.dataset.state = st;
            state.dataset.state = st;
            state.textContent = { ok: "корректен", bad: "ошибка", wait: "проверка…", empty: "весь трафик" }[st];
            msg.textContent = text || "";
            S.bpfValid = st !== "bad";
            updateStartEnabled();
        };
        if (!expr) { set("empty", "Без фильтра — весь трафик интерфейса. На загруженных портах лучше сузить."); return; }
        set("wait", "");
        try {
            const r = await Cerber.fetchJSON(API + "validate/", { json: { bpf: expr, iface: $("cap-iface").value || "any" } });
            if (r.valid) set("ok", r.warning || ""); else set("bad", r.error);
        } catch (e) {
            set("ok", "");   // проверка недоступна — не блокируем, сервер всё равно проверит при запуске
        }
    }, 500);

    function setBpf(expr, append) {
        const input = $("cap-bpf");
        const cur = input.value.trim();
        input.value = append && cur ? `(${cur}) ${append} (${expr})` : expr;
        input.parentElement.dataset.state = "wait";
        S.bpfValid = false; updateStartEnabled();
        validateBpf();
        markPreset();
    }
    function markPreset() {
        const v = $("cap-bpf")?.value.trim();
        S.root?.querySelectorAll(".cap-preset").forEach((b) => b.classList.toggle("is-active", b.dataset.bpf === v));
    }

    // ── конструктор ──────────────────────────────────────────────────────────
    function buildFromBuilder() {
        const parts = [];
        const host = $("cap-b-host").value.trim();
        const dir = $("cap-b-dir").value;
        const port = $("cap-b-port").value.trim();
        const proto = $("cap-b-proto").value;
        if (host) {
            const isNet = host.includes("/");
            const isMac = /^[0-9a-f]{2}(:[0-9a-f]{2}){5}$/i.test(host);
            parts.push(`${isMac ? "ether " : ""}${dir ? dir + " " : ""}${isNet ? "net" : "host"} ${host}`);
        }
        if (proto) parts.push(proto);
        if (port) parts.push(`${port.includes("-") ? "portrange" : "port"} ${port}`);
        if ($("cap-b-subnet").checked && S.cidr) parts.push(`net ${S.cidr}`);
        if ($("cap-b-noself").checked) {
            const it = S.ifaces.find((i) => i.name === $("cap-iface").value);
            if (it && it.ip) parts.push(`not host ${it.ip}`);
        }
        if ($("cap-b-nossh").checked) parts.push("not port 22");
        return parts.join(" and ");
    }
    function updateBuilderPreview() {
        const pv = $("cap-b-preview");
        if (pv) pv.textContent = buildFromBuilder() || "— (весь трафик)";
    }

    // ── интерфейсы ───────────────────────────────────────────────────────────
    async function loadIfaces() {
        const sel = $("cap-iface");
        try {
            const r = await Cerber.fetchJSON(API + "ifaces/");
            S.ifaces = r.ifaces || [];
        } catch (e) {
            S.ifaces = [];
            showMessage(`Интерфейсы не загружены: ${e.message}`, "crit");
        }
        const saved = loadSettings();
        const opts = [`<option value="any">any — все интерфейсы</option>`];
        for (const it of S.ifaces) {
            const extra = [it.ip, it.vlan != null ? `VLAN ${it.vlan}` : null, it.up ? null : "down"].filter(Boolean).join(", ");
            opts.push(`<option value="${esc(it.name)}">${esc(it.name)}${extra ? ` — ${esc(extra)}` : ""}</option>`);
        }
        sel.innerHTML = opts.join("");

        let pick = saved.iface && (saved.iface === "any" || S.ifaces.some((i) => i.name === saved.iface)) ? saved.iface : null;
        if (!pick && S.cidr) {
            try {
                const r = await Cerber.fetchJSON(`${API}iface-for/?cidr=${encodeURIComponent(S.cidr)}`);
                if (r.ok && r.iface) pick = r.iface;
            } catch (e) { /* не нашли — оставим any */ }
        }
        sel.value = pick || "any";
        const hint = $("cap-iface-hint");
        if (hint) {
            const it = S.ifaces.find((i) => i.name === sel.value);
            hint.textContent = sel.value === "any"
                ? "Все интерфейсы хоста; дубли VLAN и трафик Docker можно скрыть"
                : it && it.cidrs && it.cidrs.length ? it.cidrs.join(", ") : "";
        }
        updateStartEnabled();
        validateBpf();
    }

    // ── настройки (per-subnet, в браузере) ───────────────────────────────────
    function settingsKey() { return `cerber.capture.${S.cidr || "global"}`; }
    function loadSettings() {
        try { return JSON.parse(localStorage.getItem(settingsKey()) || "{}"); } catch (e) { return {}; }
    }
    function saveSettings() {
        try {
            localStorage.setItem(settingsKey(), JSON.stringify({
                iface: $("cap-iface").value, bpf: $("cap-bpf").value.trim(),
                duration: $("cap-duration").value, limit: $("cap-limit").value,
            }));
        } catch (e) { /* приватный режим */ }
    }

    // ── инициализация панели ─────────────────────────────────────────────────
    function initPanel(root) {
        if (root.dataset.ready) return;
        root.dataset.ready = "1";
        S.root = root;
        S.cidr = root.closest(".subnet-detail-page")?.dataset.cidr || "";

        $("cap-presets").innerHTML = PRESETS.map((p) => {
            const bpf = p.bpf.replace("{cidr}", S.cidr);
            if (p.bpf.includes("{cidr}") && !S.cidr) return "";
            return `<button type="button" class="cap-preset" data-bpf="${esc(bpf)}" title="${esc((p.title ? p.title + "\n" : "") + bpf + "\nShift — добавить через OR, Alt — через AND")}">${esc(p.label)}</button>`;
        }).join("");
        $("cap-duration").innerHTML = DURATIONS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
        $("cap-limit").innerHTML = LIMITS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");

        const saved = loadSettings();
        $("cap-bpf").value = saved.bpf != null ? saved.bpf : (S.cidr ? `net ${S.cidr}` : "");
        $("cap-duration").value = saved.duration || 300;
        $("cap-limit").value = saved.limit || 0;
        markPreset();

        $("cap-body").addEventListener("scroll", () => {
            const b = $("cap-body");
            const atBottom = b.scrollTop + b.clientHeight >= b.scrollHeight - ROW_H * 1.5;
            if (S.autoscroll !== atBottom) {
                S.autoscroll = atBottom;
                $("cap-autoscroll").classList.toggle("is-active", atBottom);
            }
            queueRender();
        }, { passive: true });

        setStatus("idle", "Готов");
        renderCounters();
        renderTable();
        renderSide();
        loadIfaces();
    }

    function openPanel() {
        const root = $("cap-panel");
        if (!root) return;
        root.hidden = false;
        initPanel(root);
        root.scrollIntoView({ behavior: "smooth", block: "start" });
        setTimeout(() => $("cap-bpf")?.focus(), 300);
    }
    function closePanel() {
        const root = $("cap-panel");
        if (!root) return;
        if (S.sid) stopCapture();
        root.hidden = true;
    }

    // ── обработчики (делегирование) ──────────────────────────────────────────
    document.addEventListener("click", (e) => {
        const t = e.target;
        if (t.closest("#subnet-tcpdump-btn")) {
            e.preventDefault();
            const root = $("cap-panel");
            if (root && !root.hidden) closePanel(); else openPanel();
            return;
        }
        if (!t.closest("#cap-panel")) return;

        if (t.closest("#cap-close")) return closePanel();
        if (t.closest("#cap-start")) return startCapture();
        if (t.closest("#cap-stop")) return stopCapture();
        if (t.closest("#cap-pcap")) return recordPcap();
        if (t.closest("#cap-csv")) return exportCsv();
        if (t.closest("#cap-clear")) {
            clearData(); return;
        }
        if (t.closest("#cap-bpf-clear")) { setBpf(""); $("cap-bpf").focus(); return; }
        if (t.closest("#cap-autoscroll")) {
            S.autoscroll = !S.autoscroll;
            t.closest("#cap-autoscroll").classList.toggle("is-active", S.autoscroll);
            queueRender(); return;
        }
        if (t.closest("#cap-freeze")) {
            S.frozen = !S.frozen;
            const b = t.closest("#cap-freeze");
            b.classList.toggle("is-active", S.frozen);
            b.innerHTML = S.frozen ? `<i class="bi bi-play-fill"></i> Продолжить` : `<i class="bi bi-pause-fill"></i> Пауза`;
            if (!S.frozen && S.frozenBuf) { const buf = S.frozenBuf; S.frozenBuf = null; ingest(buf); }
            return;
        }
        const preset = t.closest(".cap-preset");
        if (preset) {
            setBpf(preset.dataset.bpf, e.shiftKey ? "or" : e.altKey ? "and" : null);
            return;
        }
        if (t.closest("#cap-b-apply")) { setBpf(buildFromBuilder()); return; }
        if (t.closest("#cap-b-and")) { const x = buildFromBuilder(); if (x) setBpf(x, "and"); return; }

        const cat = t.closest(".cap-cat");
        if (cat) {
            const c = cat.dataset.cat;
            if (S.hiddenCats.has(c)) S.hiddenCats.delete(c); else S.hiddenCats.add(c);
            rebuildView(); renderCatChips(); return;
        }
        const tab = t.closest(".cap-tab");
        if (tab) {
            S.root.querySelectorAll(".cap-tab").forEach((x) => x.classList.toggle("is-active", x === tab));
            renderSide(); return;
        }
        const act = t.closest("[data-act]");
        if (act) {
            const v = act.dataset.v;
            if (act.dataset.act === "close-detail") { S.selected = null; renderDetail(); queueRender(); return; }
            if (act.dataset.act === "bpf") { setBpf(v); showMessage(`Фильтр захвата: ${v} — нажмите «Старт»`, "info"); return; }
            if (act.dataset.act === "show" || act.dataset.act === "hide") {
                const input = $("cap-dfilter");
                const cur = input.value.trim();
                input.value = act.dataset.act === "hide" && cur ? `${cur} ${v}` : v;
                applyDisplayFilter();
                return;
            }
        }
        const row = t.closest(".cap-row");
        if (row) {
            S.selected = +row.dataset.idx;
            renderDetail(); queueRender();
        }
    });

    function applyDisplayFilter() {
        const input = $("cap-dfilter");
        S.filter = { text: input.value, tokens: parseDisplayFilter(input.value) };
        input.parentElement.classList.toggle("is-filtered", S.filter.tokens.length > 0);
        rebuildView();
    }

    document.addEventListener("input", (e) => {
        if (!e.target.closest("#cap-panel")) return;
        const id = e.target.id;
        if (id === "cap-bpf") { S.bpfValid = false; updateStartEnabled(); validateBpf(); markPreset(); }
        else if (id === "cap-dfilter") debouncedDisplay();
        else if (id && id.startsWith("cap-b-")) updateBuilderPreview();
    });
    const debouncedDisplay = Cerber.debounce(applyDisplayFilter, 200);

    document.addEventListener("change", (e) => {
        if (!e.target.closest("#cap-panel")) return;
        const id = e.target.id;
        if (id === "cap-iface") {
            S.root.classList.toggle("is-any", e.target.value === "any");
            const it = S.ifaces.find((i) => i.name === e.target.value);
            const hint = $("cap-iface-hint");
            if (hint) hint.textContent = e.target.value === "any" ? "Все интерфейсы хоста; дубли VLAN и трафик Docker можно скрыть" : (it?.cidrs || []).join(", ");
            validateBpf(); updateBuilderPreview();
        }
        if (id && id.startsWith("cap-b-")) updateBuilderPreview();
    });

    document.addEventListener("keydown", (e) => {
        if (!e.target.closest || !$("cap-panel") || $("cap-panel").hidden) return;
        if (e.key === "Enter" && e.target.id === "cap-bpf") {
            e.preventDefault();
            if (S.status !== "running" && S.status !== "starting" && S.bpfValid) startCapture();
        } else if (e.key === "Escape" && S.selected != null && e.target.closest("#cap-panel")) {
            S.selected = null; renderDetail(); queueRender();
        } else if ((e.key === "ArrowDown" || e.key === "ArrowUp") && e.target.closest("#cap-body")) {
            e.preventDefault();
            const pos = S.view.indexOf(S.selected);
            const np = Math.max(0, Math.min(S.view.length - 1, pos + (e.key === "ArrowDown" ? 1 : -1)));
            if (S.view[np] == null) return;
            S.selected = S.view[np];
            const b = $("cap-body");
            if (np * ROW_H < b.scrollTop) b.scrollTop = np * ROW_H;
            else if ((np + 1) * ROW_H > b.scrollTop + b.clientHeight) b.scrollTop = (np + 1) * ROW_H - b.clientHeight;
            renderDetail(); queueRender();
        }
    });

    // ── «Кто это?» — карточка узла по MAC / IP ────────────────────────────────
    // Сервер: identify/ (производитель, IP, имена, порт коммутатора; deep — NetBIOS и DHCP).
    // Локально: что этот узел делает в текущем захвате.
    function hintIps(mac, se) {
        // IP из ARP-пакетов точно принадлежат MAC; IP-источники — только если их мало
        // (за MAC маршрутизатора видно много чужих IP)
        if (!se) return [];
        const out = new Set(se.arpIps);
        if (se.ips.size <= 2) se.ips.forEach((ip) => out.add(ip));
        return [...out].slice(0, 5);
    }
    function isServer(d) {
        const own = new Set(S.ifaces.map((i) => i.ip).filter(Boolean));
        return !!d && (d.ips || []).some((x) => own.has(x.ip) && (x.sources || []).some((src) => src !== "захват"));
    }
    function whoLabel(d) {
        if (!d) return null;
        if (isServer(d)) return "Cerber (этот сервер)";
        const name = d.names && d.names[0] && d.names[0].name;
        const ip = d.ips && d.ips[0] && d.ips[0].ip;
        const vendor = d.vendor || d.well_known;
        return [name || ip, vendor].filter(Boolean).join(" · ") || null;
    }
    function fetchWho(target, deep) {
        const q = new URLSearchParams();
        if (target.mac) q.set("mac", target.mac);
        for (const ip of target.ips || []) q.append("ip", ip);
        if (deep) { q.set("deep", "1"); q.set("iface", $("cap-iface")?.value || "any"); }
        const key = q.toString();
        if (!S.whoCache.has(key)) {
            const pr = Cerber.fetchJSON(`${API}identify/?${key}`);
            pr.catch(() => S.whoCache.delete(key));
            S.whoCache.set(key, pr);
        }
        return S.whoCache.get(key);
    }

    function localActivity(t) {
        const a = { sent: 0, recv: 0, bytes: 0, bc: 0, mc: 0, first: null, last: null,
                    protos: new Map(), peers: new Map(), vlans: new Set(), ifaces: new Set(),
                    srcIps: new Set(), macs: new Set() };
        for (const p of S.packets) {
            const isSrc = t.ip ? p.src === t.ip : p.mac_src === t.mac;
            const isDst = t.ip ? p.dst === t.ip : p.mac_dst === t.mac;
            if (!isSrc && !isDst) continue;
            if (a.first == null) a.first = p._us;
            a.last = p._us;
            if (p.vlan != null) a.vlans.add(p.vlan);
            if (p.iface) a.ifaces.add(p.iface);
            let peer;
            if (isSrc) {
                a.sent++; a.bytes += p.length || 0;
                if (p.bm === "broadcast") a.bc++; else if (p.bm === "multicast") a.mc++;
                a.protos.set(p.proto, (a.protos.get(p.proto) || 0) + 1);
                if (!t.ip && isIp(p.src) && p.src !== "0.0.0.0") a.srcIps.add(p.src);
                if (t.ip && p.mac_src) a.macs.add(p.mac_src);
                peer = t.ip ? p.dst : (p.dst || p.mac_dst);
            } else {
                a.recv++;
                peer = t.ip ? p.src : (p.src || p.mac_src);
            }
            if (peer) a.peers.set(peer, (a.peers.get(peer) || 0) + 1);
        }
        const all = S.packets;
        a.span = all.length > 1 ? (all[all.length - 1]._us - all[0]._us) / 1e6 : 0;
        a.total = all.length;
        return a;
    }

    function ensureWhoDom() {
        let box = document.getElementById("cap-who");
        if (box) return box;
        // в body: внутри AJAX-панели с backdrop-filter position:fixed ломается
        const bd = document.createElement("div");
        bd.id = "cap-who-backdrop"; bd.className = "cap-who-backdrop"; bd.hidden = true;
        box = document.createElement("div");
        box.id = "cap-who"; box.className = "cap-who"; box.hidden = true;
        box.setAttribute("role", "dialog"); box.setAttribute("aria-label", "Кто это");
        document.body.append(bd, box);
        bd.addEventListener("click", closeWho);
        return box;
    }
    function closeWho() {
        const box = document.getElementById("cap-who");
        if (box) box.hidden = true;
        const bd = document.getElementById("cap-who-backdrop");
        if (bd) bd.hidden = true;
        S.whoTarget = null;
    }

    async function openWho(target) {
        const box = ensureWhoDom();
        // по MAC: подсказки IP из захвата; по IP — сервер сам найдёт MAC
        const se = target.mac && !target.ip ? S.stats.senders.get(target.mac) : null;
        const t = { mac: target.ip ? null : target.mac, ip: target.ip || null, l2mac: target.mac || null };
        t.ips = t.ip ? [t.ip] : hintIps(t.mac, se);
        S.whoTarget = t;
        box.hidden = false;
        document.getElementById("cap-who-backdrop").hidden = false;
        renderWho(t, null, "loading");
        try {
            const d = await fetchWho(t);
            if (S.whoTarget === t) renderWho(t, d, "ok");
            if (t.mac || d.mac) S.labels.set(t.mac || d.mac, whoLabel(d) || t.mac || d.mac);
        } catch (e) {
            if (S.whoTarget === t) renderWho(t, null, "error", e.message);
        }
    }
    async function deepWho() {
        const t = S.whoTarget;
        if (!t) return;
        renderWho(t, t._data, "deep");
        try {
            const d = await fetchWho({ mac: t.mac || (t._data && t._data.mac), ips: t.ips }, true);
            if (S.whoTarget === t) renderWho(t, d, "ok");
        } catch (e) {
            if (S.whoTarget === t) renderWho(t, t._data, "error", e.message);
        }
    }

    function renderWho(t, d, state, err) {
        const box = document.getElementById("cap-who");
        if (!box) return;
        t._data = d || t._data;
        d = t._data;
        const mac = (d && d.mac) || t.mac;
        const act = localActivity(t);
        const title = (d && d.names && d.names[0] && d.names[0].name) || t.ip || mac || "?";
        const row = (k, v) => v ? `<div class="cap-who__row"><span>${esc(k)}</span><div>${v}</div></div>` : "";
        const badge = (text, kind) => `<span class="cap-badge cap-badge--${kind}">${esc(text)}</span>`;
        const parts = [];

        parts.push(
            `<header class="cap-who__head"><div><div class="cap-who__eyebrow">Кто это</div>` +
            `<h3>${esc(title)}</h3></div>` +
            `<button type="button" class="cap-icon-btn" data-who="close" title="Закрыть (Esc)"><i class="bi bi-x-lg"></i></button></header>`
        );
        parts.push(`<div class="cap-who__body">`);
        if (state === "loading") parts.push(`<div class="cap-who__wait"><span class="cap-spin"></span> Определяю…</div>`);
        if (state === "error") parts.push(`<div class="cap-msg" data-kind="crit">${esc(err || "Ошибка")}</div>`);

        // ── что известно ──
        if (d) {
            const sec = [];
            if (mac) {
                const badges = [];
                if (d.kind && d.kind.local) badges.push(badge("случайный / локальный MAC", "warn"));
                if (d.kind && d.kind.broadcast) badges.push(badge("широковещательный", "info"));
                if (d.kind && d.kind.multicast) badges.push(badge("групповой", "info"));
                sec.push(row("MAC", `<code>${esc(mac)}</code> <button type="button" class="cap-link" data-who="copy" data-v="${esc(mac)}">копировать</button> ${badges.join(" ")}`));
                const v = d.vendor || d.well_known;
                sec.push(row("Производитель", v ? esc(v) : (d.kind && d.kind.local
                    ? `<span class="cap-muted">не определяется — MAC сгенерирован (телефон/ноутбук с приватным адресом, ВМ, контейнер)</span>`
                    : `<span class="cap-muted">нет в базе OUI</span>`)));
                if (isServer(d)) {
                    sec.push(`<div class="cap-msg" data-kind="info">Это сервер Cerber. Его ARP-сканирование, ping и SNMP-опрос — это мониторинг, а не шум сети. Чтобы убрать его из захвата, в конструкторе отметьте «без трафика сервера Cerber».</div>`);
                }
                if (d.mac_from_ip) sec.push(row("", `<span class="cap-muted">MAC найден по IP в ARP-таблицах</span>`));
            }
            if (d.names && d.names.length) {
                sec.push(row("Имя", d.names.map((n) => `<b>${esc(n.name)}</b> <span class="cap-muted">· ${esc(n.source)}</span>`).join("<br>")));
            }
            for (const ip of d.ips || []) {
                const bits = [];
                if (ip.subnet) bits.push(`подсеть ${esc(ip.subnet)}`);
                if (ip.station) bits.push(`станция «${esc(ip.station)}»`);
                if (ip.description) bits.push(esc(ip.description));
                if (ip.is_alive != null) bits.push(ip.is_alive ? `<span class="cap-ok">в сети</span>` : `<span class="cap-bad">не отвечает</span>`);
                if (ip.last_rtt_ms != null) bits.push(`${esc(ip.last_rtt_ms)} мс`);
                if (ip.is_monitored) bits.push("на мониторинге");
                const dev = ip.device ? ` <a class="cap-link" href="${esc(ip.device.url)}">устройство «${esc(ip.device.name)}» →</a>` : "";
                sec.push(row("IP",
                    `<button type="button" class="cap-link cap-who-link" data-ip="${esc(ip.ip)}" data-mac=""><code>${esc(ip.ip)}</code></button>` +
                    ` <span class="cap-muted">(${esc((ip.sources || []).join(", ") || "—")})</span>` +
                    (bits.length ? `<div class="cap-muted">${bits.join(" · ")}</div>` : "") + dev));
            }
            if (mac && !(d.ips || []).length && !(d.kind && (d.kind.multicast || d.kind.broadcast))) {
                sec.push(row("IP", `<span class="cap-muted">не найден ни в базе, ни в ARP коммутаторов и сервера — попробуйте глубокий поиск</span>`));
            }
            if (d.ports && d.ports.length) {
                const acc = d.ports.filter((p) => p.access);
                const show = acc.length ? acc : d.ports.slice(0, 1);
                const lines = show.map((p) =>
                    `<b>${esc(p.device)}</b> <span class="cap-muted">(${esc(p.device_ip)})</span> · порт <b>${esc(p.port)}</b>` +
                    `${p.vlan != null ? ` · VLAN ${esc(p.vlan)}` : ""}` +
                    ` <span class="cap-muted">— ${p.access ? "порт доступа" : "транзит"}, MAC на порту: ${esc(p.macs_on_port)}` +
                    `${p.updated ? `, данные от ${esc(new Date(p.updated).toLocaleDateString("ru-RU"))}` : ""}</span>`);
                const transit = d.ports.filter((p) => !show.includes(p));
                if (transit.length) {
                    lines.push(`<span class="cap-muted">виден также через: ${transit.map((p) => `${esc(p.device)} ${esc(p.port)} (${esc(p.macs_on_port)} MAC)`).join(", ")}</span>`);
                }
                sec.push(row("Подключён", lines.join("<br>")));
            } else if (mac && !(d.kind && (d.kind.multicast || d.kind.broadcast))) {
                sec.push(row("Подключён", `<span class="cap-muted">нет в FDB опрашиваемых коммутаторов</span>`));
            }
            if (d.deep) {
                for (const [ip, nb] of Object.entries(d.deep.netbios || {})) {
                    sec.push(row(`Windows (${ip})`, [nb.netbios && `<b>${esc(nb.netbios)}</b>`, nb.domain && `домен ${esc(nb.domain)}`,
                        nb.os && esc(nb.os), nb.user && `пользователь ${esc(nb.user)}`].filter(Boolean).join(" · ")));
                }
                const dh = d.deep.dhcp || {};
                if (dh.hostname || dh.vendor_class || dh.requested_ip) {
                    sec.push(row("DHCP-запрос", [dh.hostname && `имя <b>${esc(dh.hostname)}</b>`, dh.vendor_class && `клиент ${esc(dh.vendor_class)}`,
                        dh.requested_ip && `просит ${esc(dh.requested_ip)}`].filter(Boolean).join(" · ")));
                }
                if (!Object.keys(d.deep.netbios || {}).length && !dh.hostname) {
                    sec.push(row("Глубокий поиск", `<span class="cap-muted">NetBIOS/SMB не ответил, DHCP-запросов за 6 с не было</span>`));
                }
            }
            parts.push(`<section class="cap-who__sec">${sec.join("")}</section>`);
        }

        // ── что делает в захвате ──
        const act2 = [];
        if (act.sent || act.recv) {
            const share = act.total ? (act.sent / act.total * 100) : 0;
            const rate = act.span > 0 ? act.sent / act.span : 0;
            act2.push(`<div class="cap-who__stats">` +
                `<div><span>Отправил</span><b>${fmtNum(act.sent)}</b><small>${share.toFixed(1)}% всех</small></div>` +
                `<div><span>Получил</span><b>${fmtNum(act.recv)}</b></div>` +
                `<div><span>Broadcast</span><b class="${act.bc && act.bc / Math.max(act.sent, 1) > 0.5 ? "cap-warn" : ""}">${fmtNum(act.bc)}</b></div>` +
                `<div><span>Multicast</span><b>${fmtNum(act.mc)}</b></div>` +
                `<div><span>Скорость</span><b>${rate >= 10 ? Math.round(rate) : rate.toFixed(1)} п/с</b></div>` +
                `<div><span>Объём</span><b>${fmtBytes(act.bytes)}</b></div></div>`);
            const top = (m, n) => [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, n);
            if (act.protos.size) act2.push(row("Шлёт", top(act.protos, 6).map(([k, v]) => `${esc(k)} <span class="cap-muted">${fmtNum(v)}</span>`).join(" · ")));
            if (act.peers.size) act2.push(row("С кем", top(act.peers, 6).map(([k, v]) =>
                `<button type="button" class="cap-link cap-who-link" data-ip="${isIp(k) ? esc(k) : ""}" data-mac="${isMac(k) ? esc(k) : ""}">${esc(k)}</button> <span class="cap-muted">${fmtNum(v)}</span>`).join(" · ")));
            const where = [act.vlans.size ? `VLAN ${[...act.vlans].join(", ")}` : null, act.ifaces.size ? [...act.ifaces].join(", ") : null].filter(Boolean);
            if (where.length) act2.push(row("Виден на", esc(where.join(" · "))));
            if (!t.ip && act.srcIps.size > 3) {
                act2.push(`<div class="cap-msg" data-kind="info">За этим MAC видно ${fmtNum(act.srcIps.size)} разных IP-отправителей — скорее всего это маршрутизатор или шлюз. Узлы за ним опознавайте по IP.</div>`);
            } else if (!t.ip && act.srcIps.size) {
                act2.push(row("IP в пакетах", [...act.srcIps].map((ip) => `<button type="button" class="cap-link cap-who-link" data-ip="${esc(ip)}" data-mac="">${esc(ip)}</button>`).join(" · ")));
            }
            if (t.ip && act.macs.size && mac && !act.macs.has(mac)) {
                act2.push(`<div class="cap-msg" data-kind="info">Пакеты этого IP приходят от MAC ${[...act.macs].map(esc).join(", ")} — это маршрутизатор/шлюз, а не сам узел.</div>`);
            }
        } else {
            act2.push(`<div class="cap-muted">В текущем захвате пакетов этого узла нет.</div>`);
        }
        parts.push(`<section class="cap-who__sec"><h4>В этом захвате</h4>${act2.join("")}</section>`);

        // ── действия ──
        const filt = t.ip ? `ip:${t.ip}` : `mac:${mac}`;
        const bpfExpr = t.ip ? `host ${t.ip}` : `ether host ${mac}`;
        parts.push(`</div><footer class="cap-who__foot">` +
            `<button type="button" class="cap-btn cap-btn--sm" data-who="show" data-v="${esc(filt)}"><i class="bi bi-funnel"></i> Только его пакеты</button>` +
            `<button type="button" class="cap-btn cap-btn--sm" data-who="bpf" data-v="${esc(bpfExpr)}"><i class="bi bi-record-circle"></i> Захватывать только его</button>` +
            (state === "deep"
                ? `<button type="button" class="cap-btn cap-btn--sm" disabled><span class="cap-spin"></span> Ищу (до 30 с)…</button>`
                : `<button type="button" class="cap-btn cap-btn--sm" data-who="deep" title="NetBIOS/SMB по IP и ожидание DHCP-запроса от MAC"${d && !d.deep ? "" : " disabled"}><i class="bi bi-search"></i> Глубокий поиск</button>`) +
            `</footer>`);
        box.innerHTML = parts.join("");
    }

    document.addEventListener("click", (e) => {
        const link = e.target.closest(".cap-who-link");
        if (link) {
            e.preventDefault();
            openWho({ ip: link.dataset.ip || null, mac: link.dataset.mac || null });
            return;
        }
        const btn = e.target.closest("[data-who]");
        if (!btn || !btn.closest("#cap-who")) return;
        const v = btn.dataset.v;
        switch (btn.dataset.who) {
            case "close": closeWho(); break;
            case "deep": deepWho(); break;
            case "copy":
                navigator.clipboard?.writeText(v).then(() => Cerber.toast("MAC скопирован", "success", 1500));
                break;
            case "show": {
                const input = $("cap-dfilter");
                if (input) { input.value = v; applyDisplayFilter(); }
                closeWho(); break;
            }
            case "bpf":
                setBpf(v); closeWho();
                showMessage(`Фильтр захвата: ${v} — нажмите «Старт»`, "info");
                break;
        }
    });
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && S.whoTarget) { e.stopPropagation(); closeWho(); }
    }, true);

    // Открыть панель сразу, если пришли по ссылке …&tcpdump=1 (из алерта шторма)
    function autoOpen() {
        if (new URLSearchParams(location.search).get("tcpdump") === "1" && $("cap-panel") && $("cap-panel").hidden) openPanel();
    }
    document.addEventListener("DOMContentLoaded", autoOpen);
    document.addEventListener("cerber:subnet-loaded", autoOpen);
})();
