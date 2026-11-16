import logging
"""REST API для приложения subnets."""

import logging

logger = logging.getLogger(__name__)

import ipaddress
from datetime import timedelta

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core import ops_client  # docker exec в sniffer — через cerber-ops
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


class SubnetViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
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



    @action(detail=True, methods=["get"], url_path="free-ips")
    def free_ips(self, request, pk=None):
        """Возвращает список свободных IP в подсети (первые 10)."""
        subnet = self.get_object()
        # Свободные IP: is_alive=False, is_reserved=False, и по ним нет мониторинга (или мониторинг выключен)
        free_ips = list(
            subnet.ips.filter(
                is_alive=False,
                is_reserved=False,
                is_monitored=False
            ).order_by("ip")[:10].values_list("ip", flat=True)
        )
        return Response({
            "ok": True,
            "subnet": subnet.cidr,
            "free_ips": free_ips,
            "count": len(free_ips)
        })


class IpAddressViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
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
        ip = self.request.query_params.get("ip")
        if ip:
            qs = qs.filter(ip=ip.strip())
        mac = self.request.query_params.get("mac")
        if mac:
            qs = qs.filter(mac__iexact=mac.strip())
        return qs.order_by("ip")

    @action(detail=True, methods=["post"], url_path="set-hostname")
    def set_hostname(self, request, pk=None):
        ip = self.get_object()
        hostname = (request.data.get("hostname") or "").strip()
        ip.hostname = hostname
        ip.save(update_fields=["hostname"])
        return Response({"ok": True, "hostname": hostname})

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



    @action(detail=False, methods=["post"], url_path="update-mac")
    def update_mac(self, request):
        """Обновляет MAC для IP (из внешнего скрипта)."""
        ip = request.data.get("ip")
        mac = request.data.get("mac")
        if not ip or not mac:
            return Response({"ok": False, "error": "ip and mac required"}, status=400)
        
        updated = IpAddress.objects.filter(ip=ip).update(mac=mac)
        return Response({"ok": True, "updated": updated})


    @action(detail=True, methods=["post"], url_path="update-station")
    def update_station(self, request, pk=None):
        """Обновляет станцию и подпись для IP."""
        ip = self.get_object()
        station_id = request.data.get("station")
        description = request.data.get("description", "")
        
        if station_id:
            from apps.stations.models import Station
            try:
                station = Station.objects.get(pk=station_id)
                ip.station = station
            except Station.DoesNotExist:
                return Response({"ok": False, "error": "station not found"}, status=404)
        else:
            ip.station = None
        
        ip.description = description
        ip.save(update_fields=["station", "description", "updated_at"])
        return Response({"ok": True, "station": ip.station_id, "description": ip.description})

class SubnetScanViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    """История сканирований подсетей."""
    queryset = SubnetScan.objects.select_related("subnet").all()
    serializer_class = SubnetScanSerializer

    def get_queryset(self):
        qs = super().get_queryset().order_by("-started_at")
        subnet_id = self.request.query_params.get("subnet")
        if subnet_id:
            qs = qs.filter(subnet_id=subnet_id)
        return qs[:200]


# ─── Пинг IP через fping ───
import subprocess
import json
import re
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from apps.subnets.models import IpAddress

