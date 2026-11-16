from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.access import EDIT, VIEW
from apps.accounts.models import User
from apps.alerts.models import Alert

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "alerts-tests"}}


def mk_user(name, level):
    u = User.objects.create_user(name, password="x-12345", is_active=True, first_name=name.title())
    u.access_sections = {"alerts": level, "devices": VIEW}
    u.save()
    return u


@override_settings(CACHES=LOCMEM)
@mock.patch("apps.accounts.telegram_notify._dispatch", return_value=0)
class AckTests(TestCase):
    def setUp(self):
        cache.clear()
        self.duty = mk_user("duty", EDIT)
        self.viewer = mk_user("viewer", VIEW)
        self.alert = Alert.objects.create(metric_key="if_oper_status", severity=Alert.SEVERITY_CRIT)

    def test_ack_via_api(self, dispatch):
        self.client.force_login(self.duty)
        r = self.client.post(f"/api/v1/alerts/{self.alert.pk}/ack/")
        self.assertEqual(r.status_code, 200)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.acked_by, self.duty)
        self.assertEqual(dispatch.call_args[0][3]["kind"], "alert_ack")

    def test_view_only_cannot_ack(self, dispatch):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(f"/api/v1/alerts/{self.alert.pk}/ack/").status_code, 403)

    def test_second_person_gets_conflict(self, dispatch):
        from apps.alerts.ack import acknowledge
        acknowledge(self.alert.pk, self.duty)
        other = mk_user("other", EDIT)
        alert, err = acknowledge(self.alert.pk, other)
        self.assertIn("Уже в работе", err)
        self.assertEqual(dispatch.call_count, 1)

    def test_tg_button_only_when_unacked(self, dispatch):
        from apps.accounts.telegram_notify import _reply_markup
        kb = _reply_markup(self.alert)
        self.assertEqual(kb["inline_keyboard"][0][0]["callback_data"], f"ack:{self.alert.pk}")
        self.alert.acked_by = self.duty
        self.assertNotIn("ack:", str(_reply_markup(self.alert)))

    def test_escalation_once(self, dispatch):
        from apps.alerts.ack import escalate_unacked
        Alert.objects.filter(pk=self.alert.pk).update(started_at=timezone.now() - timedelta(minutes=20))
        warn = Alert.objects.create(metric_key="x", severity=Alert.SEVERITY_WARN)
        Alert.objects.filter(pk=warn.pk).update(started_at=timezone.now() - timedelta(minutes=20))
        self.assertEqual(escalate_unacked(), 1)
        self.assertEqual(escalate_unacked(), 0)
        self.assertEqual(dispatch.call_args[0][3]["kind"], "alert_escalation")

    def test_no_escalation_if_acked(self, dispatch):
        from apps.alerts.ack import acknowledge, escalate_unacked
        Alert.objects.filter(pk=self.alert.pk).update(started_at=timezone.now() - timedelta(minutes=20))
        acknowledge(self.alert.pk, self.duty)
        self.assertEqual(escalate_unacked(), 0)
