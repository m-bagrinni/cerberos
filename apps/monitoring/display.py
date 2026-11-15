"""Форматирование и группировка метрик для UI (карточки, графики)."""

def fmt_metric_value(mv, metric):
    """
    Возвращает кортеж (formatted_value, percent_or_None) для карточки метрики.
    percent — число 0..100 для прогресс-бара (только для %-метрик), иначе None.
    """
    if mv is None or mv.value is None:
        return "—", None

    v = mv.value
    key = metric.key
    unit = (metric.unit or "").lower()

    # --- Проценты ---
    if unit == "%" or "pct" in key or "percent" in key:
        pct = max(0.0, min(100.0, float(v)))
        return f"{pct:.1f} %", pct

    # --- Температура ---
    if "temp" in key:
        return f"{float(v):.1f} °C", None

    # --- Uptime (секунды) ---
    if "uptime" in key:
        sec = int(v)
        days, rem = divmod(sec, 86400)
        hours, rem = divmod(rem, 3600)
        minutes = rem // 60
        parts = []
        if days: parts.append(f"{days} дн")
        if hours: parts.append(f"{hours} ч")
        if minutes and not days: parts.append(f"{minutes} мин")
        return " ".join(parts) or f"{sec} с", None

    # --- Байты (mem_total, mem_free, mem_used и т.п.) ---
    if unit in ("b", "byte", "bytes") or key.endswith("_total") or key.endswith("_free") or key.endswith("_used"):
        b = float(v)
        for suf, div in (("TB", 1024**4), ("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
            if abs(b) >= div:
                return f"{b/div:.2f} {suf}", None
        return f"{b:.0f} B", None

    # --- KB (D-Link отдаёт в KB) ---
    if unit == "kb":
        kb = float(v)
        if kb >= 1024 * 1024:
            return f"{kb/(1024*1024):.2f} GB", None
        if kb >= 1024:
            return f"{kb/1024:.2f} MB", None
        return f"{kb:.0f} KB", None

    # --- dBm / V / mA / прочее — просто округляем ---
    if isinstance(v, (int, float)):
        return f"{float(v):.2f}{(' ' + metric.unit) if metric.unit else ''}", None

    return str(v), None


def metric_icon(key):
    """Иконка Bootstrap Icons по ключу метрики."""
    k = key.lower()
    if "cpu" in k: return "bi-cpu"
    if "mem" in k or "dram" in k or "flash" in k or "ram" in k: return "bi-memory"
    if "temp" in k: return "bi-thermometer-half"
    if "uptime" in k: return "bi-clock-history"
    if "power" in k: return "bi-plug"
    if "voltage" in k: return "bi-lightning"
    return "bi-activity"


# ============================================================
# Группировка метрик для графиков (вкладка «Графики»)
# ============================================================

def group_metrics(metrics):
    """
    Группирует метрики по префиксу ключа.
    Возвращает список групп: [{ id, title, color, metrics: [...] }]
    """
    def cls(key):
        k = key.lower()
        if k.startswith("cpu"): return "cpu"
        if "flash" in k: return "flash"
        if k.startswith("mem") or k.startswith("dram") or "ram" in k: return "mem"
        if "temp" in k: return "temp"
        if "uptime" in k: return "uptime"
        if "power" in k: return "power"
        return "other"

    groups_map = {
        "cpu":    {"id": "cpu",    "title": "Процессор",     "order": 1},
        "mem":    {"id": "mem",    "title": "Память DRAM",   "order": 2},
        "flash":  {"id": "flash",  "title": "Flash",         "order": 3},
        "temp":   {"id": "temp",   "title": "Температура",   "order": 4},
        "uptime": {"id": "uptime", "title": "Uptime",        "order": 5},
        "power":  {"id": "power",  "title": "Питание",       "order": 6},
        "other":  {"id": "other",  "title": "Прочее",        "order": 99},
    }

    grouped = {}
    for m in metrics:
        c = cls(m.key)
        grouped.setdefault(c, {"meta": groups_map[c], "metrics": []})["metrics"].append(m)

    # Внутри группы — сортируем метрики по ключу (5s, 1m, 5m)
    for g in grouped.values():
        g["metrics"].sort(key=lambda m: m.key)

    # Формируем итоговый список, сортируя группы по order
    result = []
    for c in sorted(grouped.keys(), key=lambda k: grouped[k]["meta"]["order"]):
        g = grouped[c]
        # id canvas: chart-<group>-<ip> (безопасный)
        result.append({
            "id": g["meta"]["id"],
            "title": g["meta"]["title"],
            "metrics": [
                {"key": m.key, "name": m.name, "unit": m.unit}
                for m in g["metrics"]
            ],
        })
    return result