@require_POST
def ping_ip(request):
    # Вход обеспечивает ApiLoginRequiredMiddleware; csrf_exempt убран — с ним вошедшего
    # пользователя можно было заставить пинговать адреса с чужого сайта.
    """Запускает fping для указанного IP и возвращает результат."""
    try:
        data = json.loads(request.body)
        ip_str = data.get("ip")
        if not ip_str:
            return JsonResponse({"ok": False, "error": "IP не указан"}, status=400)
    except Exception:
        return JsonResponse({"ok": False, "error": "Неверный JSON"}, status=400)

    ip_obj = IpAddress.objects.filter(ip=ip_str).first()
    if not ip_obj:
        return JsonResponse({"ok": False, "error": "IP не найден в базе"}, status=404)

    try:
        result = subprocess.run(
            ["fping", "-c", "4", "-t", "1000", "-q", ip_str],
            capture_output=True, text=True, timeout=10
        )
        output = result.stderr + result.stdout
        
        loss_match = re.search(r'\d+/\d+/(\d+)%', output)
        packet_loss = int(loss_match.group(1)) if loss_match else 100
        
        rtt_match = re.search(r'min/avg/max = [\d.]+/([\d.]+)/[\d.]+', output)
        avg_rtt = float(rtt_match.group(1)) if rtt_match else None
        
        is_alive = (packet_loss < 100)
        
        ip_obj.is_alive = is_alive
        ip_obj.packet_loss_pct = packet_loss
        if avg_rtt is not None:
            ip_obj.last_rtt_ms = avg_rtt
        ip_obj.last_ping_at = timezone.now()
        ip_obj.save(update_fields=["is_alive", "packet_loss_pct", "last_rtt_ms", "last_ping_at"])

        return JsonResponse({
            "ok": True,
            "is_alive": is_alive,
            "packet_loss": packet_loss,
            "avg_rtt": avg_rtt,
        })
    except subprocess.TimeoutExpired:
        return JsonResponse({"ok": False, "error": "Таймаут fping"}, status=504)
    except FileNotFoundError:
        return JsonResponse({"ok": False, "error": "fping не найден"}, status=500)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


def _capture_admin_only(view):
    """
    Захват трафика (tcpdump/pcap) — только с возможностью «Захват трафика» (traffic_capture):
    sniffer слушает все интерфейсы хоста, в пакетах — чужие данные и пароли.
    Ставится ВНЕШНИМ декоратором (поверх @api_view и для обычных view).
    """
    import functools
    from django.http import JsonResponse

    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return JsonResponse({"ok": False, "error": "authentication required"}, status=401)
        if not user.access.has_cap("traffic_capture"):
            return JsonResponse({"ok": False, "error": "Нет права «Захват трафика»"}, status=403)
        return view(request, *args, **kwargs)
    return wrapper


# ============================================================
# tcpdump — запуск захвата трафика из интерфейса подсети
# ============================================================

