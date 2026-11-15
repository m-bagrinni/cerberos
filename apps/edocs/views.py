import os
import mimetypes
from django.shortcuts import render, redirect, get_object_or_404
from apps.accounts.telegram_notify import send_telegram_message
from django.conf import settings
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib.auth import get_user_model
from django.http import FileResponse, HttpResponse, JsonResponse
from django.core.files import File
from django.template.loader import render_to_string
from .models import Document, Comment, DocumentRoute
from .utils import convert_to_pdf

User = get_user_model()

@login_required
def index(request):
    inbox = Document.objects.filter(recipient=request.user).exclude(status='draft')
    return render(request, 'edocs/inbox.html', {'documents': inbox})

@login_required
def sent(request):
    outgoing = Document.objects.filter(author=request.user)
    return render(request, 'edocs/sent.html', {'documents': outgoing})

@login_required
def create_document(request):
    if request.method == 'POST':
        title = request.POST.get('title')
        message = request.POST.get('message')
        recipient_id = request.POST.get('recipient')
        document_type = request.POST.get('document_type', 'execution')
        file = request.FILES.get('file')
        
        if title and recipient_id:
            recipient = get_object_or_404(User, pk=recipient_id)
            
            doc = Document.objects.create(
                title=title,
                message=message,
                file=file,
                author=request.user,
                recipient=recipient,
                document_type=document_type,
                status='review' if document_type in ('execution', 'approval') else 'approved'
            )
            
            # Создаем первую запись в маршруте
            DocumentRoute.objects.create(document=doc, sender=request.user, recipient=recipient)
            

            
            # Конвертация в PDF
            if doc.file:
                from .utils import convert_to_pdf
                from django.core.files import File
                pdf_path = convert_to_pdf(doc.file.path)
                if pdf_path and pdf_path != doc.file.path:
                    with open(pdf_path, 'rb') as f:
                        doc.pdf_file.save(os.path.basename(pdf_path), File(f), save=True)
                    os.remove(pdf_path)
            
            # === Telegram-уведомление получателю (после конвертации) ===
            if recipient.telegram_id:
                from apps.accounts.telegram_notify import send_telegram_document
                
                caption = (
                    f"📄 <b>Новый документ в E-Docs</b>\n\n"
                    f"<b>Тема:</b> {doc.title}\n"
                    f"<b>От:</b> {request.user.get_full_name() or request.user.username}\n"
                    f"<b>Дата:</b> {doc.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
                    f"<a href='{settings.CERBER_WEBAPP_URL}/edocs/document/{doc.id}/'>Открыть в Cerberus</a>"
                )
                
                file_path = None
                filename = None
                if doc.pdf_file:
                    file_path = doc.pdf_file.path
                    filename = f"{doc.title}.pdf"
                elif doc.file:
                    file_path = doc.file.path
                    filename = os.path.basename(doc.file.name)
                
                try:
                    if file_path:
                        send_telegram_document(recipient.telegram_id, file_path, caption=caption, filename=filename)
                    else:
                        send_telegram_message(recipient.telegram_id, caption)
                except Exception as e:
                    print(f"TG_EDOCS_ERROR: {e}")
                    
            return redirect('edocs:sent')
    
    employees = User.objects.filter(is_active=True).exclude(id=request.user.id)
    return render(request, 'edocs/create.html', {'employees': employees})

@login_required
def employees(request):
    users = User.objects.filter(is_active=True).order_by('username')
    return render(request, 'edocs/employees.html', {'employees': users})

@login_required
@require_POST
def update_status(request, pk):
    doc = get_object_or_404(Document, pk=pk)
    if request.user in [doc.author, doc.recipient] or request.user.is_superuser:
        new_status = request.POST.get('status')
        if new_status in dict(Document.STATUS_CHOICES):
            doc.status = new_status
            doc.save()
    return redirect(request.META.get('HTTP_REFERER', 'edocs:index'))

