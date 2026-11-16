"""Дымовой тест: все страницы без параметров открываются без 500 (суперпользователь и «без прав»)."""
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver

from apps.accounts.models import User

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "smoke"}}
SKIP = ("/accounts/logout", "/admin/", "/media/", "/static/", "/__debug__", "/servers/api/agent/",
        "/api/v1/telegram/", "/ui-theme")


def _paths(resolver=None, prefix=""):
    resolver = resolver or get_resolver()
    for p in resolver.url_patterns:
        route = str(p.pattern)
        if isinstance(p, URLResolver):
            yield from _paths(p, prefix + route)
        elif isinstance(p, URLPattern):
            full = "/" + (prefix + route).lstrip("^").rstrip("$")
            if "<" in full or "(" in full or "\\" in full or "?" in full:
                continue
            yield full.replace("//", "/")


@override_settings(CACHES=LOCMEM)
class SmokeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.su = User.objects.create_superuser("root", password="x-12345")
        cls.nobody = User.objects.create_user("nobody", password="x-12345", is_active=True)

    def _walk(self, user):
        self.client.force_login(user)
        bad = []
        paths = sorted(set(_paths()))
        # Внешние вызовы (SNMP, ping, Telegram, docker) в тестах не нужны
        with mock.patch("apps.accounts.telegram_notify._post", return_value=(False, "test")):
            for path in paths:
                if path.startswith(SKIP):
                    continue
                try:
                    r = self.client.get(path)
                except Exception as e:  # noqa: BLE001
                    bad.append(f"{path} → {type(e).__name__}: {e}"[:200])
                    continue
                if r.status_code >= 500:
                    bad.append(f"{path} → {r.status_code}")
        return len(paths), bad

    def test_superuser_pages(self):
        n, bad = self._walk(self.su)
        self.assertGreater(n, 50)
        self.assertEqual(bad, [], "\n".join(bad))

    def test_no_access_pages_never_500(self):
        _, bad = self._walk(self.nobody)
        self.assertEqual(bad, [], "\n".join(bad))
