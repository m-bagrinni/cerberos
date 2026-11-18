"""
Страницы СЭД. Логика переходов — в services.py, здесь только HTTP: разбор формы,
проверка доступа к документу, выбор шаблона, сообщения пользователю.

Права на раздел проверяет AccessMiddleware (GET — просмотр, POST — изменение,
/edocs/journal/ — возможность edocs_journal).
"""
import csv
import datetime as dt
import logging
import os

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Exists, F, OuterRef, Prefetch, Q, Subquery
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import services as svc
from .models import Assignment, Attachment, Document
from .queries import INBOX_FILTERS, filter_label, inbox_qs, pending_counts, pending_qs

logger = logging.getLogger(__name__)
User = get_user_model()
PER_PAGE = 25

INLINE_TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}


# ── Общее для списков ────────────────────────────────────────────────────────
def _parse_date(s):
    try:
        return dt.date.fromisoformat((s or "").strip())
    except ValueError:
        return None


def _apply_filters(request, qs):
    """Поиск и фильтры из GET: q, type, status, priority, period (from/to), overdue."""
    g = request.GET
    q = (g.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(reg_number__icontains=q) | Q(message__icontains=q)
                       | Q(author__last_name__icontains=q) | Q(author__username__icontains=q)
                       | Q(assignments__user__last_name__icontains=q)).distinct()
    if g.get("type") in dict(Document.TYPE_CHOICES):
        qs = qs.filter(document_type=g["type"])
    st = g.get("status")
    if st == "open":
        qs = qs.filter(status__in=Document.OPEN_STATUSES)
    elif st in dict(Document.STATUS_CHOICES):
        qs = qs.filter(status=st)
    if g.get("priority") in dict(Document.PRIORITY_CHOICES):
        qs = qs.filter(priority=g["priority"])
    if g.get("overdue"):
        qs = qs.filter(status__in=Document.OPEN_STATUSES, due_date__lt=timezone.localdate())
    d1, d2 = _parse_date(g.get("from")), _parse_date(g.get("to"))
    if d1:
        qs = qs.filter(sent_at__date__gte=d1)
    if d2:
        qs = qs.filter(sent_at__date__lte=d2)
    return qs


SORTS = {
    "new": ("-sent_at", "-created_at"),
    "old": ("sent_at", "created_at"),
    "due": (F("due_date").asc(nulls_last=True), "-sent_at"),
    "upd": ("-updated_at",),
}


def _page(request, qs, default_sort="new"):
    sort = request.GET.get("sort") if request.GET.get("sort") in SORTS else default_sort
    qs = qs.order_by(*SORTS[sort])
    page = Paginator(qs, PER_PAGE).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return page, sort, params.urlencode()


def _with_people(qs):
    active = Assignment.objects.exclude(status="forwarded").select_related("user").order_by("role", "id")
    return (qs.select_related("author", "recipient")
            .prefetch_related(Prefetch("assignments", queryset=active, to_attr="people"))
            .annotate(n_files=Count("attachments", filter=Q(attachments__kind="main"), distinct=True)))


def _list(request, template, qs, **ctx):
    qs = _apply_filters(request, qs)
    page, sort, qstr = _page(request, _with_people(qs), ctx.pop("default_sort", "new"))
    ctx.update({"page": page, "docs": page.object_list, "sort": sort, "qstr": qstr,
                "TYPES": Document.TYPE_CHOICES, "PRIORITIES": Document.PRIORITY_CHOICES,
                "STATUSES": [("open", "В работе (все)")] + Document.STATUS_CHOICES[1:],
                "today": timezone.localdate()})
    ctx.setdefault("counts", pending_counts(request.user))
    return render(request, template, ctx)


