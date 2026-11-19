"""
Обёртка над pysnmp (asyncio API) для SNMP v2c.

pysnmp 6.2.6:
  - getCmd / nextCmd — async функции
  - UdpTransportTarget — синхронный конструктор
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Iterable

from pysnmp.hlapi.asyncio import (
    CommunityData,
    ContextData,
    Integer,
    ObjectIdentity,
    ObjectType,
    OctetString,
    SnmpEngine,
    UdpTransportTarget,
    getCmd,
    nextCmd,
    setCmd,
)

log = logging.getLogger(__name__)


class SnmpError(Exception):
    pass


class SnmpTimeout(SnmpError):
    pass


@dataclass
class SnmpTarget:
    host: str
    community: str = "public"
    port: int = 161
    timeout: float = 2.0
    retries: int = 1


class SnmpClient:
    def __init__(self, target: SnmpTarget):
        self.target = target
        self.engine = SnmpEngine()
        self.community = CommunityData(target.community, mpModel=1)  # v2c
        self.context = ContextData()
        # синхронное создание транспорта — один раз
        self.transport = UdpTransportTarget(
            (target.host, target.port),
            timeout=target.timeout,
            retries=target.retries,
        )

    async def get_many(self, oids: Iterable[str]) -> dict[str, object]:
        oid_list = list(oids)
        if not oid_list:
            return {}

        error_indication, error_status, error_index, var_binds = await getCmd(
            self.engine,
            self.community,
            self.transport,
            self.context,
            *[ObjectType(ObjectIdentity(oid)) for oid in oid_list],
        )

        if error_indication:
            if "timeout" in str(error_indication).lower():
                raise SnmpTimeout(str(error_indication))
            raise SnmpError(str(error_indication))

        if error_status:
            raise SnmpError(
                f"{error_status.prettyPrint()} at index {error_index}"
            )

        result: dict[str, object] = {}
        for name, val in var_binds:
            oid_str = str(name)
            if _is_no_such(val):
                continue
            result[oid_str] = val
        return result

    # ------- SET (write) -------

    async def set_value(self, oid: str, value, value_type: str = "Integer") -> bool:
        """
        SNMP SET одного OID.

        value_type:
            Integer, OctetString, ObjectIdentifier, IpAddress, TimeTicks
        """
        from pysnmp.hlapi.asyncio import (
            Integer, OctetString, ObjectIdentifier, IpAddress, TimeTicks,
        )

        type_map = {
            "Integer": Integer,
            "OctetString": OctetString,
            "ObjectIdentifier": ObjectIdentifier,
            "IpAddress": IpAddress,
            "TimeTicks": TimeTicks,
        }

        cls = type_map.get(value_type)
        if cls is None:
            raise SnmpError(f"Unsupported value_type: {value_type}")

        error_indication, error_status, error_index, var_binds = await setCmd(
            self.engine,
            self.community,
            self.transport,
            self.context,
            ObjectType(ObjectIdentity(oid), cls(value)),
        )

        if error_indication:
            if "timeout" in str(error_indication).lower():
                raise SnmpTimeout(str(error_indication))
            raise SnmpError(str(error_indication))

        if error_status:
            raise SnmpError(
                f"{error_status.prettyPrint()} at index {error_index}"
            )

        return True

    async def get_one(self, oid: str):
        result = await self.get_many([oid])
        return result.get(oid)

    async def walk(
        self, root_oid: str, max_rows: int = 10000, index_below: int | None = None,
    ) -> list[tuple[str, object]]:
        """
        WALK через nextCmd с защитой от "OID not increasing".

        В pysnmp 6.2.6 nextCmd — корутина, возвращающая ОДИН ответ.
        Ходим циклом, каждый раз от последнего OID.

        index_below — для столбцов с одним индексом (ifTable/ifXTable): остановиться, как только
        индекс (первый субидентификатор после root_oid) ≥ index_below. Строки идут по возрастанию
        индекса, так что дальше только ненужное — у стека Eltex это сотни фантомных портов
        юнитов 2–8 и служебных интерфейсов, на которых опрос упирался в таймауты.
        """
        rows: list[tuple[str, object]] = []
        current_oid = root_oid
        seen: set[str] = set()

        while True:
            try:
                error_indication, error_status, error_index, var_binds = await nextCmd(
                    self.engine,
                    self.community,
                    self.transport,
                    self.context,
                    ObjectType(ObjectIdentity(current_oid)),
                    lexicographicMode=False,
                )
            except Exception as e:
                if "not increasing" in str(e).lower():
                    log.warning(
                        "walk %s: stopped at %s (OID not increasing)",
                        root_oid, current_oid,
                    )
                    return rows
                raise

            if error_indication:
                msg = str(error_indication)
                if "not increasing" in msg.lower():
                    log.warning(
                        "walk %s: stopped at %s (OID not increasing)",
                        root_oid, current_oid,
                    )
                    return rows
                if "timeout" in msg.lower():
                    raise SnmpTimeout(msg)
                raise SnmpError(msg)

            if error_status:
                msg = error_status.prettyPrint()
                if "not increasing" in msg.lower():
                    log.warning(
                        "walk %s: stopped at %s (OID not increasing)",
                        root_oid, current_oid,
                    )
                    return rows
                raise SnmpError(
                    f"{msg} at index {error_index}"
                )

            # pysnmp 6.2.6: var_binds = [[ObjectType, ObjectType, ...]]
            items = var_binds
            if items and isinstance(items[0], list):
                items = items[0]

            advanced = False
            for item in items:
                try:
                    name, val = item[0], item[1]
                except (TypeError, IndexError):
                    continue

                oid_str = str(name)

                # вышли за пределы root_oid — конец таблицы
                if not oid_str.startswith(root_oid):
                    return rows

                # защита от зацикливания
                if oid_str in seen:
                    log.warning("walk %s: OID repeated %s, stopping", root_oid, oid_str)
                    return rows
                seen.add(oid_str)

                # проверка монотонности
                try:
                    prev_tuple = tuple(
                        int(x) for x in current_oid.split(".") if x.isdigit()
                    )
                    oid_tuple = tuple(
                        int(x) for x in oid_str.split(".") if x.isdigit()
                    )
                except ValueError:
                    prev_tuple = ()
                    oid_tuple = ()

                if oid_tuple and prev_tuple and oid_tuple <= prev_tuple:
                    log.warning(
                        "walk %s: OID not increasing %s <= %s, stopping",
                        root_oid, oid_str, current_oid,
                    )
                    return rows

                current_oid = oid_str
                advanced = True

                if _is_no_such(val):
                    continue

                if index_below is not None:
                    try:
                        idx = int(oid_str[len(root_oid) + 1:].split(".", 1)[0])
                    except ValueError:
                        idx = None
                    if idx is not None and idx >= index_below:
                        return rows

                rows.append((oid_str, val))
                if len(rows) >= max_rows:
                    log.warning("walk %s: reached max_rows=%d", root_oid, max_rows)
                    return rows

            if not advanced:
                return rows


    async def get_sys_info(self) -> dict[str, str]:
        oids = {
            "sysDescr": "1.3.6.1.2.1.1.1.0",
            "sysObjectID": "1.3.6.1.2.1.1.2.0",
            "sysUpTime": "1.3.6.1.2.1.1.3.0",
            "sysContact": "1.3.6.1.2.1.1.4.0",
            "sysName": "1.3.6.1.2.1.1.5.0",
            "sysLocation": "1.3.6.1.2.1.1.6.0",
        }
        raw = await self.get_many(oids.values())
        return {key: _to_str(raw.get(oid, "")) for key, oid in oids.items()}

    async def get_ent_model(self) -> str | None:
        try:
            rows = await self.walk("1.3.6.1.2.1.47.1.1.1.1.13", max_rows=100)
        except SnmpError:
            return None
        for _oid, val in rows:
            s = _to_str(val).strip()
            if s:
                return s
        return None


def _to_str(v) -> str:
    """Значения pysnmp → человекочитаемая строка (OID — numeric)."""
    if v is None:
        return ""

    # ObjectIdentity — обёртка над ObjectIdentifier. Достаём вложенный OID.
    try:
        from pysnmp.smi.rfc1902 import ObjectIdentity
        if isinstance(v, ObjectIdentity):
            inner = v.getOid() if hasattr(v, "getOid") else None
            if inner is not None:
                try:
                    return ".".join(str(int(x)) for x in inner.asTuple())
                except Exception:
                    try:
                        return ".".join(str(int(x)) for x in inner)
                    except Exception:
                        pass
    except Exception:
        pass

    # ObjectIdentifier — числовой OID
    try:
        from pysnmp.proto.rfc1902 import ObjectIdentifier as PysnmpOID
        if isinstance(v, PysnmpOID):
            try:
                return ".".join(str(int(x)) for x in v.asTuple())
            except Exception:
                try:
                    return ".".join(str(int(x)) for x in v)
                except Exception:
                    pass
    except Exception:
        pass

    # Резервный вариант — по имени класса
    if type(v).__name__ in ("ObjectIdentifier", "ObjectName", "ObjectIdentity"):
        # пробуем через __str__ получить "1.2.3.4"
        try:
            s = str(v)
            # формат может быть "1.3.6.1.4.1.171..." — оставляем
            if s and s[0].isdigit():
                return s
        except Exception:
            pass

    if isinstance(v, OctetString):
        try:
            return v.prettyPrint()
        except Exception:
            return str(v)
    if isinstance(v, Integer):
        return str(int(v))
    try:
        return v.prettyPrint()
    except Exception:
        return str(v)



def _is_no_such(val) -> bool:
    try:
        pretty = val.prettyPrint()
    except Exception:
        pretty = str(val)
    return pretty in {
        "No Such Object currently exists at this OID",
        "No Such Instance currently exists at this OID",
        "No more variables left in this MIB View",
    }


def run_async(coro):
    return asyncio.run(coro)
