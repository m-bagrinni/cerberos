"""FDB: сбор по Q-BRIDGE-MIB и определение порта подключения (apps/monitoring/fdb.py)."""
from django.test import SimpleTestCase, TestCase

from apps.devices.models import Device
from apps.monitoring.fdb import locate, ports_for
from apps.monitoring.models import ArpEntry, FdbEntry
from apps.monitoring.tasks import parse_fdb_rows


class ParseTests(SimpleTestCase):
    def test_dot1q_keeps_vlan_and_skips_cpu_port(self):
        rows = [
            ("1.3.6.1.2.1.17.7.1.2.2.1.2.60.0.25.34.0.69.80", 3),
            ("1.3.6.1.2.1.17.7.1.2.2.1.2.70.0.25.34.0.69.80", 24),   # тот же MAC в другом VLAN
            ("1.3.6.1.2.1.17.7.1.2.2.1.2.1.0.0.0.0.0.1", 0),          # CPU
        ]
        self.assertEqual(parse_fdb_rows(rows, with_vlan=True),
                         {(60, "00:19:22:00:45:50"): 3, (70, "00:19:22:00:45:50"): 24})

    def test_dot1d_without_vlan(self):
        rows = [("1.3.6.1.2.1.17.4.3.1.2.0.25.34.0.69.80", 5)]
        self.assertEqual(parse_fdb_rows(rows, with_vlan=False), {(None, "00:19:22:00:45:50"): 5})


class SaveTests(TestCase):
    def test_virtual_port_without_ifindex_dropped(self):
        from apps.monitoring.tasks import _save_fdb
        d = Device.objects.create(name="sw", ip="10.9.9.9")
        _save_fdb.func(d, {(60, "AA:00:00:00:00:01"): 3, (1, "AA:00:00:00:00:02"): 897}, {3: 3, 24: 24})
        self.assertEqual(list(FdbEntry.objects.filter(device=d).values_list("mac", "bridge_port", "if_index")),
                         [("AA:00:00:00:00:01", 3, 3)])


class LocateTests(TestCase):
    """
    gw ── opt (порт 49) ── ag (порт 23 вверх, 5 вниз) ── ac (порт 24 вверх, 3 — абонент)
    Абонент виден на всех четырёх; раньше выбирался порт с наименьшим числом MAC вообще.
    """
    HOST = "AA:AA:AA:00:00:01"

    def setUp(self):
        self.dev = {}
        for i, name in enumerate(["gw", "opt", "ag", "ac"], start=1):
            d = Device.objects.create(name=name, ip=f"10.0.0.{i}")
            self.dev[name] = d
            ArpEntry.objects.create(device=d, ip=d.ip, mac=f"02:00:00:00:0{i}:00")
        mac = self.mac = {n: f"02:00:00:00:0{i}:00" for i, n in enumerate(["gw", "opt", "ag", "ac"], start=1)}
        # gw — шлюз: самая большая ARP-таблица (иначе корень при равенстве не определён)
        ArpEntry.objects.create(device=self.dev["gw"], ip="10.1.1.1", mac="DD:00:00:00:00:01")
        F = FdbEntry.objects.create
        # ac: абонент один на порту 3; на аплинке 24 — gw/opt/ag
        F(device=self.dev["ac"], mac=self.HOST, bridge_port=3, vlan_id=60, interface_name="3")
        for n in ("gw", "opt", "ag"):
            F(device=self.dev["ac"], mac=mac[n], bridge_port=24, vlan_id=101)
        # ag: абонент и ac за портом 5 (вниз), gw/opt за портом 23
        F(device=self.dev["ag"], mac=self.HOST, bridge_port=5, vlan_id=60)
        F(device=self.dev["ag"], mac=mac["ac"], bridge_port=5, vlan_id=101)
        F(device=self.dev["ag"], mac=mac["gw"], bridge_port=23, vlan_id=101)
        # opt: абонент за портом 49 — вместе с ag и ac (всего 3 MAC)
        for m in (self.HOST, mac["ag"], mac["ac"]):
            F(device=self.dev["opt"], mac=m, bridge_port=49, vlan_id=60)
        # на ac у абонентского порта ещё 4 MAC за неуправляемым свитчом — больше, чем на ag:5
        for k in range(4):
            F(device=self.dev["ac"], mac=f"BB:BB:BB:00:00:0{k}", bridge_port=3, vlan_id=60)

    def test_access_switch_wins_over_port_with_fewer_macs(self):
        port, sure = locate(self.HOST.lower())
        self.assertTrue(sure)
        self.assertEqual((port.device, port.bridge_port, port.vlan), ("ac", 3, 60))
        others = {(p.device, p.final) for p in ports_for(self.HOST)[1:]}
        self.assertEqual(others, {("ag", False), ("opt", False)})

    def test_switch_itself_located_on_upstream_despite_its_downstream(self):
        """Ищем ag: он на opt:49; за этим портом ещё ac — но это нижестоящий самого ag, порт не «аплинк»."""
        F = FdbEntry.objects.create
        F(device=self.dev["ag"], mac=self.mac["opt"], bridge_port=23, vlan_id=101)
        port, sure = locate(self.mac["ag"])
        self.assertEqual((port.device, port.bridge_port), ("opt", 49))
        self.assertTrue(sure)

    def test_host_seen_only_on_access_uplink_goes_to_deeper_core_port(self):
        """Узел виден на аплинке ac (24) и на ag:7 — значит он за ag:7, а не «на ac»."""
        F = FdbEntry.objects.create
        guest = "CC:00:00:00:00:01"
        F(device=self.dev["ac"], mac=guest, bridge_port=24, vlan_id=1)
        F(device=self.dev["ag"], mac=guest, bridge_port=7, vlan_id=1)
        port, sure = locate(guest)
        self.assertEqual((port.device, port.bridge_port, sure), ("ag", 7, True))

    def test_only_uplinks_known_is_not_sure(self):
        FdbEntry.objects.filter(device=self.dev["ac"]).delete()     # коммутатор доступа не опрашивается
        port, sure = locate(self.HOST)
        self.assertFalse(sure)
        self.assertEqual(port.device, "ag")

    def test_core_switch_goes_towards_gateway_and_gateway_has_no_location(self):
        """opt видит gw на 1, ag на 49; ag видит opt на своём аплинке. opt «подключён» к gw, а не к ag."""
        F = FdbEntry.objects.create
        F(device=self.dev["opt"], mac=self.mac["gw"], bridge_port=1, vlan_id=101)
        F(device=self.dev["gw"], mac=self.mac["opt"], bridge_port=7, vlan_id=101)
        F(device=self.dev["gw"], mac=self.mac["ag"], bridge_port=7, vlan_id=101)
        F(device=self.dev["ag"], mac=self.mac["opt"], bridge_port=23, vlan_id=101)
        port, _ = locate(self.mac["opt"])
        self.assertEqual((port.device, port.bridge_port), ("gw", 7))
        self.assertEqual(locate(self.mac["gw"]), (None, False))

    def test_unknown_mac(self):
        self.assertEqual(locate("CC:CC:CC:CC:CC:CC"), (None, False))

    def test_sibling_mac_of_switch_is_the_switch(self):
        """L3-интерфейс ac (MAC …:04:01 рядом с управлением …:04:00) — это сам ac: он висит на ag:5."""
        FdbEntry.objects.create(device=self.dev["opt"], mac="02:00:00:00:04:01", bridge_port=49, vlan_id=70)
        port, sure = locate("02:00:00:00:04:01")
        self.assertEqual((port.device, port.bridge_port, sure), ("ag", 5, True))
