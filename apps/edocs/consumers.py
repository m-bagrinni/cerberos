import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer


class DocumentConsumer(AsyncWebsocketConsumer):
    """Живая карточка документа: сообщает открытым вкладкам, что документ изменился."""

    async def connect(self):
        self.doc_id = int(self.scope["url_route"]["kwargs"]["doc_id"])
        user = self.scope.get("user")
        # WebSocket не проходит через AccessMiddleware — проверяем раздел и документ явно
        if not user or not user.is_authenticated or not user.access.can_view("edocs"):
            await self.close()
            return
        if not await self._can_access(user):
            await self.close()
            return
        self.room_group_name = f"doc_{self.doc_id}"
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "room_group_name"):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        pass

    async def doc_changed(self, event):
        await self.send(text_data=json.dumps({"type": "doc_changed"}))

    @database_sync_to_async
    def _can_access(self, user):
        from .models import Document
        from .services import can_access
        doc = Document.objects.filter(pk=self.doc_id).first()
        return doc is not None and can_access(user, doc)
