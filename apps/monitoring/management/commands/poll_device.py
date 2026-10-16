"""
Опрос метрик устройства и запись значений в БД.

Архитектура (важно для Django 5):
  - async fetch_values()  — только SNMP, без ORM
  - sync  save_values()   — ORM-запись (вне async)
"""
import asyncio

from django.core.management.base import BaseCommand

from apps.devices.models import Device
from apps.monitoring.models import MetricValue
from apps.monitoring.snmp import SnmpClient, SnmpTarget
from apps.monitoring.status import mark_ok, mark_unreachable
from apps.monitoring.transforms import apply_transform


class Command(BaseCommand):
    help = "Опросить устройство и записать значения метрик в БД"

    def add_arguments(self, parser):
        parser.add_argument("ip")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        ip = opts["ip"]
        dry = opts["dry_run"]

        try:
            device = Device.objects.get(ip=ip)
        except Device.DoesNotExist:
            self.stderr.write(f"Устройство с IP {ip} не найдено")
            return

        profile = device.effective_profile
        if not profile:
            self.stderr.write(f"У устройства {device.ip} нет профиля")
            mark_unreachable(device, reason="no profile")
            return

        metrics = [m for m in profile.get_effective_metrics().values() if m.enabled]
        if not metrics:
            self.stderr.write("У профиля нет метрик")
            return

        self.stdout.write(
            f"Опрос {device.name} ({device.ip}), метрик: {len(metrics)}"
        )

        # 1) SNMP — async, БЕЗ ORM
        try:
            fetched = asyncio.run(self._fetch(device, metrics))
        except Exception as e:
            self.stderr.write(f"SNMP ошибка: {e}")
            mark_unreachable(device, reason=str(e))
            return

        # 2) ORM — синхронно, вне async
        for metric, instance, value, raw_str in fetched:
            tag = f"[{instance}]" if instance else ""
            self.stdout.write(f"  {metric.key}{tag} = {value}")
            if dry:
                continue
            MetricValue.objects.create(
                device=device,
                metric=metric,
                value=value,
                raw_value=raw_str[:512],
                instance=instance,
            )

        if dry:
            self.stdout.write(self.style.WARNING("dry-run: в БД ничего не записано"))
        else:
            mark_ok(device)
            self.stdout.write(self.style.SUCCESS("OK"))

    async def _fetch(self, device, metrics):
        """Только SNMP. Возвращает список (metric, instance, value, raw_str)."""
        client = SnmpClient(SnmpTarget(
            host=device.ip,
            community=device.snmp_community or "public",
            port=device.snmp_port,
        ))

        result = []

        # --- одиночные ---
        singles = [m for m in metrics if m.scale == "single"]
        if singles:
            raw = await client.get_many([m.oid for m in singles])
            for m in singles:
                v = raw.get(m.oid)
                if v is None:
                    continue
                value, raw_str = self._transform(m, v)
                result.append((m, "", value, raw_str))

        # --- таблицы ---
        walks = [m for m in metrics if m.scale == "walk"]
        for m in walks:
            try:
                rows = await client.walk(m.oid)
            except Exception as e:
                self.stderr.write(f"WALK {m.oid} failed: {e}")
                continue

            for oid, v in rows:
                if not oid.startswith(m.oid + "."):
                    continue
                instance = oid[len(m.oid) + 1:]
                value, raw_str = self._transform(m, v)
                result.append((m, instance, value, raw_str))

        return result

    def _transform(self, metric, raw):
        try:
            raw_str = raw.prettyPrint()
        except Exception:
            raw_str = str(raw)

        try:
            raw_num = float(raw)
        except Exception:
            raw_num = None

        value = apply_transform(raw_num, metric.transform, metric.transform_params)
        return value, raw_str
