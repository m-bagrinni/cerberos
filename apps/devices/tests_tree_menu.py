"""Контекстное меню дерева /local/ (static/js/tree-menu.js): страница и адреса, которые оно вызывает."""
import json
from django.contrib.auth import get_user_model
from django.test import TestCase
from apps.devices.models import Device, DeviceCategory, DeviceSubcategory


class TreeMenuApiTests(TestCase):
    def setUp(self):
        self.u = get_user_model().objects.create_superuser("su", "s@x.org", "pw")
        self.client.force_login(self.u)
        self.cat = DeviceCategory.objects.create(slug="sw", name="Коммутаторы")
        self.cat2 = DeviceCategory.objects.create(slug="rt", name="Роутеры")
        self.d = Device.objects.create(name="sw-1", ip="10.0.0.1", category=self.cat)
        Device.objects.create(name="lost", ip="10.0.0.9")

    def post(self, url, data, method="post"):
        return getattr(self.client, method)(url, json.dumps(data), content_type="application/json")

    def test_create_move_rename_delete(self):
        r = self.post("/admin-ui/subcategories/create-api/", {"category_slug": "sw", "name": "Этаж 1"})
        self.assertEqual(r.status_code, 200, r.content); slug = r.json()["slug"]
        html = self.client.get("/local/").content.decode()
        self.assertIn('id="tree-menu-data"', html); self.assertIn("tree-menu.js", html)
        self.assertIn("Этаж 1", html)            # пустая подкатегория видна
        self.assertIn("Без категории", html)     # устройство без категории не теряется
        r = self.post("/local/devices/move-subcategory/", {"ip": "10.0.0.1", "subcategory_slug": slug}); self.assertTrue(r.json()["ok"])
        r = self.post("/admin-ui/subcategories/rename-api/", {"slug": slug, "name": "Этаж 2"}); self.assertTrue(r.json()["ok"])
        r = self.post("/api/v1/devices/10.0.0.1/", {"name": "sw-renamed"}, "patch"); self.assertEqual(r.status_code, 200, r.content)
        r = self.post("/admin-ui/subcategories/delete-api/", {"slug": slug}); self.assertEqual(r.json()["devices"], 1)
        self.d.refresh_from_db()
        self.assertEqual((self.d.name, self.d.category_id, self.d.subcategory_id), ("sw-renamed", self.cat.pk, None))

    def test_tree_order_drag(self):
        sub = DeviceSubcategory.objects.create(slug="f1", name="Этаж 1", category=self.cat)
        Device.objects.create(name="sw-2", ip="10.0.0.2", category=self.cat, subcategory=sub)
        # устройство из «Без категории» бросили в подкатегорию первым
        r = self.post("/local/devices/tree-order/", {"category": "sw", "subcategory": "f1", "items": [
            {"type": "device", "id": "10.0.0.9"}, {"type": "device", "id": "10.0.0.2"}]})
        self.assertTrue(r.json()["ok"], r.content)
        lost = Device.objects.get(ip="10.0.0.9")
        self.assertEqual((lost.category_id, lost.subcategory_id), (self.cat.pk, sub.pk))
        self.assertLess(lost.sort_order, Device.objects.get(ip="10.0.0.2").sort_order)
        # подкатегорию перенесли в другую категорию — устройства едут с ней
        r = self.post("/local/devices/tree-order/", {"category": "rt", "subcategory": None,
                                                     "items": [{"type": "subcategory", "id": "f1"}]})
        self.assertTrue(r.json()["ok"])
        self.assertEqual(Device.objects.get(ip="10.0.0.2").category_id, self.cat2.pk)
        # в «Без категории»
        self.post("/local/devices/tree-order/", {"category": "_none", "items": [{"type": "device", "id": "10.0.0.1"}]})
        self.assertIsNone(Device.objects.get(ip="10.0.0.1").category_id)
        # порядок категорий
        self.post("/local/devices/tree-order/", {"categories": ["rt", "sw"]})
        self.assertEqual(list(DeviceCategory.objects.order_by("sort_order").values_list("slug", flat=True)), ["rt", "sw"])
