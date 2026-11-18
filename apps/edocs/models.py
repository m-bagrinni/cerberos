"""
СЭД: документ, адресаты (исполнители / согласующие / ознакомляемые), вложения, события, комментарии.

Жизненный цикл документа (поле Document.status):
    draft ──send──▶ review ──(все адресаты решили)──▶ approved
                      │  └──(кто-то отклонил)──────▶ rejected
                      ▼                                  │
                  revision ◀──── автор вернул ───────────┘ (и из approved)

Решение принимает каждый адресат отдельно (Assignment.status), статус документа
выводится из них — см. services.recompute_status. Вся логика переходов — в services.py.
"""
import os

from django.conf import settings
from django.db import models
from django.utils import timezone


class Document(models.Model):
    TYPE_CHOICES = [
        ("execution", "Исполнение"),
        ("approval", "Согласование"),
        ("review", "Ознакомление"),
        ("info", "Информация"),
    ]
    STATUS_CHOICES = [
        ("draft", "Черновик"),
        ("review", "В работе"),
        ("revision", "На доработке"),
        ("approved", "Завершён"),
        ("rejected", "Отклонён"),
    ]
    PRIORITY_CHOICES = [
        ("normal", "Обычный"),
        ("high", "Высокий"),
        ("urgent", "Срочно"),
    ]
    # Префиксы регистрационных номеров: ИСП-2026/0001
    REG_PREFIX = {"execution": "ИСП", "approval": "СОГ", "review": "ОЗН", "info": "ИНФ"}
    # Типы, где адресат принимает решение; остальные — только «Ознакомлен»
    DECISION_TYPES = ("execution", "approval")
    OPEN_STATUSES = ("review", "revision")

    reg_number = models.CharField("Рег. номер", max_length=32, unique=True, null=True, blank=True)
    title = models.CharField("Тема", max_length=255)
    message = models.TextField("Содержание / поручение", blank=True)
    document_type = models.CharField("Вид", max_length=20, choices=TYPE_CHOICES, default="execution")
    priority = models.CharField("Приоритет", max_length=10, choices=PRIORITY_CHOICES, default="normal")
    due_date = models.DateField("Срок исполнения", null=True, blank=True)

    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name="sent_documents", verbose_name="Автор")
    # Ответственный исполнитель (первый адресат). Полный список — assignments.
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name="received_documents", verbose_name="Ответственный")

    status = models.CharField("Статус", max_length=20, choices=STATUS_CHOICES, default="draft", db_index=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Изменён", auto_now=True)
    sent_at = models.DateTimeField("Зарегистрирован", null=True, blank=True)
    completed_at = models.DateTimeField("Завершён", null=True, blank=True)

    class Meta:
        verbose_name = "Документ"
        verbose_name_plural = "Документы"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "due_date"])]

    def __str__(self):
        return f"{self.reg_number or 'черновик'} {self.title}"

    # ── Для шаблонов ──
    @property
    def is_decision(self) -> bool:
        return self.document_type in self.DECISION_TYPES

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN_STATUSES

    @property
    def is_overdue(self) -> bool:
        return bool(self.due_date and self.is_open and self.due_date < timezone.localdate())

    @property
    def days_left(self):
        if not self.due_date:
            return None
        return (self.due_date - timezone.localdate()).days

    @property
    def overdue_days(self) -> int:
        left = self.days_left
        return -left if left is not None and left < 0 else 0

    # Прогресс по адресатам — по предвыбранному списку views._with_people (to_attr="people")
    @property
    def progress(self) -> dict:
        people = getattr(self, "people", None)
        if people is None:
            people = list(self.assignments.exclude(status="forwarded"))
        total = len(people)
        done = sum(a.status == "done" for a in people)
        return {"done": done, "total": total, "pct": round(100 * done / total) if total else 0,
                "more": max(0, total - 3)}

    @property
    def status_label(self) -> str:
        """Статус словами вида документа: «На согласовании», «Исполнен», «Ознакомлены»."""
        t, s = self.document_type, self.status
        if s == "review":
            return {"execution": "На исполнении", "approval": "На согласовании"}.get(t, "На ознакомлении")
        if s == "approved":
            return {"execution": "Исполнен", "approval": "Согласован"}.get(t, "Ознакомлены")
        if s == "rejected":
            return "Не согласован" if t == "approval" else "Отклонён"
        return self.get_status_display()

    @property
    def number(self) -> str:
        return self.reg_number or "б/н"


class Assignment(models.Model):
    """Адресат документа и его личный статус."""
    ROLE_CHOICES = [("main", "Ответственный"), ("co", "Соисполнитель")]
    STATUS_CHOICES = [
        ("pending", "Ожидает"),
        ("done", "Выполнено"),
        ("rejected", "Отклонено"),
        ("forwarded", "Передано"),
    ]

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="assignments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="edoc_assignments", verbose_name="Адресат")
    role = models.CharField("Роль", max_length=10, choices=ROLE_CHOICES, default="main")
    status = models.CharField("Статус", max_length=12, choices=STATUS_CHOICES, default="pending", db_index=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="+", verbose_name="Назначил")
    forwarded_from = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name="forwards", verbose_name="Передано от")
    is_read = models.BooleanField("Прочитан", default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    report = models.TextField("Отчёт / комментарий к решению", blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Адресат"
        verbose_name_plural = "Адресаты"
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["user", "status"])]

    def __str__(self):
        return f"{self.user} → {self.document_id} ({self.status})"

    @property
    def status_label(self) -> str:
        if self.status == "done":
            return {"execution": "Исполнено", "approval": "Согласовано"}.get(self.document.document_type, "Ознакомлен")
        if self.status == "rejected":
            return "Не согласовано" if self.document.document_type == "approval" else "Отклонено"
        if self.status == "pending" and not self.is_read:
            return "Не прочитан"
        return self.get_status_display()

    @property
    def decided_late(self) -> bool:
        d = self.document.due_date
        return bool(d and self.decided_at and timezone.localdate(self.decided_at) > d)


