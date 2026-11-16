"""
Тесты модели доступа, Telegram-привязки, фильтра уведомлений и сторожевого сигнала.

Запуск:  docker compose -f docker-compose.yml -f docker-compose.dev.yml exec web \
             python manage.py test apps.accounts apps.edocs --noinput
"""
from datetime import time as dtime, timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.access import EDIT, NONE, VIEW, Access, rule_for
from apps.accounts.models import (
    AccessChangeLog, TelegramAccount, TelegramLinkCode, User, UserPreferences,
)

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "tests"}}


def make_user(username, sections=None, caps=None, **kw):
    u = User.objects.create_user(username=username, password="pass-12345", is_active=True, **kw)
    u.access_sections = sections or {}
    u.access_caps = caps or []
    u.save()
    return u


# ── Реестр: адрес → что нужно ─────────────────────────────────────────────────
class RuleForTests(TestCase):
    def test_section_view_and_edit(self):
        r = rule_for("/local/subnets/", "GET")
        self.assertEqual((r.section, r.level), ("subnets", VIEW))
        r = rule_for("/api/v1/subnets/1/scan/", "POST")
        self.assertEqual((r.section, r.level), ("subnets", EDIT))

    def test_capabilities(self):
        self.assertEqual(rule_for("/api/v1/subnets/tcpdump/", "POST").cap, "traffic_capture")
        self.assertEqual(rule_for("/api/v1/interfaces/5/set-admin-status/", "POST").cap, "port_control")
        self.assertEqual(rule_for("/admin-ui/users/", "GET").cap, "users_manage")
        self.assertEqual(rule_for("/admin-ui/announcements/", "GET").cap, "announcements")

    def test_gateway_poll_belongs_to_mux(self):
        self.assertEqual(rule_for("/api/v1/gateway/10.0.0.1/poll-now/", "POST").section, "mux")

    def test_unknown_api_denied_and_open_pages(self):
        self.assertTrue(rule_for("/api/v1/no-such-thing/", "GET").deny)
        self.assertIsNone(rule_for("/accounts/profile/", "GET"))
        self.assertIsNone(rule_for("/api/v1/telegram/webhook/", "POST"))

    def test_admin_settings_need_edit(self):
        r = rule_for("/admin-ui/subnets/", "GET")
        self.assertEqual((r.section, r.level), ("subnets", EDIT))


# ── Access ────────────────────────────────────────────────────────────────────
class AccessTests(TestCase):
    def test_levels_and_caps(self):
        u = make_user("eng", {"subnets": VIEW, "devices": EDIT}, ["port_control", "traffic_capture"])
        acc = Access.for_user(u)
        self.assertTrue(acc.can_view("subnets"))
        self.assertFalse(acc.can_edit("subnets"))
        self.assertTrue(acc.can_edit("devices"))
        self.assertFalse(acc.can_view("servers"))
        self.assertTrue(acc.has_cap("port_control"))
        self.assertEqual(acc.edit_keys, "devices")

    def test_superuser_has_everything(self):
        su = User.objects.create_superuser("root", password="x-12345")
        acc = Access.for_user(su)
        self.assertTrue(acc.can_edit("servers"))
        self.assertTrue(acc.has_cap("users_manage"))

    def test_garbage_in_json_is_ignored(self):
        u = make_user("g", {"subnets": "99", "evil": 2, "devices": "x"}, ["nope", "port_control"])
        acc = Access.for_user(u)
        self.assertIn(acc.level("subnets"), (NONE, VIEW, EDIT))
        self.assertNotIn("evil", acc.edit_keys)
        self.assertFalse(acc.can_view("devices"))
        self.assertNotIn("nope", acc.cap_keys)


