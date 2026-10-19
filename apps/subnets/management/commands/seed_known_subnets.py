"""
Создание подсетей по списку (для ручного сидирования).

Примеры:
    python manage.py seed_known_subnets --dry-run
    python manage.py seed_known_subnets
    python manage.py seed_known_subnets --no-populate

Список задан в KNOWN_SUBNETS ниже — правь там, если нужно.
"""

import ipaddress

from django.core.management.base import BaseCommand

from apps.subnets.models import IpAddress, Subnet


# ─────────────────────────────────────────────────────────────
# Список подсетей для сидирования.
# name → (cidr-как-ввели, описание)
# ─────────────────────────────────────────────────────────────
KNOWN_SUBNETS = [
    ("MANAGEMENT",  "172.16.101.1/24", "Управление сетевым оборудованием"),
    ("SERVERS",     "172.16.100.1/24", "Серверная подсеть"),
    ("IVC_7SubNet", "10.70.7.1/24",    "IVC подсеть 7"),
]


class Command(BaseCommand):
    help = "Создаёт известные подсети из списка KNOWN_SUBNETS"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--no-populate", action="store_true",
                            help="Не создавать IpAddress для хостов")

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        populate = not opts["no_populate"]

        created = 0
        for name, cidr_raw, descr in KNOWN_SUBNETS:
            try:
                net = ipaddress.ip_network(cidr_raw, strict=False)
            except ValueError as e:
                self.stdout.write(self.style.ERROR(
                    f"  ! {name}: неверный CIDR {cidr_raw}: {e}"
                ))
                continue

            cidr = str(net)
            existing = Subnet.objects.filter(cidr=cidr).first()
            if existing:
                self.stdout.write(f"  = {cidr:20s} {existing.name}")
                continue

            self.stdout.write(f"  + {cidr:20s} {name}  ({descr})")
            if dry:
                continue

            subnet = Subnet.objects.create(
                cidr=cidr,
                name=name,
                description=descr,
                is_active=True,
                scan_interval_min=15,
            )
            created += 1

            if populate:
                ips_created = self._populate(subnet)
                self.stdout.write(f"      → IpAddress: +{ips_created}")

        if dry:
            self.stdout.write(self.style.WARNING("\n(dry-run)"))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"\nСоздано подсетей: {created}"
            ))

    def _populate(self, subnet: Subnet) -> int:
        try:
            net = ipaddress.ip_network(subnet.cidr, strict=False)
        except ValueError:
            return 0
        hosts = list(net.hosts())
        if not hosts:
            hosts = [net.network_address]

        existing = set(subnet.ips.values_list("ip", flat=True))
        bulk = [
            IpAddress(subnet=subnet, ip=str(h), state=IpAddress.STATE_UNKNOWN)
            for h in hosts if str(h) not in existing
        ]
        if bulk:
            IpAddress.objects.bulk_create(bulk, batch_size=500)
        return len(bulk)