# ── Списки ───────────────────────────────────────────────────────────────────
@login_required
def index(request):
    """Входящие. ?f=<ключ> — только ждущие меня (как плитки на главной)."""
    f = request.GET.get("f", "")
    label = filter_label(f) if f != "pending" else "Ждут моего действия"
    qs = pending_qs(request.user, f) if (label or f == "pending") else inbox_qs(request.user)
    mine = Assignment.objects.filter(document=OuterRef("pk"), user=request.user).exclude(status="forwarded")
    qs = qs.annotate(my_status=Subquery(mine.values("status")[:1]),
                     my_read=Subquery(mine.values("is_read")[:1]))
    counts = pending_counts(request.user)
    chips = [("", "Все входящие", None), ("pending", "Ждут меня", counts["total"])]
    chips += [(k, v[0], counts[k]) for k, v in INBOX_FILTERS.items()]
    chips += [("overdue", "Просрочено", counts["overdue"])]
    return _list(request, "edocs/inbox.html", qs, f=f, filter_label=label, tab="inbox", chips=chips,
                 counts=counts)


@login_required
def sent(request):
    qs = Document.objects.filter(author=request.user).exclude(status="draft")
    return _list(request, "edocs/sent.html", qs, tab="sent")


@login_required
def drafts(request):
    qs = Document.objects.filter(author=request.user, status="draft")
    return _list(request, "edocs/drafts.html", qs, tab="drafts", default_sort="upd")


@login_required
def journal(request):
    """Журнал всех документов + отчёт по исполнительской дисциплине (возможность edocs_journal)."""
    if not svc.is_supervisor(request.user):
        return HttpResponse("Нужна возможность «Журнал СЭД»", status=403)
    qs = Document.objects.exclude(status="draft")
    author = request.GET.get("author")
    executor = request.GET.get("executor")
    if author and author.isdigit():
        qs = qs.filter(author_id=author)
    if executor and executor.isdigit():
        qs = qs.filter(Exists(Assignment.objects.filter(document=OuterRef("pk"), user_id=executor)
                              .exclude(status="forwarded")))
    if request.GET.get("export") == "csv":
        return _journal_csv(_with_people(_apply_filters(request, qs)).order_by("-sent_at"))

    filtered = _apply_filters(request, qs)
    today = timezone.localdate()
    stats = filtered.aggregate(
        total=Count("id"),
        open=Count("id", filter=Q(status__in=Document.OPEN_STATUSES)),
        overdue=Count("id", filter=Q(status__in=Document.OPEN_STATUSES, due_date__lt=today)),
        done=Count("id", filter=Q(status="approved")),
        rejected=Count("id", filter=Q(status="rejected")),
        done_late=Count("id", filter=Q(status="approved", completed_at__date__gt=F("due_date"))),
    )
    stats["on_time_pct"] = (round(100 * (stats["done"] - stats["done_late"]) / stats["done"])
                            if stats["done"] else None)
    report = _discipline(filtered, today)
    people = User.objects.filter(is_active=True).order_by("last_name", "first_name", "username")
    return _list(request, "edocs/journal.html", qs, tab="journal", stats=stats, report=report,
                 people=people, sel_author=author, sel_executor=executor)


def _discipline(docs, today):
    """По каждому исполнителю: назначено, исполнено (в срок / с опозданием), в работе, просрочено."""
    rows = (Assignment.objects.filter(document__in=docs).exclude(status="forwarded")
            .values("user_id", "user__last_name", "user__first_name", "user__username")
            .annotate(
                total=Count("id"),
                done=Count("id", filter=Q(status="done")),
                late=Count("id", filter=Q(status="done", decided_at__date__gt=F("document__due_date"))),
                rejected=Count("id", filter=Q(status="rejected")),
                pending=Count("id", filter=Q(status="pending")),
                overdue=Count("id", filter=Q(status="pending", document__due_date__lt=today)),
            ).order_by("-overdue", "-pending", "user__last_name"))
    out = []
    for r in rows:
        name = " ".join(x for x in (r["user__last_name"], r["user__first_name"]) if x) or r["user__username"]
        on_time = r["done"] - r["late"]
        out.append({**r, "name": name, "on_time": on_time,
                    "pct": round(100 * on_time / r["done"]) if r["done"] else None})
    return out


