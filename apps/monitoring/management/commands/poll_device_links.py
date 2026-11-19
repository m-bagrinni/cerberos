"""
Состояние портов коммутаторов на концах оптических линий (ifOperStatus) — раз в минуту.

Пишет ТОЛЬКО link_a_up / link_b_up. Статус линии, алерты и DDM ведёт poll_optical_links_fast
(apps/monitoring/tasks.py): раньше эта команда сама записывала link_status, и быстрый опрос не
видел перехода — линия уходила в warn/crit без алерта (ЛАЗ — ГТС-Аламедин, 2026-11-18).

Порт → интерфейс — через _port_interface (как и DDM): у стека Eltex номер порта ≠ ifIndex
(gi1/0/5 — ifIndex 53), раньше опрашивался несуществующий ifIndex 5.
"""
import asyncio

from django.core.management.base import BaseCommand

from apps.monitoring.snmp import SnmpClient, SnmpTarget
from apps.stations.models import OpticalLink

OID_IF_OPER = "1.3.6.1.2.1.2.2.1.8"


class Command(BaseCommand):
    help = "Состояние портов коммутаторов на концах оптических линий (link_a_up / link_b_up)"

    def add_arguments(self, parser):
        parser.add_argument("--link-id", type=int, help="ID линии")

    def handle(self, *args, **opts):
        from apps.monitoring.tasks import _port_interface

        links = OpticalLink.objects.filter(is_active=True).select_related("device_a", "device_b")
        if opts.get("link_id"):
            links = links.filter(id=opts["link_id"])

        for link in links:
            changed = []
            for side in ("a", "b"):
                device = getattr(link, f"device_{side}")
                port = getattr(link, f"port_{side}_num")
                if not device or not port:
                    continue
                iface = _port_interface(device, port)
                if_index = iface.if_index if iface else port
                up = self._oper_up(device, if_index)
                self.stdout.write(f"линия {link.id} {side}: {device.name} порт {port} (ifIndex {if_index}) → "
                                  f"{'UP' if up else 'DOWN' if up is False else 'нет ответа'}")
                if up is not None and getattr(link, f"link_{side}_up") != up:
                    setattr(link, f"link_{side}_up", up)
                    changed.append(f"link_{side}_up")
            if changed:
                OpticalLink.objects.filter(pk=link.pk).update(**{f: getattr(link, f) for f in changed})

    def _oper_up(self, device, if_index):
        """True/False по ifOperStatus; None — нет ответа (не меняем прежнее значение)."""
        async def fetch():
            client = SnmpClient(SnmpTarget(
                host=device.ip, community=device.snmp_community or "public", port=device.snmp_port or 161,
            ))
            oid = f"{OID_IF_OPER}.{if_index}"
            try:
                val = (await client.get_many([oid])).get(oid)
                return (int(val) == 1) if val is not None else None
            except Exception:
                return None
        return asyncio.run(fetch())
