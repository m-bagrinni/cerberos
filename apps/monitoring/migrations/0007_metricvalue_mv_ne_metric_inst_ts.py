# Индекс под запросы «последнее значение метрики NE по порту»
# (DDM в /api/v1/optical-links/, мультиплексоры, порты станций).
# CONCURRENTLY — чтобы не блокировать запись опросчиков Celery.

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('devices', '0017_device_sort_order'),
        ('monitoring', '0006_interfacestatushistory'),
    ]

    operations = [
        AddIndexConcurrently(
            model_name='metricvalue',
            index=models.Index(fields=['ne_device', 'metric', 'instance', '-ts'], name='mv_ne_metric_inst_ts'),
        ),
    ]
