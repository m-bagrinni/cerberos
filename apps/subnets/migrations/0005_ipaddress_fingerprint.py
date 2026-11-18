from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subnets", "0004_broadcast_storm_event"),
    ]

    operations = [
        migrations.AddField(
            model_name="ipaddress",
            name="fingerprint",
            field=models.JSONField(blank=True, default=dict, null=True),
        ),
    ]
