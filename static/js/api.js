/**
 * Клиент REST API Cerberus.
 * Все методы возвращают Promise с данными (уже развёрнутыми из paginated).
 */

const API_BASE = '/api/v1';

class ApiError extends Error {
    constructor(message, status, payload) {
        super(message);
        this.status = status;
        this.payload = payload;
    }
}

async function request(path, options = {}) {
    const url = path.startsWith('http') ? path : API_BASE + path;
    const resp = await fetch(url, {
        headers: {
            'Accept': 'application/json',
            ...options.headers,
        },
        ...options,
    });

    let payload;
    try {
        payload = await resp.json();
    } catch (e) {
        payload = null;
    }

    if (!resp.ok) {
        throw new ApiError(
            `API ${resp.status} ${resp.statusText}`,
            resp.status,
            payload,
        );
    }
    return payload;
}

/** Разворачивает DRF-пагинацию: {count, results: [...]} → [...] */
function unwrap(payload) {
    if (payload && Array.isArray(payload.results)) return payload.results;
    return payload;
}

export const API = {
    async devices() {
        return unwrap(await request('/devices/?page_size=1000'));
    },

    async device(ip) {
        return request(`/devices/${encodeURIComponent(ip)}/`);
    },

    async deviceMetrics(ip, { latest = true } = {}) {
        const q = new URLSearchParams({ latest: String(latest) });
        return request(`/devices/${encodeURIComponent(ip)}/metrics/?${q}`);
    },

    async deviceMetricHistory(ip, key, hours = 24) {
        const q = new URLSearchParams({ key, hours: String(hours) });
        return request(`/devices/${encodeURIComponent(ip)}/metric-history/?${q}`);
    },

    async deviceInterfaces(ip) {
        return request(`/devices/${encodeURIComponent(ip)}/interfaces/`);
    },

    async deviceVlans(ip) {
        return request(`/devices/${encodeURIComponent(ip)}/vlans/`);
    },

    async profiles() {
        return unwrap(await request('/profiles/'));
    },

    async vendors() {
        return unwrap(await request('/vendors/'));
    },
};

window.CerberAPI = API;   // для отладки из консоли
