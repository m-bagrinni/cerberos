"""REST API для мониторинга."""
import asyncio

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from apps.devices.models import ActionLog
from apps.monitoring.snmp import SnmpClient, SnmpError, SnmpTarget

from .models import Interface, MetricValue, Vlan
from .serializers import (
    InterfaceDetailSerializer,
    MetricValueSerializer,
    VlanSerializer,
)


OID_IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"

IF_ADMIN_UP = 1
IF_ADMIN_DOWN = 2


class MetricValueViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = MetricValue.objects.select_related("metric", "device").all()
    serializer_class = MetricValueSerializer


class InterfaceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Interface.objects.select_related("device").prefetch_related(
        "vlan_links__vlan"
    )
    serializer_class = InterfaceDetailSerializer

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsAdminUser],
        url_path="set-admin-status",
    )
    def set_admin_status(self, request, pk=None):
        """
        Установить ifAdminStatus (up/down).
        POST /api/v1/interfaces/<id>/set-admin-status/
        Body: {"state": "up" | "down"}
        """
        iface = self.get_object()
        device = iface.device

        state = (request.data.get("state") or "").lower()
        if state not in ("up", "down"):
            return Response(
                {"error": "state must be 'up' or 'down'"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Проверка что порт физический
        if not iface.is_physical:
            return Response(
                {"error": "Можно управлять только физическими портами"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Аудит
        log = ActionLog.objects.create(
            device=device,
            user=request.user if request.user.is_authenticated else None,
            action=(
                ActionLog.ACTION_PORT_UP if state == "up"
                else ActionLog.ACTION_PORT_DOWN
            ),
            target=iface.name or f"if{iface.if_index}",
            status=ActionLog.STATUS_PENDING,
        )

        # SNMP SET
        try:
            self._set_admin_status(device, iface.if_index, state)
        except Exception as e:
            log.status = ActionLog.STATUS_ERROR
            log.message = str(e)
            log.save(update_fields=["status", "message"])
            return Response(
                {"error": str(e), "log_id": log.id},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Обновим запись Interface в БД сразу
        iface.admin_status = IF_ADMIN_UP if state == "up" else IF_ADMIN_DOWN
        iface.save(update_fields=["admin_status"])

        log.status = ActionLog.STATUS_SUCCESS
        log.message = f"Порт {iface.name} переведён в {state}"
        log.save(update_fields=["status", "message"])

        # Отдаём обновлённый интерфейс
        serializer = self.get_serializer(iface)
        return Response({
            "ok": True,
            "interface": serializer.data,
            "log_id": log.id,
            "message": log.message,
        })

    def _set_admin_status(self, device, if_index: int, state: str):
        write_community = (
            getattr(device, "snmp_write_community", None)
            or device.snmp_community
        )
        if not write_community:
            raise RuntimeError("SNMP community не настроен")

        client = SnmpClient(SnmpTarget(
            host=device.ip,
            community=write_community,
            port=device.snmp_port,
            timeout=5.0, retries=2,
        ))
        value = IF_ADMIN_UP if state == "up" else IF_ADMIN_DOWN
        oid = f"{OID_IF_ADMIN_STATUS}.{if_index}"
        asyncio.run(client.set_value(oid, value, value_type="Integer"))


class VlanViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Vlan.objects.select_related("device").all()
    serializer_class = VlanSerializer


# ============================================================
# Traffic history для интерфейса
# ============================================================

def interface_traffic_history(request, pk):
    """
    История трафика порта за N часов.
    ?hours=1|6|24|168 (по умолчанию 24)
    """
    from datetime import timedelta
    from django.utils import timezone
    from rest_framework.decorators import api_view, permission_classes
    from rest_framework.permissions import IsAuthenticated
    from rest_framework.response import Response
    from .models import Interface, InterfaceSample

    # декораторы применяются вручную — т.к. внутри функции
    @api_view(["GET"])
    @permission_classes([IsAuthenticated])
    def _view(request, pk):
        try:
            hours = int(request.query_params.get("hours", 24))
        except (TypeError, ValueError):
            hours = 24
        hours = max(1, min(hours, 24 * 30))

        try:
            iface = Interface.objects.get(pk=pk)
        except Interface.DoesNotExist:
            return Response({"error": "interface not found"}, status=404)

        since = timezone.now() - timedelta(hours=hours)
        qs = (
            InterfaceSample.objects
            .filter(interface=iface, ts__gte=since)
            .order_by("ts")
            .values("ts", "in_bps", "out_bps", "in_errors", "out_errors")
        )

        data = [
            {
                "ts": row["ts"].isoformat(),
                "in_bps": row["in_bps"],
                "out_bps": row["out_bps"],
                "in_errors": row["in_errors"],
                "out_errors": row["out_errors"],
            }
            for row in qs
        ]
        return Response(data)

    return _view(request, pk)



# ============================================================
# DDM history для интерфейса (SFP RX/TX)
# ============================================================

def interface_ddm_history(request, pk):
    """
    История DDM (SFP RX/TX) для интерфейса за N часов.
    ?hours=1|6|24|168 (по умолчанию 24)
    """
    from datetime import timedelta
    from django.utils import timezone
    from rest_framework.decorators import api_view, permission_classes
    from rest_framework.permissions import IsAuthenticated
    from rest_framework.response import Response
    from .models import Interface, MetricValue

    @api_view(["GET"])
    @permission_classes([IsAuthenticated])
    def _view(request, pk):
        try:
            hours = int(request.query_params.get("hours", 24))
        except (TypeError, ValueError):
            hours = 24
        hours = max(1, min(hours, 24 * 30))

        try:
            iface = Interface.objects.get(pk=pk)
        except Interface.DoesNotExist:
            return Response({"error": "interface not found"}, status=404)

        since = timezone.now() - timedelta(hours=hours)
        qs = (
            MetricValue.objects
            .filter(
                device=iface.device,
                instance=iface.if_index,
                metric__key__in=["sfp_rx_dbm", "sfp_tx_dbm", "sfp_temp", "sfp_bias", "sfp_voltage"],
                ts__gte=since,
            )
            .order_by("ts")
            .values("ts", "metric__key", "value")
        )

        # Группируем по ts (округление до минуты, чтобы RX и TX совпали)
        by_ts = {}
        for row in qs:
            ts = row["ts"]
            ts_min = ts.replace(second=0, microsecond=0).isoformat()
            if ts_min not in by_ts:
                by_ts[ts_min] = {"ts": ts_min}
            key = row["metric__key"]
            if key == "sfp_rx_dbm":
                by_ts[ts_min]["rx_dbm"] = row["value"]
            elif key == "sfp_tx_dbm":
                by_ts[ts_min]["tx_dbm"] = row["value"]
            elif key == "sfp_temp":
                by_ts[ts_min]["temp_c"] = row["value"]
            elif key == "sfp_bias":
                by_ts[ts_min]["bias_ma"] = row["value"]
            elif key == "sfp_voltage":
                by_ts[ts_min]["volt_v"] = row["value"]

        return Response(list(by_ts.values()))

    return _view(request, pk)


# ============================================================
# Суммарный трафик устройства (для Tab 2 «Метрики»)
# ============================================================

def device_traffic_summary(request, ip):
    """
    Суммарный трафик по всем физическим портам устройства.
    ?hours=1|6|24|168
    Возвращает [{ts, in_bps, out_bps}, ...]
    """
    from datetime import timedelta
    from django.utils import timezone
    from django.db.models import Sum
    from rest_framework.decorators import api_view, permission_classes
    from rest_framework.permissions import IsAuthenticated
    from rest_framework.response import Response
    from apps.devices.models import Device
    from .models import Interface, InterfaceSample

    @api_view(["GET"])
    @permission_classes([IsAuthenticated])
    def _view(request, ip):
        try:
            hours = int(request.query_params.get("hours", 24))
        except (TypeError, ValueError):
            hours = 24
        hours = max(1, min(hours, 24 * 30))

        try:
            device = Device.objects.get(ip=ip)
        except Device.DoesNotExist:
            return Response({"error": "device not found"}, status=404)

        since = timezone.now() - timedelta(hours=hours)

        # Берём только физические интерфейсы
        physical_ids = list(
            Interface.objects
            .filter(
                device=device,
                if_type__in=Interface.PHYSICAL_IF_TYPES,
                if_index__lt=150,
            )
            .values_list("id", flat=True)
        )

        if not physical_ids:
            return Response([])

        # Агрегируем по ts: сумма in/out всех портов
        qs = (
            InterfaceSample.objects
            .filter(interface_id__in=physical_ids, ts__gte=since)
            .values("ts")
            .annotate(in_bps=Sum("in_bps"), out_bps=Sum("out_bps"))
            .order_by("ts")
        )

        data = [
            {
                "ts": row["ts"].isoformat(),
                "in_bps": row["in_bps"] or 0,
                "out_bps": row["out_bps"] or 0,
            }
            for row in qs
        ]
        return Response(data)

    return _view(request, ip)


# ============================================================
# История метрик для NE (мультиплексоры)
# ============================================================

from django.http import JsonResponse
from django.views.decorators.http import require_GET
from datetime import timedelta
from django.utils import timezone


@require_GET
def ne_metric_history(request, ne_id):
    """
    История одной метрики NE.

    Query params:
        key      — ключ метрики (sfp_temp, sfp_rx_dbm, sfp_tx_dbm, sfp_bias, sfp_voltage)
        instance — номер порта ("1", "2") или пусто
        hours    — за сколько часов (default: 24)
        limit    — максимум точек (default: 1000)
    """
    from apps.devices.models import NeDevice
    from apps.monitoring.models import MetricValue

    key = request.GET.get("key", "").strip()
    instance = request.GET.get("instance", "").strip()
    try:
        hours = int(request.GET.get("hours", "24"))
        limit = int(request.GET.get("limit", "1000"))
    except ValueError:
        return JsonResponse({"ok": False, "error": "invalid hours/limit"}, status=400)

    if not key:
        return JsonResponse({"ok": False, "error": "key required"}, status=400)

    try:
        ne = NeDevice.objects.get(id=ne_id)
    except NeDevice.DoesNotExist:
        return JsonResponse({"ok": False, "error": "ne not found"}, status=404)

    since = timezone.now() - timedelta(hours=hours)

    qs = (
        MetricValue.objects
        .filter(
            ne_device=ne,
            metric__key=key,
            instance=instance,
            ts__gte=since,
            value__isnull=False,
        )
        .order_by("ts")
        .values("ts", "value")[:limit]
    )

    points = [{"ts": row["ts"].isoformat(), "value": float(row["value"])} for row in qs]

    return JsonResponse({
        "ok": True,
        "key": key,
        "instance": instance,
        "hours": hours,
        "ne_id": ne.id,
        "ne_name": ne.display_name,
        "points": points,
    })


# ============================================================
# История метрик NE (мультиплексоры)
# ============================================================

from django.http import JsonResponse
from django.views.decorators.http import require_GET
from datetime import timedelta
from django.utils import timezone


@require_GET
def ne_metric_history(request, ne_id):
    """
    История значений одной метрики NE.

    Query:
        key      — sfp_temp / sfp_rx_dbm / sfp_tx_dbm / sfp_bias / sfp_voltage
        instance — "1" или "2" (номер SFP-порта)
        hours    — за сколько часов (по умолчанию 24)
        limit    — максимум точек (по умолчанию 1000)
    """
    from apps.devices.models import NeDevice
    from apps.monitoring.models import MetricValue

    key = request.GET.get("key", "").strip()
    instance = request.GET.get("instance", "").strip()
    try:
        hours = int(request.GET.get("hours", "24"))
        limit = int(request.GET.get("limit", "1000"))
    except ValueError:
        return JsonResponse({"ok": False, "error": "invalid hours/limit"}, status=400)

    if not key:
        return JsonResponse({"ok": False, "error": "key required"}, status=400)

    try:
        ne = NeDevice.objects.get(id=ne_id)
    except NeDevice.DoesNotExist:
        return JsonResponse({"ok": False, "error": "ne not found"}, status=404)

    since = timezone.now() - timedelta(hours=hours)
    qs = (
        MetricValue.objects
        .filter(
            ne_device=ne,
            metric__key=key,
            instance=instance,
            ts__gte=since,
            value__isnull=False,
        )
        .order_by("ts")
        .values("ts", "value")[:limit]
    )
    points = [{"ts": r["ts"].isoformat(), "value": float(r["value"])} for r in qs]
    return JsonResponse({
        "ok": True,
        "key": key,
        "instance": instance,
        "hours": hours,
        "ne_id": ne.id,
        "ne_name": ne.display_name,
        "points": points,
    })


@require_GET
def ne_ddm_ports(request, ne_id):
    """
    Возвращает список портов (instance) с DDM для NE.
    GET /api/v1/ne/<pk>/ddm-ports/
    """
    from apps.devices.models import NeDevice
    from apps.monitoring.models import MetricValue

    try:
        ne = NeDevice.objects.get(id=ne_id)
    except NeDevice.DoesNotExist:
        return JsonResponse({"ok": False, "error": "ne not found"}, status=404)

    # Уникальные instance, где есть sfp_rx_dbm
    instances = (
        MetricValue.objects
        .filter(ne_device=ne, metric__key="sfp_rx_dbm")
        .values_list("instance", flat=True)
        .distinct()
        .order_by("instance")
    )
    
    ports = []
    for inst in instances:
        rx = MetricValue.objects.filter(ne_device=ne, metric__key="sfp_rx_dbm", instance=inst).order_by("-ts").first()
        tx = MetricValue.objects.filter(ne_device=ne, metric__key="sfp_tx_dbm", instance=inst).order_by("-ts").first()
        ports.append({
            "instance": inst,
            "rx": rx.value if rx else None,
            "tx": tx.value if tx else None,
            "updated_at": rx.ts.isoformat() if rx else None,
        })
    
    return JsonResponse({
        "ok": True,
        "ne_id": ne.id,
        "ne_name": ne.display_name,
        "ports": ports,
    })


@require_GET
def ne_metrics_bulk(request):
    """
    Массовая выгрузка метрики по всем NE шлюза.

    Query:
        gateway_ip — IP шлюза (172.16.101.12)
        key        — метрика (sfp_temp / sfp_rx_dbm / sfp_tx_dbm / sfp_bias / sfp_voltage)
        instance   — номер SFP-порта ("1" / "2")
        hours      — период (default 24)
        limit      — макс. точек на NE (default 500)

    Response: {
        "ok": true,
        "key": ...,
        "hours": ...,
        "nes": [
            {"ne_id": 2, "name": "NE-2", "points": [{"ts": ..., "value": ...}]},
            ...
        ]
    }
    """
    from apps.devices.models import Device, NeDevice
    from apps.monitoring.models import MetricValue

    gw_ip = request.GET.get("gateway_ip", "").strip()
    key = request.GET.get("key", "").strip()
    instance = request.GET.get("instance", "").strip()
    try:
        hours = int(request.GET.get("hours", "24"))
        limit = int(request.GET.get("limit", "500"))
    except ValueError:
        return JsonResponse({"ok": False, "error": "invalid hours/limit"}, status=400)

    if not gw_ip or not key:
        return JsonResponse({"ok": False, "error": "gateway_ip and key required"}, status=400)

    try:
        gateway = Device.objects.get(ip=gw_ip)
    except Device.DoesNotExist:
        return JsonResponse({"ok": False, "error": "gateway not found"}, status=404)

    since = timezone.now() - timedelta(hours=hours)
    nes = NeDevice.objects.filter(gateway=gateway).order_by("ne_id")

    result = []
    for ne in nes:
        qs = (
            MetricValue.objects
            .filter(
                ne_device=ne,
                metric__key=key,
                instance=instance,
                ts__gte=since,
                value__isnull=False,
            )
            .order_by("ts")
            .values("ts", "value")[:limit]
        )
        points = [{"ts": r["ts"].isoformat(), "value": float(r["value"])} for r in qs]
        if points:
            result.append({
                "id": ne.id,
                "ne_id": ne.ne_id,
                "name": ne.display_name,
                "points": points,
            })

    return JsonResponse({
        "ok": True,
        "gateway_ip": gw_ip,
        "key": key,
        "instance": instance,
        "hours": hours,
        "nes": result,
    })


# ============================================================
# Переупорядочивание NE (drag & drop)
# ============================================================

from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect
import json


@require_POST
def ne_reorder(request, ne_id):
    """
    POST {"sort_order": 123} — установить порядок для NE.

    Просто сохраняет переданное число в NeDevice.sort_order.
    Полный список sort_order клиент присылает при drop — все пересчитываются.
    """
    from apps.devices.models import NeDevice

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "invalid json"}, status=400)

    try:
        new_order = int(payload.get("sort_order", 0))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "invalid sort_order"}, status=400)

    try:
        ne = NeDevice.objects.get(id=ne_id)
    except NeDevice.DoesNotExist:
        return JsonResponse({"ok": False, "error": "not found"}, status=404)

    ne.sort_order = new_order
    ne.save(update_fields=["sort_order"])

    return JsonResponse({"ok": True, "id": ne.id, "sort_order": ne.sort_order})


@require_POST
def ne_reorder_bulk(request):
    """
    POST {"order": [{"id": 1, "sort_order": 10}, ...]}
    Массовое обновление порядка (используется после drop).
    """
    from apps.devices.models import NeDevice

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "invalid json"}, status=400)

    order = payload.get("order", [])
    if not isinstance(order, list):
        return JsonResponse({"ok": False, "error": "order must be list"}, status=400)

    updated = 0
    for item in order:
        try:
            ne_id = int(item["id"])
            sort_order = int(item["sort_order"])
        except (KeyError, TypeError, ValueError):
            continue
        NeDevice.objects.filter(id=ne_id).update(sort_order=sort_order)
        updated += 1

    return JsonResponse({"ok": True, "updated": updated})