# ── Middleware через настоящие запросы ───────────────────────────────────────
@override_settings(CACHES=LOCMEM)
class MiddlewareTests(TestCase):
    def setUp(self):
        self.viewer = make_user("viewer", {"subnets": VIEW})
        self.editor = make_user("editor", {"subnets": EDIT})

    def test_view_only_cannot_write(self):
        self.client.force_login(self.viewer)
        r = self.client.post("/api/v1/subnets/", {"cidr": "10.99.0.0/24"})
        self.assertEqual(r.status_code, 403)

    def test_no_section_no_read(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get("/servers/").status_code, 403)
        self.assertEqual(self.client.get("/api/v1/devices/").status_code, 403)

    def test_view_can_read(self):
        self.client.force_login(self.viewer)
        self.assertNotEqual(self.client.get("/api/v1/subnets/").status_code, 403)

    def test_tcpdump_requires_cap_even_for_editor(self):
        self.client.force_login(self.editor)
        r = self.client.post("/api/v1/subnets/tcpdump/", {}, content_type="application/json")
        self.assertEqual(r.status_code, 403)

    def test_admin_users_requires_users_manage(self):
        self.client.force_login(self.editor)
        self.assertEqual(self.client.get("/admin-ui/users/").status_code, 403)

    def test_body_carries_access_for_ui(self):
        self.client.force_login(self.viewer)
        html = self.client.get("/accounts/profile/").content.decode()
        self.assertIn('data-acc-e=""', html)
        self.client.force_login(self.editor)
        html = self.client.get("/accounts/profile/").content.decode()
        self.assertIn('data-acc-e="subnets"', html)


# ── Подтверждение регистрации и журнал прав ──────────────────────────────────
@override_settings(CACHES=LOCMEM)
class ApproveAndLogTests(TestCase):
    def setUp(self):
        self.admin = make_user("boss", {}, ["users_manage"])
        self.newbie = User.objects.create_user("newbie", password="x-12345", is_active=False,
                                               registration_status="pending")

    def test_approve_sets_access_and_logs(self):
        self.client.force_login(self.admin)
        r = self.client.post(f"/admin-ui/users/{self.newbie.pk}/approve/",
                             {"acc_subnets": "1", "acc_edocs": "2", "acc_caps": []})
        self.assertEqual(r.status_code, 302)
        self.newbie.refresh_from_db()
        self.assertTrue(self.newbie.is_active)
        self.assertEqual(self.newbie.access.level("edocs"), EDIT)
        log = AccessChangeLog.objects.get(target=self.newbie)
        self.assertEqual((log.action, log.actor_id), ("approve", self.admin.pk))
        self.assertIn("Подсети: Нет → Просмотр", log.changes)

    def test_non_superuser_cannot_grant_superuser(self):
        self.client.force_login(self.admin)
        u = make_user("plain", {"subnets": VIEW})
        self.client.post(f"/admin-ui/users/{u.pk}/edit/", {
            "username": "plain", "is_active": "on", "is_superuser": "on", "is_staff": "on",
            "acc_subnets": "1",
        })
        u.refresh_from_db()
        self.assertFalse(u.is_superuser)


# ── Telegram: привязка токеном ───────────────────────────────────────────────
@override_settings(CACHES=LOCMEM)
class TelegramLinkTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("tg", {"devices": VIEW})

    def test_token_hashed_one_time(self):
        from apps.accounts.telegram_link import consume_token, issue_token
        token = issue_token(self.user)
        self.assertFalse(TelegramLinkCode.objects.filter(code=token).exists())  # в БД только хэш
        user, err = consume_token(token, 111, "nick")
        self.assertEqual((user, err), (self.user, ""))
        self.user.refresh_from_db()
        self.assertEqual(self.user.telegram_id, 111)
        self.assertTrue(TelegramAccount.objects.filter(user=self.user, tg_id=111).exists())
        user, err = consume_token(token, 222)
        self.assertIsNone(user)

    def test_expired_token(self):
        from apps.accounts.telegram_link import consume_token, issue_token
        token = issue_token(self.user)
        TelegramLinkCode.objects.update(created_at=timezone.now() - timedelta(minutes=11))
        self.assertIsNone(consume_token(token, 333)[0])

    def test_rate_limit(self):
        from apps.accounts.telegram_link import consume_token, issue_token
        for _ in range(5):
            consume_token("wrong-token", 444)
        token = issue_token(self.user)
        user, err = consume_token(token, 444)  # даже верный — после 5 ошибок
        self.assertIsNone(user)
        self.assertIn("попыток", err)

    def test_relink_moves_telegram(self):
        from apps.accounts.telegram_link import consume_token, issue_token
        other = make_user("other")
        consume_token(issue_token(self.user), 555)
        consume_token(issue_token(other), 555)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.telegram_id)
        self.assertEqual(TelegramAccount.objects.get(tg_id=555).user, other)


