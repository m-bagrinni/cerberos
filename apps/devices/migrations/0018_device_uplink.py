import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0017_device_sort_order"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="uplink",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="downlinks", to="devices.device", verbose_name="Вышестоящее устройство",
                help_text="Через какое устройство это доступно. Если оно упадёт — алерты этого устройства "
                          "не будут слать уведомлений, а свернутся под алертом вышестоящего."),
        ),
    ]
