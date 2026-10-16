"""
Создание Station из переименованных NeDevice (Olencom NE).

Идея: у нас уже есть 20 NE с человеческими именами («Бишкек», «Сокулук», ...).
Станция — это «обёртка» над NE, к которой позже привяжем коммутаторы.

По умолчанию:
  - создаёт Station только для тех NE, у которых name != "" и ещё нет привязки;
  - x/y распределяет по сетке (кроме уже существующих — их не трогает);
  - не трогает существующие привязки.

Флаги:
  --dry-run        показать план без записи в БД
  --overwrite-xy   перезаписать координаты ВСЕХ станций по сетке
  --reset          удалить все Station (осторожно! OpticalLink тоже уйдут)
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.devices.models import NeDevice
from apps.stations.models import OpticalLink, Station, StationNe


# ── Транслит рус → лат (slug для станций) ───────────────────
_TRANSLIT = {
    "а": "a",  "б": "b",  "в": "v",  "г": "g",  "д": "d",
    "е": "e",  "ё": "e",  "ж": "zh", "з": "z",  "и": "i",
    "й": "y",  "к": "k",  "л": "l",  "м": "m",  "н": "n",
    "о": "o",  "п": "p",  "р": "r",  "с": "s",  "т": "t",
    "у": "u",  "ф": "f",  "х": "h",  "ц": "c",  "ч": "ch",
    "ш": "sh", "щ": "sch", "ъ": "",  "ы": "y",  "ь": "",
    "э": "e",  "ю": "yu", "я": "ya",
    " ": "-",
}


def slugify_ru(text: str) -> str:
    """Простой транслит: 'Кара - Балта' -> 'kara-balta'."""
    out = []
    for ch in text.lower():
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isalnum() or ch in "-_":
            out.append(ch)
        else:
            out.append("-")
    s = "".join(out)
    # Схлопнуть повторные дефисы
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")[:60] or "station"


def unique_slug(base: str, reserved: set) -> str:
    """bishkek, bishkek-2, bishkek-3, ..."""
    if base not in reserved:
        return base
    i = 2
    while f"{base}-{i}" in reserved:
        i += 1
    return f"{base}-{i}"


class Command(BaseCommand):
    help = "Создать Station из переименованных NeDevice и привязать NE"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Показать план без записи")
        parser.add_argument("--overwrite-xy", action="store_true",
                            help="Перезаписать x/y всех станций по сетке")
        parser.add_argument("--reset", action="store_true",
                            help="Удалить все Station и OpticalLink (осторожно!)")

    def handle(self, *args, **opts):
        dry = opts["dry_run"]

        if opts["reset"]:
            if dry:
                n_st = Station.objects.count()
                n_lk = OpticalLink.objects.count()
                self.stdout.write(self.style.WARNING(
                    f"[DRY] Удалим {n_st} Station и {n_lk} OpticalLink"
                ))
            else:
                with transaction.atomic():
                    n_lk = OpticalLink.objects.count()
                    OpticalLink.objects.all().delete()
                    n_st = Station.objects.count()
                    Station.objects.all().delete()
                self.stdout.write(self.style.WARNING(
                    f"УДАЛЕНО: {n_st} Station, {n_lk} OpticalLink"
                ))

        nes = (
            NeDevice.objects
            .exclude(name="")
            .order_by("ne_id")
        )
        total_ne = nes.count()
        self.stdout.write(f"NE с именем: {total_ne}")

        if not total_ne:
            self.stdout.write(self.style.WARNING("Нечего делать."))
            return

        # Существующие слаги — чтобы не конфликтовать
        reserved = set(Station.objects.values_list("slug", flat=True))

        # Сетка для новых станций: 4 колонки × N строк
        COLS = 5
        COL_W = 240
        ROW_H = 150
        START_X = 140
        START_Y = 120

        created = 0
        bound = 0
        kept = 0

        # Сначала — все существующие станции (их x/y не трогаем)
        existing_by_slug = {st.slug: st for st in Station.objects.all()}

        # Все существующие привязки — set(ne_id)
        already_bound_ne_ids = set(
            StationNe.objects.values_list("ne_id", flat=True)
        )

        # Пройдём по NE
        new_station_index = 0
        for ne in nes:
            name = ne.name.strip()
            base_slug = slugify_ru(name)

            # Ищем существующую станцию с таким именем
            station = (
                Station.objects.filter(name=name).first()
                or Station.objects.filter(slug=base_slug).first()
            )

            action = None

            if station:
                action = "BOUND-ONLY"
            else:
                if dry:
                    action = "CREATE"
                    station = None  # чтобы не использовать ниже
                else:
                    slug = unique_slug(base_slug, reserved)
                    # Позиция
                    col = new_station_index % COLS
                    row = new_station_index // COLS
                    x = START_X + col * COL_W
                    y = START_Y + row * ROW_H

                    station = Station.objects.create(
                        slug=slug,
                        name=name,
                        x=x,
                        y=y,
                        sort_order=ne.ne_id * 10,  # по ne_id, стабильно
                    )
                    reserved.add(slug)
                    created += 1
                    new_station_index += 1
                    action = "CREATE"

            # Привязка NE → Station
            if station:
                if ne.id not in already_bound_ne_ids:
                    if dry:
                        self.stdout.write(f"  [DRY-BIND] {station.slug} ← NE-{ne.ne_id} ({name})")
                    else:
                        StationNe.objects.create(station=station, ne=ne)
                        already_bound_ne_ids.add(ne.id)
                    bound += 1
                else:
                    kept += 1
            else:
                # dry-run для CREATE
                slug = unique_slug(base_slug, reserved)
                col = new_station_index % COLS
                row = new_station_index // COLS
                x = START_X + col * COL_W
                y = START_Y + row * ROW_H
                self.stdout.write(f"  [DRY] CREATE {slug:<20} ({name}) x={x} y={y}")
                reserved.add(slug)
                created += 1
                new_station_index += 1

        # Опция: перезаписать x/y всем
        if opts["overwrite_xy"]:
            self.stdout.write(self.style.WARNING("Перезаписываем x/y всех станций по сетке…"))
            if not dry:
                all_st = Station.objects.order_by("sort_order", "id")
                for idx, st in enumerate(all_st):
                    col = idx % COLS
                    row = idx // COLS
                    st.x = START_X + col * COL_W
                    st.y = START_Y + row * ROW_H
                    st.save(update_fields=["x", "y"])

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"ИТОГО: создано={created}, привязано={bound}, уже было={kept}, всего NE={total_ne}"
        ))
        if dry:
            self.stdout.write(self.style.WARNING("(DRY-RUN — в БД ничего не записано)"))