def _journal_csv(docs):
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="edocs-journal-{timezone.localdate():%Y%m%d}.csv"'
    resp.write("﻿")  # BOM — Excel откроет кириллицу
    w = csv.writer(resp, delimiter=";")
    w.writerow(["Рег. номер", "Дата регистрации", "Вид", "Приоритет", "Тема", "Автор", "Адресаты",
                "Срок", "Статус", "Завершён", "Просрочен"])
    for d in docs:
        w.writerow([
            d.number, timezone.localtime(d.sent_at).strftime("%d.%m.%Y %H:%M") if d.sent_at else "",
            d.get_document_type_display(), d.get_priority_display(), d.title,
            d.author.short_name,
            ", ".join(f"{a.user.short_name} ({a.status_label})" for a in d.people),
            d.due_date.strftime("%d.%m.%Y") if d.due_date else "",
            d.status_label,
            timezone.localtime(d.completed_at).strftime("%d.%m.%Y %H:%M") if d.completed_at else "",
            "да" if d.is_overdue else "",
        ])
    return resp


# ── Создание / редактирование черновика ──────────────────────────────────────
def _employees(exclude_ids=()):
    return (User.objects.filter(is_active=True).exclude(pk__in=list(exclude_ids))
            .select_related("position_ref").order_by("last_name", "first_name", "username"))


@login_required
def create_document(request, pk=None):
    doc = None
    if pk is not None:
        doc = get_object_or_404(Document, pk=pk, author=request.user)
        if doc.status != "draft":
            messages.info(request, "Документ уже отправлен — изменить его нельзя.")
            return redirect("edocs:document_detail", pk=doc.pk)

    if request.method == "POST":
        p = request.POST
        send = p.get("submit") != "draft"
        try:
            doc = svc.save_document(
                request.user, doc=doc, title=p.get("title"), message=p.get("message"),
                document_type=p.get("document_type", "execution"), priority=p.get("priority", "normal"),
                due_date=_parse_date(p.get("due_date")), main_id=p.get("main"),
                co_ids=p.getlist("co"), files=request.FILES.getlist("files"),
                remove_files=[int(x) for x in p.getlist("remove_file") if x.isdigit()], send=send)
        except svc.EdocsError as e:
            messages.error(request, str(e))
            return render(request, "edocs/create.html", _form_ctx(request, doc, p), status=400)
        if send:
            messages.success(request, f"Документ {doc.reg_number} зарегистрирован и направлен.")
            return redirect("edocs:document_detail", pk=doc.pk)
        messages.success(request, "Черновик сохранён.")
        return redirect("edocs:edit", pk=doc.pk)
    return render(request, "edocs/create.html", _form_ctx(request, doc))


def _form_ctx(request, doc, post=None):
    """Значения формы: из POST (после ошибки), из черновика или по умолчанию (?to=<id>)."""
    if post is not None:
        v = {"title": post.get("title", ""), "message": post.get("message", ""),
             "document_type": post.get("document_type", "execution"), "priority": post.get("priority", "normal"),
             "due_date": post.get("due_date", ""), "main": post.get("main", ""), "co": post.getlist("co")}
    elif doc is not None:
        people = list(doc.assignments.order_by("id"))
        v = {"title": doc.title, "message": doc.message, "document_type": doc.document_type,
             "priority": doc.priority, "due_date": doc.due_date.isoformat() if doc.due_date else "",
             "main": str(doc.recipient_id or ""), "co": [str(a.user_id) for a in people if a.role == "co"]}
    else:
        v = {"title": "", "message": "", "document_type": "execution", "priority": "normal",
             "due_date": "", "main": request.GET.get("to", ""), "co": []}
    return {"doc": doc, "v": v, "employees": _employees([request.user.pk]), "tab": "create",
            "TYPES": Document.TYPE_CHOICES, "PRIORITIES": Document.PRIORITY_CHOICES,
            "files": doc.attachments.filter(kind="main") if doc else [],
            "today": timezone.localdate(), "max_mb": svc.MAX_FILE_MB,
            "allowed_ext": ",".join(sorted(svc.ALLOWED_EXT)), "counts": pending_counts(request.user)}


# ── Карточка ─────────────────────────────────────────────────────────────────
def _get_doc(request, pk):
    doc = get_object_or_404(Document.objects.select_related("author", "recipient"), pk=pk)
    if not svc.can_access(request.user, doc):
        raise Http404
    return doc


