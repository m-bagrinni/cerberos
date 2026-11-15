from django.core.management.base import BaseCommand
from apps.devices.models import Device
from apps.monitoring.cli_manager import set_port_pvid

class Command(BaseCommand):
    help = "Устанавливает PVID через CLI (SSH/Telnet)"

    def add_arguments(self, parser):
        parser.add_argument("ip")
        parser.add_argument("port_name")
        parser.add_argument("vlan_id", type=int)
        parser.add_argument("--username", default="admin")
        parser.add_argument("--password", required=True)

    def handle(self, *args, **opts):
        device = Device.objects.get(ip=opts["ip"])
        self.stdout.write(f"Установка PVID {opts['vlan_id']} для {opts['port_name']} на {device.ip}...")
        
        try:
            success, output = set_port_pvid(
                device,
                opts["username"],
                opts["password"],
                opts["port_name"],
                opts["vlan_id"],
            )
            if success:
                self.stdout.write(self.style.SUCCESS(f"OK — PVID {opts['port_name']} → {opts['vlan_id']}"))
                self.stdout.write(output)
        except Exception as e:
            self.stderr.write(f"Ошибка: {e}")