@_capture_admin_only
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def tcpdump_view(request):
    """
    Запуск tcpdump.

    Body (JSON):
        {
            "bpf": "host 8.8.8.8 and icmp",  # опционально
            "iface": "any",                   # one of: any, eth0, lo
            "duration": 10,                   # сек, 1..30
            "packets": 50                     # 1..500
        }
    """
    from .tcpdump import TcpdumpError, run_tcpdump

    bpf = (request.data.get("bpf") or "").strip()
    iface = (request.data.get("iface") or "any").strip()
    try:
        duration = int(request.data.get("duration") or 10)
        packets = int(request.data.get("packets") or 50)
    except (TypeError, ValueError):
        return Response(
            {"ok": False, "error": "duration/packets должны быть числами"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        result = run_tcpdump(bpf=bpf, iface=iface, duration=duration, packets=packets)
    except TcpdumpError as e:
        return Response(
            {"ok": False, "error": str(e)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as e:
        logger.exception("tcpdump failed")
        return Response(
            {"ok": False, "error": f"Внутренняя ошибка: {e}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return Response(result)

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def tcpdump_ifaces_view(request):
    """Интерфейсы хоста через sniffer, с IP."""
    import subprocess as _sp
    import re as _re
    try:
        out = ops_client.sniffer_run(["ip", "-o", "-4", "a"], timeout=5)
        result = []
        for line in out.stdout.splitlines():
            m = _re.search(r"^\d+:\s+([^:@]+).*?inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", line)
            if m:
                name = m.group(1).strip()
                if name.startswith("veth") or name.startswith("br-"):
                    continue
                result.append({"name": name, "ip": m.group(2)})
        if not result:
            result = [{"name": "any", "ip": None}]
        return Response({"ok": True, "ifaces": result})
    except Exception as e:
        return Response({"ok": False, "error": str(e)}, status=500)



@api_view(["GET"])
@permission_classes([IsAuthenticated])
def tcpdump_subnet_iface_view(request):
    """Интерфейс хоста, в котором лежит подсеть (через sniffer)."""
    cidr = (request.query_params.get("cidr") or "").strip()
    if not cidr:
        return Response({"ok": False, "error": "cidr is required"}, status=400)

    import subprocess as _sp
    import re as _re
    import ipaddress as _ip

    try:
        target = _ip.ip_network(cidr, strict=False)
    except ValueError:
        return Response({"ok": False, "error": "invalid cidr"}, status=400)

    try:
        out = ops_client.sniffer_run(["ip", "-o", "-4", "a"], timeout=5)
    except Exception as e:
        return Response({"ok": False, "error": str(e)}, status=500)

    for line in out.stdout.splitlines():
        m = _re.search(r"^\d+:\s+([^:@]+).*?inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", line)
        if not m:
            continue
        name, ip, mask = m.group(1).strip(), m.group(2), int(m.group(3))
        if name.startswith("veth") or name.startswith("br-"):
            continue
        try:
            net = _ip.ip_network(f"{ip}/{mask}", strict=False)
        except ValueError:
            continue
        if net == target:
            return Response({"ok": True, "iface": name, "ip": ip})

    return Response({"ok": False, "error": f"Интерфейс для {cidr} не найден"})

# ============================================================
# tcpdump → pcap + скачивание
# ============================================================

@_capture_admin_only
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def tcpdump_pcap_view(request):
    """Захват в .pcap (для Wireshark). Body: {bpf, iface, duration, packets}."""
    from .tcpdump_pcap import run_tcpdump_pcap
    from .tcpdump import TcpdumpError

    bpf = (request.data.get("bpf") or "").strip()
    iface = (request.data.get("iface") or "any").strip()
    try:
        duration = int(request.data.get("duration") or 10)
        packets = int(request.data.get("packets") or 50)
    except (TypeError, ValueError):
        return Response({"ok": False, "error": "duration/packets должны быть числами"},
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        result = run_tcpdump_pcap(bpf=bpf, iface=iface, duration=duration, packets=packets)
    except TcpdumpError as e:
        return Response({"ok": False, "error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.exception("tcpdump pcap failed")
        return Response({"ok": False, "error": f"Внутренняя ошибка: {e}"},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    if result.get("ok") and result.get("file_id"):
        result["download_url"] = f"/api/v1/subnets/tcpdump/download/{result['file_id']}/"
    return Response(result)


@_capture_admin_only
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def tcpdump_download_view(request, file_id: str):
    """Отдаёт .pcap-файл по file_id."""
    from .tcpdump_pcap import PCAP_DIR
    from django.http import FileResponse, Http404
    import re as _re

    if not _re.match(r"^[a-f0-9]{16}$", file_id):
        raise Http404("Некорректный file_id")
    path = PCAP_DIR / f"{file_id}.pcap"
    if not path.exists():
        raise Http404("Файл не найден (истёк TTL)")
    return FileResponse(
        open(path, "rb"),
        content_type="application/vnd.tcpdump.pcap",
        as_attachment=True,
        filename=f"capture_{file_id}.pcap",
    )

# ============================================================
# tcpdump — SSE-стрим в реальном времени
# ============================================================

# ============================================================
# tcpdump — SSE-стрим в реальном времени (обычный Django-view)
# ============================================================

@_capture_admin_only
def tcpdump_stream_view(request):
    """
    SSE-стрим пакетов. Без @api_view (иначе DRF вернёт 406 на text/event-stream).
    Проверяем аутентификацию вручную.
    """
    from django.http import StreamingHttpResponse, JsonResponse
    from .tcpdump_stream import stream_tcpdump

    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "error": "auth required"}, status=401)

    bpf = (request.GET.get("bpf") or "ip").strip()
    iface = (request.GET.get("iface") or "any").strip()
    try:
        duration = int(request.GET.get("duration") or 60)
    except (TypeError, ValueError):
        duration = 60

    gen = stream_tcpdump(bpf=bpf, iface=iface, duration=duration)
    response = StreamingHttpResponse(gen, content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response

# ============================================================
# tcpdump — polling (обходной путь для daphne + SSE)
# ============================================================

@_capture_admin_only
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def tcpdump_poll_view(request):
    """
    Захват трафика на короткий интервал (polling). Возвращает пакеты.
    Body: {bpf, iface, duration: 2, packets: 30}
    """
    from .tcpdump import run_tcpdump, TcpdumpError

    bpf = (request.data.get("bpf") or "ip").strip()
    iface = (request.data.get("iface") or "any").strip()
    try:
        duration = int(request.data.get("duration") or 2)
        packets = int(request.data.get("packets") or 30)
    except (TypeError, ValueError):
        return Response({"ok": False, "error": "bad params"}, status=400)

    try:
        result = run_tcpdump(bpf=bpf, iface=iface, duration=duration, packets=packets)
    except TcpdumpError as e:
        return Response({"ok": False, "error": str(e)}, status=400)
    except Exception as e:
        logger.exception("tcpdump poll failed")
        return Response({"ok": False, "error": str(e)}, status=500)

    return Response({
        "ok": result.get("ok"),
        "packets": result.get("packets", []),
        "captured": result.get("packets_captured", 0),
        "bpf": result.get("bpf"),
        "iface": result.get("iface"),
        "elapsed": result.get("elapsed"),
    })

# ============================================================
# Broadcast storm: данные для UI
# ============================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def subnet_traffic_view(request, subnet_id: int):
    """
    Возвращает:
      - samples: последние N (по умолчанию 24 часа / 60 точек)
      - events: последние storm-события
      - stats: агрегаты (avg/max bcast_pct, count storms)
    """
    from django.utils import timezone
    from datetime import timedelta
    from django.db.models import Avg, Max, Count, Q
    from .models import Subnet
    from .models_traffic import TrafficSample, BroadcastStormEvent

    try:
        subnet = Subnet.objects.get(pk=subnet_id)
    except Subnet.DoesNotExist:
        return Response({"ok": False, "error": "not found"}, status=404)

    hours = int(request.GET.get("hours") or 24)
    hours = max(1, min(hours, 168))  # до 7 дней
    since = timezone.now() - timedelta(hours=hours)

    samples_qs = TrafficSample.objects.filter(subnet=subnet, ts__gte=since).order_by("ts")
    # Downsample: не более 200 точек
    samples = list(samples_qs.values("ts", "bcast_pct", "mcast_pct", "total", "iface"))
    if len(samples) > 200:
        step = len(samples) // 200
        samples = samples[::step]

    events_qs = BroadcastStormEvent.objects.filter(
        subnet=subnet, started_at__gte=since,
    ).order_by("-started_at")[:20]

    events = [
        {
            "id": e.pk,
            "iface": e.iface,
            "severity": e.severity,
            "state": e.state,
            "bcast_pct_peak": e.bcast_pct_peak,
            "bcast_per_sec_peak": e.bcast_per_sec_peak,
            "bcast_pct_now": e.bcast_pct_now,
            "top_macs": e.top_macs[:5],
            "reason": e.reason,
            "started_at": e.started_at.isoformat(),
            "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
        }
        for e in events_qs
    ]

    agg = samples_qs.aggregate(
        avg_bcast=Avg("bcast_pct"),
        max_bcast=Max("bcast_pct"),
        avg_mcast=Avg("mcast_pct"),
        max_mcast=Max("mcast_pct"),
        total_samples=Count("id"),
    )

    active = BroadcastStormEvent.objects.filter(
        subnet=subnet, state=BroadcastStormEvent.STATE_FIRING,
    ).first()

    return Response({
        "ok": True,
        "subnet_id": subnet.pk,
        "subnet_cidr": subnet.cidr,
        "hours": hours,
        "samples": samples,
        "events": events,
        "stats": {
            "avg_bcast_pct": round(agg["avg_bcast"] or 0, 2),
            "max_bcast_pct": round(agg["max_bcast"] or 0, 2),
            "avg_mcast_pct": round(agg["avg_mcast"] or 0, 2),
            "max_mcast_pct": round(agg["max_mcast"] or 0, 2),
            "total_samples": agg["total_samples"] or 0,
            "storm_count": events_qs.count(),
        },
        "active_storm": {
            "id": active.pk,
            "severity": active.severity,
            "bcast_pct_now": active.bcast_pct_now,
            "started_at": active.started_at.isoformat(),
            "top_macs": active.top_macs[:3],
        } if active else None,
    })



# ============================================================
# Поиск IP по MAC (в базе и в ARP-таблице)
# ============================================================
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def resolve_mac_view(request):
    mac = (request.query_params.get("mac") or "").strip().lower()
    if not mac:
        return Response({"ok": False, "error": "mac is required"}, status=400)

    # 1. Ищем в базе
    ip_obj = IpAddress.objects.filter(mac__iexact=mac).first()
    if ip_obj:
        return Response({
            "ok": True,
            "ip": ip_obj.ip,
            "source": "db",
            "hostname": ip_obj.hostname or "",
            "is_alive": ip_obj.is_alive,
            "is_monitored": ip_obj.is_monitored,
            "last_rtt_ms": ip_obj.last_rtt_ms,
        })
    # 2. Ищем в ARP-таблице (через контейнер sniffer)
    try:
        import subprocess
        out = ops_client.sniffer_run(["ip", "neigh", "show"], timeout=5)
        for line in out.stdout.splitlines():
            if mac in line.lower():
                parts = line.split()
                if parts and parts[0].count('.') == 3:
                    return Response({
                        "ok": True,
                        "ip": parts[0],
                        "source": "arp",
                        "hostname": "",
                        "is_alive": True,
                        "is_monitored": False,
                        "last_rtt_ms": None,
                    })
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"ARP lookup failed: {e}")

    # 3. Если MAC не найден в ARP — пробуем вытащить Hostname из DHCP
    try:
        import subprocess
        # Берём интерфейс из запроса (фронтенд передаёт выбранный в панели)
        import re as _re
        from .tcpdump import TcpdumpError, _validate_iface
        if not _re.fullmatch(r"[0-9a-f]{2}([:-][0-9a-f]{2}){5}", mac):
            raise ValueError("bad mac")
        try:
            iface = _validate_iface((request.query_params.get("iface") or "enp4s0.50").strip())
        except TcpdumpError as e:
            raise ValueError(str(e))
        # Запускаем tcpdump на 3 секунды, ищем пакеты от этого MAC
        out = ops_client.sniffer_run(["timeout", "3s", "tcpdump", "-i", iface, "-c", "5", "-nn", "-v", f"ether host {mac}"], timeout=6)
        hostname = None
        for line in out.stdout.splitlines():
            if "Hostname" in line and '"' in line:
                # Парсим строку: Hostname (12), length 10: "PriemnayaU"
                parts = line.split('"')
                if len(parts) >= 2:
                    hostname = parts[1]
                    break
        if hostname:
            return Response({
                "ok": True,
                "ip": None,
                "source": "dhcp",
                "hostname": hostname,
                "is_alive": True,
                "is_monitored": False,
                "last_rtt_ms": None,
                "note": "IP ещё не получен (DHCP Discover). Устройство активно."
            })
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"DHCP lookup failed: {e}")

    return Response({"ok": False, "error": "MAC не найден ни в базе, ни в ARP, ни в DHCP"})


# ============================================================
# Получение PC Name и MAC по IP (через nmap SMB)
# ============================================================
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def resolve_ip_info_view(request):
    ip = (request.query_params.get("ip") or "").strip()
    if not ip:
        return Response({"ok": False, "error": "ip is required"}, status=400)

    # 1. Ищем в базе
    ip_obj = IpAddress.objects.filter(ip=ip).first()
    if ip_obj and ip_obj.hostname:
        return Response({
            "ok": True,
            "ip": ip,
            "mac": ip_obj.mac or "",
            "hostname": ip_obj.hostname,
            "vendor": "",
            "source": "db",
        })

    # 1.5. Если в IpAddress нет hostname — ищем в Device
    from apps.devices.models import Device
    device = Device.objects.filter(ip=ip).first()
    if device:
        return Response({
            "ok": True,
            "ip": ip,
            "mac": (ip_obj.mac if ip_obj else ""),
            "hostname": device.name,
            "vendor": device.description[:50] if device.description else "",
            "source": "device",
        })

    # 2. Если hostname нет — пробуем nmap (SMB, NetBIOS) и SNMP
    hostname = None
    mac = None
    vendor = None
    source = "nmap"

    try:
        import subprocess
        # 2.1. SMB (Windows)
        out = ops_client.sniffer_run(["nmap", "-p", "445", "--script", "smb-os-discovery", ip], timeout=15)
        for line in out.stdout.splitlines():
            if "Computer name:" in line:
                hostname = line.split("Computer name:")[1].strip()
            if "MAC Address:" in line:
                parts = line.split("MAC Address:")
                if len(parts) > 1:
                    rest = parts[1].strip()
                    mac = rest.split("(")[0].strip()
                    if "(" in rest:
                        vendor = rest.split("(")[1].split(")")[0]
            if hostname:
                break

        # 2.2. NetBIOS (если SMB не дал имени)
        if not hostname:
            out2 = ops_client.sniffer_run(["nmap", "-p", "137", "--script", "nbstat.nse", ip], timeout=15)
            for line in out2.stdout.splitlines():
                if "NetBIOS name:" in line:
                    hostname = line.split("NetBIOS name:")[1].strip()
                    source = "nbstat"
                    break
                if "MAC Address:" in line and not mac:
                    parts = line.split("MAC Address:")
                    if len(parts) > 1:
                        rest = parts[1].strip()
                        mac = rest.split("(")[0].strip()
                        if "(" in rest:
                            vendor = rest.split("(")[1].split(")")[0]

        # 2.3. SNMP (если SMB/NetBIOS не дали имени)
        if not hostname:
            try:
                out3 = ops_client.sniffer_run(["snmpget", "-v2c", "-c", "public", "-Oqv", ip, "1.3.6.1.2.1.1.5.0"], timeout=5)
                if out3.stdout.strip():
                    hostname = out3.stdout.strip().strip('"')
                    source = "snmp"
            except Exception:
                pass

        if hostname or mac:
            # Сохраняем в базу
            if ip_obj:
                if hostname:
                    ip_obj.hostname = hostname
                if mac:
                    ip_obj.mac = mac
                ip_obj.save(update_fields=["hostname", "mac"])
            return Response({
                "ok": True,
                "ip": ip,
                "mac": mac or (ip_obj.mac if ip_obj else ""),
                "hostname": hostname or (ip_obj.hostname if ip_obj else ""),
                "vendor": vendor or "",
                "source": source,
            })
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"nmap/SNMP failed: {e}")

    return Response({"ok": False, "error": "Не удалось получить PC Name"})

# ============================================================
# Массовое прикрепление активных IP подсети к станции
# ============================================================
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def bulk_attach_station_view(request):
    subnet_id = request.data.get("subnet_id")
    station_id = request.data.get("station_id")
    
    if not subnet_id:
        return Response({"ok": False, "error": "subnet_id is required"}, status=400)
    if not station_id:
        return Response({"ok": False, "error": "station_id is required"}, status=400)
    
    try:
        from apps.stations.models import Station
        station = Station.objects.get(pk=station_id)
    except Station.DoesNotExist:
        return Response({"ok": False, "error": "station not found"}, status=404)
    
    # Прикрепляем все активные IP этой подсети к станции
    from apps.subnets.models import IpAddress
    updated = IpAddress.objects.filter(
        subnet_id=subnet_id,
        is_alive=True
    ).update(station=station)
    
    return Response({
        "ok": True,
        "updated": updated,
        "station": station.name,
        "station_id": station.id,
    })