@login_required
def document_detail(request, pk):
    doc = _get_doc(request, pk)
    if doc.status == "draft":
        return redirect("edocs:edit", pk=doc.pk)
    svc.mark_read(request.user, doc)
    asg = svc.my_assignment(request.user, doc)
    can = svc.allowed(request.user, doc, asg)
    if not request.user.access.can_edit("edocs"):
        can = svc.Allowed()  # только просмотр — без кнопок действий

    people = list(doc.assignments.select_related("user", "user__position_ref", "assigned_by", "document")
                  .prefetch_related("files").order_by("id"))
    active = [a for a in people if a.status != "forwarded"]
    main_files = list(doc.attachments.filter(kind="main"))
    reply_files = [f for a in active for f in a.files.all()]

    timeline = [{"at": r.created_at, "kind": "event", "obj": r}
                for r in doc.routes.select_related("sender", "recipient")]
    timeline += [{"at": c.created_at, "kind": "comment", "obj": c}
                 for c in doc.comments.select_related("author")]
    timeline.sort(key=lambda x: x["at"])

    busy = {a.user_id for a in active} | {doc.author_id}
    progress_done = sum(a.status == "done" for a in active)
    return render(request, "edocs/document_detail.html", {
        "doc": doc, "asg": asg, "can": can, "people": active, "history_people": people,
        "main_files": main_files, "reply_files": reply_files,
        "preview": next((f for f in main_files if f.can_preview), None),
        "timeline": timeline, "n_comments": sum(t["kind"] == "comment" for t in timeline),
        "employees": _employees(busy | {request.user.pk}),
        "progress": {"done": progress_done, "total": len(active),
                     "pct": round(100 * progress_done / len(active)) if active else 0},
        "today": timezone.localdate(), "tab": "", "counts": pending_counts(request.user),
        "max_mb": svc.MAX_FILE_MB, "allowed_ext": ",".join(sorted(svc.ALLOWED_EXT)),
        "is_supervisor_only": svc.is_supervisor(request.user) and not asg and request.user.pk != doc.author_id,
    })


@login_required
@require_POST
def document_action(request, pk):
    """Все действия с документом. action: send | delete | done | reject | ack | forward | revision | add | comment."""
    doc = _get_doc(request, pk)
    p, u = request.POST, request.user
    action = p.get("action", "")
    ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    ok_msg = ""
    try:
        if action == "send":
            svc.send_draft(u, doc)
            ok_msg = f"Документ {doc.reg_number or ''} зарегистрирован и направлен."
        elif action == "delete":
            svc.delete_draft(u, doc)
            messages.success(request, "Черновик удалён.")
            return redirect("edocs:drafts")
        elif action in ("done", "reject"):
            svc.decide(u, doc, "done" if action == "done" else "rejected", p.get("report", ""),
                       request.FILES.getlist("files"))
            ok_msg = "Решение отправлено автору."
        elif action == "ack":
            svc.acknowledge(u, doc)
            ok_msg = "Отмечено: ознакомлен."
        elif action == "forward":
            svc.forward(u, doc, p.get("to"), p.get("comment", ""))
            ok_msg = "Документ передан."
        elif action == "revision":
            svc.return_for_revision(u, doc, p.get("comment", ""), _parse_date(p.get("due_date")))
            ok_msg = "Документ возвращён на доработку."
        elif action == "add":
            svc.add_assignee(u, doc, p.get("to"))
            ok_msg = "Адресат добавлен."
        elif action == "comment":
            svc.add_comment(u, doc, p.get("text", ""))
            ok_msg = "Комментарий добавлен."
        else:
            raise svc.EdocsError("Неизвестное действие.")
    except svc.EdocsError as e:
        if ajax:
            return JsonResponse({"ok": False, "error": str(e)}, status=400)
        messages.error(request, str(e))
        return redirect("edocs:document_detail", pk=doc.pk)
    if ajax:
        return JsonResponse({"ok": True, "message": ok_msg})
    messages.success(request, ok_msg)
    return redirect("edocs:document_detail", pk=doc.pk)


# ── Файлы ────────────────────────────────────────────────────────────────────
def _get_att(request, att_pk):
    att = get_object_or_404(Attachment.objects.select_related("document"), pk=att_pk)
    if not svc.can_access(request.user, att.document):
        raise Http404
    return att


