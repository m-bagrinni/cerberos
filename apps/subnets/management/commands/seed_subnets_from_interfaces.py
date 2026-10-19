"""
Автопоиск подсетей по IP-адресам интерфейсов устройств и локальным сетям сервера.

Проходит по:
  • Interface всех Device (models.Interface.ip, если есть)
  • локальным сетям контейнера/хоста (через `ip addr`)

Создаёт Subnet, если такого CIDR ещё нет.
Для каждой подсети создаёт IpAddress по всем хостам (со state=unknown).

Пример:
    python manage.py seed_subnets_from_interfaces --dry-run
    python manage.py seed_subnets_from_interfaces
    python manage.py seed_subnets_from_interfaces --no-populate
"""

import ipaddress
import re
import subprocess

from django.core.management.base import BaseCommand

from apps.devices.models import Device
from apps.monitoring.models import Interface
from apps.subnets.models import IpAddress, Subnet


class Command(BaseCommand):
    help = "Автопоиск подсетей по интерфейсам устройств и локальной сети"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Только показать, что будет создано")
        parser.add_argument("--no-populate", action="store_true",
                            help="Не создавать IpAddress для хостов")
        parser.add_argument("--no-local", action="store_true",
                            help="Не смотреть локальные интерфейсы сервера")

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        populate = not opts["no_populate"]

        cidrs: dict[str, dict] = {}   # cidr → {"source": "...", "name": "..."}

        # 1. Собираем из интерфейсов устройств
        self._collect_from_interfaces(cidrs)

        # 2. Локальные подсети (server/container)
        if not opts["no_local"]:
            self._collect_local(cidrs)

        if not cidrs:
            self.stdout.write(self.style.WARNING("Подсетей не найдено"))
            return

        self.stdout.write(f"Найдено подсетей: {len(cidrs)}\n")

        created_subnets = 0
        for cidr, meta in sorted(cidrs.items()):
            existing = Subnet.objects.filter(cidr=cidr).first()
            if existing:
                self.stdout.write(f"  = {cidr}  {meta.get('name', '')}  (уже в БД)")
                continue

            self.stdout.write(
                f"  + {cidr}  {meta.get('name', '')}  ← {meta.get('source', '')}"
            )
            if dry:
                continue

            subnet = Subnet.objects.create(
                cidr=cidr,
                name=meta.get("name", ""),
                description=meta.get("source", ""),
                is_active=True,
                scan_interval_min=15,
            )
            created_subnets += 1

            if populate:
                self._populate_ips(subnet)

        if dry:
            self.stdout.write(self.style.WARNING("\n(dry-run — ничего не создано)"))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"\nСоздано подсетей: {created_subnets}"
            ))

    # ────────────────────────────────────────────────────

    def _collect_from_interfaces(self, cidrs: dict):
        """Из Interface устройств — если там есть IP (пока нет, но предусмотрим)."""
        # В текущей модели Interface нет ip-поля — но оставим хуком на будущее
        if not hasattr(Interface, "ip"):
            return

        for iface in Interface.objects.exclude(ip="").exclude(ip__isnull=True):
            self._add_from_ip(cidrs, iface.ip, iface.prefix_len if hasattr(iface, "prefix_len") else 24,
                              source=f"Interface {iface.device.name}:{iface.name}")

    def _collect_local(self, cidrs: dict):
        """Локальные сети контейнера — через `ip -o -4 addr show`."""
        try:
            out = subprocess.check_output(
                ["ip", "-o", "-4", "addr", "show"],
                text=True, timeout=5,
            )
        except Exception as e:
            self.stdout.write(f"  (ip addr недоступен: {e})")
            return

        # Формат: "2: eth0    inet 172.17.0.2/16 brd 172.17.255.255 scope global eth0"
        for line in out.splitlines():
            m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", line)
            if not m:
                continue
            ip, prefix = m.group(1), int(m.group(2))
            # Пропускаем loopback
            if ipaddress.ip_address(ip).is_loopback:
                continue
            # Пропускаем docker-сети /16 (служебные)
            if prefix == 16 and ip.startswith("172.1"):
                continue
            iface_name = line.split(":")[1].strip() if ":" in line else "?"
            self._add_from_ip(cidrs, ip, prefix,
                              source=f"Локальный iface {iface_name}")

    def _add_from_ip(self, cidrs: dict, ip: str, prefix: int, source: str):
        try:
            net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
        except ValueError:
            return

        cidr = str(net)
        if cidr not in cidrs:
            cidrs[cidr] = {
                "name": f"Auto {cidr}",
                "source": source,
            }

    def _populate_ips(self, subnet: Subnet):
        """Создаёт все IpAddress в подсети со state=unknown."""
        try:
            net = ipaddress.ip_network(subnet.cidr, strict=False)
        except ValueError:
            return

        # Если /31 или /32 — ipaddress.ip_network.hosts() вернёт 1-2 адреса
        hosts = list(net.hosts())
        if not hosts:
            # /32 — единственный адрес
            hosts = [net.network_address]

        bulk = []
        existing = set(
            IpAddress.objects.filter(subnet=subnet).values_list("ip", flat=True)
        )
        for host in hosts:
            ip_str = str(host)
            if ip_str in existing:
                continue
            bulk.append(IpAddress(
                subnet=subnet, ip=ip_str, state=IpAddress.STATE_UNKNOWN,
            ))

        if bulk:
            IpAddress.objects.bulk_create(bulk, batch_size=500)
