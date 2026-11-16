"""
API для алертов.
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import Alert


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def alerts_list(request):
    """
    GET /api/v1/alerts/
    ?state=firing|ok
    ?severity=crit|warn
    """
    state = request.query_params.get("state")
    severity = request.query_params.get("severity")
    
    qs = Alert.objects.select_related("device", "ip_address", "ne_device", "acked_by").order_by("-started_at")
    
    if state:
        qs = qs.filter(state=state)
    
    if severity:
        qs = qs.filter(severity=severity)
    
    data = []
    for a in qs[:100]:
        # Определяем цель (device / ip / ne)
        target = "?"
        if a.device:
            target = a.device.name
        elif a.ip_address:
            target = a.ip_address.ip
        elif a.ne_device:
            target = f"NE-{a.ne_device.ne_id}"
        
        data.append({
            "id": a.id,
            "severity": a.severity,
            "state": a.state,
            "metric_key": a.metric_key,
            "message": a.message,
            "target": target,
            "value": a.value,
            "threshold": a.threshold,
            "started_at": a.started_at.isoformat() if a.started_at else None,
            "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
            "is_firing": a.state == Alert.STATE_FIRING,
            "acked_by": (a.acked_by.get_full_name() or a.acked_by.username) if a.acked_by else None,
            "acked_at": a.acked_at.isoformat() if a.acked_at else None,
        })
    
    return Response({
        "count": len(data),
        "results": data,
    })


# ============================================================
# Broadcast storm events (для вкладки «Сеть»)
# ============================================================

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def storm_events_view(request):
    """Активные + история BroadcastStormEvent."""
    from apps.subnets.models_traffic import BroadcastStormEvent

    active = BroadcastStormEvent.objects.filter(
        state=BroadcastStormEvent.STATE_FIRING,
    ).order_by("-started_at")

    history = BroadcastStormEvent.objects.exclude(
        state=BroadcastStormEvent.STATE_FIRING,
    ).order_by("-started_at")[:200]

    def _ser(e):
        return {
            "id": e.pk,
            "subnet_id": e.subnet_id,
            "subnet_cidr": e.subnet.cidr,
            "iface": e.iface,
            "severity": e.severity,
            "state": e.state,
            "bcast_pct_peak": e.bcast_pct_peak,
            "bcast_pct_now": e.bcast_pct_now,
            "mcast_pct_peak": e.mcast_pct_peak,
            "mcast_pct_now": e.mcast_pct_now,
            "total_now": e.total_now,
            "top_macs": e.top_macs[:5],
            "reason": e.reason,
            "started_at": e.started_at.isoformat(),
            "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
        }

    return Response({
        "ok": True,
        "active": [_ser(e) for e in active],
        "history": [_ser(e) for e in history],
        "counts": {
            "active_crit": active.filter(severity="crit").count(),
            "active_warn": active.filter(severity="warn").count(),
            "total_active": active.count(),
        },
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def alert_ack(request, pk):
    """POST /api/v1/alerts/<pk>/ack/ — взять в работу (раздел «Алерты»: изменение)."""
    from .ack import acknowledge, user_label
    alert, err = acknowledge(pk, request.user)
    if alert is None:
        return Response({"ok": False, "error": err}, status=403 if "права" in err else 404)
    return Response({"ok": not err, "error": err, "acked_by": user_label(alert.acked_by),
                     "acked_at": alert.acked_at.isoformat() if alert.acked_at else None},
                    status=200 if not err else 409)
