"""
Реестр трансформаций значений SNMP.

Метрика в БД хранит имя функции и параметры (transform, transform_params).
Поллер применяет функцию к сырому значению.
"""
from __future__ import annotations

import ipaddress
from typing import Any, Callable


def identity(v: Any, **_params) -> Any:
    return v


def div_10(v: Any, **_params) -> float:
    return float(v) / 10


def div_100(v: Any, **_params) -> float:
    return float(v) / 100


def div_1000(v: Any, **_params) -> float:
    return float(v) / 1000


def mul_8(v: Any, **_params) -> float:
    return float(v) * 8


def celsius_from_half(v: Any, **_params) -> float:
    return float(v) / 2


def celsius_from_half_kelvin(v: Any, **_params) -> float:
    return float(v) / 2 - 273


def kelvin_to_celsius(v: Any, **_params) -> float:
    return float(v) - 273


def millivolt_to_volt(v: Any, **_params) -> float:
    return float(v) / 1000


def hex_to_ip(v: Any, **_params) -> str:
    if isinstance(v, (bytes, bytearray)) and len(v) == 4:
        return str(ipaddress.IPv4Address(bytes(v)))
    return str(v)


def enum_map(v: Any, params: dict | None = None, **_kw) -> Any:
    if not params:
        return v
    return params.get("map", {}).get(str(v), v)


TRANSFORMS: dict[str, Callable[..., Any]] = {
    "identity": identity,
    "div_10": div_10,
    "div_100": div_100,
    "div_1000": div_1000,
    "mul_8": mul_8,
    "celsius_from_half": celsius_from_half,
    "celsius_from_half_kelvin": celsius_from_half_kelvin,
    "kelvin_to_celsius": kelvin_to_celsius,
    "millivolt_to_volt": millivolt_to_volt,
    "hex_to_ip": hex_to_ip,
    "enum_map": enum_map,
}


def apply_transform(value: Any, name: str, params: dict | None = None) -> Any:
    fn = TRANSFORMS.get(name)
    if fn is None:
        return value
    try:
        return fn(value, params=params or {})
    except Exception:
        return value
