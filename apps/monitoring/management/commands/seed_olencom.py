"""
Seed Olencom RP1000 (PDH Multiplexer).

Создаёт:
  - Vendor: olencom (enterprise_id = 3399)
  - DeviceProfile: olencom-rp1000 (match: 1.3.6.1.4.1.3399.2.3.3)
  - MetricDefinition: ~15 метрик для NE, SFP и Ethernet портов

Идемпотентно: повторный запуск обновляет существующие записи.
"""

from django.core.management.base import BaseCommand
from apps.devices.models import Vendor, DeviceProfile, MetricDefinition


# Список NE-ID, которые реально отвечают на этом шасси.
# Формируется автоматически при discovery, но здесь — фиксированный
# список для первичного seed. Discovery-команда обновит при необходимости.
NE_IDS = [1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 21, 22, 25, 37]


METRICS = [
    # --- NE метаданные (walk по ne_id) ---
    {
        "key": "ne_status",
        "name": "NE Status (1=online)",
        "oid": "1.3.6.1.4.1.3399.2.3.3.2.8.1.5",
        "value_type": "gauge",
        "scale": "walk",
        "unit": "",
        "warn": None,
        "crit": None,
    },
    {
        "key": "ne_name",
        "name": "NE Name",
        "oid": "1.3.6.1.4.1.3399.2.3.3.2.8.1.20",
        "value_type": "string",
        "scale": "walk",
        "unit": "",
    },
    {
        "key": "ne_ip",
        "name": "NE IP address",
        "oid": "1.3.6.1.4.1.3399.2.3.3.1.2.1.1",
        "value_type": "string",
        "scale": "walk",
        "unit": "",
    },

    # --- SFP порты 1, 2 (WAN) ---
    {
        "key": "sfp_present_active",
        "name": "SFP Present/Active",
        "oid": "1.3.6.1.4.1.3399.2.3.3.2.11.1.2",
        "value_type": "string",
        "scale": "walk",
        "unit": "",
    },
    {
        "key": "sfp_vendor",
        "name": "SFP Vendor",
        "oid": "1.3.6.1.4.1.3399.2.3.3.2.11.1.4",
        "value_type": "string",
        "scale": "walk",
        "unit": "",
    },
    {
        "key": "sfp_pn",
        "name": "SFP Part Number",
        "oid": "1.3.6.1.4.1.3399.2.3.3.2.11.1.4",
        "value_type": "string",
        "scale": "walk",
        "unit": "",
    },
    {
        "key": "sfp_sn",
        "name": "SFP Serial",
        "oid": "1.3.6.1.4.1.3399.2.3.3.2.11.1.4",
        "value_type": "string",
        "scale": "walk",
        "unit": "",
    },

    # --- SFP DDM (отдельные метрики — правильный вариант B) ---
    {
        "key": "sfp_temp",
        "name": "SFP Temperature",
        "oid": "1.3.6.1.4.1.3399.2.3.3.2.11.1.5",
        "value_type": "gauge",
        "scale": "walk",
        "unit": "°C",
        "warn": 60,
        "crit": 70,
    },
    {
        "key": "sfp_bias",
        "name": "SFP Bias Current",
        "oid": "1.3.6.1.4.1.3399.2.3.3.2.11.1.5",
        "value_type": "gauge",
        "scale": "walk",
        "unit": "mA",
    },
    {
        "key": "sfp_voltage",
        "name": "SFP Voltage",
        "oid": "1.3.6.1.4.1.3399.2.3.3.2.11.1.5",
        "value_type": "gauge",
        "scale": "walk",
        "unit": "V",
    },
    {
        "key": "sfp_rx_dbm",
        "name": "SFP RX Power",
        "oid": "1.3.6.1.4.1.3399.2.3.3.2.11.1.5",
        "value_type": "gauge",
        "scale": "walk",
        "unit": "dBm",
        "warn": -20,
        "crit": -25,
    },
    {
        "key": "sfp_tx_dbm",
        "name": "SFP TX Power",
        "oid": "1.3.6.1.4.1.3399.2.3.3.2.11.1.5",
        "value_type": "gauge",
        "scale": "walk",
        "unit": "dBm",
    },
    {
        "key": "sfp_link",
        "name": "SFP Link (1=UP)",
        "oid": "1.3.6.1.4.1.3399.2.3.3.2.11.1.5",
        "value_type": "gauge",
        "scale": "walk",
        "unit": "",
    },

    # --- Ethernet порты 3, 4, 5, 6 ---
    {
        "key": "eth_enable",
        "name": "ETH Enable",
        "oid": "1.3.6.1.4.1.3399.2.3.3.2.1.1.2",
        "value_type": "gauge",
        "scale": "walk",
        "unit": "",
    },
    {
        "key": "eth_pvid",
        "name": "ETH PVID (VLAN ID)",
        "oid": "1.3.6.1.4.1.3399.2.3.3.2.1.1.6",
        "value_type": "gauge",
        "scale": "walk",
        "unit": "",
    },
    {
        "key": "eth_mode",
        "name": "ETH Port Mode (0=access, 2=hybrid)",
        "oid": "1.3.6.1.4.1.3399.2.3.3.2.1.1.7",
        "value_type": "gauge",
        "scale": "walk",
        "unit": "",
    },
    {
        "key": "eth_status",
        "name": "ETH Status/Alarm",
        "oid": "1.3.6.1.4.1.3399.2.3.3.2.1.1.11",
        "value_type": "gauge",
        "scale": "walk",
        "unit": "",
    },
    {
        "key": "eth_counter1",
        "name": "ETH Counter 1 (raw, uint64 LE)",
        "oid": "1.3.6.1.4.1.3399.2.3.3.2.1.1.13",
        "value_type": "counter",
        "scale": "walk",
        "unit": "",
    },
    {
        "key": "eth_counter2",
        "name": "ETH Counter 2 (raw, uint64 LE)",
        "oid": "1.3.6.1.4.1.3399.2.3.3.2.1.1.20",
        "value_type": "counter",
        "scale": "walk",
        "unit": "",
    },
]


