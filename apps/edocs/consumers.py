import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.template.loader import render_to_string
from .models import Document

class DocumentConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.doc_id = self.scope['url_route']['kwargs']['doc_id']
        self.room_group_name = f'doc_{self.doc_id}'
        
        # Проверяем доступ
        user = self.scope.get('user')
        if not user or not user.is_authenticated:
            await self.close()
            return
            
        doc = await self.get_document(self.doc_id)
        if not doc:
            await self.close()
            return
            
        # Проверка: автор, получатель или участник маршрута
        if user not in [doc.author, doc.recipient] and not user.is_superuser:
            is_participant = await self.is_route_participant(self.doc_id, user)
            if not is_participant:
                await self.close()
                return
        
        # Добавляем в группу
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()
    
    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )
    
    async def receive(self, text_data):
        """Обработка сообщений от клиента (если нужно)"""
        pass
    
    async def history_update(self, event):
        """Отправка обновленной истории всем клиентам в группе"""
        html = event['html']
        await self.send(text_data=json.dumps({
            'type': 'history_update',
            'html': html
        }))
    
    @database_sync_to_async
    def get_document(self, doc_id):
        try:
            return Document.objects.get(pk=doc_id)
        except Document.DoesNotExist:
            return None
    
    @database_sync_to_async
    def is_route_participant(self, doc_id, user):
        from .models import DocumentRoute
        return DocumentRoute.objects.filter(document_id=doc_id, sender=user).exists() or \
               DocumentRoute.objects.filter(document_id=doc_id, recipient=user).exists()