@login_required
def attachment_view(request, att_pk):
    """Просмотр во встроенном окне: PDF/картинка как есть, офис — через PDF (конвертация один раз)."""
    att = _get_att(request, att_pk)
    path = svc.ensure_pdf(att)
    if not path or not os.path.exists(path):
        return HttpResponse("Этот файл нельзя показать в браузере — скачайте его.", status=415,
                            content_type="text/plain; charset=utf-8")
    ext = os.path.splitext(path)[1].lower()
    name = att.name if ext == att.ext else os.path.splitext(att.name)[0] + ".pdf"
    resp = FileResponse(open(path, "rb"), content_type=INLINE_TYPES.get(ext, "application/octet-stream"),
                        filename=name)
    resp["X-Frame-Options"] = "SAMEORIGIN"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


@login_required
def attachment_download(request, att_pk):
    """Скачивание оригинала. Всегда attachment: браузер не исполняет файл на домене сайта."""
    att = _get_att(request, att_pk)
    try:
        fh = att.file.open("rb")
    except FileNotFoundError:
        raise Http404
    resp = FileResponse(fh, as_attachment=True, filename=att.name, content_type="application/octet-stream")
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


@login_required
@require_POST
def send_document_to_tg(request, pk):
    """Прислать файлы документа себе в Telegram."""
    doc = _get_doc(request, pk)
    if not request.user.telegram_id:
        return JsonResponse({"ok": False, "error": "У вас не привязан Telegram — профиль → Telegram."})
    files = list(doc.attachments.filter(kind="main"))
    from apps.accounts.telegram_notify import send_telegram_document, send_telegram_message

    from .tasks import doc_text
    try:
        send_telegram_message(request.user.telegram_id, doc_text(doc, "📄 <b>Документ</b>",
                                                                  doc.author.get_full_name() or doc.author.username))
        sent = 0
        for att in files[:10]:
            if att.size <= 49 * 1024 * 1024 and send_telegram_document(
                    request.user.telegram_id, att.file.path, caption=att.name, filename=att.name):
                sent += 1
    except Exception:  # noqa: BLE001
        logger.exception("edocs send_to_tg %s", doc.pk)
        return JsonResponse({"ok": False, "error": "Telegram недоступен, попробуйте позже."})
    return JsonResponse({"ok": True, "message": f"Отправлено в Telegram{f': файлов {sent}' if files else ''}."})


# ── Сотрудники ───────────────────────────────────────────────────────────────
@login_required
def employees(request):
    q = (request.GET.get("q") or "").strip()
    users = _employees()
    if q:
        users = users.filter(Q(last_name__icontains=q) | Q(first_name__icontains=q) | Q(username__icontains=q)
                             | Q(position_ref__name__icontains=q))
    users = users.annotate(load=Count("edoc_assignments", filter=Q(
        edoc_assignments__status="pending",
        edoc_assignments__document__status__in=Document.OPEN_STATUSES)))
    return render(request, "edocs/employees.html", {"employees": users, "q": q, "tab": "employees",
                                                    "counts": pending_counts(request.user)})


@login_required
def employee_detail(request, pk):
    employee = get_object_or_404(User.objects.select_related("position_ref"), pk=pk)
    me = request.user
    # Общая переписка: мои документы ему и его — мне
    shared = (Document.objects.exclude(status="draft")
              .filter(Q(author=me, assignments__user=employee) | Q(author=employee, assignments__user=me))
              .distinct().select_related("author").order_by("-sent_at")[:20])
    return render(request, "edocs/employee_detail.html", {
        "employee": employee, "shared": shared, "tab": "employees", "counts": pending_counts(me),
        "load": Assignment.objects.filter(user=employee, status="pending",
                                          document__status__in=Document.OPEN_STATUSES).count(),
    })


# ── Совместимость со старыми ссылками ────────────────────────────────────────
@login_required
def legacy_view(request, pk):
    doc = _get_doc(request, pk)
    att = doc.attachments.filter(kind="main").first()
    if att is None:
        raise Http404
    return redirect(reverse("edocs:file_view", args=[att.pk]))
