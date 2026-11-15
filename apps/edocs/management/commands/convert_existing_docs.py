import os
from django.core.management.base import BaseCommand
from django.core.files import File
from apps.edocs.models import Document
from apps.edocs.utils import convert_to_pdf

class Command(BaseCommand):
    help = 'Конвертирует все существующие документы в PDF (если еще не сконвертированы)'

    def handle(self, *args, **options):
        docs = Document.objects.all()
        total = docs.count()
        converted = 0
        
        self.stdout.write(f"Найдено документов: {total}")
        
        for doc in docs:
            if not doc.file or doc.pdf_file:
                continue
                
            self.stdout.write(f"Обработка: {doc.title} ({doc.file.name})...", ending=' ')
            
            pdf_path = convert_to_pdf(doc.file.path)
            if pdf_path and pdf_path != doc.file.path:
                with open(pdf_path, 'rb') as f:
                    doc.pdf_file.save(os.path.basename(pdf_path), File(f), save=True)
                os.remove(pdf_path)
                converted += 1
                self.stdout.write(self.style.SUCCESS("OK"))
            else:
                self.stdout.write(self.style.WARNING("SKIP (не удалось конвертировать или уже PDF/картинка)"))
                
        self.stdout.write(self.style.SUCCESS(f"Готово. Сконвертировано: {converted}"))
