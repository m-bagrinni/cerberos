"""REST API для устройств."""
from django.db.models import Max
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.monitoring.models import MetricValue

from .models import Device, DeviceProfile, MetricDefinition, Vendor
from .serializers import (
    DeviceDetailSerializer,
    DeviceListSerializer,
    DeviceProfileSerializer,
    MetricDefinitionSerializer,
    VendorSerializer,
)


class VendorViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Vendor.objects.all()
    serializer_class = VendorSerializer
    lookup_field = "slug"


class DeviceProfileViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = DeviceProfile.objects.select_related("vendor").all()
    serializer_class = DeviceProfileSerializer


class MetricDefinitionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = MetricDefinition.objects.select_related("profile").all()
    serializer_class = MetricDefinitionSerializer


class DeviceViewSet(viewsets.ModelViewSet):
    queryset = Device.objects.select_related("vendor", "profile", "profile_override")
    serializer_class = DeviceListSerializer
    lookup_field = "ip"
    lookup_value_regex = r"[\w\.\:]+"

    def get_serializer_class(self):
        if self.action in ("retrieve", "create", "update", "partial_update"):
            return DeviceDetailSerializer
        return DeviceListSerializer

    @action(detail=True, methods=["get"], url_path="metrics")
    def metrics(self, request, ip=None):
        """GET /api/v1/devices/<ip>/metrics/?latest=true"""
        device = self.get_object()
        latest_only = request.query_params.get("latest", "true") == "true"

        qs = MetricValue.objects.filter(device=device).select_related("metric")

        if latest_only:
            latest_ids = (
                qs.values("metric_id")
                .annotate(last_id=Max("id"))
                .values_list("last_id", flat=True)
            )
            qs = qs.filter(id__in=list(latest_ids))

        qs = qs.order_by("metric__key", "instance")

        from apps.monitoring.serializers import MetricValueSerializer
        return Response(MetricValueSerializer(qs, many=True).data)

    @action(detail=True, methods=["get"], url_path="metric-history")
    def metric_history(self, request, ip=None):
        """GET /api/v1/devices/<ip>/metric-history/?key=cpu_5s&hours=24"""
        from datetime import timedelta

        from django.utils import timezone

        device = self.get_object()
        key = request.query_params.get("key")
        hours = int(request.query_params.get("hours", 24))

        if not key:
            return Response({"error": "key is required"}, status=400)

        since = timezone.now() - timedelta(hours=hours)
        qs = (
            MetricValue.objects
            .filter(device=device, metric__key=key, ts__gte=since)
            .select_related("metric")
            .order_by("ts")
        )

        from apps.monitoring.serializers import MetricValueSerializer
        return Response(MetricValueSerializer(qs, many=True).data)

    @action(detail=True, methods=["get"], url_path="interfaces")
    def interfaces(self, request, ip=None):
        from apps.monitoring.models import Interface
        from apps.monitoring.serializers import InterfaceDetailSerializer

        device = self.get_object()
        qs = Interface.objects.filter(device=device).prefetch_related(
            "vlan_links__vlan"
        ).order_by("if_index")
        return Response(InterfaceDetailSerializer(qs, many=True).data)

    @action(detail=True, methods=["get"], url_path="vlans")
    def vlans(self, request, ip=None):
        from apps.monitoring.models import Vlan
        from apps.monitoring.serializers import VlanSerializer

        device = self.get_object()
        qs = Vlan.objects.filter(device=device).order_by("vlan_id")
        return Response(VlanSerializer(qs, many=True).data)


# ─── Live-статусы устройств ──────────────────────────────

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def device_statuses(request):
    """
    GET /api/v1/devices/statuses/
    Возвращает краткий статус всех устройств (для live-обновления).
    """
    from apps.devices.models import Device
    devices = Device.objects.all().only("ip", "name", "status", "is_reachable", "last_polled_at")
    data = []
    for d in devices:
        data.append({
            "ip": d.ip,
            "name": d.name,
            "status": d.status,
            "is_reachable": d.is_reachable,
            "last_polled_at": d.last_polled_at.isoformat() if d.last_polled_at else None,
        })
    return Response({"ok": True, "devices": data})

