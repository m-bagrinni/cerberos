import re
from django.core.management.base import BaseCommand
from apps.stations.models import OpticalLink, OpticalClosure

class Command(BaseCommand):
    help = "Импорт оптических муфт из текстового файла"

    def add_arguments(self, parser):
        parser.add_argument("file", type=str, help="Путь к файлу")
        parser.add_argument("--link-id", type=int, required=True, help="ID OpticalLink")

    def handle(self, *args, **options):
        file_path = options['file']
        link_id = options['link_id']
        
        try:
            link = OpticalLink.objects.get(id=link_id)
        except OpticalLink.DoesNotExist:
            self.stderr.write(f"❌ OpticalLink с ID {link_id} не найден")
            return

        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        count = 0
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            # Ищем два числа с плавающей точкой (широта, долгота)
            nums = re.findall(r'-?\d+\.\d+', line)
            if len(nums) < 2:
                self.stderr.write(f"⚠️ Пропущена строка (нет координат): {line}")
                continue
            
            lat, lon = float(nums[0]), float(nums[1])
            
            # Имя муфты — всё, что до координат
            name_match = re.match(r'([^:]+):', line)
            if name_match:
                name = name_match.group(1).strip()
            else:
                parts = line.split(',')
                name = parts[2].strip() if len(parts) > 2 else f"Муфта {count+1}"

            OpticalClosure.objects.update_or_create(
                link=link,
                name=name,
                defaults={'lat': lat, 'lon': lon}
            )
            count += 1
            self.stdout.write(f"  ✅ {name}: {lat}, {lon}")

        self.stdout.write(self.style.SUCCESS(f"Импортировано {count} муфт для {link}"))
