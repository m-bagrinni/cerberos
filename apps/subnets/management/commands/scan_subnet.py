"""
Management-команда: сканирование подсети.

Примеры:
    python manage.py scan_subnet 172.16.101.0/29
    python manage.py scan_subnet --all
    python manage.py scan_subnet 172.16.101.0/29 --sync
"""

from django.core.management.base import BaseCommand, CommandError

from apps.subnets.models import Subnet
from apps.subnets.scanner import scan_subnet as scan_subnet_sync
from apps.subnets.tasks import scan_subnet_task


class Command(BaseCommand):
    help = "Сканирует подсеть через fping (обновляет IpAddress)"

    def add_arguments(self, parser):
        parser.add_argument(
            "cidr", nargs="?", type=str,
            help="CIDR подсети, например 172.16.101.0/29",
        )
        parser.add_argument(
            "--all", action="store_true",
            help="Сканировать все активные подсети",
        )
        parser.add_argument(
            "--sync", action="store_true",
            help="Запустить сканер синхронно (для отладки, без Celery)",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Только показать, что будет сделано",
        )

    def handle(self, *args, **opts):
        if opts["all"]:
            subnets = list(Subnet.objects.filter(is_active=True))
            if not subnets:
                raise CommandError("Нет активных подсетей")
            for s in subnets:
                self._run_one(s, opts)
            return

        cidr = opts.get("cidr")
        if not cidr:
            raise CommandError("Укажи CIDR или --all")

        try:
            subnet = Subnet.objects.get(cidr=cidr)
        except Subnet.DoesNotExist:
            raise CommandError(f"Подсеть {cidr} не найдена в БД. Создай её через админку.")

        self._run_one(subnet, opts)

    def _run_one(self, subnet: Subnet, opts):
        self.stdout.write(f"→ {subnet.cidr} (id={subnet.pk})")

        if opts["dry_run"]:
            self.stdout.write("  (dry-run, пропуск)")
            return

        if opts["sync"]:
            self.stdout.write("  синхронно...")
            r = scan_subnet_sync(subnet.cidr)
            if r.error:
                self.stdout.write(self.style.ERROR(f"  ошибка: {r.error}"))
                return
            self.stdout.write(self.style.SUCCESS(
                f"  alive={len(r.alive_ips)} из {r.hosts_total}"
            ))
            for ip in r.alive_ips:
                rtt = r.rtt_map.get(ip)
                rtt_s = f"{rtt:.2f} ms" if rtt is not None else "?"
                self.stdout.write(f"    {ip}  {rtt_s}")
        else:
            task = scan_subnet_task.delay(subnet.pk)
            self.stdout.write(self.style.SUCCESS(
                f"  задача отправлена, task_id={task.id}"
            ))
