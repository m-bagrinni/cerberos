"""BFS по оптическим линкам — достижимость от корня."""
from collections import deque


def compute_reachable(root_slug="laz"):
    """Множество slug'ов станций, достижимых от корня (через "живые" линки)."""
    from apps.stations.models import OpticalLink, Station

    root = Station.objects.filter(slug=root_slug).first()
    if not root:
        return set()

    # Граф (только не crit/unknown)
    graph = {}
    links = OpticalLink.objects.filter(is_active=True).values_list(
        "station_a_id", "station_b_id", "link_status",
    )
    for a, b, status in links:
        if status in ("crit", "unknown"):
            continue
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)

    # BFS
    reachable_ids = set()
    queue = deque([root.id])
    while queue:
        node = queue.popleft()
        if node in reachable_ids:
            continue
        reachable_ids.add(node)
        for n in graph.get(node, set()):
            if n not in reachable_ids:
                queue.append(n)

    return set(
        Station.objects.filter(id__in=reachable_ids).values_list("slug", flat=True)
    )
