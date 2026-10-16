"""Опрос всех устройств в БД (или только активных)."""
from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.devices.models import Device


class Command(BaseCommand):
    help = "Опросить все устройства в БД"

    def add_arguments(self, parser):
        parser.add_argument(
            "--only-active", action="store_true",
            help="Только устройства со статусом ok/unknown",
        )
        parser.add_argument(
            "--skip-interfaces", action="store_true",
            help="Не опрашивать интерфейсы",
        )

    def handle(self, *args, **opts):
        qs = Device.objects.all()
        if opts["only_active"]:
            qs = qs.exclude(status=Device.STATUS_UNREACHABLE)

        total = qs.count()
        self.stdout.write(f"Устройств к опросу: {total}")

        for i, device in enumerate(qs, 1):
            self.stdout.write(f"\n[{i}/{total}] {device.name} ({device.ip})")
            try:
                call_command("poll_device", device.ip)
                if not opts["skip_interfaces"]:
                    call_command("poll_interfaces", device.ip)
            except Exception as e:
                self.stderr.write(f"Ошибка для {device.ip}: {e}")

        self.stdout.write(self.style.SUCCESS("\nГотово"))
