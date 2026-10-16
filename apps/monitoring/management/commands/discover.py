"""
Discovery-обход устройства.

Использование:
    python manage.py discover <IP>
    python manage.py discover <IP> --root 1.3.6.1.4.1.171.14.5
    python manage.py discover <IP> --no-resolve-names
    python manage.py discover <IP> --save
    python manage.py discover <IP> --top 50
"""
import asyncio

from django.core.management.base import BaseCommand

from apps.devices.models import Device, DeviceDiscovery
from apps.monitoring.discovery import discover


class Command(BaseCommand):
    help = "Discovery-обход устройства: SNMP walk + резолв OID в имена"

    def add_arguments(self, parser):
        parser.add_argument("ip")
        parser.add_argument("--community", default=None)
        parser.add_argument("--port", type=int, default=None)
        parser.add_argument("--root", default=None)
        parser.add_argument("--no-resolve-names", action="store_true")
        parser.add_argument("--save", action="store_true")
        parser.add_argument("--top", type=int, default=200)
        parser.add_argument("--max-rows", type=int, default=50000)

    def handle(self, *args, **opts):
        ip = opts["ip"]

        try:
            device = Device.objects.get(ip=ip)
            community = opts["community"] or device.snmp_community or "public"
            port = opts["port"] or device.snmp_port or 161
        except Device.DoesNotExist:
            device = None
            community = opts["community"] or "public"
            port = opts["port"] or 161
            self.stdout.write(self.style.WARNING(
                f"Устройство {ip} не найдено в БД — работаю без сохранения"
            ))

        self.stdout.write(f"Discovery {ip} (community={community})")
        self.stdout.write(f"  resolve_names: {not opts['no_resolve_names']}")
        if opts["root"]:
            self.stdout.write(f"  root_oid: {opts['root']}")

        try:
            snapshot = asyncio.run(discover(
                ip=ip,
                community=community,
                port=port,
                root_oid=opts["root"],
                max_rows=opts["max_rows"],
                resolve_names=not opts["no_resolve_names"],
            ))
        except Exception as e:
            self.stderr.write(f"Discovery ошибка: {e}")
            return

        self.stdout.write("")
        self.stdout.write(f"  sysObjectID: {snapshot.sys_object_id}")
        self.stdout.write(f"  sysDescr:    {snapshot.sys_descr}")
        self.stdout.write(f"  sysName:     {snapshot.sys_name}")
        self.stdout.write(f"  Строк:       {len(snapshot.rows)}")
        if snapshot.errors:
            self.stdout.write(self.style.WARNING(f"  Ошибки:      {len(snapshot.errors)}"))
            for err in snapshot.errors:
                self.stdout.write(f"    - {err}")

        self.stdout.write("")
        self.stdout.write(f"{'OID':<45} {'NAME':<40} VALUE")
        self.stdout.write("-" * 120)

        rows = snapshot.rows
        if opts["top"] and opts["top"] > 0:
            rows = rows[:opts["top"]]

        for row in rows:
            name = row.name or ""
            value = row.value[:50]
            self.stdout.write(f"{row.oid:<45} {name:<40} {value}")

        if opts["top"] and len(snapshot.rows) > opts["top"]:
            self.stdout.write(f"... (показано {opts['top']} из {len(snapshot.rows)})")

        if opts["save"]:
            if not device:
                self.stderr.write(self.style.ERROR(
                    "Не могу сохранить: устройство не найдено в БД"
                ))
                return
            discovery = DeviceDiscovery.objects.create(
                device=device,
                note=f"discover: root={opts['root'] or 'auto'}",
                snapshot=snapshot.to_dict()["rows"],
            )
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS(
                f"Снимок сохранён: DeviceDiscovery id={discovery.id} "
                f"({len(snapshot.rows)} строк)"
            ))
