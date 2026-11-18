from django.urls import path

from . import views

app_name = "edocs"

urlpatterns = [
    path("", views.index, name="index"),                       # Входящие
    path("sent/", views.sent, name="sent"),                    # Исходящие
    path("drafts/", views.drafts, name="drafts"),              # Черновики
    path("journal/", views.journal, name="journal"),           # Журнал и отчёт (edocs_journal)
    path("create/", views.create_document, name="create"),
    path("document/<int:pk>/edit/", views.create_document, name="edit"),
    path("document/<int:pk>/", views.document_detail, name="document_detail"),
    path("document/<int:pk>/act/", views.document_action, name="action"),
    path("document/<int:pk>/send-to-tg/", views.send_document_to_tg, name="send_document_to_tg"),
    path("document/<int:pk>/view/", views.legacy_view, name="view_document"),  # старые ссылки
    path("file/<int:att_pk>/", views.attachment_view, name="file_view"),
    path("file/<int:att_pk>/download/", views.attachment_download, name="file_download"),
    path("employees/", views.employees, name="employees"),
    path("employees/<int:pk>/", views.employee_detail, name="employee_detail"),
]
