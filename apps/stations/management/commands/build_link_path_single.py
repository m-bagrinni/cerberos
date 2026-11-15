import json
import os
from math import radians, cos, sin, asin, sqrt

import networkx as nx
from django.core.management.base import BaseCommand
from apps.stations.models import OpticalLink

RAILWAY_GEOJSON = "/app/static/geojson/kyrgyzstan_railways.geojson"

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

class Command(BaseCommand):
    help = "Строит path_geojson для ОДНОЙ линии (по ж/д)"

    def add_arguments(self, parser):
        parser.add_argument("--link-id", type=int, required=True, help="ID линии")
        parser.add_argument("--max-snap", type=int, default=10000, help="Макс. расстояние до ж/д (м)")

    def handle(self, *args, **opts):
        link_id = opts["link_id"]
        max_snap = opts["max_snap"]

        if not os.path.exists(RAILWAY_GEOJSON):
            self.stderr.write(f"❌ Нет файла {RAILWAY_GEOJSON}")
            return

        self.stdout.write("⏳ Строим граф ж/д из GeoJSON...")
        with open(RAILWAY_GEOJSON, encoding="utf-8") as f:
            data = json.load(f)

        G = nx.Graph()
        for feat in data["features"]:
            geom = feat.get("geometry", {})
            if geom.get("type") != "LineString":
                continue
            coords = geom.get("coordinates", [])
            pts = [(c[1], c[0]) for c in coords if len(c) >= 2]
            for i in range(len(pts) - 1):
                p1, p2 = pts[i], pts[i+1]
                dist = haversine(p1[0], p1[1], p2[0], p2[1])
                if G.has_edge(p1, p2):
                    if G[p1][p2]["weight"] > dist:
                        G[p1][p2]["weight"] = dist
                else:
                    G.add_edge(p1, p2, weight=dist)

        self.stdout.write(f"✅ Граф: {G.number_of_nodes()} узлов, {G.number_of_edges()} рёбер")

        all_nodes = list(G.nodes())

        def snap(lat, lon):
            best = None
            best_d = float("inf")
            for node in all_nodes:
                d = haversine(lat, lon, node[0], node[1])
                if d < best_d:
                    best_d = d
                    best = node
            if best_d > max_snap:
                return None
            return best

        try:
            link = OpticalLink.objects.get(id=link_id)
        except OpticalLink.DoesNotExist:
            self.stderr.write(f"❌ Линия {link_id} не найдена")
            return

        if not link.station_a or not link.station_b:
            self.stderr.write(f"❌ У линии {link_id} нет станций")
            return

        if not link.station_a.latitude or not link.station_b.latitude:
            self.stderr.write(f"❌ У станций нет координат")
            return

        # Находим ближайшие узлы для станций
        node_a = snap(link.station_a.latitude, link.station_a.longitude)
        node_b = snap(link.station_b.latitude, link.station_b.longitude)

        if not node_a or not node_b:
            self.stderr.write(f"❌ Не удалось найти ж/д рядом со станциями")
            return

        try:
            path = nx.shortest_path(G, node_a, node_b, weight="weight")
        except nx.NetworkXNoPath:
            self.stderr.write(f"❌ Нет пути по ж/д между станциями")
            return

        full_path = [[n[0], n[1]] for n in path]
        link.path_geojson = full_path
        link.save(update_fields=["path_geojson"])
        self.stdout.write(self.style.SUCCESS(f"✅ Линия {link.id} ({link}): {len(full_path)} точек"))
