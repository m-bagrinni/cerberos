from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("stations", "0010_alter_opticallink_link_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="station",
            name="is_hub",
            field=models.BooleanField(default=False, verbose_name="Центр мониторинга"),
        ),
    ]