@login_required
def view_document(request, pk):
    """Отдает готовый PDF (или оригинал, если PDF не создан)"""
    doc = get_object_or_404(Document, pk=pk)
    
    # Если есть PDF - отдаем его
    if doc.pdf_file:
        response = FileResponse(open(doc.pdf_file.path, 'rb'), content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{doc.title}.pdf"'
        response['X-Frame-Options'] = 'SAMEORIGIN'
        return response
        
    # Если PDF нет, но есть оригинал (например, картинка или PDF) - отдаем его
    if doc.file:
        ext = os.path.splitext(doc.file.path)[1].lower()
        if ext in ['.pdf', '.jpg', '.jpeg', '.png', '.gif', '.webp']:
            content_type, _ = mimetypes.guess_type(doc.file.path)
            response = FileResponse(open(doc.file.path, 'rb'), content_type=content_type)
            response['Content-Disposition'] = f'inline; filename="{os.path.basename(doc.file.name)}"'
            response['X-Frame-Options'] = 'SAMEORIGIN'
            return response
            
    return HttpResponse("Файл не найден или не может быть отображен", status=404)

@login_required
def document_detail(request, pk):
    """Детальная страница документа"""
    doc = get_object_or_404(Document, pk=pk)
    
    # Отмечаем как прочитанный (если текущий пользователь — получатель)
    if request.user == doc.recipient and not doc.is_read:
        doc.is_read = True
        doc.save(update_fields=['is_read'])
    
    is_route_participant = doc.routes.filter(sender=request.user).exists() or doc.routes.filter(recipient=request.user).exists()
    if request.user not in [doc.author, doc.recipient] and not is_route_participant and not request.user.is_superuser:
        return HttpResponse("У вас нет доступа к этому документу", status=403)
        
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    action = None
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        # 1. Отправка черновика
        if action == 'send' and doc.status == 'draft' and request.user == doc.author:
            recipient_id = request.POST.get('recipient')
            if recipient_id:
                recipient = get_object_or_404(User, pk=recipient_id)
                doc.recipient = recipient
                doc.status = 'review'
                doc.save()
                DocumentRoute.objects.create(
                    document=doc, sender=request.user, recipient=recipient,
                    status_at_moment='review', comment='Отправлен в работу'
                )
                # Рассылаем обновление по WebSocket
            channel_layer = get_channel_layer()
            routes = doc.routes.all().select_related('sender', 'recipient')
            comments = doc.comments.all().select_related('author')
            history_html = render_to_string('edocs/_history_partial.html', {
                'doc': doc, 'routes': routes, 'comments': comments, 'request': request
            })
            async_to_sync(channel_layer.group_send)(
                f'doc_{doc.pk}',
                {'type': 'history_update', 'html': history_html}
            )
            if not is_ajax: return redirect('edocs:document_detail', pk=doc.pk)
                
        # 2. Пересылка дальше
        elif action == 'forward' and request.user == doc.recipient and doc.status in ['review', 'approved']:
            new_recipient_id = request.POST.get('new_recipient')
            if new_recipient_id:
                new_recipient = get_object_or_404(User, pk=new_recipient_id)
                comment_text = request.POST.get('comment_text', '').strip()
                
                DocumentRoute.objects.create(
                    document=doc, sender=request.user, recipient=new_recipient,
                    status_at_moment='review', comment=comment_text or 'Переслано'
                )
                
                auto_text = f"Переслано: {new_recipient.username} ({timezone.now().strftime('%d.%m.%Y %H:%M')})"
                if doc.resolution:
                    doc.resolution += f"\n{auto_text}"
                else:
                    doc.resolution = auto_text
                    
                doc.recipient = new_recipient
                doc.status = 'review'
                doc.save()
                # Рассылаем обновление по WebSocket
            channel_layer = get_channel_layer()
            routes = doc.routes.all().select_related('sender', 'recipient')
            comments = doc.comments.all().select_related('author')
            history_html = render_to_string('edocs/_history_partial.html', {
                'doc': doc, 'routes': routes, 'comments': comments, 'request': request
            })
            async_to_sync(channel_layer.group_send)(
                f'doc_{doc.pk}',
                {'type': 'history_update', 'html': history_html}
            )
            if not is_ajax: return redirect('edocs:document_detail', pk=doc.pk)
                
        # 3. Возврат на доработку (только автор)
        elif action == 'revision' and request.user == doc.author and doc.status in ['approved', 'rejected']:
            comment_text = request.POST.get('comment_text', '').strip()
            
            DocumentRoute.objects.create(
                document=doc, sender=request.user, recipient=doc.recipient,
                status_at_moment='revision', comment=comment_text or 'Возврат на доработку'
            )
            
            doc.status = 'revision'
            doc.save()
            # Рассылаем обновление по WebSocket
            channel_layer = get_channel_layer()
            routes = doc.routes.all().select_related('sender', 'recipient')
            comments = doc.comments.all().select_related('author')
            history_html = render_to_string('edocs/_history_partial.html', {
                'doc': doc, 'routes': routes, 'comments': comments, 'request': request
            })
            async_to_sync(channel_layer.group_send)(
                f'doc_{doc.pk}',
                {'type': 'history_update', 'html': history_html}
            )
            if not is_ajax: return redirect('edocs:document_detail', pk=doc.pk)
                
        # 4. Смена статуса (только получатель) - теперь работает и из revision
        elif action == 'status' and request.user == doc.recipient and doc.status in ['review', 'revision']:
            new_status = request.POST.get('status')
            if new_status in ['approved', 'rejected']:
                doc.status = new_status
                comment_text = request.POST.get('comment_text', '').strip()
                
                DocumentRoute.objects.create(
                    document=doc, sender=request.user, recipient=doc.recipient,
                    status_at_moment=new_status, comment=comment_text
                )
                
                if comment_text:
                    Comment.objects.create(document=doc, author=request.user, text=comment_text)
                doc.save()
                # Рассылаем обновление по WebSocket
            channel_layer = get_channel_layer()
            routes = doc.routes.all().select_related('sender', 'recipient')
            comments = doc.comments.all().select_related('author')
            history_html = render_to_string('edocs/_history_partial.html', {
                'doc': doc, 'routes': routes, 'comments': comments, 'request': request
            })
            async_to_sync(channel_layer.group_send)(
                f'doc_{doc.pk}',
                {'type': 'history_update', 'html': history_html}
            )
            if not is_ajax: return redirect('edocs:document_detail', pk=doc.pk)
                
        # 5. Комментарий/резолюция
        else:
            resolution = request.POST.get('resolution')
            reply_comment = request.POST.get('reply_comment')
            reply_file = request.FILES.get('reply_file')
            comment_text = request.POST.get('comment_text', '').strip()
            
            if resolution is not None: doc.resolution = resolution
            if reply_comment is not None: doc.reply_comment = reply_comment
            if reply_file: doc.reply_file = reply_file
            
            if comment_text:
                Comment.objects.create(document=doc, author=request.user, text=comment_text)
                
            doc.save()
            # Рассылаем обновление по WebSocket
            channel_layer = get_channel_layer()
            routes = doc.routes.all().select_related('sender', 'recipient')
            comments = doc.comments.all().select_related('author')
            history_html = render_to_string('edocs/_history_partial.html', {
                'doc': doc, 'routes': routes, 'comments': comments, 'request': request
            })
            async_to_sync(channel_layer.group_send)(
                f'doc_{doc.pk}',
                {'type': 'history_update', 'html': history_html}
            )
            if not is_ajax: return redirect('edocs:document_detail', pk=doc.pk)

        # Если AJAX - возвращаем JSON с обновленной историей и статусом
        if is_ajax:
            doc.refresh_from_db()
            routes = doc.routes.all().select_related('sender', 'recipient')
            comments = doc.comments.all().select_related('author')
            
            # Формируем HTML для чата (упрощенно) или отдаем JSON
            history_html = render_to_string('edocs/_history_partial.html', {
                'doc': doc, 'routes': routes, 'comments': comments, 'request': request
            })
            return JsonResponse({'status': 'ok', 'history_html': history_html, 'doc_status': doc.status})

    employees = User.objects.filter(is_active=True).exclude(id=request.user.id)
    comments = doc.comments.all().select_related('author')
    routes = doc.routes.all().select_related('sender', 'recipient')
        
    return render(request, 'edocs/document_detail.html', {
        'doc': doc, 'employees': employees, 'comments': comments, 'routes': routes
    })










@login_required
def view_reply(request, pk):
    """Просмотр ответного файла (конвертация в PDF)"""
    doc = get_object_or_404(Document, pk=pk)
    if not doc.reply_file:
        return HttpResponse("Ответный файл не прикреплен", status=404)
        
    file_path = doc.reply_file.path
    ext = os.path.splitext(file_path)[1].lower()
    
    # Если PDF или картинка - отдаем как есть
    if ext in ['.pdf', '.jpg', '.jpeg', '.png', '.gif', '.webp']:
        content_type, _ = mimetypes.guess_type(file_path)
        response = FileResponse(open(file_path, 'rb'), content_type=content_type)
        response['Content-Disposition'] = f'inline; filename="{os.path.basename(file_path)}"'
        response['X-Frame-Options'] = 'SAMEORIGIN'
        return response

    # Если офисный документ - конвертируем в PDF
    if ext in ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods', '.rtf']:
        from .utils import convert_to_pdf
        pdf_path = convert_to_pdf(file_path)
        if pdf_path:
            response = FileResponse(open(pdf_path, 'rb'), content_type='application/pdf')
            response['Content-Disposition'] = f'inline; filename="{doc.title}_reply.pdf"'
            response['X-Frame-Options'] = 'SAMEORIGIN'
            # Удаляем временный PDF (если это не оригинальный файл)
            if pdf_path != file_path:
                os.remove(pdf_path)
            return response
        else:
            return HttpResponse("Ошибка конвертации ответного файла", status=500)
            
    return HttpResponse("Этот формат не поддерживается для просмотра", status=415)


@login_required
def get_history(request, pk):
    """Возвращает только HTML истории (для AJAX-обновления)"""
    doc = get_object_or_404(Document, pk=pk)
    
    if request.user not in [doc.author, doc.recipient] and not request.user.is_superuser:
        return HttpResponse("Нет доступа", status=403)
        
    routes = doc.routes.all().select_related('sender', 'recipient')
    comments = doc.comments.all().select_related('author')
    
    history_html = render_to_string('edocs/_history_partial.html', {
        'doc': doc, 'routes': routes, 'comments': comments, 'request': request
    })
    return HttpResponse(history_html)


@login_required
def employee_detail(request, pk):
    """Профиль сотрудника"""
    employee = get_object_or_404(User, pk=pk)
    return render(request, 'edocs/employee_detail.html', {'employee': employee})

from django.http import JsonResponse
from django.views.decorators.http import require_POST

@login_required
@require_POST
def send_document_to_tg(request, pk):
    """Отправляет файл документа в Telegram текущему пользователю."""
    doc = get_object_or_404(Document, pk=pk)
    
    if not request.user.telegram_id:
        return JsonResponse({'ok': False, 'error': 'У вас не привязан Telegram'})
    
    from apps.accounts.telegram_notify import send_telegram_document
    from django.conf import settings
    
    # Выбираем файл: PDF (если есть) или оригинал
    file_path = None
    filename = None
    if doc.pdf_file:
        file_path = doc.pdf_file.path
        filename = f"{doc.title}.pdf"
    elif doc.file:
        import os
        file_path = doc.file.path
        filename = os.path.basename(doc.file.name)
    
    if not file_path:
        return JsonResponse({'ok': False, 'error': 'У документа нет файла'})
    
    caption = (
        f"📄 <b>{doc.title}</b>\n"
        f"От: {doc.author.get_full_name() or doc.author.username}\n"
        f"Статус: {doc.get_status_display()}\n"
        f"<a href='{settings.CERBER_WEBAPP_URL}/edocs/document/{doc.id}/'>Открыть в Cerberus</a>"
    )
    
    try:
        success = send_telegram_document(request.user.telegram_id, file_path, caption=caption, filename=filename)
        if success:
            return JsonResponse({'ok': True})
        else:
            return JsonResponse({'ok': False, 'error': 'Не удалось отправить файл'})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)})
