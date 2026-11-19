"""
Полуживой NE (отвечает раз в несколько циклов) не должен закрывать/переоткрывать аварию по кругу:
раньше один удачный ответ = «восстановлено» в Telegram, через 2 цикла — снова авария (молча), и так
каждые 3–4 минуты (NE «Муньке», линия merke→munke, 2026-11-18).
"""
from unittest import mock

from django.test import TestCase, override_settings

from apps.devices.models import Device, NeDevice

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "ne-flap"}}


def _unwrapped(task):
    fn = task.run
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


@override_settings(CACHES=LOCMEM)
class NeHysteresisTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        gw = Device.objects.create(name="gw-olencom", ip="10.5.5.5")
        self.ne = NeDevice.objects.create(gateway=gw, ne_id=17, name="Муньке")

    def run_cycles(self, answers):
        from django.core.cache import cache
        from apps.monitoring import tasks
        poll = _unwrapped(tasks.poll_ne_availability_fast)
        seen = []
        for ok in answers:
            with mock.patch.object(tasks, "_check_ne_alive_fast", lambda nid, ok=ok: (nid, ok, None)):
                poll()
            seen.append(cache.get(f"ne_alive_eff_{self.ne.pk}"))
        return seen

    def test_single_answer_does_not_recover(self):
        # жив → 2 провала (авария) → 1 ответ → провалы: подтверждённо мёртв всё время после аварии
        self.assertEqual(self.run_cycles([True, False, False, True, False, False, True, False]),
                         [1, 1, 0, 0, 0, 0, 0, 0])

    def test_three_answers_in_a_row_recover(self):
        self.assertEqual(self.run_cycles([False, False, True, True, True]), [1, 0, 0, 0, 1])

    def test_status_follows_confirmation(self):
        self.run_cycles([False, False, False])
        self.ne.refresh_from_db()
        self.assertEqual(self.ne.status, "unreachable")
        self.run_cycles([True])
        self.ne.refresh_from_db()
        self.assertEqual(self.ne.status, "unreachable")     # один ответ — ещё не восстановился
        self.run_cycles([True, True])
        self.ne.refresh_from_db()
        self.assertEqual(self.ne.status, "ok")


@override_settings(CACHES=LOCMEM)
class ResolvedNotifyDedupTests(TestCase):
    def test_same_alert_resolved_twice_sent_once(self):
        from django.core.cache import cache
        from apps.accounts import telegram_notify as tn
        cache.clear()
        alert = mock.Mock(id=1824)
        with mock.patch("apps.alerts.correlation.should_announce_resolve", return_value=True), \
             mock.patch.object(tn, "_resolved_text", return_value="ok"), \
             mock.patch.object(tn, "_alert_meta", return_value={}), \
             mock.patch.object(tn, "_dispatch", return_value=3) as dispatch:
            self.assertEqual(tn.notify_alert_resolved(alert), 3)
            self.assertEqual(tn.notify_alert_resolved(alert), 0)
            self.assertEqual(tn.notify_alert_resolved(mock.Mock(id=1825)), 3)
        self.assertEqual(dispatch.call_count, 2)
