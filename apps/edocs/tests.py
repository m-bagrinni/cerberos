"""
Тесты СЭД: маршрут документа (services), права, страницы, файлы, журнал.
(Миграции в тестах отключены — cerber/settings/base.py; перенос данных 0013 проверяется на копии БД.)

Запуск:  docker compose -f docker-compose.yml -f docker-compose.dev.yml exec web \
             python manage.py test apps.edocs --noinput
"""
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.access import EDIT, VIEW
from apps.accounts.models import User
from apps.edocs import services as svc
from apps.edocs.models import Assignment, Attachment, Document, DocumentRoute
from apps.edocs.queries import INBOX_FILTERS, pending_counts, pending_qs

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "edocs-tests"}}
MEDIA = tempfile.mkdtemp(prefix="edocs-test-media-")


def mk(username, level=EDIT, caps=None, **kw):
    u = User.objects.create_user(username, password="x-12345", is_active=True, **kw)
    u.access_sections = {"edocs": level} if level else {}
    u.access_caps = caps or []
    u.save()
    return User.objects.get(pk=u.pk)


def upl(name="act.pdf", data=b"%PDF-1.4 test"):
    return SimpleUploadedFile(name, data, content_type="application/octet-stream")


@override_settings(CACHES=LOCMEM, MEDIA_ROOT=MEDIA)
class BaseCase(TestCase):
    """Уведомления (Celery) и WebSocket подменены; self.notify — мок notify_users.delay."""
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()
        for target in ("apps.edocs.services.broadcast", "apps.edocs.tasks.notify_users.delay"):
            p = mock.patch(target)
            setattr(self, "notify" if target.endswith("delay") else "bc", p.start())
            self.addCleanup(p.stop)
        self.author = mk("author", last_name="Автор", first_name="Анна")
        self.ivanov = mk("ivanov", last_name="Иванов", first_name="Иван")
        self.petrov = mk("petrov", last_name="Петров", first_name="Пётр")
        self.sidorov = mk("sidorov", last_name="Сидоров", first_name="Сидор")
        self.outsider = mk("outsider")

    def send(self, doc_type="execution", main=None, co=(), due=None, files=(), **kw):
        with self.captureOnCommitCallbacks(execute=True):
            return svc.save_document(self.author, title=kw.pop("title", "Акт приёмки"), message="Текст",
                                     document_type=doc_type, due_date=due, main_id=(main or self.ivanov).pk,
                                     co_ids=[u.pk for u in co], files=list(files), send=True, **kw)


