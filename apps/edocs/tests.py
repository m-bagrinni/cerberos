from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.access import EDIT, VIEW
from apps.accounts.models import User
from apps.edocs.models import Document

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "edocs-tests"}}


@override_settings(CACHES=LOCMEM)
class CreateDocumentTests(TestCase):
    def setUp(self):
        cache.clear()
        self.author = User.objects.create_user("author", password="x-12345", is_active=True)
        self.author.access_sections = {"edocs": EDIT}
        self.author.save()
        self.recipient = User.objects.create_user("rcpt", password="x-12345", is_active=True)

    def test_create_without_message_no_500(self):
        self.client.force_login(self.author)
        r = self.client.post("/edocs/create/", {"title": "Акт", "recipient": self.recipient.pk,
                                               "document_type": "execution"})
        self.assertIn(r.status_code, (200, 302))
        self.assertEqual(Document.objects.get(title="Акт").message, "")

    def test_view_only_cannot_create(self):
        self.author.access_sections = {"edocs": VIEW}
        self.author.save()
        self.client.force_login(self.author)
        r = self.client.post("/edocs/create/", {"title": "Акт", "recipient": self.recipient.pk})
        self.assertEqual(r.status_code, 403)
