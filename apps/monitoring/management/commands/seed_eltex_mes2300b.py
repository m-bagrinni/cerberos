"""
Создаёт профиль Eltex MES2300B-24F и его метрики.

Матчинг (проверено на живом устройстве 172.16.101.4):
    sysObjectID  = 1.3.6.1.4.1.35265.1.268
    sysDescr     ~ "MES2300B-24F"
    entModel     = "MES2300B-24F"

Метрики (все подтверждены на живом устройстве):
    CPU 5s / 1m / 5m — ELTEX-PROCESS-MIB (eltexProcessCPU, .35265.41.1.1)
        Индекс составной <unit>.<interval>, interval ∈ {5, 60, 300} сек.
        unit=0 (единственный CPU).
    DRAM total / free / free% — ELTEX-PROCESS-MIB (eltexProcessMemory, .35265.41.1.2)
        Значения в БАЙТАХ (Gauge32 + Counter64).
    sys_uptime — стандартный SNMPv2-MIB
    Temperature ×4 сенсора — ELTEX-MES-HWENVIROMENT-MIB (.35265.1.23.11.3.1.1)
        Значения ×100 (2280 = 22.80 °C), поэтому transform="div_100".
        4 сенсора: 67109633..67109636 (передние/задние/CPU/чип).

Что НЕ поддерживается по SNMP:
    - SFP/DDM: отдаётся только «паспорт» модулей (вендор, серийник,
      wavelength, part number), но НЕ температура/напряжение/TX/RX.
      DDM доступен только через Web/CLI.

Идемпотентно: повторный запуск обновляет значения, не создаёт дубли.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.devices.models import DeviceProfile, MetricDefinition, Vendor


PROFILE_SLUG = "eltex-mes2300b"
PROFILE_NAME = "Eltex MES2300B"

# sysObjectID конкретной модели MES2300B-24F
SYSOBJECT_PREFIXES = ["1.3.6.1.4.1.35265.1.268"]

METRICS = [
    # --- CPU (eltexProcessCPU) ---
    {
        "key": "cpu_5s",
        "name": "CPU 5 сек",
        "oid": "1.3.6.1.4.1.35265.41.1.1.1.1.4.0.5",
        "value_type": "gauge",
        "scale": "single",
        "unit": "%",
        "warn": 70.0,
        "crit": 90.0,
        "transform": "identity",
    },
    {
        "key": "cpu_1m",
        "name": "CPU 1 мин",
        "oid": "1.3.6.1.4.1.35265.41.1.1.1.1.4.0.60",
        "value_type": "gauge",
        "scale": "single",
        "unit": "%",
        "warn": 70.0,
        "crit": 90.0,
        "transform": "identity",
    },
    {
        "key": "cpu_5m",
        "name": "CPU 5 мин",
        "oid": "1.3.6.1.4.1.35265.41.1.1.1.1.4.0.300",
        "value_type": "gauge",
        "scale": "single",
        "unit": "%",
        "warn": 70.0,
        "crit": 90.0,
        "transform": "identity",
    },
    # --- DRAM (eltexProcessMemory) ---
    {
        "key": "mem_total",
        "name": "DRAM всего",
        "oid": "1.3.6.1.4.1.35265.41.1.2.1.1.3.0",
        "value_type": "gauge",
        "scale": "single",
        "unit": "B",
        "warn": None,
        "crit": None,
        "transform": "identity",
    },
    {
        "key": "mem_free",
        "name": "DRAM свободно",
        "oid": "1.3.6.1.4.1.35265.41.1.2.1.1.7.0",
        "value_type": "gauge",
        "scale": "single",
        "unit": "B",
        "warn": None,
        "crit": None,
        "transform": "identity",
    },
    {
        "key": "mem_free_pct",
        "name": "DRAM свободно %",
        "oid": "1.3.6.1.4.1.35265.41.1.2.1.1.6.0",
        "value_type": "gauge",
        "scale": "single",
        "unit": "%",
        # warn/crit на "свободно %" — инвертированы, ставим None:
        # алерты на низкий free% появятся отдельно (пункт C — алерты).
        "warn": None,
        "crit": None,
        "transform": "identity",
    },
    # --- Uptime ---
    {
        "key": "sys_uptime",
        "name": "Uptime",
        "oid": "1.3.6.1.2.1.1.3.0",
        "value_type": "gauge",
        "scale": "single",
        "unit": "s",
        "warn": None,
        "crit": None,
        "transform": "identity",
    },
    # --- Температура (walk по 4 сенсорам) ---
    {
        "key": "temp_sensor",
        "name": "Температура",
        "oid": "1.3.6.1.4.1.35265.1.23.11.3.1.1",
        "value_type": "gauge",
        "scale": "walk",
        "unit": "°C",
        "warn": 60.0,
        "crit": 70.0,
        "transform": "div_100",
    },
]


class Command(BaseCommand):
    help = "Создать/обновить профиль Eltex MES2300B и его метрики"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать, что будет создано",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        dry = opts["dry_run"]

        # 1) Вендор
        vendor, v_created = Vendor.objects.get_or_create(
            slug="eltex",
            defaults={"name": "Eltex", "enterprise_ids": ["35265"]},
        )
        if v_created:
            self.stdout.write(self.style.SUCCESS(
                f"  + Vendor: {vendor.name} (enterprise_ids={vendor.enterprise_ids})"
            ))
        else:
            if "35265" not in (vendor.enterprise_ids or []):
                vendor.enterprise_ids = list(set((vendor.enterprise_ids or []) + ["35265"]))
                if not dry:
                    vendor.save(update_fields=["enterprise_ids"])
                self.stdout.write(f"  ~ Vendor: {vendor.name} — обновил enterprise_ids")
            else:
                self.stdout.write(f"  = Vendor: {vendor.name} (уже есть)")

        # 2) Профиль
        profile, p_created = DeviceProfile.objects.update_or_create(
            vendor=vendor,
            slug=PROFILE_SLUG,
            defaults={
                "name": PROFILE_NAME,
                "parent": None,
                "match_sysobject_prefixes": SYSOBJECT_PREFIXES,
                "match_sysdescr_regex": r"MES2300B",
                "match_model_regex": r"MES2300B",
                "priority": 150,
            },
        )
        action = "создан" if p_created else "обновлён"
        self.stdout.write(self.style.SUCCESS(
            f"  {'+' if p_created else '~'} Profile: {profile} ({action})"
        ))

        # 3) Метрики
        for m in METRICS:
            data = dict(m)
            key = data.pop("key")
            metric, m_created = MetricDefinition.objects.update_or_create(
                profile=profile,
                key=key,
                defaults={**data, "enabled": True},
            )
            mark = "+" if m_created else "~"
            self.stdout.write(
                f"    {mark} {metric.key:16s} oid={metric.oid}"
            )

        if dry:
            self.stdout.write(self.style.WARNING(
                "\n  --dry-run: изменения НЕ сохранены"
            ))
            raise SystemExit(0)

        self.stdout.write(self.style.SUCCESS(
            f"\nOK: профиль {PROFILE_SLUG} готов, метрик: {len(METRICS)}"
        ))
