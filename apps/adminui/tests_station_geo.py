"""Станция: место выбирается на карте (adminui/stations/form.html + static/js/adminui-station-geo.js)."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.stations.models import Station


class StationGeoTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("su", "s@x.org", "pw"))
        Station.objects.create(name="ЛАЗ", slug="laz", latitude=42.8746, longitude=74.5698)
        Station.objects.create(name="Без точки", slug="nogeo")

    def test_form_has_map_and_other_stations(self):
        r = self.client.get("/admin-ui/stations/new/")
        self.assertContains(r, 'id="station-geo-map"')
        self.assertContains(r, "adminui-station-geo.js")
        self.assertContains(r, "leaflet.js")
        self.assertEqual(r.context["stations_geo"], [{"name": "ЛАЗ", "lat": 42.8746, "lon": 74.5698}])

    def test_save_coordinates(self):
        r = self.client.post("/admin-ui/stations/new/", {
            "name": "УКЖД", "slug": "ukjd-new", "latitude": "42.876543", "longitude": "74.601234", "sort_order": 0})
        self.assertEqual(r.status_code, 302, r.context["form"].errors if r.context else "")
        st = Station.objects.get(slug="ukjd-new")
        self.assertEqual((st.latitude, st.longitude), (42.876543, 74.601234))
        # правка: своя точка не попадает в «остальные»
        r = self.client.get(f"/admin-ui/stations/{st.pk}/edit/")
        self.assertNotIn("УКЖД", [s["name"] for s in r.context["stations_geo"]])

    def test_light_api_has_geo(self):
        r = self.client.get("/api/v1/stations/?light=1").json()
        rows = r.get("results", r)
        laz = next(s for s in rows if s["slug"] == "laz")
        self.assertEqual((laz["latitude"], laz["longitude"]), (42.8746, 74.5698))
