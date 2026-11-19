"""Админ-панель: каркас (меню, крошки, флеш), пагинация, фильтры, обзор."""
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.alerts.models import Alert
from apps.devices.models import Device, DeviceCategory
from apps.stations.models import Station

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "adminui"}}


@override_settings(CACHES=LOCMEM)
class AdminUiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.su = User.objects.create_superuser("root", password="x-12345")
        cls.cat = DeviceCategory.objects.create(name="Коммутаторы", slug="sw")
        cls.up = Device.objects.create(name="sw-up", ip="10.0.0.1", status=Device.STATUS_OK, category=cls.cat)
        cls.down = Device.objects.create(name="sw-down", ip="10.0.0.2", status=Device.STATUS_UNREACHABLE)

    def setUp(self):
        self.client.force_login(self.su)

    def test_nav_highlights_section_on_detail_and_form_pages(self):
        # Раньше пункт «Устройства» подсвечивался только на самом списке
        for name, args in (("adminui:device_detail", [self.up.pk]), ("adminui:device_update", [self.up.pk])):
            html = self.client.get(reverse(name, args=args)).content.decode()
            self.assertIn('class="adminui-nav__item is-active"><i class="bi bi-hdd-network"></i>', html)
            # и хлебная крошка ведёт на список
            self.assertIn(f'<a href="{reverse("adminui:devices")}">Устройства</a>', html)

    def test_device_filter_by_status_and_category(self):
        r = self.client.get(reverse("adminui:devices"), {"status": "unreachable"})
        names = [d.name for d in r.context["devices"]]
        self.assertEqual(names, ["sw-down"])
        r = self.client.get(reverse("adminui:devices"), {"category": self.cat.pk})
        self.assertEqual([d.name for d in r.context["devices"]], ["sw-up"])

    def test_pagination_shown_on_lists_that_lacked_it(self):
        Station.objects.bulk_create(Station(name=f"Ст {i:03}", slug=f"st-{i}") for i in range(55))
        r = self.client.get(reverse("adminui:stations"), {"q": "Ст"})
        html = r.content.decode()
        self.assertIn("aui-pagination", html)
        self.assertIn("из 55", html)                       # общий счётчик, а не размер страницы
        self.assertIn("page=2&amp;q=%D0%A1%D1%82", html)   # фильтр сохраняется в ссылке

    def test_dashboard_attention_and_health(self):
        Alert.objects.create(device=self.down, metric_key="device.reachable", severity="crit", state="firing")
        User.objects.create_user("newbie", password="x-12345", registration_status="pending")
        r = self.client.get(reverse("adminui:dashboard"))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn("никто не взял в работу", html)
        self.assertIn("на регистрацию ждёт подтверждения", html)
        self.assertIn("sw-down", html)                     # блок «Недоступные устройства»
        self.assertEqual(r.context["stats"]["devices_down"], 1)
        # пульсов в пустом кэше нет — все очереди помечены как молчащие
        self.assertTrue(r.context["health_down"])

    def test_alerts_script_included_once(self):
        # Скрипт был внутри admin_content и подключался дважды → двойной bulk-resolve
        html = self.client.get(reverse("adminui:alerts")).content.decode()
        self.assertEqual(html.count("adminui-alerts.js"), 1)

    def test_flash_messages_are_rendered(self):
        # Раньше base_admin.html вообще не выводил messages — ошибки «молча» терялись
        from apps.devices.models import Vendor
        v = Vendor.objects.create(name="Acme", slug="acme")
        self.down.vendor = v
        self.down.save(update_fields=["vendor"])
        r = self.client.post(reverse("adminui:vendor_delete", args=[v.pk]))
        self.assertContains(r, 'class="flash flash--crit"')
        self.assertContains(r, "Нельзя удалить вендора")

    def test_user_search(self):
        User.objects.create_user("ivanov", first_name="Иван", last_name="Иванов", password="x-12345")
        r = self.client.get(reverse("adminui:users"), {"q": "Иванов"})
        self.assertEqual([u.username for u in r.context["users"]], ["ivanov"])
