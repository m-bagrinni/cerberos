import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0018_device_uplink"),
        ("subnets", "0005_ipaddress_fingerprint"),
    ]

    operations = [
        migrations.AddField(
            model_name="subnet",
            name="uplink",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="dependent_subnets", to="devices.device", verbose_name="Вышестоящее устройство",
                help_text="Коммутатор/маршрутизатор, через который доступна подсеть. Если он упадёт — "
                          "алерты «IP не отвечает» свернутся под его алертом."),
        ),
    ]
