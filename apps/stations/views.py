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

    def get_serializer_context(self):
        """Передаём в сериализатор множество достижимых slug'ов (BFS)."""
        context = super().get_serializer_context()
        from .chain import compute_reachable
        context["reachable"] = compute_reachable()
        return context

    @action(detail=False, methods=["get"], url_path="traffic-summary")
    def traffic_summary(self, request):
        """
        Суммарный трафик сети по минутам — для графика на /local/stations/.

        Раньше браузер собирал его сам: interfaces/ на каждое устройство
        станций + traffic-history/ на каждый поднятый физический порт
        (~130 запросов, ~20 с работы web на каждое открытие страницы).
        Здесь — один SQL с тем же фильтром выбросов (> 100 Мбит/с → 0).
        ?hours=1..168 (по умолчанию 24)
        """
        from datetime import timedelta

        from django.db.models import Case, IntegerField, Sum, Value, When
        from django.db.models.functions import Coalesce, TruncMinute
        from django.utils import timezone

        from apps.monitoring.models import Interface, InterfaceSample

        try:
            hours = max(1, min(int(request.query_params.get("hours", 24)), 168))
        except (TypeError, ValueError):
            hours = 24

        cap = 100_000_000
        iface_ids = (
            Interface.objects
            .filter(
                device__station_links__isnull=False,
                oper_status=1,
                if_type__in=Interface.PHYSICAL_IF_TYPES,
                if_index__lt=150,
            )
            .values("id")
        )

        def capped(field):
            return Sum(Case(
                When(**{f"{field}__gt": cap}, then=Value(0)),
                default=Coalesce(field, Value(0)),
                output_field=IntegerField(),
            ))

        rows = (
            InterfaceSample.objects
            .filter(interface_id__in=iface_ids,
                    ts__gte=timezone.now() - timedelta(hours=hours))
            .annotate(minute=TruncMinute("ts"))
            .values("minute")
            .annotate(in_bps=capped("in_bps"), out_bps=capped("out_bps"))
            .order_by("minute")
        )
        return Response([
            {"ts": r["minute"].isoformat(), "in_bps": r["in_bps"], "out_bps": r["out_bps"]}
            for r in rows
        ])

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



    @action(detail=True, methods=["get"], url_path="ips")
    def ips(self, request, slug=None):
        """Возвращает IP, привязанные к станции."""
        from apps.subnets.serializers import IpAddressSerializer
        station = self.get_object()
        ips = station.ip_addresses.all().select_related('subnet')
        data = IpAddressSerializer(ips, many=True).data
        return Response({"ok": True, "count": len(data), "ips": data})

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

        return Response({"ok": True, "status": link.link_status})

    @action(detail=False, methods=["post"], url_path="poll")
    def poll(self, request):
        """Глобальный опрос всех линий (запуск poll_optical_links_fast)."""
        from apps.monitoring.tasks import poll_optical_links_fast
        try:
            poll_optical_links_fast.delay()
            return Response({"ok": True, "message": "Опрос запущен"})
        except Exception as e:
            return Response({"ok": False, "error": str(e)}, status=500)

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

    @action(detail=False, methods=["post"], url_path="poll-all-now")
    def poll_all_now(self, request):
        """Запускает опрос всех оптических линий (через Celery)."""
        from apps.monitoring.tasks import poll_optical_links
        from apps.stations.models import OpticalLink
        
        # Собираем названия линий (station_a — station_b)
        links = OpticalLink.objects.select_related('station_a', 'station_b').all()
        link_names = []
        for link in links:
            name = f"{link.station_a.name} — {link.station_b.name}"
            link_names.append(name)
        
        result = poll_optical_links.delay()
        return Response({
            "ok": True,
            "task_id": result.id,
            "links": link_names,
            "count": len(link_names)
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


# ─── Маршрут по графу линий (для линейки) ─────────────────────

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def route_between_points(request):
    """
    POST /api/v1/route/
    Body: {"from": [lat, lng], "to": [lat, lng]}
    Returns: {"path": [[lat, lng], ...], "distance_km": X, ...}
    """
    from .routing import find_shortest_path

    try:
        data = request.data
        from_lat, from_lng = data["from"]
        to_lat, to_lng = data["to"]
    except (KeyError, ValueError, TypeError):
        return Response({"error": "Invalid 'from' or 'to' (ожидается [lat, lng])"}, status=400)

    result = find_shortest_path(from_lat, from_lng, to_lat, to_lng)
    if not result:
        return Response({"error": "Путь не найден"}, status=404)

    return Response(result)



# ── API: запуск опроса оптических линков ────────────────────
from rest_framework.decorators import api_view


@api_view(['POST'])
def poll_links_api(request):
    """Запуск poll_optical_links_fast через Celery."""
    from apps.monitoring.tasks import poll_optical_links_fast
    try:
        poll_optical_links_fast.delay()
        return Response({'ok': True, 'message': 'Опрос запущен'})
    except Exception as e:
        return Response({'ok': False, 'error': str(e)}, status=500)


# ============================================================
# HTML-страница «Станции» (/local/stations/)
# ============================================================

from django.shortcuts import render  # noqa: E402


def stations_list(request):
    """
    Вкладка «Станции» — список станций с оборудованием (NE + Device).
    ?partial=1 → только партиал (для AJAX-обновления без F5).

    Дополнительно: BFS по оптическим линкам от корня (bishkek).
    Все станции, недостижимые от корня — crit.
    """
    from collections import deque
    from apps.stations.models import OpticalLink, Station

    stations = (
        Station.objects
        .prefetch_related("nes__ne__gateway", "devices__device__vendor")
        .order_by("sort_order", "name")
    )

    links = (
        OpticalLink.objects
        .select_related("station_a", "station_b")
        .filter(is_active=True)
    )

    # ── BFS: граф достижимости от корня ────────────────────
    root = Station.objects.filter(slug="bishkek").first()

    # Рёбра — только "живые" линки (не crit/unknown)
    graph = {}
    for l in links:
        if l.link_status in ("crit", "unknown"):
            continue
        a, b = l.station_a_id, l.station_b_id
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)

    reachable = set()
    if root:
        queue = deque([root.id])
        while queue:
            node = queue.popleft()
            if node in reachable:
                continue
            reachable.add(node)
            for n in graph.get(node, set()):
                if n not in reachable:
                    queue.append(n)
    # ──────────────────────────────────────────────────────

    # Группируем связи по станции
    links_by_station = {}
    for l in links:
        links_by_station.setdefault(l.station_a_id, []).append(l)
        links_by_station.setdefault(l.station_b_id, []).append(l)

    cards = []
    for st in stations:
        st_links = links_by_station.get(st.id, [])

        nes = list(st.nes.all())
        devices = list(st.devices.all())

        # Агрегат статуса (учитываем NE, devices, optical links)
        statuses = [sn.ne.status for sn in nes] + [sd.device.status for sd in devices]
        link_statuses = [l.link_status for l in st_links]

        if "unreachable" in statuses or "crit" in link_statuses:
            agg_status = "crit"
        elif "warn" in link_statuses or "degraded" in link_statuses:
            agg_status = "warn"
        elif not statuses and not link_statuses:
            agg_status = "unknown"
        elif statuses and all(s == "ok" for s in statuses):
            agg_status = "ok"
        elif any(s == "ok" for s in statuses):
            agg_status = "warn"
        else:
            agg_status = "unknown"

        # ── BFS override: если нет связи от корня — crit ──
        if root and st.id not in reachable:
            agg_status = "crit"

        cards.append({
            "station": st,
            "links": st_links,
            "links_count": len(st_links),
            "ne_count": len(nes),
            "device_count": len(devices),
            "agg_status": agg_status,
        })

    if request.GET.get("partial"):
        return render(request, "core/_stations_list_partial.html", {
            "cards": cards,
        })

    return render(request, "core/stations_list.html", {
        "cards": cards,
        "total": len(cards),
    })