class Command(BaseCommand):
    help = "Seed Olencom RP1000 vendor/profile/metrics (idempotent)"

    def handle(self, *args, **options):
        # 1. Vendor
        vendor, created = Vendor.objects.get_or_create(
            slug="olencom",
            defaults={
                "name": "Olencom",
                "enterprise_ids": ["3399"],
            },
        )
        self.stdout.write(f"{'[+]' if created else '[=]'} Vendor: {vendor}")

        # 2. DeviceProfile
        profile, created = DeviceProfile.objects.get_or_create(
            vendor=vendor,
            slug="olencom-rp1000",
            defaults={
                "name": "Olencom RP1000 (PDH Multiplexer)",
                "match_sysobject_prefixes": ["1.3.6.1.4.1.3399.2.3.3"],
                "priority": 10,
            },
        )
        self.stdout.write(f"{'[+]' if created else '[=]'} Profile: {profile}")

        # 3. MetricDefinition
        n_created, n_updated = 0, 0
        for m in METRICS:
            obj, created = MetricDefinition.objects.update_or_create(
                profile=profile,
                key=m["key"],
                defaults={
                    "name": m["name"],
                    "oid": m["oid"],
                    "value_type": m["value_type"],
                    "scale": m["scale"],
                    "unit": m.get("unit", ""),
                    "warn": m.get("warn"),
                    "crit": m.get("crit"),
                    "enabled": True,
                },
            )
            if created:
                n_created += 1
            else:
                n_updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Metrics: created={n_created}, updated={n_updated}, total={len(METRICS)}"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Seed complete. Profile: {profile.slug} "
                f"(vendor={vendor.slug}, NE_IDS={len(NE_IDS)})"
            )
        )
