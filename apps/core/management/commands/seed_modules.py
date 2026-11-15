"""Seed предустановленных модулей."""
from django.core.management.base import BaseCommand
from apps.core.models import Module


MODULES = [
    {
        "slug": "local",
        "name": "Локальная сеть",
        "description": "SNMP-мониторинг: устройства, метрики, SFP, алерты, карта",
        "icon": "bi-globe2",
        "color": "#ff780a",
        "url_prefix": "/local/",
        "is_installed": True,
        "sort_order": 10,
    },
    {
        "slug": "hwu",
        "name": "Huawei CC08",
        "description": "Технический учёт: линии, номера, станции",
        "icon": "bi-telephone",
        "color": "#0abfbc",
        "url_prefix": "/hwu/",
        "is_installed": False,
        "sort_order": 20,
    },
    {
        "slug": "edocs",
        "name": "E-Docs",
        "description": "Электронный документооборот: загрузка, хранение, согласование",
        "icon": "bi-file-earmark-text",
        "color": "#73bf69",
        "url_prefix": "/edocs/",
        "is_installed": True,
        "sort_order": 30,
    },
]


class Command(BaseCommand):
    help = "Создать предустановленные модули"

    def handle(self, *args, **opts):
        for m in MODULES:
            obj, created = Module.objects.update_or_create(
                slug=m["slug"],
                defaults=m,
            )
            self.stdout.write(
                f"  {'OK' if created else 'UPD'} {obj.slug} — {obj.name}"
            )
        self.stdout.write(self.style.SUCCESS("Done"))
