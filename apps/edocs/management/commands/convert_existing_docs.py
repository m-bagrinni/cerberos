from django.core.management.base import BaseCommand

from apps.edocs.models import Attachment
from apps.edocs.services import ensure_pdf


class Command(BaseCommand):
    help = "Заранее готовит PDF для просмотра офисных вложений (иначе — при первом открытии)"

    def handle(self, *args, **options):
        qs = Attachment.objects.filter(pdf_file="")
        done = 0
        for att in qs:
            if att.ext not in Attachment.CONVERT_EXT:
                continue
            self.stdout.write(f"{att.name} … ", ending="")
            if ensure_pdf(att):
                done += 1
                self.stdout.write(self.style.SUCCESS("OK"))
            else:
                self.stdout.write(self.style.WARNING("не удалось"))
        self.stdout.write(self.style.SUCCESS(f"Готово: {done}"))
