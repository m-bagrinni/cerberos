import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("alerts", "0006_alert_resolution"),
    ]

    operations = [
        migrations.AddField(
            model_name="alert",
            name="suppressed_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name="suppressed", to="alerts.alert", verbose_name="Подавлен алертом"),
        ),
        migrations.AddField(
            model_name="alert",
            name="notify_held_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="alert",
            index=models.Index(fields=["state", "suppressed_by"], name="alerts_aler_state_57ac18_idx"),
        ),
    ]
