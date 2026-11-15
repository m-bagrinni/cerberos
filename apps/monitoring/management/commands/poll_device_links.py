import asyncio
from django.core.management.base import BaseCommand
from apps.stations.models import OpticalLink
from apps.devices.models import Device
from apps.monitoring.models import Interface, SfpModule
from apps.monitoring.snmp import SnmpClient, SnmpTarget

class Command(BaseCommand):
    help = "Опрос портов D-Link/Eltex (статус + полный DDM)"

    def add_arguments(self, parser):
        parser.add_argument("--link-id", type=int, help="ID линии")

    def handle(self, *args, **opts):
        link_id = opts.get("link_id")
        links = OpticalLink.objects.filter(is_active=True)
        if link_id:
            links = links.filter(id=link_id)

        for link in links:
            if not link.device_a and not link.device_b:
                continue

            self.stdout.write(f"Опрос линии {link.id} ({link})...")

            # --- Сторона A ---
            if link.device_a and link.port_a_num:
                status_a, ddm_a = self._check_port(link.device_a, link.port_a_num)
                if status_a is not None:
                    link.link_a_up = status_a
                if ddm_a.get('rx') is not None: link.rx_a_dbm = ddm_a['rx']
                if ddm_a.get('tx') is not None: link.tx_a_dbm = ddm_a['tx']
                if ddm_a.get('temp') is not None: link.temp_a_c = ddm_a['temp']
                if ddm_a.get('bias') is not None: link.bias_a_ma = ddm_a['bias']
                if ddm_a.get('volt') is not None: link.volt_a_v = ddm_a['volt']
                self.stdout.write(f"  A ({link.device_a.name}): port {link.port_a_num} -> {'UP' if status_a else 'DOWN'}, DDM: {ddm_a}")

            # --- Сторона B ---
            if link.device_b and link.port_b_num:
                status_b, ddm_b = self._check_port(link.device_b, link.port_b_num)
                if status_b is not None:
                    link.link_b_up = status_b
                if ddm_b.get('rx') is not None: link.rx_b_dbm = ddm_b['rx']
                if ddm_b.get('tx') is not None: link.tx_b_dbm = ddm_b['tx']
                if ddm_b.get('temp') is not None: link.temp_b_c = ddm_b['temp']
                if ddm_b.get('bias') is not None: link.bias_b_ma = ddm_b['bias']
                if ddm_b.get('volt') is not None: link.volt_b_v = ddm_b['volt']
                self.stdout.write(f"  B ({link.device_b.name}): port {link.port_b_num} -> {'UP' if status_b else 'DOWN'}, DDM: {ddm_b}")

            # --- Статус (через _calc_link_status) ---
            from apps.monitoring.tasks import _calc_link_status
            sa = {
                "link_up": link.link_a_up,
                "rx_dbm": link.rx_a_dbm,
                "tx_dbm": link.tx_a_dbm,
                "temp_c": link.temp_a_c,
                "bias_ma": link.bias_a_ma,
                "volt_v": link.volt_a_v,
            }
            sb = {
                "link_up": link.link_b_up,
                "rx_dbm": link.rx_b_dbm,
                "tx_dbm": link.tx_b_dbm,
                "temp_c": link.temp_b_c,
                "bias_ma": link.bias_b_ma,
                "volt_v": link.volt_b_v,
            }
            link.link_status = _calc_link_status(sa, sb)

            link.save(update_fields=[
                "link_a_up", "link_b_up", "link_status",
                "rx_a_dbm", "tx_a_dbm", "temp_a_c", "bias_a_ma", "volt_a_v",
                "rx_b_dbm", "tx_b_dbm", "temp_b_c", "bias_b_ma", "volt_b_v",
                "updated_at"
            ])
            self.stdout.write(self.style.SUCCESS(f"  ✅ Статус: {link.link_status}"))

    def _check_port(self, device, port_num):
        """Возвращает (status, ddm_dict)."""
        async def fetch_status():
            target = SnmpTarget(
                host=device.ip,
                community=device.snmp_community or "public",
                port=device.snmp_port or 161,
            )
            client = SnmpClient(target)
            oid_status = f"1.3.6.1.2.1.2.2.1.8.{port_num}"
            try:
                res = await client.get_many([oid_status])
                val = res.get(oid_status)
                return (int(val) == 1) if val is not None else None
            except Exception:
                return None

        status = asyncio.run(fetch_status())

        # DDM из SfpModule
        ddm = {'rx': None, 'tx': None, 'temp': None, 'bias': None, 'volt': None}
        try:
            iface = Interface.objects.filter(device=device, if_index=port_num).first()
            if iface:
                sfp = SfpModule.objects.filter(interface=iface).first()
                if sfp:
                    ddm['rx'] = sfp.rx_power_dbm
                    ddm['tx'] = sfp.tx_power_dbm
                    ddm['temp'] = sfp.temperature_c
                    ddm['bias'] = sfp.bias_current_ma
                    ddm['volt'] = sfp.voltage_v
        except Exception as e:
            self.stderr.write(f"  Ошибка SfpModule для {device.ip}: {e}")

        return status, ddm
