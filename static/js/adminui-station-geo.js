/* Выбор места станции на карте (adminui/stations/form.html).
 *
 *  Подложка — Google (спутник / гибрид с подписями / схема), как на карте станций.
 *  Клик по карте ставит маркер и заполняет широту/долготу; маркер можно тащить.
 *  Ввод в поля двигает маркер; «42.874600, 74.569800» из Google Карт, вставленное в любое
 *  поле, делится на оба. Серые точки — остальные станции, для ориентира.
 */
(function () {
    "use strict";
    const el = document.getElementById("station-geo-map");
    if (!el || !window.L) return;
    const latIn = document.getElementById("id_latitude");
    const lonIn = document.getElementById("id_longitude");
    const hint = document.getElementById("station-geo-hint");
    const others = JSON.parse(document.getElementById("stations-geo")?.textContent || "[]");

    const google = (lyrs) => L.tileLayer(`https://mt{s}.google.com/vt/lyrs=${lyrs}&x={x}&y={y}&z={z}&hl=ru`,
        { subdomains: "0123", maxZoom: 20, attribution: "&copy; Google" });
    const layers = { "Гибрид": google("y"), "Спутник": google("s"), "Схема": google("m") };
    const map = L.map(el, { layers: [layers["Гибрид"]], zoomControl: true });
    L.control.layers(layers, null, { position: "topright" }).addTo(map);
    L.control.scale({ imperial: false }).addTo(map);

    // Остальные станции — ориентиры
    const ref = L.layerGroup().addTo(map);
    others.forEach((s) => {
        L.circleMarker([s.lat, s.lon], { radius: 5, color: "#fff", weight: 1.5, fillColor: "#64748b", fillOpacity: .9 })
            .bindTooltip(s.name, { direction: "top", offset: [0, -4] }).addTo(ref);
    });

    const pin = L.divIcon({ className: "aui-geo__pin", html: '<i class="bi bi-geo-alt-fill"></i>', iconSize: [30, 30], iconAnchor: [15, 29] });
    let marker = null;

    const num = (v) => {
        const n = parseFloat(String(v ?? "").trim().replace(",", "."));
        return Number.isFinite(n) ? n : null;
    };
    const valid = (lat, lon) => lat !== null && lon !== null && Math.abs(lat) <= 90 && Math.abs(lon) <= 180;

    function fire(input) { input.dispatchEvent(new Event("change", { bubbles: true })); }   // savebar

    function place(lat, lon, { pan = false, write = true } = {}) {
        if (!marker) {
            marker = L.marker([lat, lon], { icon: pin, draggable: true, autoPan: true }).addTo(map);
            marker.on("dragend", () => {
                const p = marker.getLatLng();
                place(p.lat, p.lng);
            });
        } else {
            marker.setLatLng([lat, lon]);
        }
        if (write) {
            latIn.value = lat.toFixed(6); lonIn.value = lon.toFixed(6);
            fire(latIn); fire(lonIn);
        }
        if (pan) map.setView([lat, lon], Math.max(map.getZoom() || 0, 15));
        hint.textContent = `Станция: ${lat.toFixed(6)}, ${lon.toFixed(6)} — перетащите маркер или нажмите в другом месте`;
    }

    map.on("click", (e) => place(e.latlng.lat, e.latlng.lng));

    // Поля → маркер; «lat, lon» одной строкой — в оба поля
    function fromInputs(e) {
        const raw = (e && e.target && e.target.value) || "";
        const pair = raw.match(/^\s*(-?\d+(?:[.,]\d+)?)\s*[,;\s]\s*(-?\d+(?:[.,]\d+)?)\s*$/);
        if (pair && raw.includes(" ") || (pair && (raw.match(/,/g) || []).length === 1 && raw.includes("."))) {
            const lat = num(pair[1]), lon = num(pair[2]);
            if (valid(lat, lon)) { place(lat, lon, { pan: true }); return; }
        }
        const lat = num(latIn.value), lon = num(lonIn.value);
        if (valid(lat, lon)) place(lat, lon, { pan: true, write: false });
    }
    [latIn, lonIn].forEach((i) => { i.addEventListener("change", fromInputs); i.addEventListener("paste", (e) => setTimeout(() => fromInputs(e), 0)); });
    // Перед отправкой — запятая → точка (FloatField не понимает «42,87»)
    latIn.form && latIn.form.addEventListener("submit", () => {
        [latIn, lonIn].forEach((i) => { if (i.value) i.value = String(i.value).trim().replace(",", "."); });
    });

    // Старт: точка станции → иначе все станции → иначе центр сети
    const lat0 = num(latIn.value), lon0 = num(lonIn.value);
    if (valid(lat0, lon0)) {
        place(lat0, lon0, { write: false });
        map.setView([lat0, lon0], 16);
    } else if (others.length) {
        map.fitBounds(L.latLngBounds(others.map((s) => [s.lat, s.lon])).pad(0.15));
    } else {
        map.setView([42.8, 74.6], 9);
    }
    // карта в скрытом/только что отрисованном блоке — пересчитать размер
    setTimeout(() => map.invalidateSize(), 150);
})();
