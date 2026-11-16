from django.urls import path
from . import views

app_name = 'edocs'

urlpatterns = [
    path('', views.index, name='index'), # Входящие
    path('sent/', views.sent, name='sent'), # Исходящие
    path('create/', views.create_document, name='create'), # Новое письмо
    path('employees/', views.employees, name='employees'),
    path('employees/<int:pk>/', views.employee_detail, name='employee_detail'), # Сотрудники
    path('document/<int:pk>/', views.document_detail, name='document_detail'),
    path('document/<int:pk>/view/', views.view_document, name='view_document'),
    path('document/<int:pk>/reply/', views.view_reply, name='view_reply'),
    path('document/<int:pk>/status/', views.update_status, name='update_status'),
    path('document/<int:pk>/send-to-tg/', views.send_document_to_tg, name='send_document_to_tg'),
    path('document/<int:pk>/download/<str:kind>/', views.download_file, name='download_file'),
]
