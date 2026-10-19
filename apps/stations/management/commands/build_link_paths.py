"""Snap OpticalLink к ж/д из OSM → path_geojson."""
import json
import os
from math import radians, cos, sin, asin, sqrt

import networkx as nx
from django.core.management.base import BaseCommand

from apps.stations.models import OpticalLink


RAILWAY_JSON = "/app/kgz_railways.json"


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))


class Command(BaseCommand):
    help = "Snap OpticalLink к ж/д из OSM → path_geojson"

    def add_arguments(self, parser):
        parser.add_argument("--max-snap", type=int, default=10000,
                            help="Макс. расстояние до ближайшего ж/д узла (м)")
        parser.add_argument("--json", type=str, default=RAILWAY_JSON)

    def handle(self, *args, **opts):
        max_snap = opts["max_snap"]
        json_path = opts["json"]

        if not os.path.exists(json_path):
            self.stderr.write(f"Нет файла {json_path}")
            return

        self.stdout.write("Строим граф ж/д...")
        with open(json_path) as f:
            data = json.load(f)

        G = nx.Graph()
        for el in data["elements"]:
            if el.get("type") != "way":
                continue
            geom = el.get("geometry", [])
            for i in range(len(geom) - 1):
                p1 = (geom[i]["lat"], geom[i]["lon"])
                p2 = (geom[i + 1]["lat"], geom[i + 1]["lon"])
                dist = haversine(p1[0], p1[1], p2[0], p2[1])
                if G.has_edge(p1, p2):
                    if G[p1][p2]["weight"] > dist:
                        G[p1][p2]["weight"] = dist
                else:
                    G.add_edge(p1, p2, weight=dist)

        self.stdout.write(f"  Узлов: {G.number_of_nodes()}, рёбер: {G.number_of_edges()}")

        all_nodes = list(G.nodes())
        nearest_cache = {}

        def snap(lat, lon):
            key = (round(lat, 4), round(lon, 4))
            if key in nearest_cache:
                return nearest_cache[key]
            best = None
            best_d = float("inf")
            for node in all_nodes:
                d = haversine(lat, lon, node[0], node[1])
                if d < best_d:
                    best_d = d
                    best = node
            if best_d > max_snap:
                nearest_cache[key] = (None, best_d)
                return None, best_d
            nearest_cache[key] = (best, best_d)
            return best, best_d

        links = OpticalLink.objects.select_related("station_a", "station_b").all()
        total = links.count()
        self.stdout.write(f"Обрабатываем {total} линков...")

        ok, skipped = 0, 0
        for link in links:
            a, b = link.station_a, link.station_b
            if not (a.latitude and a.longitude and b.latitude and b.longitude):
                skipped += 1
                self.stdout.write(f"  SKIP {link.id}: нет координат")
                continue

            a_node, a_dist = snap(float(a.latitude), float(a.longitude))
            b_node, b_dist = snap(float(b.latitude), float(b.longitude))

            if not a_node or not b_node:
                skipped += 1
                self.stdout.write(
                    f"  SKIP {link.id} {a.slug}→{b.slug}: snap A={a_dist:.0f}м, B={b_dist:.0f}м"
                )
                continue

            try:
                path = nx.shortest_path(G, a_node, b_node, weight="weight")
            except nx.NetworkXNoPath:
                skipped += 1
                self.stdout.write(f"  NO PATH {link.id} {a.slug}→{b.slug}")
                continue

            path_coords = [[float(n[0]), float(n[1])] for n in path]

            # Добавить "хвост" от точной станции до ближайшего ж/д узла
            if a_dist > 10:
                path_coords.insert(0, [float(a.latitude), float(a.longitude)])
            if b_dist > 10:
                path_coords.append([float(b.latitude), float(b.longitude)])

            link.path_geojson = path_coords
            link.save(update_fields=["path_geojson"])
            ok += 1
            self.stdout.write(
                f"  OK {link.id} {a.slug}→{b.slug}: {len(path)} точек "
                f"(snap A={a_dist:.0f}м, B={b_dist:.0f}м)"
            )

        self.stdout.write(self.style.SUCCESS(f"Готово: OK={ok}, пропущено={skipped}"))
