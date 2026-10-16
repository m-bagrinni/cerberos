"""
Создаёт профиль D-Link DGS-3120 и его метрики.

Матчинг (проверено на DGS-3120-24TC, sysObjectID 1.3.6.1.4.1.171.10.117.2.1):
    sysObjectID  ~ 1.3.6.1.4.1.171.10.117.*
    sysDescr     ~ "DGS-3120"
    entModel     ~ "DGS-3120"

Метрики (все подтверждены на живом устройстве 172.16.101.20):
    CPU 5s / 1m / 5m — DLINK-AGENT-MIB.agentCPUutilization
    DRAM total/used/% — DLINK-AGENT-MIB.agentDRAMutilizationTable  (BasicInfo 9)
    Flash total/used/% — DLINK-AGENT-MIB (BasicInfo 10)
    sys_uptime        — SNMPv2-MIB
    power_internal / power_external — ENTITY-MIB entPhysicalName (string)

Что НЕ поддерживается на 3120:
    - Температура (DLINKSW-ENTITY-EXT-MIB отсутствует)
    - DDM (SFP) по SNMP — только через Web/CLI
    - Fan-статусы — вентиляторов физически нет (пассивное охлаждение),
      entPhysicalName всегда "... Fan is fail" — не даём в метрики, чтобы
      не было ложных алертов.

Идемпотентно: повторный запуск обновляет значения, не создаёт дубли.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.devices.models import DeviceProfile, MetricDefinition, Vendor


PROFILE_SLUG = "dgs-3120"
PROFILE_NAME = "D-Link DGS-3120"

# sysObjectID всей линейки DGS-3120-* начинается с этого префикса
SYSOBJECT_PREFIXES = ["1.3.6.1.4.1.171.10.117"]

# 12 метрик (проверены на DGS-3120-24TC, 172.16.101.20)
METRICS = [
    # --- CPU ---
    {
        "key": "cpu_5s",
        "name": "CPU 5 сек",
        "oid": "1.3.6.1.4.1.171.12.1.1.6.1.0",
        "value_type": "gauge",
        "scale": "single",
        "unit": "%",
        "warn": 70.0,
        "crit": 90.0,
    },
    {
        "key": "cpu_1m",
        "name": "CPU 1 мин",
        "oid": "1.3.6.1.4.1.171.12.1.1.6.2.0",
        "value_type": "gauge",
        "scale": "single",
        "unit": "%",
        "warn": 70.0,
        "crit": 90.0,
    },
    {
        "key": "cpu_5m",
        "name": "CPU 5 мин",
        "oid": "1.3.6.1.4.1.171.12.1.1.6.3.0",
        "value_type": "gauge",
        "scale": "single",
        "unit": "%",
        "warn": 70.0,
        "crit": 90.0,
    },
    # --- DRAM (BasicInfo 9) ---
    {
        "key": "mem_dram_total",
        "name": "DRAM всего",
        "oid": "1.3.6.1.4.1.171.12.1.1.9.1.2.1",
        "value_type": "gauge",
        "scale": "single",
        "unit": "KB",
        "warn": None,
        "crit": None,
    },
    {
        "key": "mem_dram_used",
        "name": "DRAM занято",
        "oid": "1.3.6.1.4.1.171.12.1.1.9.1.3.1",
        "value_type": "gauge",
        "scale": "single",
        "unit": "KB",
        "warn": None,
        "crit": None,
    },
    {
        "key": "mem_dram_pct",
        "name": "DRAM %",
        "oid": "1.3.6.1.4.1.171.12.1.1.9.1.4.1",
        "value_type": "gauge",
        "scale": "single",
        "unit": "%",
        "warn": 80.0,
        "crit": 95.0,
    },
    # --- Flash / NVRAM (BasicInfo 10) ---
    {
        "key": "mem_flash_total",
        "name": "Flash всего",
        "oid": "1.3.6.1.4.1.171.12.1.1.10.1.2.1",
        "value_type": "gauge",
        "scale": "single",
        "unit": "KB",
        "warn": None,
        "crit": None,
    },
    {
        "key": "mem_flash_used",
        "name": "Flash занято",
        "oid": "1.3.6.1.4.1.171.12.1.1.10.1.3.1",
        "value_type": "gauge",
        "scale": "single",
        "unit": "KB",
        "warn": None,
        "crit": None,
    },
    {
        "key": "mem_flash_pct",
        "name": "Flash %",
        "oid": "1.3.6.1.4.1.171.12.1.1.10.1.4.1",
        "value_type": "gauge",
        "scale": "single",
        "unit": "%",
        "warn": 80.0,
        "crit": 95.0,
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
    },
    # --- Питание (ENTITY-MIB, entPhysicalName) ---
    # value=None, raw_value="Internal Power is OK" / "External Power is fail".
    # Строковые метрики: пойдут в raw_value, value будет NULL.
    {
        "key": "power_internal",
        "name": "Питание (внутреннее)",
        "oid": "1.3.6.1.2.1.47.1.1.1.1.2.3",
        "value_type": "string",
        "scale": "single",
        "unit": "",
        "warn": None,
        "crit": None,
    },
    {
        "key": "power_external",
        "name": "Питание (внешнее)",
        "oid": "1.3.6.1.2.1.47.1.1.1.1.2.4",
        "value_type": "string",
        "scale": "single",
        "unit": "",
        "warn": None,
        "crit": None,
    },
]


class Command(BaseCommand):
    help = "Создать/обновить профиль D-Link DGS-3120 и его метрики"

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
            slug="dlink",
            defaults={"name": "D-Link", "enterprise_ids": ["171"]},
        )
        if v_created:
            self.stdout.write(self.style.SUCCESS(
                f"  + Vendor: {vendor.name} (enterprise_ids={vendor.enterprise_ids})"
            ))
        else:
            if "171" not in (vendor.enterprise_ids or []):
                vendor.enterprise_ids = list(set((vendor.enterprise_ids or []) + ["171"]))
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
                "match_sysdescr_regex": r"DGS-3120",
                "match_model_regex": r"DGS-3120",
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
                defaults={**data, "transform": "identity", "enabled": True},
            )
            mark = "+" if m_created else "~"
            self.stdout.write(
                f"    {mark} {metric.key:20s} oid={metric.oid}"
            )

        if dry:
            self.stdout.write(self.style.WARNING(
                "\n  --dry-run: изменения НЕ сохранены"
            ))
            raise SystemExit(0)

        self.stdout.write(self.style.SUCCESS(
            f"\nOK: профиль {PROFILE_SLUG} готов, метрик: {len(METRICS)}"
        ))
