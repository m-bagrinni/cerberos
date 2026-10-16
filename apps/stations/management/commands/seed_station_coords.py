"""
Заполнение координат станций (широта/долгота).
Источники: Alta.ru, Railwayz.info, Mapcarta, Яндекс.Карты, FreiCON.
"""
from django.core.management.base import BaseCommand

from apps.stations.models import Station


COORDS = {
    # Главный ход: Бишкек — Рыбачье
    "bishkek":        (42.86639, 74.55167, "Бишкек I (Пишпек)"),
    "kant":           (42.88604, 74.85048, "Кант (ст. код 71700)"),
    "ivanovka":       (42.83835, 75.06474, "Ивановка-1 (ст. код 71710)"),
    "tokmak":         (42.81994, 75.30945, "Токмак"),
    "zhel-aryk":      (42.74619, 75.82035, "Жиль-Арык (Джиль-Арык)"),
    "balykchy":       (42.46404, 76.17970, "Рыбачье (Балыкчы)"),
    "kyyamat-kurkol": (42.46487, 76.03890, "Каямат-Куркол"),

    # Западная ветка: Бишкек — Чалдовар
    "sokuluk":        (42.85340, 74.42541, "Сокулук (разъезд, код 716402)"),
    "shopokovo":      (42.84113, 74.30728, "Шопоково (ст. код 716501)"),
    "belovodsk":      (42.81789, 74.12405, "Беловодская (ст. код 716605)"),
    "rzd-141":        (42.82105, 74.01275, "Разъезд №141"),
    "kara-balta":     (42.81602, 73.85895, "Кара-Балта (ст. код 716804)"),
    "chaldovar":      (42.84197, 73.51788, "Чалдовар"),
    "munke":          (42.90692, 73.01222, "Муньке"),
    "merke":          (42.88472, 73.22935, "Мерке"),
    "kainda":         (42.82431, 73.67806, "Каинды (ст. код 71570)"),

    # Разъезд №3639 — на участке Бишкек — Луговая (Жамбылская обл., Казахстан)
    "rzd-3639":       (42.91698, 72.92101, "Разъезд №3639 (аул Казак, Каз.)"),

    # Не ж/д станции / приблизительные
    "rzd-148":        (42.64400, 75.85900, "Кыз-Кыя / РЗД-148 (приблизительно)"),
    "kochkor":        (42.21300, 75.75600, "Кочкор (не ж/д станция)"),
}


class Command(BaseCommand):
    help = "Заполнить latitude/longitude для станций"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Показать, но не писать")

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        found = 0
        missing = []

        for st in Station.objects.all().order_by("sort_order", "name"):
            coords = COORDS.get(st.slug)
            if not coords:
                missing.append(st.slug)
                continue
            lat, lng, comment = coords
            if not dry:
                st.latitude = lat
                st.longitude = lng
                st.save(update_fields=["latitude", "longitude"])
            self.stdout.write(f"  {st.slug:<20} → {lat:>9.5f}, {lng:>9.5f}  ({comment})")
            found += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Обновлено: {found} из {Station.objects.count()}"))
        if missing:
            self.stdout.write(self.style.WARNING(f"Без координат: {', '.join(missing)}"))
        if dry:
            self.stdout.write(self.style.WARNING("(DRY-RUN)"))
