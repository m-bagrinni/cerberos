"""Строит path_geojson для OpticalLink с учетом муфт (маршрут по ж/д из GeoJSON)."""
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
    help = "Строит path_geojson для OpticalLink с учетом муфт"

    def add_arguments(self, parser):
        parser.add_argument("--link-id", type=int, help="ID линии (если не указан — все)")
        parser.add_argument("--max-snap", type=int, default=10000, help="Макс. расстояние до ж/д (м)")

    def handle(self, *args, **opts):
        link_id = opts.get("link_id")
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
            # GeoJSON: [lon, lat] → переводим в [lat, lon]
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
            """Ближайший узел графа к точке (lat, lon)."""
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

        links = OpticalLink.objects.all()
        if link_id:
            links = links.filter(id=link_id)

        for link in links:
            closures = list(link.closures.all().order_by("id"))
            
            # Собираем точки: A → муфты → B
            points = []
            if link.station_a and link.station_a.latitude and link.station_a.longitude:
                points.append((link.station_a.latitude, link.station_a.longitude, "A"))
            for c in closures:
                points.append((c.lat, c.lon, f"муфта {c.id}"))
            if link.station_b and link.station_b.latitude and link.station_b.longitude:
                points.append((link.station_b.latitude, link.station_b.longitude, "B"))

            if len(points) < 2:
                self.stderr.write(f"⚠️ Линия {link.id}: мало точек")
                continue

            # Находим ближайшие узлы
            nodes = []
            for lat, lon, label in points:
                node = snap(lat, lon)
                if node:
                    nodes.append(node)
                else:
                    self.stderr.write(f"⚠️ Линия {link.id}: не найдено ж/д рядом с {label}")

            if len(nodes) < 2:
                self.stderr.write(f"❌ Линия {link.id}: недостаточно узлов на графе")
                continue

            # Строим путь между узлами
            full_path = []
            ok = True
            for i in range(len(nodes) - 1):
                try:
                    path = nx.shortest_path(G, nodes[i], nodes[i+1], weight="weight")
                    for n in path:
                        full_path.append([n[0], n[1]])  # [lat, lon]
                except nx.NetworkXNoPath:
                    self.stderr.write(f"⚠️ Линия {link.id}: нет пути между {nodes[i]} и {nodes[i+1]}")
                    ok = False
                    break
                except Exception as e:
                    self.stderr.write(f"⚠️ Линия {link.id}: ошибка {e}")
                    ok = False
                    break

            if ok and len(full_path) > 1:
                link.path_geojson = full_path
                link.save(update_fields=["path_geojson"])
                self.stdout.write(self.style.SUCCESS(
                    f"✅ Линия {link.id} ({link}): {len(full_path)} точек, муфт: {len(closures)}"
                ))
            else:
                self.stderr.write(f"❌ Линия {link.id}: не удалось построить путь")
