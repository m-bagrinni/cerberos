"""REST API для приложения subnets."""

import ipaddress
from datetime import timedelta

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import IpAddress, PingSample, Subnet, SubnetScan
from .scanner import ping_one
from .serializers import (
    IpAddressSerializer,
    IpGridSerializer,
    PingSampleSerializer,
    SubnetListSerializer,
    SubnetScanSerializer,
)
from .tasks import scan_subnet_task


class NoAuth(BaseAuthentication):
    """Пустая аутентификация — отключает SessionAuth и CSRF-проверку для API."""
    def authenticate(self, request):
        return None


class SubnetViewSet(viewsets.ModelViewSet):
    authentication_classes = [NoAuth]
    permission_classes = [AllowAny]
    """CRUD над подсетями + действия scan-now, populate-ips, ip-grid, stats."""

    queryset = Subnet.objects.all().order_by("cidr")
    serializer_class = SubnetListSerializer

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        data = self.get_serializer(obj).data
        scans = obj.scans.all()[:10]
        data["recent_scans"] = SubnetScanSerializer(scans, many=True).data
        return Response(data)

    @action(detail=True, methods=["post"], url_path="scan-now")
    def scan_now(self, request, pk=None):
        subnet = self.get_object()
        task = scan_subnet_task.delay(subnet.pk)
        return Response({"ok": True, "task_id": task.id, "subnet": subnet.cidr})

    @action(detail=True, methods=["get"], url_path="ip-grid")
    def ip_grid(self, request, pk=None):
        subnet = self.get_object()
        ips = list(subnet.ips.all().order_by("ip"))
        n = len(ips)
        if n == 0:
            return Response({"cols": 0, "rows": 0, "ips": []})
        cols = int(n ** 0.5)
        while cols * cols < n:
            cols += 1
        rows = (n + cols - 1) // cols
        return Response({
            "subnet_id": subnet.pk,
            "cidr": subnet.cidr,
            "cols": cols,
            "rows": rows,
            "ips": IpGridSerializer(ips, many=True).data,
        })

    @action(detail=True, methods=["get"], url_path="stats")
    def stats(self, request, pk=None):
        subnet = self.get_object()
        return Response(subnet.stats())

    @action(detail=True, methods=["post"], url_path="populate-ips")
    def populate_ips(self, request, pk=None):
        subnet = self.get_object()
        try:
            net = ipaddress.ip_network(subnet.cidr, strict=False)
        except ValueError as e:
            return Response({"ok": False, "error": str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        hosts = list(net.hosts()) or [net.network_address]
        existing = set(subnet.ips.values_list("ip", flat=True))
        bulk = [
            IpAddress(subnet=subnet, ip=str(h), state=IpAddress.STATE_UNKNOWN)
            for h in hosts if str(h) not in existing
        ]
        if bulk:
            IpAddress.objects.bulk_create(bulk, batch_size=500)
        return Response({"ok": True, "created": len(bulk)})

    @action(detail=False, methods=["post"], url_path="scan-all-now")
    def scan_all_now(self, request):
        qs = Subnet.objects.filter(is_active=True)
        ids = [scan_subnet_task.delay(s.pk).id for s in qs]
        return Response({"ok": True, "launched": len(ids)})


class IpAddressViewSet(viewsets.ReadOnlyModelViewSet):
    authentication_classes = [NoAuth]
    permission_classes = [AllowAny]
    """Просмотр IpAddress + toggle-monitor + ping-now + ping-history."""

    queryset = IpAddress.objects.select_related("subnet").all()
    serializer_class = IpAddressSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        subnet_id = self.request.query_params.get("subnet")
        if subnet_id:
            qs = qs.filter(subnet_id=subnet_id)
        state = self.request.query_params.get("state")
        if state:
            qs = qs.filter(state=state)
        return qs.order_by("ip")

    @action(detail=True, methods=["post"], url_path="toggle-monitor")
    def toggle_monitor(self, request, pk=None):
        ip = self.get_object()
        ip.is_monitored = not ip.is_monitored
        ip.save(update_fields=["is_monitored", "updated_at"])
        return Response({
            "ok": True, "id": ip.pk, "ip": ip.ip,
            "is_monitored": ip.is_monitored,
        })

    @action(detail=True, methods=["post"], url_path="set-reserved")
    def set_reserved(self, request, pk=None):
        ip = self.get_object()
        ip.is_reserved = not ip.is_reserved
        ip.state = (IpAddress.STATE_RESERVED if ip.is_reserved
                    else IpAddress.STATE_UNKNOWN)
        ip.save(update_fields=["is_reserved", "state", "updated_at"])
        return Response({"ok": True, "is_reserved": ip.is_reserved, "state": ip.state})

    @action(detail=True, methods=["post"], url_path="ping-now")
    def ping_now(self, request, pk=None):
        ip = self.get_object()
        result = ping_one(ip.ip, retries=2)
        now = timezone.now()
        ip.is_alive = result.alive
        ip.last_ping_at = now
        ip.last_rtt_ms = result.rtt_ms
        ip.packet_loss_pct = result.packet_loss_pct
        if result.alive:
            ip.last_seen_at = now
            if ip.state == IpAddress.STATE_UNKNOWN:
                ip.state = IpAddress.STATE_USED
        ip.save()
        return Response(IpAddressSerializer(ip).data)

    @action(detail=True, methods=["get"], url_path="ping-history")
    def ping_history(self, request, pk=None):
        ip = self.get_object()
        try:
            hours = int(request.query_params.get("hours", 24))
            hours = max(1, min(hours, 168))
        except ValueError:
            hours = 24
        cutoff = timezone.now() - timedelta(hours=hours)
        samples = ip.pings.filter(ts__gte=cutoff).order_by("ts")
        return Response({
            "ip": ip.ip,
            "hours": hours,
            "count": samples.count(),
            "samples": PingSampleSerializer(samples, many=True).data,
        })


class SubnetScanViewSet(viewsets.ReadOnlyModelViewSet):
    authentication_classes = [NoAuth]
    permission_classes = [AllowAny]
    """История сканирований подсетей."""
    queryset = SubnetScan.objects.select_related("subnet").all()
    serializer_class = SubnetScanSerializer

    def get_queryset(self):
        qs = super().get_queryset().order_by("-started_at")
        subnet_id = self.request.query_params.get("subnet")
        if subnet_id:
            qs = qs.filter(subnet_id=subnet_id)
        return qs[:200]