# ============================================================
# Ручной запуск опроса (кнопка «Опросить сейчас»)
# ============================================================

from django.views.decorators.http import require_POST


@require_POST
def ne_poll_now(request, ne_id):
    """POST — запустить poll для одного NE (синхронно)."""
    from apps.devices.models import NeDevice
    from django.core.management import call_command

    try:
        ne = NeDevice.objects.select_related("gateway").get(id=ne_id)
    except NeDevice.DoesNotExist:
        return JsonResponse({"ok": False, "error": "not found"}, status=404)

    try:
        call_command("poll_olencom", ne.gateway.ip, ne=[ne.ne_id])
        return JsonResponse({"ok": True, "message": f"Poll завершён для NE-{ne.ne_id}"})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@require_POST
def gateway_poll_now(request, gateway_ip):
    """POST — запустить poll всех NE шлюза (синхронно)."""
    from django.core.management import call_command

    try:
        call_command("poll_olencom", gateway_ip)
        return JsonResponse({"ok": True, "message": f"Poll завершён для {gateway_ip}"})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


# ============================================================
# Interface — установка ifAlias (SNMP SET)
# ============================================================

from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["POST"])
def set_interface_alias(request, pk):
    """Установить описание (ifAlias) интерфейса через SNMP SET (subprocess)."""
    import subprocess
    from django.shortcuts import get_object_or_404
    from apps.monitoring.models import Interface

    iface = get_object_or_404(
        Interface.objects.select_related("device"), pk=pk,
    )
    new_alias = (request.data.get("alias") or "").strip()[:255]
    device = iface.device

    community = (
        getattr(device, "snmp_write_community", None)
        or device.snmp_community
    )
    if not community:
        return Response(
            {"ok": False, "error": "Не задана write-community"},
            status=400,
        )

    v = str(device.snmp_version or "2c").strip().lower()
    if v in ("2", "2c", "v2", "v2c"):
        v = "2c"
    elif v in ("1", "v1"):
        v = "1"
    else:
        v = "2c"

    oid = f".1.3.6.1.2.1.31.1.1.1.18.{iface.if_index}"

    try:
        cmd = [
            "snmpset", "-v", v, "-c", community,
            "-t", "3", "-r", "1",
            device.ip, oid, "s", new_alias,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()[:300]
            return Response(
                {"ok": False, "error": err or "snmpset failed"},
                status=400,
            )
    except Exception as e:
        return Response(
            {"ok": False, "error": f"{type(e).__name__}: {e}"},
            status=400,
        )

    iface.alias = new_alias
    iface.save(update_fields=["alias"])
    return Response({"ok": True, "alias": iface.alias})




@require_POST
@require_POST
def device_poll_interfaces_now(request, ip):
    """POST — запустить poll интерфейсов для одного устройства."""
    from apps.devices.models import Device
    from django.core.management import call_command

    try:
        device = Device.objects.get(ip=ip)
    except Device.DoesNotExist:
        return JsonResponse({"ok": False, "error": "not found"}, status=404)

    try:
        call_command("poll_interfaces", ip)
        return JsonResponse({"ok": True, "message": f"Интерфейсы опрошены для {ip}"})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@require_POST
def device_poll_now(request, ip):
    """POST — запустить poll для одного устройства (синхронно)."""
    from apps.devices.models import Device
    from django.core.management import call_command

    try:
        device = Device.objects.get(ip=ip)
    except Device.DoesNotExist:
        return JsonResponse({"ok": False, "error": "not found"}, status=404)

    try:
        call_command("poll_device", ip)
        return JsonResponse({"ok": True, "message": f"Poll завершён для {ip}"})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)
