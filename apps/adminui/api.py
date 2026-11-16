"""AJAX-эндпоинты админ-панели."""
import json
import subprocess

from django.contrib.auth.decorators import login_required  # права — AccessMiddleware (раздел по /admin-ui/<сегмент>/)
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from apps.devices.models import Device, NeDevice
from apps.monitoring.tasks import poll_device_task


def _snmpget(host: str, community: str, version: str, oid: str,
             timeout: int = 3, retries: int = 1) -> tuple[bool, str | None]:
    """Универсальный snmpget через net-snmp subprocess.

    Поддерживает v1 и v2c. Возвращает (ok, value).
    """
    # pysnmp принимает v1/v2c как "1"/"2c"; net-snmp — как "1"/"2c"
    # но у нас в БД snmp_version может быть "2" — нормализуем
    v = str(version).strip().lower()
    if v in ("2", "2c", "v2", "v2c"):
        v = "2c"
    elif v in ("1", "v1"):
        v = "1"
    else:
        v = "2c"  # fallback

    try:
        cmd = [
            "snmpget",
            "-v", v,
            "-c", community,
            "-t", str(timeout),
            "-r", str(retries),
            "-Oqv",
            host, oid,
        ]
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=(timeout * (retries + 1)) + 1,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()[:200] or "snmpget failed"
            return False, err
        return True, r.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except FileNotFoundError:
        return False, "snmpget not installed in container"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


@login_required
@require_POST
def test_snmp(request):
    """Проверка SNMP: snmpget sysDescr + sysObjectID."""
    try:
        payload = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    ip = (payload.get("ip") or "").strip()
    community = (payload.get("community") or "").strip()
    version = str(payload.get("version") or "2").strip()

    if not ip or not community:
        return JsonResponse({"ok": False, "error": "Укажите IP и Community"})

    ok1, sys_descr = _snmpget(ip, community, version, "1.3.6.1.2.1.1.1.0")
    if not ok1:
        return JsonResponse({"ok": False, "error": f"snmpget sysDescr: {sys_descr}"})

    ok2, sys_object_id = _snmpget(ip, community, version, "1.3.6.1.2.1.1.2.0")
    return JsonResponse({
        "ok": True,
        "sys_descr": sys_descr if ok2 else sys_descr,
        "sys_object_id": sys_object_id if ok2 else "—",
    })


@login_required
@require_POST
def test_oid(request):
    """Проверка OID: snmpget одного OID на устройстве."""
    try:
        payload = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    ip = (payload.get("ip") or "").strip()
    community = (payload.get("community") or "").strip()
    version = str(payload.get("version") or "2").strip()
    oid = (payload.get("oid") or "").strip()

    if not ip or not community or not oid:
        return JsonResponse({"ok": False, "error": "Укажите ip, community и oid"})

    ok, value = _snmpget(ip, community, version, oid)
    if ok:
        return JsonResponse({"ok": True, "value": value})
    return JsonResponse({"ok": False, "error": value or "unknown"})


@login_required
@require_POST
def poll_now(request, pk):
    """Запуск poll_device_task в Celery по кнопке."""
    try:
        device = Device.objects.get(pk=pk)
    except Device.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Устройство не найдено"}, status=404)

    try:
        result = poll_device_task.delay(device.ip)
        return JsonResponse({"ok": True, "task_id": str(result.id)})
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"{type(e).__name__}: {e}"})


@login_required
@require_POST
def subnet_scan_now(request, pk):
    """Запуск scan_subnet_task для подсети."""
    from apps.subnets.models import Subnet
    try:
        subnet = Subnet.objects.get(pk=pk)
    except Subnet.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Подсеть не найдена"}, status=404)

    try:
        from apps.subnets.tasks import scan_subnet_task
        result = scan_subnet_task.delay(subnet.pk)
        return JsonResponse({"ok": True, "task_id": str(result.id)})
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"{type(e).__name__}: {e}"})


@login_required
@require_POST
def ne_rename(request, pk):
    """Inline-переименование NE."""
    try:
        ne = NeDevice.objects.get(pk=pk)
    except NeDevice.DoesNotExist:
        return JsonResponse({"ok": False, "error": "NE не найден"}, status=404)

    try:
        payload = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    new_name = (payload.get("name") or "").strip()
    if not new_name:
        return JsonResponse({"ok": False, "error": "Имя обязательно"})

    ne.name = new_name
    ne.save(update_fields=["name"])
    return JsonResponse({"ok": True, "name": ne.name})


@login_required
@require_POST
def alert_resolve(request, pk):
    """Ручное разрешение одного алерта."""
    from django.utils import timezone
    from apps.alerts.models import Alert
    try:
        alert = Alert.objects.get(pk=pk)
    except Alert.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Алерт не найден"}, status=404)

    if alert.state != "firing":
        return JsonResponse({"ok": True, "state": alert.state, "skipped": True})

    alert.state = "ok"
    alert.resolved_at = timezone.now()
    alert.save(update_fields=["state", "resolved_at", "updated_at"])
    return JsonResponse({"ok": True, "state": "ok", "alert_id": alert.pk})


@login_required
@require_POST
def alert_bulk_resolve(request):
    """Разрешение нескольких алертов (JSON {ids: [...]})"""
    from django.utils import timezone
    from apps.alerts.models import Alert
    try:
        payload = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    ids = payload.get("ids") or []
    if not ids:
        return JsonResponse({"ok": False, "error": "Пустой список ids"})

    now = timezone.now()
    n = (
        Alert.objects
        .filter(pk__in=ids, state="firing")
        .update(state="ok", resolved_at=now, updated_at=now)
    )
    return JsonResponse({"ok": True, "resolved": n})