class WorkflowTests(BaseCase):
    def test_send_registers_and_assigns(self):
        doc = self.send(co=[self.petrov], files=[upl()])
        year = timezone.localdate().year
        self.assertEqual(doc.reg_number, f"ИСП-{year}/0001")
        self.assertEqual(doc.status, "review")
        self.assertEqual(doc.recipient, self.ivanov)
        roles = dict(doc.assignments.values_list("user__username", "role"))
        self.assertEqual(roles, {"ivanov": "main", "petrov": "co"})
        self.assertEqual(doc.attachments.get().name, "act.pdf")
        self.assertTrue(DocumentRoute.objects.filter(document=doc, action="sent").exists())
        notified = sorted(uid for uid, kind in self.notify.call_args.args[1])
        self.assertEqual(notified, sorted([self.ivanov.pk, self.petrov.pk]))
        # Нумерация по виду документа идёт подряд
        self.assertEqual(self.send(title="Второй").reg_number, f"ИСП-{year}/0002")
        self.assertEqual(self.send("approval").reg_number, f"СОГ-{year}/0001")

    def test_author_not_assignee_and_duplicates_dropped(self):
        doc = self.send(co=[self.ivanov, self.author, self.petrov, self.petrov])
        self.assertEqual(sorted(doc.assignments.values_list("user__username", flat=True)), ["ivanov", "petrov"])

    def test_past_due_rejected(self):
        with self.assertRaises(svc.EdocsError):
            self.send(due=timezone.localdate() - timedelta(days=1))

    def test_bad_extension_rejected(self):
        with self.assertRaises(svc.EdocsError):
            self.send(files=[upl("x.html", b"<script>")])
        self.assertFalse(Document.objects.exists())

    def test_completed_only_when_everyone_done(self):
        doc = self.send(co=[self.petrov])
        with self.captureOnCommitCallbacks(execute=True):
            svc.decide(self.ivanov, doc, "done", "Сделано", [upl("report.pdf")])
        doc.refresh_from_db()
        self.assertEqual(doc.status, "review")
        self.assertEqual(self.notify.call_args.args[1], [(self.author.pk, "done")])
        svc.decide(self.petrov, doc, "done")
        doc.refresh_from_db()
        self.assertEqual(doc.status, "approved")
        self.assertIsNotNone(doc.completed_at)
        self.assertTrue(doc.routes.filter(action="completed").exists())
        self.assertEqual(Attachment.objects.get(kind="reply").assignment.user, self.ivanov)
        with self.assertRaises(svc.EdocsError):
            svc.decide(self.petrov, doc, "done")

    def test_reject_needs_reason_then_revision_cycle(self):
        doc = self.send()
        with self.assertRaises(svc.EdocsError):
            svc.decide(self.ivanov, doc, "rejected", "")
        svc.decide(self.ivanov, doc, "rejected", "Нет подписи")
        doc.refresh_from_db()
        self.assertEqual(doc.status, "rejected")
        with self.assertRaises(svc.EdocsError):
            svc.return_for_revision(self.ivanov, doc, "не автор")
        with self.assertRaises(svc.EdocsError):
            svc.return_for_revision(self.author, doc, "")
        new_due = timezone.localdate() + timedelta(days=5)
        svc.return_for_revision(self.author, doc, "Подпишите", new_due)
        doc.refresh_from_db()
        self.assertEqual((doc.status, doc.due_date), ("revision", new_due))
        self.assertEqual(doc.assignments.get().status, "pending")
        self.assertEqual(pending_counts(self.ivanov)["revision"], 1)
        svc.decide(self.ivanov, doc, "done", "Подписано")
        doc.refresh_from_db()
        self.assertEqual(doc.status, "approved")

    def test_acknowledge_for_review(self):
        doc = self.send("review", co=[self.petrov])
        self.assertFalse(svc.allowed(self.ivanov, doc).decide)
        self.assertTrue(svc.allowed(self.ivanov, doc).acknowledge)
        with self.assertRaises(svc.EdocsError):
            svc.decide(self.ivanov, doc, "done")
        svc.acknowledge(self.ivanov, doc)
        svc.acknowledge(self.petrov, doc)
        doc.refresh_from_db()
        self.assertEqual((doc.status, doc.status_label), ("approved", "Ознакомлены"))

    def test_forward_moves_responsibility(self):
        doc = self.send()
        svc.forward(self.ivanov, doc, self.petrov.pk, "Прошу исполнить")
        doc.refresh_from_db()
        self.assertEqual(doc.recipient, self.petrov)
        self.assertEqual(doc.assignments.get(user=self.ivanov).status, "forwarded")
        self.assertIsNone(svc.my_assignment(self.ivanov, doc))
        self.assertTrue(svc.can_access(self.ivanov, doc))           # остаётся в истории
        with self.assertRaises(svc.EdocsError):
            svc.decide(self.ivanov, doc, "done")
        with self.assertRaises(svc.EdocsError):
            svc.forward(self.petrov, doc, self.author.pk)              # автору нельзя
        svc.decide(self.petrov, doc, "done")
        doc.refresh_from_db()
        self.assertEqual(doc.status, "approved")

    def test_add_assignee_reopens_progress(self):
        doc = self.send()
        svc.add_assignee(self.author, doc, self.sidorov.pk)
        with self.assertRaises(svc.EdocsError):
            svc.add_assignee(self.author, doc, self.sidorov.pk)
        svc.decide(self.ivanov, doc, "done")
        doc.refresh_from_db()
        self.assertEqual(doc.status, "review")

    def test_drafts(self):
        doc = svc.save_document(self.author, title="Черновик", main_id=self.ivanov.pk)
        self.assertEqual((doc.status, doc.reg_number), ("draft", None))
        self.assertFalse(svc.can_access(self.ivanov, doc))
        self.assertEqual(pending_counts(self.ivanov)["total"], 0)
        self.notify.assert_not_called()
        with self.assertRaises(svc.EdocsError):
            svc.send_draft(self.ivanov, doc)
        with self.captureOnCommitCallbacks(execute=True):
            svc.send_draft(self.author, doc)
        doc.refresh_from_db()
        self.assertEqual(doc.status, "review")
        self.assertTrue(doc.reg_number)
        self.notify.assert_called_once()

    def test_comment_rights(self):
        doc = self.send()
        svc.add_comment(self.ivanov, doc, "Принял")
        with self.assertRaises(svc.EdocsError):
            svc.add_comment(self.outsider, doc, "Я тут")
        with self.assertRaises(svc.EdocsError):
            svc.add_comment(self.author, doc, "   ")

    def test_counts_match_lists(self):
        today = timezone.localdate()
        self.send()
        self.send("approval", due=today)
        self.send("info")
        late = self.send(due=today + timedelta(days=1))
        Document.objects.filter(pk=late.pk).update(due_date=today - timedelta(days=2))
        c = pending_counts(self.ivanov)
        self.assertEqual(c["total"], 4)
        self.assertEqual(c["overdue"], 1)
        for key in list(INBOX_FILTERS) + ["overdue", "unread"]:
            self.assertEqual(pending_qs(self.ivanov, key).count(), c[key], key)


