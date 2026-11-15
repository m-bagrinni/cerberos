import math
from django.core.management.base import BaseCommand
from apps.stations.models import OpticalLink

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(a))

class Command(BaseCommand):
    help = "Рассчитывает ожидаемый RX и потери (с калибровкой по реальным данным)"

    def add_arguments(self, parser):
        parser.add_argument("--link-id", type=int, default=21, help="ID линии")

    def handle(self, *args, **opts):
        link_id = opts["link_id"]
        link = OpticalLink.objects.get(id=link_id)
        
        if not link.rx_a_dbm or not link.rx_b_dbm:
            self.stderr.write(f"❌ У линии {link_id} нет RX A или RX B")
            return

        if not link.station_a or not link.station_a.latitude:
            self.stderr.write(f"❌ У станции А нет координат")
            return
        if not link.station_b or not link.station_b.latitude:
            self.stderr.write(f"❌ У станции Б нет координат")
            return
        
        lat_a = link.station_a.latitude
        lon_a = link.station_a.longitude
        lat_b = link.station_b.latitude
        lon_b = link.station_b.longitude

        # 1. Считаем общую длину линии по прямой (можно уточнить по path_geojson)
        total_dist_km = haversine(lat_a, lon_a, lat_b, lon_b)
        
        # 2. Общие реальные потери
        total_real_loss = link.rx_a_dbm - link.rx_b_dbm  # (разница)
        
        # 3. Количество сварок (муфт)
        closures = list(link.closures.all())
        total_splices = len(closures)
        
        # 4. Среднее затухание на 1 км (с учётом сварок)
        # Предполагаем, что затухание на сварку = 0.2 dB (можно вынести в параметр)
        SPLICE_LOSS = 0.2
        if total_dist_km > 0:
            attenuation_per_km = (total_real_loss - (total_splices * SPLICE_LOSS)) / total_dist_km
        else:
            attenuation_per_km = 0.25  # fallback
        
        self.stdout.write(f"Линия {link}: RX A = {link.rx_a_dbm}, RX B = {link.rx_b_dbm}")
        self.stdout.write(f"Общая длина: {total_dist_km:.1f} км, сварок: {total_splices}")
        self.stdout.write(f"Реальные потери: {total_real_loss:.2f} dB")
        self.stdout.write(f"Среднее затухание: {attenuation_per_km:.3f} dB/км")

        closures.sort(key=lambda c: haversine(lat_a, lon_a, c.lat, c.lon))

        prev_dist_a = 0.0
        for idx, c in enumerate(closures):
            dist_km_a = haversine(lat_a, lon_a, c.lat, c.lon)
            dist_km_b = haversine(lat_b, lon_b, c.lat, c.lon)
            splices_a = idx + 1
            splices_b = total_splices - idx
            
            # От А
            total_loss_a = (dist_km_a * attenuation_per_km) + (splices_a * SPLICE_LOSS)
            expected_a = link.rx_a_dbm - total_loss_a
            
            # От Б
            total_loss_b = (dist_km_b * attenuation_per_km) + (splices_b * SPLICE_LOSS)
            expected_b = link.rx_b_dbm - total_loss_b
            
            # Потери на участке (от предыдущей муфты)
            segment_dist = dist_km_a - prev_dist_a
            segment_loss = (segment_dist * attenuation_per_km) + SPLICE_LOSS
            
            c.expected_rx_dbm = round(expected_a, 2)
            c.total_loss_db = round(total_loss_a, 2)
            c.segment_loss_db = round(segment_loss, 2)
            c.expected_rx_b_dbm = round(expected_b, 2)
            c.total_loss_b_db = round(total_loss_b, 2)
            c.save(update_fields=[
                'expected_rx_dbm', 'total_loss_db', 'segment_loss_db',
                'expected_rx_b_dbm', 'total_loss_b_db'
            ])
            
            self.stdout.write(
                f"  {c.name}: A={dist_km_a:.1f}км -> RX={c.expected_rx_dbm} | "
                f"Б={dist_km_b:.1f}км -> RX={c.expected_rx_b_dbm}"
            )
            
            prev_dist_a = dist_km_a

        self.stdout.write(self.style.SUCCESS(f"✅ Рассчитано для {len(closures)} муфт"))
