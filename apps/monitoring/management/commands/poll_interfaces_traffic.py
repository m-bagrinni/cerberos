"""
Опрос трафика и ошибок на всех интерфейсах устройства.

Хранит сырые счётчики (ifHCInOctets / ifHCOutOctets) и рассчитывает rate
(бит/с) из разницы с предыдущим сэмплом.

Использование:
    python manage.py poll_interfaces_traffic <IP>
"""
import asyncio
import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.devices.models import Device
from apps.monitoring.models import Interface, InterfaceSample
from apps.monitoring.snmp import SnmpClient, SnmpTarget
from apps.monitoring.status import mark_unreachable

log = logging.getLogger(__name__)

OID_IF_HC_IN_OCTETS = "1.3.6.1.2.1.31.1.1.1.6"
OID_IF_HC_OUT_OCTETS = "1.3.6.1.2.1.31.1.1.1.10"
OID_IF_IN_ERRORS = "1.3.6.1.2.1.2.2.1.14"
OID_IF_OUT_ERRORS = "1.3.6.1.2.1.2.2.1.20"

# Ограничение Counter64 — 2^64 для обработки wrap-around
COUNTER64_MAX = 2**64


class Command(BaseCommand):
    help = "Опрос трафика и ошибок на интерфейсах"

    def add_arguments(self, parser):
        parser.add_argument("ip")

    def handle(self, *args, **opts):
        try:
            device = Device.objects.get(ip=opts["ip"])
        except Device.DoesNotExist:
            self.stderr.write(f"Устройство {opts['ip']} не найдено")
            return

        if not Interface.objects.filter(device=device).exists():
            self.stdout.write(self.style.WARNING(
                "Интерфейсы не опрошены — сначала запусти poll_interfaces"
            ))
            return

        self.stdout.write(f"Опрос трафика {device.name} ({device.ip})...")

        try:
            data = asyncio.run(self._fetch(device))
        except Exception as e:
            self.stderr.write(f"SNMP ошибка: {e}")
            mark_unreachable(device, reason=str(e))
            return

        self._save(device, data)
        self.stdout.write(self.style.SUCCESS("OK"))

    async def _fetch(self, device) -> dict:
        client = SnmpClient(SnmpTarget(
            host=device.ip,
            community=device.snmp_community or "public",
            port=device.snmp_port,
        ))
        return {
            "in_octets": self._to_dict(await client.walk(OID_IF_HC_IN_OCTETS)),
            "out_octets": self._to_dict(await client.walk(OID_IF_HC_OUT_OCTETS)),
            "in_errors": self._to_dict(await client.walk(OID_IF_IN_ERRORS)),
            "out_errors": self._to_dict(await client.walk(OID_IF_OUT_ERRORS)),
        }

    def _save(self, device, data):
        now = timezone.now()
        ifaces = {i.if_index: i for i in Interface.objects.filter(device=device)}

        all_idx = set(data["in_octets"]) | set(data["out_octets"])
        saved = 0
        skipped = 0

        for idx in all_idx:
            if idx not in ifaces:
                skipped += 1
                continue
            iface = ifaces[idx]

            in_oct = self._int(data["in_octets"].get(idx))
            out_oct = self._int(data["out_octets"].get(idx))
            in_err = self._int(data["in_errors"].get(idx))
            out_err = self._int(data["out_errors"].get(idx))

            if in_oct is None and out_oct is None:
                continue

            # Rate считаем из предыдущего сэмпла
            prev = (
                InterfaceSample.objects
                .filter(interface=iface)
                .order_by("-ts")
                .first()
            )
            in_bps = out_bps = None
            if prev and prev.ts:
                dt = (now - prev.ts).total_seconds()
                if dt > 0:
                    if prev.in_octets is not None and in_oct is not None:
                        delta = self._counter_delta(prev.in_octets, in_oct)
                        in_bps = int(delta * 8 / dt)
                    if prev.out_octets is not None and out_oct is not None:
                        delta = self._counter_delta(prev.out_octets, out_oct)
                        out_bps = int(delta * 8 / dt)

            InterfaceSample.objects.create(
                interface=iface,
                ts=now,
                in_octets=in_oct,
                out_octets=out_oct,
                in_errors=in_err,
                out_errors=out_err,
                in_bps=in_bps,
                out_bps=out_bps,
            )
            saved += 1

        self.stdout.write(f"  сохранено: {saved}, пропущено: {skipped}")

    def _to_dict(self, rows):
        out = {}
        for oid, val in rows:
            try:
                idx = int(oid.rsplit(".", 1)[1])
            except (ValueError, IndexError):
                continue
            out[idx] = val
        return out

    def _int(self, v, default=None):
        if v is None:
            return default
        try:
            return int(v)
        except Exception:
            return default

    def _counter_delta(self, prev, curr):
        """Wrap-around для Counter64."""
        if curr >= prev:
            return curr - prev
        # откат счётчика — считаем как (MAX - prev + curr)
        return (COUNTER64_MAX - prev) + curr
