from django.utils import timezone
"""
Опрос интерфейсов, VLAN и их связей.

Архитектура (важно для Django 5):
  - async fetch_all()  — SNMP walk-и, БЕЗ ORM
  - sync  save_all()   — обновление БД, вне async
"""
import asyncio
import logging

logger = logging.getLogger(__name__)

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.devices.models import Device
from apps.monitoring.models import Interface, InterfaceVlan, Vlan
from apps.monitoring.snmp import SnmpClient, SnmpTarget
from apps.monitoring.status import mark_ok, mark_unreachable


# Eltex (стек MES): нужны только физические порты юнита 1 — ifIndex 49..108 (см. Interface.is_physical).
# Выше — фантомные порты юнитов 2–8 (150–999) и служебные (Po, tunnel, loopback, stack-port — 1000+).
# D-Link не ограничиваем: там Port-channel (ifIndex 897) показывается на странице устройства с трафиком.
ELTEX_IF_INDEX_LIMIT = 150


def _is_eltex(device) -> bool:
    return "eltex" in ((device.vendor.name if device.vendor_id else "") or "").lower()

OID_IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
OID_IF_TYPE = "1.3.6.1.2.1.2.2.1.3"
OID_IF_ADMIN = "1.3.6.1.2.1.2.2.1.7"
OID_IF_OPER = "1.3.6.1.2.1.2.2.1.8"
OID_IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
OID_IF_HIGH_SPEED = "1.3.6.1.2.1.31.1.1.1.15"
OID_IF_ALIAS = "1.3.6.1.2.1.31.1.1.1.18"

OID_VLAN_ID = "1.3.6.1.2.1.17.7.1.4.3.1.1"
OID_VLAN_NAME = "1.3.6.1.2.1.17.7.1.4.3.1.1"   # dot1qVlanStaticName (человеческое имя)
# Current* — работает на 3630 (индекс: timeMark.vlanId)
OID_VLAN_EGRESS = "1.3.6.1.2.1.17.7.1.4.2.1.4"   # dot1qVlanCurrentEgressPorts
OID_VLAN_UNTAGGED = "1.3.6.1.2.1.17.7.1.4.2.1.5"  # dot1qVlanCurrentUntaggedPorts

# Static* — работает на 3120 (индекс: vlanId)
OID_VLAN_EGRESS_STATIC = "1.3.6.1.2.1.17.7.1.4.3.1.4"   # dot1qVlanStaticEgressPorts
OID_VLAN_UNTAGGED_STATIC = "1.3.6.1.2.1.17.7.1.4.3.1.5"  # dot1qVlanStaticUntaggedPorts

OID_VLAN_PVID = "1.3.6.1.2.1.17.7.1.4.5.1.1"


