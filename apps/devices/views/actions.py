"""JSON-действия над деревом устройств: перемещение, сортировка, статусы, опрос."""
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST


@require_POST
@csrf_protect
def move_device_to_category(request):
    """
    POST /local/devices/move-category/
    Body: {"ip": "...", "category_slug": "..."}
    """
    from apps.devices.models import Device, DeviceCategory

    try:
        data = json.loads(request.body)
        ip = data.get("ip")
        category_slug = data.get("category_slug")

        if not ip or not category_slug:
            return JsonResponse({"ok": False, "error": "ip и category_slug обязательны"}, status=400)

        try:
            device = Device.objects.get(ip=ip)
        except Device.DoesNotExist:
            return JsonResponse({"ok": False, "error": "Устройство не найдено"}, status=404)

        try:
            category = DeviceCategory.objects.get(slug=category_slug)
        except DeviceCategory.DoesNotExist:
            return JsonResponse({"ok": False, "error": "Категория не найдена"}, status=404)

        device.category = category
        device.subcategory = None  # сброс подкатегории при перемещении в категорию
        device.save(update_fields=["category", "subcategory"])

        return JsonResponse({"ok": True, "device": device.name, "category": category.name})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@require_POST
@csrf_protect
def move_subcategory(request):
    """
    POST /local/devices/move-subcategory/
    Body: {"ip": "...", "subcategory_slug": "..."}
    """
    from apps.devices.models import Device, DeviceSubcategory

    try:
        data = json.loads(request.body)
        ip = data.get("ip")
        subcategory_slug = data.get("subcategory_slug")

        if not ip or not subcategory_slug:
            return JsonResponse({"ok": False, "error": "ip и subcategory_slug обязательны"}, status=400)

        try:
            device = Device.objects.get(ip=ip)
        except Device.DoesNotExist:
            return JsonResponse({"ok": False, "error": "Устройство не найдено"}, status=404)

        try:
            subcategory = DeviceSubcategory.objects.get(slug=subcategory_slug)
        except DeviceSubcategory.DoesNotExist:
            return JsonResponse({"ok": False, "error": "Подкатегория не найдена"}, status=404)

        device.subcategory = subcategory
        device.save(update_fields=["subcategory"])

        return JsonResponse({"ok": True, "device": device.name, "subcategory": subcategory.name})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


# FIXME: require_POST + require_GET вместе => эндпоинт ВСЕГДА отвечает 405.
# Так было в apps/core/views.py; оставлено без изменений поведения.
# Маршрут перекрывает рабочий apps.devices.api.device_statuses (тот же URL).
@require_POST
@csrf_protect
@require_GET
def devices_statuses(request):
    """Возвращает {ip: status} для всех устройств."""
    from apps.devices.models import Device
    
    statuses = {}
    for d in Device.objects.all().values('ip', 'status'):
        statuses[d['ip']] = d['status']
    
    return JsonResponse({"ok": True, "statuses": statuses})


@require_POST
@csrf_protect
def reorder_all(request):
    """
    POST /local/devices/reorder-all/
    Body: {"order": [{"type": "device", "id": "ip"}, {"type": "subcategory", "id": "slug"}]}
    """
    from apps.devices.models import Device, DeviceSubcategory

    try:
        data = json.loads(request.body)
        order = data.get("order", [])
        if not order:
            return JsonResponse({"ok": False, "error": "order обязателен"}, status=400)
        
        for idx, item in enumerate(order):
            if item.get("type") == "device":
                Device.objects.filter(ip=item["id"]).update(sort_order=idx)
            elif item.get("type") == "subcategory":
                DeviceSubcategory.objects.filter(slug=item["id"]).update(sort_order=idx)
        
        return JsonResponse({"ok": True, "count": len(order)})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@require_POST
@csrf_protect
def reorder_devices(request):
    """
    POST /local/devices/reorder/
    Body: {"order": ["ip1", "ip2", ...]}
    """
    from apps.devices.models import Device

    try:
        data = json.loads(request.body)
        order = data.get("order", [])
        if not order:
            return JsonResponse({"ok": False, "error": "order обязателен"}, status=400)
        
        for idx, ip in enumerate(order):
            Device.objects.filter(ip=ip).update(sort_order=idx)
        
        return JsonResponse({"ok": True, "count": len(order)})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@require_POST
@csrf_protect
def move_subcategory_to_category(request):
    """
    POST /local/subcategories/move-category/
    Body: {"slug": "...", "category_slug": "..."}
    """
    from apps.devices.models import DeviceSubcategory, DeviceCategory

    try:
        data = json.loads(request.body)
        slug = data.get("slug")
        category_slug = data.get("category_slug")

        if not slug or not category_slug:
            return JsonResponse({"ok": False, "error": "slug и category_slug обязательны"}, status=400)

        try:
            subcategory = DeviceSubcategory.objects.get(slug=slug)
        except DeviceSubcategory.DoesNotExist:
            return JsonResponse({"ok": False, "error": "Подкатегория не найдена"}, status=404)

        try:
            category = DeviceCategory.objects.get(slug=category_slug)
        except DeviceCategory.DoesNotExist:
            return JsonResponse({"ok": False, "error": "Категория не найдена"}, status=404)

        subcategory.category = category
        subcategory.save(update_fields=["category"])

        return JsonResponse({"ok": True, "subcategory": subcategory.name, "category": category.name})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@require_POST
@csrf_protect
def reorder_subcategories(request):
    """
    POST /local/subcategories/reorder/
    Body: {"order": ["slug1", "slug2", ...]}
    """
    from apps.devices.models import DeviceSubcategory

    try:
        data = json.loads(request.body)
        order = data.get("order", [])
        if not order:
            return JsonResponse({"ok": False, "error": "order обязателен"}, status=400)
        
        for idx, slug in enumerate(order):
            DeviceSubcategory.objects.filter(slug=slug).update(sort_order=idx)
        
        return JsonResponse({"ok": True, "count": len(order)})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@require_POST
@csrf_protect
def poll_all_devices_now(request):
    """
    POST /api/v1/devices/poll-all-now/
    Запускает опрос всех устройств.
    """
    from apps.monitoring.tasks import poll_all_devices
    try:
        result = poll_all_devices.delay()
        return JsonResponse({"ok": True, "task_id": result.id})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)
