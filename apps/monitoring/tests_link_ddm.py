"""DDM оптических линий с портов коммутаторов (линия ЛАЗ–УКЖД, 2026-11-18)."""
from types import SimpleNamespace

from django.test import TestCase

from apps.devices.models import Device
from apps.monitoring.models import Interface, SfpModule
from apps.monitoring.tasks import _apply_end_states, _port_interface


class PortInterfaceTests(TestCase):
    def setUp(self):
        self.opt = Device.objects.create(name="opt", ip="10.0.0.4")

    def test_stack_prefers_port_with_sfp_then_unit1(self):
        # создаём «чужие» раньше нужного — первый по pk не тот
        for if_index, name in ((107, "te1/0/3"), (159, "gi2/0/3"), (51, "gi1/0/3")):
            Interface.objects.create(device=self.opt, if_index=if_index, name=name)
        self.assertEqual(_port_interface(self.opt, 3).name, "gi1/0/3")       # первый юнит
        sfp_on = Interface.objects.get(device=self.opt, name="gi2/0/3")
        SfpModule.objects.create(interface=sfp_on, rx_power_dbm=-9.0)
        self.assertEqual(_port_interface(self.opt, 3).name, "gi2/0/3")       # где есть SFP

    def test_dlink_if_index_equals_port(self):
        Interface.objects.create(device=self.opt, if_index=24, name="eth1/0/24")
        self.assertEqual(_port_interface(self.opt, 24).if_index, 24)
        self.assertIsNone(_port_interface(self.opt, 7))


class ApplyEndStatesTests(TestCase):
    def test_none_does_not_erase_and_changes_reported(self):
        link = SimpleNamespace(**{f"{k}_{s}_{u}": None for s in "ab"
                                  for k, u in (("rx", "dbm"), ("tx", "dbm"), ("temp", "c"),
                                               ("bias", "ma"), ("volt", "v"))},
                               link_a_up=None, link_b_up=True)
        link.rx_b_dbm = -7.0
        changed = _apply_end_states(link, {"link_up": True, "rx_dbm": -8.4, "tx_dbm": -2.3},
                                    {"link_up": None, "rx_dbm": None})
        self.assertEqual(set(changed), {"link_a_up", "rx_a_dbm", "tx_a_dbm"})
        self.assertEqual((link.rx_a_dbm, link.rx_b_dbm, link.link_b_up), (-8.4, -7.0, True))


class EndStateTests(TestCase):
    """Конец-коммутатор: свежий DDM / устаревший / без DDM (статус по порту)."""

    def setUp(self):
        from apps.stations.models import OpticalLink, Station
        self.sw = Device.objects.create(name="ac.post-dc", ip="10.0.0.22")
        self.iface = Interface.objects.create(device=self.sw, if_index=24, name="eth1/0/24", if_type=6, oper_status=1)
        sa = Station.objects.create(name="ЛАЗ", slug="laz"); sb = Station.objects.create(name="Пост", slug="post")
        self.link = OpticalLink(station_a=sa, station_b=sb, device_b=self.sw, port_b_num=24,
                                link_b_up=True, rx_b_dbm=-12.99, tx_b_dbm=-2.03)

    def test_fresh_ddm_used(self):
        from apps.monitoring.tasks import _end_state
        SfpModule.objects.create(interface=self.iface, rx_power_dbm=-7.0, rx_state="")
        st = _end_state(self.link, "b")
        self.assertEqual((st["ddm"], st["rx_dbm"], st["link_up"]), (True, -7.0, True))   # rx_state пуст → по порту

    def test_stale_ddm_ignored_and_cleared(self):
        from datetime import timedelta
        from django.utils import timezone
        from apps.monitoring.tasks import _end_state
        sfp = SfpModule.objects.create(interface=self.iface, rx_power_dbm=-12.99)
        SfpModule.objects.filter(pk=sfp.pk).update(updated_at=timezone.now() - timedelta(days=26))
        st = _end_state(self.link, "b")
        self.assertEqual((st["ddm"], st["rx_dbm"], st["link_up"]), (False, None, True))
        changed = _apply_end_states(self.link, {"link_up": None, "rx_dbm": None}, st)
        self.assertEqual(set(changed), {"rx_b_dbm", "tx_b_dbm"})
        self.assertIsNone(self.link.rx_b_dbm)
        self.assertTrue(self.link.link_b_up)

    def test_no_ddm_port_down_is_crit(self):
        from apps.monitoring.tasks import _calc_link_status, _end_state
        self.link.link_b_up = False
        self.assertEqual(_calc_link_status({"link_up": True, "rx_dbm": -8.0}, _end_state(self.link, "b")), "crit")
