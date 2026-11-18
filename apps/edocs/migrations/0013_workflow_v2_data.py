"""Перенос данных в новую схему СЭД.

- Document.file / pdf_file      → Attachment(kind=main)
- Document.reply_file           → Attachment(kind=reply) у ответственного
- Document.recipient / is_read  → Assignment (ответственный)
- reply_comment                 → Assignment.report
- прошлые получатели из маршрута → Assignment(status=forwarded), чтобы документ остался у них во входящих
- resolution (кроме автоматических строк «Переслано: …») → комментарий автора
- регистрационные номера и sent_at для всех отправленных документов
- DocumentRoute.action из status_at_moment; пустой маршрут → событие «Зарегистрирован»
"""
import os

from django.db import migrations

PREFIX = {"execution": "ИСП", "approval": "СОГ", "review": "ОЗН", "info": "ИНФ"}
DECISION = ("execution", "approval")


def _size(f):
    try:
        return os.path.getsize(f.path)
    except Exception:  # noqa: BLE001 — файла может не быть на диске
        return 0


def forward(apps, schema_editor):
    Document = apps.get_model("edocs", "Document")
    Assignment = apps.get_model("edocs", "Assignment")
    Attachment = apps.get_model("edocs", "Attachment")
    Comment = apps.get_model("edocs", "Comment")
    Route = apps.get_model("edocs", "DocumentRoute")
    Counter = apps.get_model("edocs", "RegCounter")

    counters = {}
    for doc in Document.objects.order_by("created_at", "id"):
        upd = {}
        decision = doc.document_type in DECISION

        if doc.file:
            a = Attachment.objects.create(
                document=doc, kind="main", file=doc.file.name, pdf_file=doc.pdf_file.name or "",
                name=os.path.basename(doc.file.name), size=_size(doc.file), uploaded_by_id=doc.author_id)
            Attachment.objects.filter(pk=a.pk).update(created_at=doc.created_at)

        status = doc.status
        if doc.recipient_id:
            if decision:
                a_status = {"approved": "done", "rejected": "rejected"}.get(status, "pending")
            else:
                a_status = "done" if doc.is_read else "pending"
                if status != "draft":
                    status = "approved" if doc.is_read else "review"
            asg = Assignment.objects.create(
                document=doc, user_id=doc.recipient_id, role="main", status=a_status,
                assigned_by_id=doc.author_id, is_read=doc.is_read or a_status != "pending",
                read_at=doc.updated_at if (doc.is_read or a_status != "pending") else None,
                report=doc.reply_comment or "",
                decided_at=doc.updated_at if a_status in ("done", "rejected") else None)
            Assignment.objects.filter(pk=asg.pk).update(created_at=doc.created_at)
            if doc.reply_file:
                Attachment.objects.create(
                    document=doc, assignment=asg, kind="reply", file=doc.reply_file.name,
                    name=os.path.basename(doc.reply_file.name), size=_size(doc.reply_file),
                    uploaded_by_id=doc.recipient_id)

        # Прежние получатели по маршруту
        seen = {doc.recipient_id, doc.author_id}
        for uid in Route.objects.filter(document=doc).values_list("recipient_id", flat=True):
            if uid and uid not in seen:
                seen.add(uid)
                Assignment.objects.create(document=doc, user_id=uid, role="main", status="forwarded",
                                          assigned_by_id=doc.author_id, is_read=True)

        lines = [ln for ln in (doc.resolution or "").splitlines()
                 if ln.strip() and not ln.startswith("Переслано:")]
        if lines:
            c = Comment.objects.create(document=doc, author_id=doc.author_id, text="Резолюция: " + "\n".join(lines))
            Comment.objects.filter(pk=c.pk).update(created_at=doc.created_at)

        if status != "draft":
            key = f"{PREFIX.get(doc.document_type, 'ДОК')}-{doc.created_at.year}"
            counters[key] = counters.get(key, 0) + 1
            upd["reg_number"] = f"{key}/{counters[key]:04d}"
            upd["sent_at"] = doc.created_at
        if status in ("approved", "rejected"):
            upd["completed_at"] = doc.updated_at
        upd["status"] = status
        # update() — не трогаем updated_at (auto_now)
        Document.objects.filter(pk=doc.pk).update(**upd)

        # Документы старше журнала маршрутов: событие регистрации, чтобы хронология не была пустой
        if status != "draft" and not Route.objects.filter(document=doc).exists():
            r = Route.objects.create(document=doc, action="sent", sender_id=doc.author_id,
                                     recipient_id=doc.recipient_id, status_at_moment="review")
            Route.objects.filter(pk=r.pk).update(created_at=doc.created_at)

        first = True
        for r in Route.objects.filter(document=doc).order_by("created_at", "id"):
            s = r.status_at_moment
            if s == "approved":
                action = "approved" if doc.document_type == "approval" else "done"
            elif s in ("rejected", "revision"):
                action = s
            else:
                action = "sent" if first else "forwarded"
            first = False
            Route.objects.filter(pk=r.pk).update(action=action)

    for key, value in counters.items():
        Counter.objects.update_or_create(key=key, defaults={"value": value})


class Migration(migrations.Migration):

    dependencies = [
        ("edocs", "0012_workflow_v2"),
    ]

    operations = [
        migrations.RunPython(forward, migrations.RunPython.noop),
    ]
