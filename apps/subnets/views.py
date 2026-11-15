"""HTML-страницы подсетей."""
from django.shortcuts import render


def subnets_list(request):
    """Список подсетей."""
    from apps.subnets.models import Subnet
    from django.db.models import Count, Q

    # Оптимизированный запрос: считаем IP-адреса прямо в базе (без N+1)
    subnets = Subnet.objects.annotate(
        total_count=Count('ips'),
        alive_count=Count('ips', filter=Q(ips__is_alive=True)),
        monitored_count=Count('ips', filter=Q(ips__is_monitored=True)),
        reserved_count=Count('ips', filter=Q(ips__is_reserved=True)),
    ).order_by("cidr")

    # Сводка (берем из уже подготовленных данных)
    total = subnets.count()
    active = sum(1 for s in subnets if s.is_active)
    total_ips = sum(s.total_count for s in subnets)
    total_alive = sum(s.alive_count for s in subnets)

    return render(request, "core/subnets_list.html", {
        "subnets": subnets,
        "summary": {
            "total": total,
            "active": active,
            "total_ips": total_ips,
            "total_alive": total_alive,
        },
    })


def subnet_detail(request, subnet_id):
    """Детали подсети — карта IP + таблица."""
    from django.shortcuts import get_object_or_404
    from apps.subnets.models import Subnet

    subnet = get_object_or_404(Subnet, pk=subnet_id)
    return render(request, "core/subnet_detail.html", {
        "subnet": subnet,
    })