class PagesTests(BaseCase):
    def test_pages_render(self):
        doc = self.send(co=[self.petrov], files=[upl()], due=timezone.localdate() + timedelta(days=2))
        svc.add_comment(self.ivanov, doc, "Комментарий")
        draft = svc.save_document(self.author, title="Черновик", main_id=self.ivanov.pk)
        self.client.force_login(self.author)
        for url in ("/edocs/", "/edocs/?f=pending", "/edocs/?f=overdue&q=акт&type=execution&sort=due",
                    "/edocs/sent/?status=open&overdue=1", "/edocs/drafts/", "/edocs/create/",
                    "/edocs/create/?to=%d" % self.ivanov.pk, f"/edocs/document/{doc.pk}/",
                    f"/edocs/document/{draft.pk}/edit/", "/edocs/employees/?q=иван",
                    f"/edocs/employees/{self.ivanov.pk}/"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
        self.client.force_login(self.ivanov)
        r = self.client.get(f"/edocs/document/{doc.pk}/")
        self.assertContains(r, doc.reg_number)
        self.assertContains(r, "Поручено вам")
        self.assertTrue(doc.assignments.get(user=self.ivanov).is_read)
        r = self.client.get("/edocs/?f=pending")
        self.assertContains(r, "Акт приёмки")

    def test_create_via_form(self):
        self.client.force_login(self.author)
        r = self.client.post("/edocs/create/", {
            "title": "Акт", "document_type": "approval", "priority": "urgent", "main": self.ivanov.pk,
            "co": [self.petrov.pk], "due_date": (timezone.localdate() + timedelta(days=3)).isoformat(),
            "files": [upl("a.pdf"), upl("b.docx", b"PK")], "submit": "send"})
        doc = Document.objects.get(title="Акт")
        self.assertRedirects(r, f"/edocs/document/{doc.pk}/")
        self.assertEqual((doc.priority, doc.attachments.count(), doc.assignments.count()), ("urgent", 2, 2))

    def test_create_errors_keep_form(self):
        self.client.force_login(self.author)
        r = self.client.post("/edocs/create/", {"title": "", "main": self.ivanov.pk, "message": "мой текст"})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "мой текст", status_code=400)
        r = self.client.post("/edocs/create/", {"title": "Без адресата", "submit": "send"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/edocs/create/", {"title": "Только тема", "submit": "draft"})
        self.assertEqual(Document.objects.get(title="Только тема").status, "draft")

    def test_actions_via_http(self):
        doc = self.send()
        self.client.force_login(self.ivanov)
        r = self.client.post(f"/edocs/document/{doc.pk}/act/", {"action": "reject", "report": ""})
        self.assertRedirects(r, f"/edocs/document/{doc.pk}/")
        doc.refresh_from_db()
        self.assertEqual(doc.status, "review")
        self.client.post(f"/edocs/document/{doc.pk}/act/", {"action": "done", "report": "Готово",
                                                             "files": [upl("r.pdf")]})
        doc.refresh_from_db()
        self.assertEqual(doc.status, "approved")
        r = self.client.post(f"/edocs/document/{doc.pk}/act/", {"action": "comment", "text": "ok"},
                             HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r.json()["ok"], True)
        self.client.force_login(self.author)
        self.client.post(f"/edocs/document/{doc.pk}/act/", {"action": "revision", "comment": "Ещё раз"})
        doc.refresh_from_db()
        self.assertEqual(doc.status, "revision")

    def test_view_only_cannot_act(self):
        doc = self.send()
        viewer = mk("viewer", level=VIEW)
        Assignment.objects.create(document=doc, user=viewer, role="co")
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(f"/edocs/document/{doc.pk}/").status_code, 200)
        r = self.client.post(f"/edocs/document/{doc.pk}/act/", {"action": "done"})
        self.assertEqual(r.status_code, 403)
        r = self.client.post("/edocs/create/", {"title": "Акт", "main": self.ivanov.pk})
        self.assertEqual(r.status_code, 403)

    def test_outsider_and_files(self):
        doc = self.send(files=[upl("scan.png", b"\x89PNG")])
        att = doc.attachments.get()
        self.client.force_login(self.outsider)
        for url in (f"/edocs/document/{doc.pk}/", f"/edocs/file/{att.pk}/", f"/edocs/file/{att.pk}/download/"):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertEqual(self.client.post(f"/edocs/document/{doc.pk}/act/", {"action": "comment", "text": "x"})
                         .status_code, 404)
        self.client.force_login(self.ivanov)
        r = self.client.get(f"/edocs/file/{att.pk}/")
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "image/png"))
        r = self.client.get(f"/edocs/file/{att.pk}/download/")
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        # прямой /media/ к вложениям закрыт
        self.assertEqual(self.client.get("/media/" + att.file.name).status_code, 404)

    def test_journal_needs_capability(self):
        doc = self.send()
        svc.decide(self.ivanov, doc, "done")
        self.send(due=timezone.localdate())
        self.client.force_login(self.author)
        self.assertEqual(self.client.get("/edocs/journal/").status_code, 403)
        boss = mk("boss", caps=["edocs_journal"])
        self.client.force_login(boss)
        r = self.client.get("/edocs/journal/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["stats"]["total"], 2)
        self.assertEqual(r.context["report"][0]["total"], 2)
        self.assertEqual(self.client.get(f"/edocs/document/{doc.pk}/").status_code, 200)
        r = self.client.get("/edocs/journal/?export=csv&executor=%d" % self.ivanov.pk)
        body = r.content.decode("utf-8-sig")
        self.assertIn("Рег. номер;", body)
        self.assertIn(doc.reg_number, body)


class BotTests(BaseCase):
    """Экраны Telegram-бота на новой модели: списки, карточка, решение, ознакомление, поиск."""

    def ctx(self, user):
        from apps.accounts.access import Access
        from apps.accounts.bot import core
        return core.Ctx(user=user, acc=Access.for_user(user), tg_id=7)

    def test_bot_flow(self):
        from apps.accounts.bot import core
        from apps.accounts.bot.ui import Notice
        doc = self.send(co=[self.petrov], files=[upl()], due=timezone.localdate())
        info = self.send("info", title="Приказ")
        ctx = self.ctx(self.ivanov)
        home = core.run("dc.h", ctx, [])
        self.assertIn("ждут меня: 2", home.text)
        for key in list(INBOX_FILTERS) + ["overdue", "inbox", "sent"]:
            core.run("dc.l", ctx, [key, "0"])
        card = core.run("dc.c", ctx, [str(doc.pk)])
        self.assertIn(doc.reg_number, card.text)
        cbs = [b.callback_data for row in card.kb.rows for b in row if b.callback_data]
        self.assertIn(f"dc.s:{doc.pk}:approved", cbs)
        att = doc.attachments.get()
        self.assertIn(f"dc.f:{doc.pk}:{att.pk}", cbs)
        self.assertEqual(core.run("dc.f", ctx, [str(doc.pk), str(att.pk)]).file[1], "act.pdf")
        with self.assertRaises(Notice):          # отклонить без причины нельзя
            core.run("dc.S", ctx, [str(doc.pk), "rejected"])
        core.run("dc.S", ctx, [str(doc.pk), "approved"])
        self.assertEqual(doc.assignments.get(user=self.ivanov).status, "done")
        card = core.run("dc.c", ctx, [str(info.pk)])
        self.assertIn(f"dc.a:{info.pk}", [b.callback_data for row in card.kb.rows for b in row])
        core.run("dc.a", ctx, [str(info.pk)])
        info.refresh_from_db()
        self.assertEqual(info.status, "approved")
        with self.assertRaises(Notice):          # чужой документ
            core.run("dc.c", self.ctx(self.outsider), [str(doc.pk)])
        scr = core.run_input(ctx, doc.reg_number)
        self.assertIn(doc.title, scr.text)
