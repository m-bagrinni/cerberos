"""
ASGI config for cerber project.

It exposes the ASGI callable as a module-level variable named ``application``.
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cerber.settings')

# Сначала инициализируем Django (чтобы приложение загрузилось)
django_asgi_app = get_asgi_application()

# Затем подключаем Channels
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from apps.edocs.routing import websocket_urlpatterns

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(websocket_urlpatterns)
    ),
})
