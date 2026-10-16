"""
Определение вендора и профиля устройства через SNMP.

Использование:
    python manage.py detect_device <IP>
"""
import asyncio

from django.core.management.base import BaseCommand

from apps.devices.models import Device
from apps.monitoring.detection import (
    apply_detection,
    gather_snmp_info,
    resolve_vendor_profile,
)


class Command(BaseCommand):
    help = "Определить вендора и профиль устройства через SNMP"

    def add_arguments(self, parser):
        parser.add_argument("ip", help="IP устройства из БД")

    def handle(self, *args, **opts):
        ip = opts["ip"]
        try:
            device = Device.objects.get(ip=ip)
        except Device.DoesNotExist:
            self.stderr.write(f"Устройство с IP {ip} не найдено в БД")
            return

        community = device.snmp_community or "public"
        self.stdout.write(f"Опрашиваю {device.ip} (community={community})...")

        # 1. SNMP — async, без ORM внутри
        try:
            info = asyncio.run(gather_snmp_info(
                ip=device.ip,
                community=community,
                port=device.snmp_port,
            ))
        except Exception as e:
            self.stderr.write(f"SNMP ошибка: {e}")
            return

        if info.error:
            self.stderr.write(f"Ошибка: {info.error}")
            return

        # 2. ORM — синхронно, вне async
        result = resolve_vendor_profile(info)

        self.stdout.write(f"  sysObjectID: {result.sys_object_id}")
        self.stdout.write(f"  sysDescr:    {result.sys_descr}")
        self.stdout.write(f"  sysName:     {result.sys_name}")
        self.stdout.write(f"  entModel:    {result.ent_model}")
        self.stdout.write(f"  Vendor:      {result.vendor}")
        self.stdout.write(f"  Profile:     {result.profile}")

        apply_detection(device, result)
        self.stdout.write(self.style.SUCCESS("OK"))
