from django.db import models
from django.conf import settings

class Document(models.Model):
    TYPE_CHOICES = [
        ('execution', 'Исполнение'),
        ('approval', 'Согласование'),
        ('review', 'Ознакомление'),
        ('info', 'Информация'),
    ]
    
    STATUS_CHOICES = [
        ('draft', 'Черновик'),
        ('review', 'На исполнении'),
        ('revision', 'На доработке'),
        ('approved', 'Выполнен'),
        ('rejected', 'Отклонен'),
    ]
    
    title = models.CharField(max_length=255, verbose_name="Тема")
    message = models.TextField(blank=True, verbose_name="Сопроводительный текст")
    file = models.FileField(upload_to='edocs/%Y/%m/%d/', verbose_name="Файл", blank=True, null=True)
    pdf_file = models.FileField(upload_to='edocs_pdf/%Y/%m/%d/', verbose_name="PDF версия", blank=True, null=True)
    
    # Автор (отправитель)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='sent_documents',
        verbose_name="Отправитель"
    )
    # Получатель (назначенный исполнитель)
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='received_documents',
        verbose_name="Получатель"
    )
    
    resolution = models.TextField(blank=True, verbose_name="Резолюция/Комментарий")
    reply_comment = models.TextField(blank=True, verbose_name="Комментарий к ответу")
    reply_file = models.FileField(upload_to='edocs_reply/%Y/%m/%d/', verbose_name="Ответный файл", blank=True, null=True)
    document_type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default='execution',
        verbose_name="Тип документа"
    )
    status = models.CharField(
        max_length=20, 
        choices=STATUS_CHOICES, 
        default='draft',
        verbose_name="Статус"
    )
    is_read = models.BooleanField(default=False, verbose_name="Прочитан")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Документ"
        verbose_name_plural = "Документы"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"


class Comment(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='comments', verbose_name="Документ")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="Автор")
    text = models.TextField(verbose_name="Текст комментария")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата")

    class Meta:
        verbose_name = "Комментарий"
        verbose_name_plural = "Комментарии"
        ordering = ['created_at']

    def __str__(self):
        return f"Комментарий от {self.author.username} к {self.document.title}"


class DocumentRoute(models.Model):
    """История перемещения документа по цепочке."""
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='routes', verbose_name="Документ")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sent_routes', verbose_name="Отправитель")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='received_routes', verbose_name="Получатель")
    status_at_moment = models.CharField(max_length=20, blank=True, verbose_name="Статус на момент действия")
    comment = models.TextField(blank=True, verbose_name="Комментарий/Резолюция")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата отправки")

    class Meta:
        verbose_name = "Маршрут документа"
        verbose_name_plural = "Маршруты документов"
        ordering = ['created_at']

    def __str__(self):
        return f"{self.sender.username} -> {self.recipient.username}"
