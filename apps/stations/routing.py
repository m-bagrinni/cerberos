"""
Поиск кратчайшего пути между станциями по графу оптических линий.
Точки from/to привязываются к ближайшим точкам на ЛИНИЯХ (а не станциях).
"""
import networkx as nx
from math import radians, cos, sin, asin, sqrt


def haversine(lat1, lon1, lat2, lon2):
    """Расстояние между двумя точками (км)."""
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))


def point_to_segment_dist(p, a, b):
    """Расстояние от точки p до отрезка a-b (км). Возвращает (расстояние, проекция)."""
    x, y = p
    x1, y1 = a
    x2, y2 = b
    dx, dy = x2 - x1, y2 - y1
    denom = dx*dx + dy*dy
    if denom == 0:
        return haversine(x, y, x1, y1), (x1, y1)
    t = max(0, min(1, ((x - x1)*dx + (y - y1)*dy) / denom))
    proj = (x1 + t*dx, y1 + t*dy)
    dist = haversine(x, y, proj[0], proj[1])
    return dist, proj


def closest_point_on_line(point, line_coords):
    """Ближайшая точка на линии (path_geojson) к заданной точке. Возвращает (index, proj, dist)."""
    best_dist = float('inf')
    best_idx = 0
    best_proj = None

    for i in range(1, len(line_coords)):
        a = line_coords[i-1]
        b = line_coords[i]
        dist, proj = point_to_segment_dist(point, a, b)
        if dist < best_dist:
            best_dist = dist
            best_idx = i
            best_proj = proj

    return best_idx, best_proj, best_dist


def find_nearest_link(point):
    """Находит ближайшую линию к точке. Возвращает (link, dist, proj)."""
    from apps.stations.models import OpticalLink
    best_link = None
    best_dist = float('inf')
    best_proj = None

    for link in OpticalLink.objects.filter(is_active=True):
        if not link.path_geojson:
            continue
        idx, proj, dist = closest_point_on_line(point, link.path_geojson)
        if dist < best_dist:
            best_dist = dist
            best_link = link
            best_proj = proj

    return best_link, best_dist, best_proj


def build_graph():
    """Строит граф: узлы — станции, рёбра — линии (с весами = длина path_geojson)."""
    from apps.stations.models import OpticalLink

    G = nx.Graph()

    for link in OpticalLink.objects.filter(is_active=True).select_related("station_a", "station_b"):
        a, b = link.station_a, link.station_b
        if not (a.latitude and a.longitude and b.latitude and b.longitude):
            continue

        if link.path_geojson:
            dist = 0
            pts = link.path_geojson
            for i in range(1, len(pts)):
                dist += haversine(pts[i-1][0], pts[i-1][1], pts[i][0], pts[i][1])
        else:
            dist = haversine(a.latitude, a.longitude, b.latitude, b.longitude)

        G.add_edge(a.slug, b.slug, weight=dist, link_id=link.id)

    return G


def find_shortest_path(from_lat, from_lng, to_lat, to_lng):
    """
    Находит кратчайший путь между двумя точками.
    Точки from/to привязываются к ближайшим точкам на ЛИНИЯХ.
    """
    from apps.stations.models import Station, OpticalLink

    G = build_graph()

    # 1. Находим ближайшие линии к from и to
    from_link, from_dist, from_proj = find_nearest_link((from_lat, from_lng))
    to_link, to_dist, to_proj = find_nearest_link((to_lat, to_lng))

    if not from_link or not to_link:
        return None

    # 2. Определяем, к каким станциям привязаны линии
    from_station = from_link.station_a if from_link.station_a.slug == from_link.station_a.slug else from_link.station_b
    # Нам нужно определить, какая станция ближе к from_proj (по линии)
    # Проще: используем from_link.station_a (или b — в зависимости от направления)
    # Но для графа нам нужны станции, между которыми идёт линия.
    # Используем ближайшую станцию к from_proj (по координатам)
    stations = list(Station.objects.exclude(latitude=None).exclude(longitude=None))
    from_station = min(stations, key=lambda s: haversine(from_proj[0], from_proj[1], s.latitude, s.longitude))
    to_station = min(stations, key=lambda s: haversine(to_proj[0], to_proj[1], s.latitude, s.longitude))

    # 3. Если from_link и to_link — одна и та же линия, и from/to на ней —
    #    просто считаем по сегментам между from_proj и to_proj
    if from_link.id == to_link.id and from_link.path_geojson:
        pts = from_link.path_geojson
        # Определяем индексы
        idx1, proj1, _ = closest_point_on_line((from_lat, from_lng), pts)
        idx2, proj2, _ = closest_point_on_line((to_lat, to_lng), pts)
        seg_start = min(idx1, idx2)
        seg_end = max(idx1, idx2)
        path = [proj1] + pts[seg_start:seg_end] + [proj2]
        if idx1 > idx2:
            path.reverse()
        # Расстояние
        total = 0
        for i in range(1, len(path)):
            total += haversine(path[i-1][0], path[i-1][1], path[i][0], path[i][1])
        return {
            "path": path,
            "distance_km": round(total, 2),
            "from_station": from_station.slug,
            "to_station": to_station.slug,
        }

    # 4. Иначе — строим маршрут через граф (от from_station до to_station)
    try:
        path = nx.shortest_path(G, from_station.slug, to_station.slug, weight="weight")
    except nx.NetworkXNoPath:
        return None

    # 5. Собираем path_geojson вдоль пути
    full_path = []
    total_dist = 0

    for i in range(len(path) - 1):
        a_slug = path[i]
        b_slug = path[i+1]
        edge_data = G.get_edge_data(a_slug, b_slug)
        link = OpticalLink.objects.get(pk=edge_data["link_id"])

        if link.path_geojson:
            pts = link.path_geojson
            if link.station_a.slug != a_slug:
                pts = list(reversed(pts))
        else:
            a = link.station_a
            b = link.station_b
            pts = [[a.latitude, a.longitude], [b.latitude, b.longitude]]

        # Если это первая линия — обрезаем до from_proj (только если близко)
        if i == 0:
            idx, proj, dist = closest_point_on_line((from_lat, from_lng), pts)
            if dist < 0.5:
                pts = [proj] + pts[idx:]

        # Если это последняя линия — обрезаем до to_proj (только если близко)
        if i == len(path) - 2:
            idx, proj, dist = closest_point_on_line((to_lat, to_lng), pts)
            if dist < 0.5:
                pts = pts[:idx] + [proj]

        if full_path:
            pts = pts[1:]
        full_path.extend(pts)
        total_dist += edge_data["weight"]

    # Пересчёт
    if full_path:
        total_dist = 0
        for i in range(1, len(full_path)):
            total_dist += haversine(full_path[i-1][0], full_path[i-1][1], full_path[i][0], full_path[i][1])

    return {
        "path": full_path,
        "distance_km": round(total_dist, 2),
        "from_station": from_station.slug,
        "to_station": to_station.slug,
    }