class Command(BaseCommand):
    help = "Опросить интерфейсы и VLAN устройства и обновить БД"

    def add_arguments(self, parser):
        parser.add_argument("ip")

    def handle(self, *args, **opts):
        try:
            device = Device.objects.select_related("vendor").get(ip=opts["ip"])   # vendor читается в async _fetch
        except Device.DoesNotExist:
            self.stderr.write(f"Устройство с IP {opts['ip']} не найдено")
            return

        if "olencom" in ((device.vendor.name if device.vendor_id else "") or "").lower():
            # PDH-мультиплексор: ifTable нет, опрос давал только таймауты (состояние — через NE)
            self.stdout.write(f"{device.name}: Olencom — интерфейсы не опрашиваются")
            return

        self.stdout.write(f"Опрос интерфейсов {device.name} ({device.ip})...")

        try:
            data = asyncio.run(self._fetch(device))
        except Exception as e:
            self.stderr.write(f"SNMP ошибка: {e}")
            mark_unreachable(device, reason=str(e))
            return

        self._save(device, data)
        mark_ok(device)
        self.stdout.write(self.style.SUCCESS("OK"))

    async def _fetch(self, device) -> dict:
        client = SnmpClient(SnmpTarget(
            host=device.ip,
            community=device.snmp_community or "public",
            port=device.snmp_port,
            timeout=5, retries=3,
        ))

        # Параллельные walk по независимым OID — каждый walk идёт
        # отдельной SNMP-сессией; gather резко сокращает общее время
        # при наличии «пустых»/timeout OID (например, ifAlias на Eltex).
        oid_map = [
            ("descr", OID_IF_DESCR),
            ("iftype", OID_IF_TYPE),
            ("admin", OID_IF_ADMIN),
            ("oper", OID_IF_OPER),
            ("name", OID_IF_NAME),
            ("speed", OID_IF_HIGH_SPEED),
            ("alias", OID_IF_ALIAS),
            ("pvid", OID_VLAN_PVID),
            ("vlan_ids", OID_VLAN_ID),
            ("vlan_names", OID_VLAN_NAME),
        ]

        # Eltex: столбцы ifTable/ifXTable — только до ELTEX_IF_INDEX_LIMIT. Раньше выкачивалась вся
        # таблица (~10 параллельных обходов по ~900 строк, почти всё — фантомы и служебные, которые
        # не сохраняются), и опрос opt.LAZ в ~3 случаях из 4 падал по таймауту.
        limit = ELTEX_IF_INDEX_LIMIT if _is_eltex(device) else None
        # pvid (dot1qPvid) у Eltex индексирован тем же ifIndex — 256 строк, ~11 с без ограничения
        if_columns = {"descr", "iftype", "admin", "oper", "name", "speed", "alias", "pvid"}
        results = await asyncio.gather(
            *[client.walk(oid, index_below=limit if key in if_columns else None)
              for key, oid in oid_map],
            return_exceptions=True,
        )

        data = {}
        for (key, _), res in zip(oid_map, results):
            if isinstance(res, Exception):
                logger.warning("walk %s failed: %s", key, res)
                data[key] = {}
            else:
                data[key] = self._to_dict(res)

        # VLAN fallback (Current* → Static*) — оставляем последовательно,
        # т.к. второй вызов зависит от результата первого.
        data["vlan_egress"] = await self._walk_vlan_with_fallback(
            client, OID_VLAN_EGRESS, OID_VLAN_EGRESS_STATIC,
        )
        data["vlan_untagged"] = await self._walk_vlan_with_fallback(
            client, OID_VLAN_UNTAGGED, OID_VLAN_UNTAGGED_STATIC,
        )
        return data

    async def _walk_vlan_with_fallback(
        self, client, oid_current: str, oid_static: str,
    ) -> dict:
        """
        VLAN egress/untagged: сначала Current*, если пусто — Static*.

        У D-Link (3630, 3120) обе таблицы имеют одинаковую структуру,
        но разные источники:
          - Current* — рабочая таблица (индекс <timeMark>.<vid>),
            есть служебные записи с vid=0 — их отбрасываем.
          - Static*  — конфиг (индекс <vid>).

        Current* даёт более свежие данные, поэтому используем его первым.
        """
        rows = await client.walk(oid_current)
        data = self._vlan_from_current(rows)
        if data:
            self.stdout.write(f"    VLAN: Current* → {len(data)} VLAN")
            return data

        self.stdout.write("    VLAN: Current* пусто, пробуем Static*")
        rows = await client.walk(oid_static)
        data = self._vlan_from_static(rows)
        self.stdout.write(f"    VLAN: Static* → {len(data)} VLAN")
        return data

    def _vlan_from_current(self, rows) -> dict:
        """
        Разбор dot1qVlanCurrent*: индекс <timeMark>.<vid>.
        vid=0 — служебная запись, пропускаем.
        Если на vid несколько timeMark'ов — берём с максимальным
        (самое свежее состояние VLAN).
        """
        best: dict[int, tuple[int, object]] = {}  # vid -> (timeMark, value)
        for oid, val in rows:
            parts = oid.split(".")
            if len(parts) < 2:
                continue
            try:
                tm = int(parts[-2])
                vid = int(parts[-1])
            except (ValueError, IndexError):
                continue
            if vid <= 0:
                continue
            prev = best.get(vid)
            if prev is None or tm > prev[0]:
                best[vid] = (tm, val)
        return {vid: val for vid, (_, val) in best.items()}

    def _vlan_from_static(self, rows) -> dict:
        """Разбор dot1qVlanStatic*: индекс = <vid>."""
        out: dict[int, object] = {}
        for oid, val in rows:
            try:
                vid = int(oid.rsplit(".", 1)[1])
            except (ValueError, IndexError):
                continue
            if vid <= 0:
                continue
            out[vid] = val
        return out

    @transaction.atomic
    def _save(self, device, data):
        descr = data["descr"]
        iftype = data["iftype"]
        admin = data["admin"]
        oper = data["oper"]
        name = data["name"]
        speed = data["speed"]
        alias = data["alias"]
        pvid = data["pvid"]

        all_idx = set(descr) | set(name) | set(iftype)
        self.stdout.write(f"  найдено ifIndex: {len(all_idx)}")

        iface_by_idx = {}
        for idx in sorted(all_idx):
            # Фильтр: пропускаем служебные интерфейсы
            # - D-Link: VLAN (if_index >= 1024)
            # - Eltex: всё выше ELTEX_IF_INDEX_LIMIT (фантомные юниты, Po, tunnel…)
            if_type_val = self._int(iftype.get(idx), 0)
            if idx >= 1024:
                continue
            if idx >= ELTEX_IF_INDEX_LIMIT and (if_type_val in (6, 62, 71, 117) or _is_eltex(device)):
                continue

            # Получаем старый oper_status (если интерфейс существует)
            old_iface = Interface.objects.filter(device=device, if_index=idx).first()
            old_oper = old_iface.oper_status if old_iface else None

            new_oper = self._int(oper.get(idx), 2)

            iface, _ = Interface.objects.update_or_create(
                device=device, if_index=idx,
                defaults={
                    "descr": self._str(descr.get(idx, ""))[:255],
                    "name": self._str(name.get(idx, ""))[:128],
                    "alias": self._str(alias.get(idx, ""))[:255],
                    "if_type": self._int(iftype.get(idx), 0),
                    "speed_mbps": self._int(speed.get(idx), None),
                    "admin_status": self._int(admin.get(idx), 1),
                    "oper_status": new_oper,
                    "pvid": self._int(pvid.get(idx), None),
                },
            )

            # Записываем историю и создаём алерт, если oper_status изменился
            if old_oper is not None and old_oper != new_oper:
                from apps.monitoring.models import InterfaceStatusHistory
                InterfaceStatusHistory.objects.create(
                    interface=iface,
                    old_status=old_oper,
                    new_status=new_oper,
                )

                # Алерт при переходе в NO LINK (oper_status: 1 → 2)
                from apps.alerts.models import Alert
                from apps.devices.models import MetricDefinition

                if old_oper == 1 and new_oper == 2:
                    # Порт отключился — создаём алерт
                    port_label = (iface.name or iface.descr or "").strip() or f"if{iface.if_index}"
                    msg = f"Порт {iface.if_index} «{port_label}» на {device.name}: NO LINK"
                    alert = Alert.objects.create(
                        device=device,
                        metric_key=f"if_oper_status:{iface.if_index}",
                        severity=Alert.SEVERITY_WARN,
                        state=Alert.STATE_FIRING,
                        message=msg[:255],
                        value=new_oper,
                    )
                    try:
                        from apps.accounts.telegram_notify import notify_alert_created
                        notify_alert_created(alert)
                    except Exception as _e:
                        logger.warning("TG notify (created) failed: %s", _e)
                elif old_oper == 2 and new_oper == 1:
                    # Порт восстановился — закрываем алерт + уведомляем
                    active_alerts = Alert.objects.filter(
                        device=device,
                        metric_key=f"if_oper_status:{iface.if_index}",
                        state=Alert.STATE_FIRING,
                    )
                    for alert in active_alerts:
                        alert.state = Alert.STATE_OK
                        alert.resolved_at = timezone.now()
                        alert.save(update_fields=["state", "resolved_at", "updated_at"])
                        try:
                            from apps.accounts.telegram_notify import notify_alert_resolved
                            notify_alert_resolved(alert)
                        except Exception as _e:
                            logger.warning("TG notify (resolved) failed: %s", _e)
                        logger.info("Port %s on %s is UP again (alert %s closed)", iface.name, device.name, alert.id)

            iface_by_idx[idx] = iface

        vlan_ids = data["vlan_ids"]
        vlan_names = data["vlan_ids"]   # тот же OID, что vlan_names (dot1qVlanStaticName)
        vlan_egress = data["vlan_egress"]
        vlan_untagged = data["vlan_untagged"]

        self.stdout.write(f"  найдено VLAN: {len(vlan_ids)}")

        vlan_by_id = {}
        for vid in vlan_ids:
            name_str = self._str(vlan_names.get(vid, "")).strip()
            # Фильтр hex-мусора (если SNMP по какой-то причине отдал bitmap)
            if name_str.startswith("0x") or len(name_str) > 40:
                name_str = ""
            vlan, _ = Vlan.objects.update_or_create(
                device=device, vlan_id=vid,
                defaults={"name": name_str[:64]},
            )
            vlan_by_id[vid] = vlan

        InterfaceVlan.objects.filter(interface__device=device).delete()

        total_links = 0
        for vid, vlan in vlan_by_id.items():
            egress_bits = self._bitmap_set(vlan_egress.get(vid))
            untagged_bits = self._bitmap_set(vlan_untagged.get(vid))

            for idx in egress_bits:
                if idx not in iface_by_idx:
                    continue
                is_tagged = idx not in untagged_bits
                InterfaceVlan.objects.create(
                    interface=iface_by_idx[idx],
                    vlan=vlan,
                    is_tagged=is_tagged,
                )
                total_links += 1

        self.stdout.write(f"  связей порт-VLAN: {total_links}")

    def _to_dict(self, rows):
        out = {}
        for oid, val in rows:
            try:
                idx = int(oid.rsplit(".", 1)[1])
            except (ValueError, IndexError):
                continue
            out[idx] = val
        return out

    def _str(self, v):
        if v is None:
            return ""
        try:
            return v.prettyPrint()
        except Exception:
            return str(v)

    def _int(self, v, default=None):
        """Парсит число из SNMP-ответа. Поддерживает "up(1)", "down(2)", "1", 1."""
        if v is None:
            return default
        if isinstance(v, (int, float)):
            return int(v)
        s = str(v).strip()
        # "up(1)" / "down(2)" / "notPresent(6)" → берём число в скобках
        import re as _re
        mm = _re.search(r"\((\d+)\)", s)
        if mm:
            return int(mm.group(1))
        try:
            return int(s)
        except (ValueError, TypeError):
            return default

    def _bitmap_set(self, value):
        """
        OCTET STRING (dot1qVlanStaticEgressPorts / UntaggedPorts) -> set of ifIndex.
        Порт N = бит (N-1) в big-endian bitstring.
        """
        if value is None:
            return set()

        raw = None

        # 1) OctetString.asOctets() — правильный путь для pysnmp 6.2.6
        try:
            raw = value.asOctets()
        except AttributeError:
            pass

        # 2) ObjectType — достаём значение изнутри
        if raw is None:
            inner = getattr(value, "_value", None)
            if inner is None:
                getter = getattr(value, "getSimpleValue", None)
                if callable(getter):
                    try:
                        inner = getter()
                    except Exception:
                        inner = None
            if inner is not None:
                try:
                    raw = inner.asOctets()
                except AttributeError:
                    try:
                        raw = bytes(inner)
                    except Exception:
                        pass

        # 3) fallback на bytes(value)
        if raw is None:
            try:
                raw = bytes(value)
            except Exception:
                return set()

        if not raw:
            return set()

        # Если пришло ASCII-hex "BF 9F FF ..." — распарсим в сырые байты
        # (признак: пробелы или двоеточия в строке)
        if b" " in raw or b":" in raw:
            try:
                hex_str = raw.decode("ascii", errors="ignore")
                hex_str = hex_str.replace("0x", "").replace(":", "").replace(" ", "")
                raw = bytes.fromhex(hex_str)
            except Exception:
                pass

        # Для D-Link отдаёт 72-байтные строки — берём первые 8 (64 бита)
        if len(raw) > 8:
            raw = raw[:8]

        result = set()
        for byte_idx, byte in enumerate(raw):
            for bit in range(8):
                if byte & (1 << (7 - bit)):
                    ifindex = byte_idx * 8 + bit + 1
                    result.add(ifindex)
        return result
