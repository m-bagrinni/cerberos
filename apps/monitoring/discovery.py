"""
Discovery-обход устройства:
  - SNMP walk по вендорскому корню
  - попытка резолвить OID → человеческое имя через snmptranslate
  - возврат списка строк для DeviceDiscovery.snapshot
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .snmp import SnmpClient, SnmpError, SnmpTarget, SnmpTimeout

log = logging.getLogger(__name__)


@dataclass
class DiscoveredOid:
    oid: str
    name: str = ""
    syntax: str = ""
    value: str = ""
    index: str = ""

    def to_dict(self) -> dict:
        return {
            "oid": self.oid,
            "name": self.name,
            "syntax": self.syntax,
            "value": self.value,
            "index": self.index,
        }


@dataclass
class DiscoverySnapshot:
    device_ip: str
    sys_object_id: str = ""
    sys_descr: str = ""
    sys_name: str = ""
    rows: list[DiscoveredOid] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "device_ip": self.device_ip,
            "sys_object_id": self.sys_object_id,
            "sys_descr": self.sys_descr,
            "sys_name": self.sys_name,
            "rows": [r.to_dict() for r in self.rows],
            "errors": self.errors,
        }


class MibResolver:
    """Обёртка над snmptranslate. Если утилита недоступна — возвращает пустые имена."""

    def __init__(self, mibdirs: list[str] | None = None):
        self.snmptranslate = shutil.which("snmptranslate")
        self.mibdirs = mibdirs or self._default_mibdirs()
        self.cache: dict[str, tuple[str, str]] = {}

    def _default_mibdirs(self) -> list[str]:
        paths = [
            "/app/mibs/active",
            "/app/mibs/standard",
            "/usr/share/snmp/mibs",
            "/var/lib/mibs/ietf",
        ]
        return [p for p in paths if Path(p).exists()]

    @property
    def available(self) -> bool:
        return self.snmptranslate is not None and bool(self.mibdirs)

    def resolve(self, oid: str) -> tuple[str, str]:
        if not self.available:
            return ("", "")
        if oid in self.cache:
            return self.cache[oid]
        name, syntax = self._call(oid)
        self.cache[oid] = (name, syntax)
        return (name, syntax)

    def _call(self, oid: str) -> tuple[str, str]:
        env = {
            "MIBDIRS": ":".join(self.mibdirs),
            "MIBS": "DLINK-ID-REC-MIB:DLINK-AGENT-MIB:DLINKSW-ENTITY-EXT-MIB:DLINKSW-DDM-MIB:DLINKSW-IF-COUNTER-MIB:DLINKSW-SNMP-MIB:SNMPv2-SMI:SNMPv2-TC:SNMPv2-CONF:SNMPv2-MIB:IF-MIB:ENTITY-MIB:IANAifType-MIB",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        }
        try:
            name_out = subprocess.run(
                [self.snmptranslate, "-Os", oid],
                capture_output=True, text=True, timeout=3, env=env,
            )
            full = (name_out.stdout or "").strip()
        except Exception as e:
            log.debug("snmptranslate name failed for %s: %s", oid, e)
            return ("", "")

        try:
            detail_out = subprocess.run(
                [self.snmptranslate, "-Td", oid],
                capture_output=True, text=True, timeout=3, env=env,
            )
            detail = detail_out.stdout or ""
        except Exception:
            detail = ""

        # -Os возвращает "dEntityExtCpuUtilOneMinute" или "dEntityExtCpuUtilOneMinute.1.1"
        # или ".1.3.6.1..." если имя не найдено
        if full.startswith(".") or not full:
            short_name = ""
        else:
            # берём только имя (до первой точки, если она не часть OID)
            short_name = full.split(".", 1)[0] if not full[0].isdigit() else ""

        syntax = ""
        for line in detail.splitlines():
            line = line.strip()
            if line.startswith("SYNTAX"):
                syntax = line[len("SYNTAX"):].strip()
                break

        return (short_name, syntax)


async def discover(
    ip: str,
    community: str = "public",
    port: int = 161,
    root_oid: str | None = None,
    max_rows: int = 50000,
    resolve_names: bool = True,
    mibdirs: list[str] | None = None,
) -> DiscoverySnapshot:
    client = SnmpClient(SnmpTarget(
        host=ip, community=community, port=port,
        timeout=3.0, retries=1,
    ))

    snapshot = DiscoverySnapshot(device_ip=ip)

    try:
        sys_info = await client.get_sys_info()
        snapshot.sys_object_id = sys_info.get("sysObjectID", "")
        snapshot.sys_descr = sys_info.get("sysDescr", "")
        snapshot.sys_name = sys_info.get("sysName", "")
    except SnmpTimeout as e:
        snapshot.errors.append(f"timeout on sysinfo: {e}")
        return snapshot
    except SnmpError as e:
        snapshot.errors.append(f"snmp error on sysinfo: {e}")
        return snapshot

    if root_oid is None:
        root_oid = _guess_root(snapshot.sys_object_id)
        if root_oid is None:
            snapshot.errors.append(
                "cannot guess root_oid from sysObjectID, pass --root explicitly"
            )
            return snapshot

    resolver = MibResolver(mibdirs) if resolve_names else None

    try:
        rows = await client.walk(root_oid, max_rows=max_rows)
    except SnmpTimeout as e:
        snapshot.errors.append(f"timeout on walk {root_oid}: {e}")
        return snapshot
    except SnmpError as e:
        snapshot.errors.append(f"snmp error on walk {root_oid}: {e}")
        return snapshot

    for oid, val in rows:
        try:
            value_str = val.prettyPrint()
        except Exception:
            value_str = str(val)

        name = ""
        syntax = ""
        if resolver is not None:
            name, syntax = resolver.resolve(oid)

        instance = oid[len(root_oid) + 1:] if oid.startswith(root_oid + ".") else ""

        snapshot.rows.append(DiscoveredOid(
            oid=oid,
            name=name,
            syntax=syntax,
            value=value_str[:512],
            index=instance,
        ))

    return snapshot


def _guess_root(sys_object_id: str) -> str | None:
    """
    sysObjectID: 1.3.6.1.4.1.171.10.147.1.1
    Возвращаем:  1.3.6.1.4.1.171   (весь вендорский корень D-Link)
    """
    if not sys_object_id:
        return None
    parts = sys_object_id.split(".")
    if len(parts) >= 7 and parts[:6] == ["1", "3", "6", "1", "4", "1"]:
        return "1.3.6.1.4.1." + parts[6]
    return None