def _upload_to(instance, filename):
    return timezone.now().strftime("edocs/att/%Y/%m/") + filename


def _pdf_upload_to(instance, filename):
    return timezone.now().strftime("edocs_pdf/%Y/%m/") + filename


class Attachment(models.Model):
    """Файл документа (kind=main) или отчёта адресата (kind=reply)."""
    KIND_CHOICES = [("main", "Документ"), ("reply", "Отчёт")]
    PREVIEW_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp"}
    CONVERT_EXT = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".rtf", ".txt", ".csv"}

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="attachments")
    assignment = models.ForeignKey(Assignment, on_delete=models.SET_NULL, null=True, blank=True, related_name="files")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default="main")
    file = models.FileField("Файл", upload_to=_upload_to, max_length=255)
    pdf_file = models.FileField("PDF для просмотра", upload_to=_pdf_upload_to, max_length=255, blank=True)
    name = models.CharField("Имя файла", max_length=255)
    size = models.PositiveBigIntegerField(default=0)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                    related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Вложение"
        verbose_name_plural = "Вложения"
        ordering = ["created_at", "id"]

    def __str__(self):
        return self.name

    @property
    def ext(self) -> str:
        return os.path.splitext(self.name)[1].lower()

    @property
    def can_preview(self) -> bool:
        return bool(self.pdf_file) or self.ext in self.PREVIEW_EXT or self.ext in self.CONVERT_EXT

    @property
    def icon(self) -> str:
        e = self.ext
        if e == ".pdf":
            return "bi-file-earmark-pdf"
        if e in (".doc", ".docx", ".odt", ".rtf", ".txt"):
            return "bi-file-earmark-word"
        if e in (".xls", ".xlsx", ".ods", ".csv"):
            return "bi-file-earmark-excel"
        if e in (".ppt", ".pptx"):
            return "bi-file-earmark-ppt"
        if e in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            return "bi-file-earmark-image"
        if e in (".zip", ".rar", ".7z"):
            return "bi-file-earmark-zip"
        return "bi-file-earmark"


class Comment(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="comments", verbose_name="Документ")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="Автор")
    text = models.TextField("Текст")
    created_at = models.DateTimeField("Дата", auto_now_add=True)

    class Meta:
        verbose_name = "Комментарий"
        verbose_name_plural = "Комментарии"
        ordering = ["created_at"]

    def __str__(self):
        return f"Комментарий {self.author} к {self.document_id}"


class DocumentRoute(models.Model):
    """Журнал событий документа (хронология в карточке)."""
    ACTION_CHOICES = [
        ("created", "Создан"),
        ("sent", "Зарегистрирован и направлен"),
        ("forwarded", "Передан"),
        ("assigned", "Добавлен адресат"),
        ("done", "Исполнено"),
        ("approved", "Согласовано"),
        ("rejected", "Отклонено"),
        ("acknowledged", "Ознакомлен"),
        ("revision", "Возвращён на доработку"),
        ("completed", "Завершён"),
    ]

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="routes", verbose_name="Документ")
    action = models.CharField("Действие", max_length=20, choices=ACTION_CHOICES, default="forwarded")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_routes",
                               verbose_name="Кто")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
                                  related_name="received_routes", verbose_name="Кому")
    status_at_moment = models.CharField("Статус документа", max_length=20, blank=True)
    comment = models.TextField("Комментарий / резолюция", blank=True)
    created_at = models.DateTimeField("Когда", auto_now_add=True)

    class Meta:
        verbose_name = "Событие документа"
        verbose_name_plural = "События документов"
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.get_action_display()}: {self.sender} → {self.recipient}"

    @property
    def tone(self) -> str:
        return {"done": "ok", "approved": "ok", "acknowledged": "ok", "completed": "ok",
                "rejected": "crit", "revision": "warn"}.get(self.action, "info")

    @property
    def icon(self) -> str:
        return {"created": "bi-file-earmark-plus", "sent": "bi-send", "forwarded": "bi-arrow-right-circle",
                "assigned": "bi-person-plus", "done": "bi-check2-circle", "approved": "bi-check2-circle",
                "acknowledged": "bi-eye", "rejected": "bi-x-circle", "revision": "bi-arrow-counterclockwise",
                "completed": "bi-flag"}.get(self.action, "bi-dot")


class RegCounter(models.Model):
    """Счётчик регистрационных номеров: ключ «ИСП-2026» → последнее значение."""
    key = models.CharField(max_length=20, unique=True)
    value = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Счётчик номеров"
        verbose_name_plural = "Счётчики номеров"

    def __str__(self):
        return f"{self.key}: {self.value}"