# ── Telegram: кому уходит алерт ──────────────────────────────────────────────
class NotifyFilterTests(TestCase):
    def test_can_see_by_section(self):
        from apps.accounts.telegram_notify import _can_see
        meta = {"section": "subnets"}
        self.assertTrue(_can_see((False, {"subnets": VIEW}), meta))
        self.assertFalse(_can_see((False, {"devices": EDIT}), meta))
        self.assertTrue(_can_see((True, {}), meta))

    def test_prefs(self):
        from apps.accounts.telegram_notify import _wants
        u = make_user("p")
        prefs = UserPreferences.for_user(u)
        warn = {"kind": "alert", "severity": "warn", "section": "devices"}
        crit = {"kind": "alert", "severity": "crit", "section": "devices"}
        resolved = {"kind": "alert_resolved", "severity": "crit", "section": "devices"}
        self.assertTrue(_wants(prefs, warn))
        prefs.notify_min_severity = "crit"
        self.assertFalse(_wants(prefs, warn))
        self.assertTrue(_wants(prefs, crit))
        prefs.notify_resolved = False
        self.assertFalse(_wants(prefs, resolved))

    def test_quiet_hours(self):
        from apps.accounts.telegram_notify import _wants
        u = make_user("q")
        prefs = UserPreferences.for_user(u)
        prefs.quiet_enabled, prefs.quiet_from, prefs.quiet_to = True, dtime(22, 0), dtime(7, 0)
        prefs.quiet_allow_crit = True
        night = timezone.localtime().replace(hour=23, minute=0)
        self.assertFalse(_wants(prefs, {"kind": "alert", "severity": "warn"}, night))
        self.assertTrue(_wants(prefs, {"kind": "alert", "severity": "crit"}, night))

    def test_send_to_all_respects_access(self):
        from apps.accounts import telegram_notify
        a = make_user("has", {"subnets": VIEW})
        b = make_user("hasnot", {"devices": EDIT})
        TelegramAccount.objects.create(user=a, tg_id=1)
        TelegramAccount.objects.create(user=b, tg_id=2)
        with mock.patch.object(telegram_notify, "send_telegram_message", return_value=True) as send:
            n = telegram_notify._send_to_all("x", meta={"kind": "alert", "severity": "warn",
                                                        "section": "subnets"})
        self.assertEqual(n, 1)
        self.assertEqual(send.call_args[0][0], 1)


# ── Сторожевой сигнал Celery ─────────────────────────────────────────────────
@override_settings(CACHES=LOCMEM)
class WatchdogTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_down_and_recovery_notify_once(self):
        from apps.core import watchdog as w
        sent = []
        T = 1_000_000.0
        with mock.patch.object(w, "_send", side_effect=sent.append):
            self.assertEqual(w.check_and_notify(T), set())  # старт: тишина допустима
            for q in w.QUEUES:
                cache.set(w.beat_key(q), T + 400)
            cache.set(w.beat_key("low"), T)
            self.assertEqual(w.check_and_notify(T + 420), {"low"})
            self.assertEqual(w.check_and_notify(T + 500), {"low"})
            self.assertEqual(len(sent), 1)
            for q in w.QUEUES:
                cache.set(w.beat_key(q), T + 600)
            self.assertEqual(w.check_and_notify(T + 610), set())
        self.assertEqual(len(sent), 2)
        self.assertIn("снова работают", sent[1])
