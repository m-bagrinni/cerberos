from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.devices.models import Device, NeDevice
from apps.monitoring.models import MetricValue

from .models import OpticalLink, Station
from .serializers import (
    OpticalLinkSerializer,
    PortSerializer,
    StationListSerializer,
    StationSerializer,
)


class StationViewSet(viewsets.ModelViewSet):
    queryset = Station.objects.all().order_by("sort_order", "name")
    lookup_field = "slug"

    def get_serializer_class(self):
        if self.action == "list" and self.request.query_params.get("light") == "1":
            return StationListSerializer
        return StationSerializer

    @action(detail=True, methods=["get"], url_path="ports")
    def ports(self, request, slug=None):
        """
        Все порты станции: SFP+LAN у каждого NE, if_index у каждого Device.
        Каждый порт знает, занят ли он линией (is_used).
        """
        station = self.get_object()
        ports = []

        # Занятые порты — соберём set (kind, owner_id, port_type, port_num)
        used = set()
        for link in OpticalLink.objects.filter(station_a=station):
            if link.ne_a_id:
                used.add(("ne", link.ne_a_id, link.port_a_type, link.port_a_num))
            if link.device_a_id:
                used.add(("device", link.device_a_id, link.port_a_type, link.port_a_num))
        for link in OpticalLink.objects.filter(station_b=station):
            if link.ne_b_id:
                used.add(("ne", link.ne_b_id, link.port_b_type, link.port_b_num))
            if link.device_b_id:
                used.add(("device", link.device_b_id, link.port_b_type, link.port_b_num))

        # ── NE (Olencom) — порты SFP 1..3, LAN 1..4 ──
        for sn in station.nes.select_related("ne"):
            ne = sn.ne
            owner_label = f"{station.name} · {ne.display_name}"

            for pnum in (1, 2, 3):
                # берём последнее значение метрик по этому порту
                rx = _last_metric(ne, "sfp_rx_dbm", str(pnum))
                temp = _last_metric(ne, "sfp_temp", str(pnum))
                link = _last_metric(ne, "sfp_link", str(pnum))
                link_up = None if link is None else bool(link >= 0.5)

                # если DDM пусто и link=None — порт, скорее всего, пустой
                present = rx is not None or link is not None
                ports.append(PortSerializer(dict(
                    kind="ne",
                    owner_id=ne.id,
                    owner_label=owner_label,
                    port_type="sfp",
                    port_num=pnum,
                    port_label=f"SFP{pnum}",
                    link_up=link_up if present else None,
                    rx_dbm=rx,
                    temp_c=temp,
                    is_used=("ne", ne.id, "sfp", pnum) in used,
                )).data)

            for pnum in (1, 2, 3, 4):
                rx_bytes = _last_metric(ne, "eth_rx_bytes", str(pnum))
                link_up = None if rx_bytes is None else (rx_bytes > 1024)
                ports.append(PortSerializer(dict(
                    kind="ne",
                    owner_id=ne.id,
                    owner_label=owner_label,
                    port_type="lan",
                    port_num=pnum,
                    port_label=f"LAN{pnum}",
                    link_up=link_up,
                    rx_dbm=None,
                    temp_c=None,
                    is_used=("ne", ne.id, "lan", pnum) in used,
                )).data)

        # ── Device (D-Link/Eltex) — if_index из Interface ──
        from apps.monitoring.models import Interface
        for sd in station.devices.select_related("device"):
            dev = sd.device
            owner_label = f"{station.name} · {dev.name}"
            ifaces = Interface.objects.filter(device=dev).order_by("if_index")[:64]
            for iface in ifaces:
                if not iface.is_physical:
                    continue
                ports.append(PortSerializer(dict(
                    kind="device",
                    owner_id=dev.id,
                    owner_label=owner_label,
                    port_type="if",
                    port_num=iface.if_index,
                    port_label=iface.name or f"if{iface.if_index}",
                    link_up=(iface.oper_status == 1) if iface.oper_status is not None else None,
                    rx_dbm=None,
                    temp_c=None,
                    is_used=("device", dev.id, "if", iface.if_index) in used,
                )).data)

        return Response(ports)


    @action(detail=True, methods=["post"], url_path="attach-device")
    def attach_device(self, request, slug=None):
        """Привязать Device к станции. body: {device_id, role?}"""
        station = self.get_object()
        device_id = request.data.get("device_id")
        role = request.data.get("role", "")
        if not device_id:
            return Response({"error": "device_id обязателен"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            device = Device.objects.get(pk=device_id)
        except Device.DoesNotExist:
            return Response({"error": "Device не найден"},
                            status=status.HTTP_404_NOT_FOUND)

        from .models import StationDevice
        sd, created = StationDevice.objects.get_or_create(
            station=station, device=device,
            defaults={"role": role},
        )
        if not created and role:
            sd.role = role
            sd.save(update_fields=["role"])
        return Response({"ok": True, "created": created,
                         "station": station.slug, "device_id": device.id})

    @action(detail=True, methods=["delete"], url_path=r"detach-device/(?P<device_id>[0-9]+)")
    def detach_device(self, request, slug=None, device_id=None):
        """Отвязать Device от станции."""
        station = self.get_object()
        from .models import StationDevice
        deleted, _ = StationDevice.objects.filter(
            station=station, device_id=device_id
        ).delete()
        if not deleted:
            return Response({"error": "Привязка не найдена"},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class OpticalLinkViewSet(viewsets.ModelViewSet):
    queryset = OpticalLink.objects.select_related(
        "station_a", "station_b", "ne_a", "ne_b", "device_a", "device_b",
    ).order_by("label", "pk")
    serializer_class = OpticalLinkSerializer
    filterset_fields = ("link_status", "is_active", "station_a", "station_b")
    search_fields = ("label", "description")

    @action(detail=True, methods=["post"], url_path="poll-now")
    def poll_now(self, request, pk=None):
        """
        Точечный опрос линии.
        Пока — заглушка: пересчитывает link_status из текущего кэша DDM.
        Реальный SNMP-опрос будет на шаге B (Celery poll_optical_links).
        """
        link = self.get_object()

        # Заглушка: считаем статус из сохранённых rx_*_dbm / link_*_up
        link.link_status = link.recalc_status()
        link.save(update_fields=["link_status", "updated_at"])

        return Response({
            "id": link.id,
            "label": link.label,
            "link_status": link.link_status,
            "rx_a_dbm": link.rx_a_dbm,
            "rx_b_dbm": link.rx_b_dbm,
            "link_a_up": link.link_a_up,
            "link_b_up": link.link_b_up,
            "end_a_label": link.end_a_label,
            "end_b_label": link.end_b_label,
            "updated_at": link.updated_at.isoformat() if link.updated_at else None,
        })


# ── helpers ────────────────────────────────────────────────
def _last_metric(ne: NeDevice, key: str, instance: str):
    """Последнее значение MetricValue для NE по ключу и instance."""
    mv = (
        MetricValue.objects
        .filter(ne_device=ne, metric__key=key, instance=instance)
        .order_by("-ts")
        .values_list("value", flat=True)
        .first()
    )
    return mv
