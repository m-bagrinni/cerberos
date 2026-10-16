"""
Управление портами: admin up/down через ifAdminStatus.

OID ifAdminStatus = 1.3.6.1.2.1.2.2.1.7.<ifIndex>
  1 = up
  2 = down

Использование:
    python manage.py manage_port <IP> <ifIndex> up
    python manage.py manage_port <IP> <ifIndex> down
    python manage.py manage_port <IP> --list          # показать порты
"""
import asyncio

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.devices.models import ActionLog, Device
from apps.monitoring.models import Interface
from apps.monitoring.snmp import SnmpClient, SnmpError, SnmpTarget

OID_IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"

IF_ADMIN_UP = 1
IF_ADMIN_DOWN = 2


class Command(BaseCommand):
    help = "Управление портом: включить/выключить (SNMP SET)"

    def add_arguments(self, parser):
        parser.add_argument("ip")
        parser.add_argument("if_index", nargs="?", type=int)
        parser.add_argument("state", nargs="?", choices=["up", "down"])
        parser.add_argument("--list", action="store_true")
        parser.add_argument("--user-id", type=int, default=None,
                            help="ID пользователя для аудита")

    def handle(self, *args, **opts):
        try:
            device = Device.objects.get(ip=opts["ip"])
        except Device.DoesNotExist:
            self.stderr.write(f"Устройство {opts['ip']} не найдено")
            return

        if opts["list"]:
            self._list_ports(device)
            return

        if_index = opts["if_index"]
        state = opts["state"]

        if not if_index or not state:
            self.stderr.write("Укажите ifIndex и state (up/down)")
            return

        # запись в аудит
        log = ActionLog.objects.create(
            device=device,
            user_id=opts.get("user_id"),
            action=(
                ActionLog.ACTION_PORT_UP if state == "up"
                else ActionLog.ACTION_PORT_DOWN
            ),
            target=f"if{if_index}",
            status=ActionLog.STATUS_PENDING,
        )

        iface = Interface.objects.filter(device=device, if_index=if_index).first()
        iface_name = iface.name if iface else f"if{if_index}"

        self.stdout.write(f"{'UP' if state == 'up' else 'DOWN'} порт {iface_name} "
                          f"(ifIndex={if_index}) на {device.name}...")

        try:
            asyncio.run(self._set_admin_status(
                device, if_index, state,
            ))
        except Exception as e:
            msg = f"SNMP SET error: {e}"
            self.stderr.write(msg)
            log.status = ActionLog.STATUS_ERROR
            log.message = msg
            log.save(update_fields=["status", "message"])
            return

        log.status = ActionLog.STATUS_SUCCESS
        log.message = f"Порт {iface_name} переведён в {state}"
        log.save(update_fields=["status", "message"])

        self.stdout.write(self.style.SUCCESS(f"OK — {iface_name} → {state}"))

    async def _set_admin_status(self, device, if_index: int, state: str):
        client = SnmpClient(SnmpTarget(
            host=device.ip,
            community=device.snmp_community or "public",
            port=device.snmp_port,
            timeout=5.0, retries=2,
        ))
        value = IF_ADMIN_UP if state == "up" else IF_ADMIN_DOWN
        oid = f"{OID_IF_ADMIN_STATUS}.{if_index}"
        await client.set_value(oid, value, value_type="Integer")

    def _list_ports(self, device):
        # Учитываем разные вендоры:
        #   D-Link 3630/3120: if_type 6 (ethernet) и 117 (gigabitEthernet)
        #   Eltex MES2300B: if_type 6 (ethernet) + фантомные unit'ы (отсекаем по if_index<150)
        #   D-Link: VLAN/SVI (if_index>=1024) — отсекаем
        ifaces = Interface.objects.filter(
            device=device,
            if_type__in=Interface.PHYSICAL_IF_TYPES,
            if_index__lt=150,
        ).order_by("if_index")

        self.stdout.write(f"Порты {device.name} ({device.ip}):")
        self.stdout.write(f"{'ifIndex':>8} {'Name':<20} {'Admin':<8} {'Oper':<8}")
        self.stdout.write("-" * 50)
        for i in ifaces:
            admin = "up" if i.admin_status == 1 else "down"
            oper = "up" if i.oper_status == 1 else "down"
            self.stdout.write(f"{i.if_index:>8} {i.name:<20} {admin:<8} {oper:<8}")
