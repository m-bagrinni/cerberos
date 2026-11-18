import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0018_device_uplink"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="uplink_ne",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="dependent_devices", to="devices.nedevice", verbose_name="Вышестоящий NE",
                help_text="NE мультиплексора, через который подключено устройство. Обрыв оптики до этого NE "
                          "свернёт и алерты устройства."),
        ),
    ]
