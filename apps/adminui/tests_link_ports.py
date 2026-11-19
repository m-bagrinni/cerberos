"""Выбор порта конца линии: реальные порты коммутатора с description (adminui/links/_end.html)."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.devices.models import Device
from apps.monitoring.models import Interface, SfpModule
from apps.stations.models import OpticalLink, Station, StationDevice


class LinkPortsTests(TestCase):
    def setUp(self):
        u = get_user_model().objects.create_superuser("su", "s@x.org", "pw")
        self.client.force_login(u)
        self.sw = Device.objects.create(name="opt", ip="10.0.0.4")
        I = Interface.objects.create
        I(device=self.sw, if_index=49, name="gi1/0/1", alias="mux.LAZ", if_type=6, oper_status=1)
        sfp = I(device=self.sw, if_index=51, name="gi1/0/3", alias="ag.ukjd", if_type=6, oper_status=1, speed_mbps=1000)
        I(device=self.sw, if_index=107, name="te1/0/3", if_type=6, oper_status=2)       # тот же номер 3
        I(device=self.sw, if_index=159, name="gi2/0/3", if_type=6, oper_status=2)       # другой юнит — не показываем
        I(device=self.sw, if_index=1001, name="vlan101", if_type=135, oper_status=1)     # не физический
        SfpModule.objects.create(interface=sfp, rx_power_dbm=-8.4)
        self.sa = Station.objects.create(name="ЛАЗ", slug="laz", x=74.6, y=42.8)
        self.sb = Station.objects.create(name="УКЖД", slug="ukjd", x=74.5, y=42.9)
        self.ac = Device.objects.create(name="ac", ip="10.0.0.10")
        StationDevice.objects.create(station=self.sa, device=self.sw)
        StationDevice.objects.create(station=self.sb, device=self.ac)

    def test_ports_api(self):
        OpticalLink.objects.create(station_a=self.sa, station_b=self.sb, device_a=self.sw, port_a_num=1, label="ЛАЗ - MUX")
        d = self.client.get(f"/admin-ui/links/device-ports/{self.sw.pk}/").json()
        self.assertEqual([(p["num"], p["name"], p["alias"]) for p in d["ports"]],
                         [(1, "gi1/0/1", "mux.LAZ"), (3, "gi1/0/3", "ag.ukjd")])
        p3 = d["ports"][1]
        self.assertEqual((p3["up"], p3["sfp"], p3["rx"], p3["speed"]), (True, True, -8.4, 1000))
        self.assertEqual(d["ports"][0]["used_by"], "ЛАЗ - MUX")

    def test_form_renders_and_saves(self):
        r = self.client.get("/admin-ui/links/new/")
        self.assertContains(r, "adminui-link-ports.js")
        self.assertContains(r, 'data-link-end="a"')
        self.assertContains(r, 'name="port_b_type"', count=1)          # поле типа — один раз
        r = self.client.post("/admin-ui/links/new/", {
            "label": "ЛАЗ - УКЖД", "station_a": self.sa.pk, "station_b": self.sb.pk,
            "device_a": self.sw.pk, "port_a_type": "sfp", "port_a_num": 3,
            "device_b": self.ac.pk, "port_b_type": "lan", "port_b_num": 24, "is_active": "on",
        })
        self.assertEqual(r.status_code, 302, r.context["form"].errors if r.context else r.status_code)
        l = OpticalLink.objects.get(label="ЛАЗ - УКЖД")
        self.assertEqual((l.device_a_id, l.port_a_num, l.port_a_type), (self.sw.pk, 3, "sfp"))
