"""Telegram-бот: маршруты, права, обход всех экранов, ввод текста."""
from collections import deque
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.access import EDIT, VIEW, Access
from apps.accounts.bot import core
from apps.accounts.bot.ui import Notice
from apps.accounts.models import TelegramAccount, User
from apps.alerts.models import Alert
from apps.devices.models import Device

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "bot-tests"}}
MUTATING = {"al.a", "al.x", "al.R", "dc.S", "dv.p", "ne.p", "ne.P", "ip.p", "ip.m", "me.t", "me.v", "me.U",
            "pt.T", "sy.A", "st.f", "noop", "dc.f", "dc.a"}


def ctx_for(user, tg_id=1):
    return core.Ctx(user=user, acc=Access.for_user(user), tg_id=tg_id)


@override_settings(CACHES=LOCMEM)
@mock.patch("apps.services.api._docker_ps", return_value=[
    {"name": "cerber-web-1", "short_name": "web", "status": "Up 1 hour", "state": "running", "is_up": True}])
@mock.patch("apps.accounts.telegram_notify._dispatch", return_value=0)
class BotTests(TestCase):
    def setUp(self):
        cache.clear()
        self.su = User.objects.create_superuser("root", password="x-12345")
        self.duty = User.objects.create_user("duty", password="x-12345", is_active=True)
        self.duty.access_sections = {"alerts": EDIT, "devices": VIEW, "stations": VIEW}
        self.duty.save()
        TelegramAccount.objects.create(user=self.duty, tg_id=42)
        self.dev = Device.objects.create(name="sw-core", ip="10.0.0.1")
        self.alert = Alert.objects.create(device=self.dev, metric_key="if_oper_status:1",
                                          severity=Alert.SEVERITY_CRIT, message="NO LINK")

    def crawl(self, user):
        ctx, seen, q, errors = ctx_for(user), set(), deque(["home"]), []
        while q and len(seen) < 600:
            cb = q.popleft()
            if cb in seen:
                continue
            seen.add(cb)
            key, _, rest = cb.partition(":")
            if key in MUTATING or key in ("ack", "res", "rel"):
                continue
            try:
                scr = core.run(key, ctx, rest.split(":") if rest else [])
            except Notice:
                continue
            except Exception as e:  # noqa: BLE001
                errors.append(f"{cb}: {type(e).__name__}: {e}")
                continue
            self.assertLessEqual(len(scr.text), 4096, cb)
            for row in (scr.kb.rows if scr.kb else []):
                for b in row:
                    if b.callback_data:
                        k = b.callback_data.split(":")[0]
                        self.assertTrue(k in core.ROUTES or k in ("ack", "res", "rel"), b.callback_data)
                        q.append(b.callback_data)
        return seen, errors

    def test_crawl_superuser(self, *_):
        seen, errors = self.crawl(self.su)
        self.assertEqual(errors, [], "\n".join(errors))
        self.assertIn(f"al.c:{self.alert.pk}", seen)
        self.assertIn(f"dv.c:{self.dev.pk}", seen)

    def test_crawl_limited_never_sees_forbidden(self, *_):
        seen, errors = self.crawl(self.duty)
        self.assertEqual(errors, [])
        self.assertFalse(any(cb.startswith(("sv.", "sy.", "dc.", "sn.", "ne.")) for cb in seen), seen)

    def test_route_rights(self, *_):
        ctx = ctx_for(self.duty)
        with self.assertRaises(Notice):
            core.run("sy.l", ctx, [])            # нет раздела «Службы»
        with self.assertRaises(Notice):
            core.run("dv.p", ctx, [str(self.dev.pk)])   # устройства только просмотр

    def test_resolve_with_comment_via_text(self, *_):
        ctx = ctx_for(self.duty, tg_id=42)
        core.run("al.a", ctx, [str(self.alert.pk)])
        scr = core.run("al.r", ctx, [str(self.alert.pk)])
        self.assertIn("Что сделано", scr.text)
        scr = core.run_input(ctx, "Перезагрузил коммутатор")
        self.alert.refresh_from_db()
        self.assertEqual((self.alert.state, self.alert.resolution), ("ok", "Перезагрузил коммутатор"))
        self.assertIn("Алерт закрыт", scr.text)

    def test_text_without_wait_is_search(self, *_):
        scr = core.run_input(ctx_for(self.duty), "sw-co")   # единственный результат — сразу карточка
        self.assertIn("sw-core", scr.text)

    def test_not_linked(self, *_):
        self.assertIsNone(core.load_ctx(999))
        self.assertIsNotNone(core.load_ctx(42))
