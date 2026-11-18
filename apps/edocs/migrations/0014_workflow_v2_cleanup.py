from django.db import migrations


class Migration(migrations.Migration):
    """Старые поля перенесены в Assignment / Attachment (0013)."""

    dependencies = [
        ("edocs", "0013_workflow_v2_data"),
    ]

    operations = [
        migrations.RemoveField(model_name="document", name="file"),
        migrations.RemoveField(model_name="document", name="pdf_file"),
        migrations.RemoveField(model_name="document", name="reply_file"),
        migrations.RemoveField(model_name="document", name="reply_comment"),
        migrations.RemoveField(model_name="document", name="resolution"),
        migrations.RemoveField(model_name="document", name="is_read"),
    ]
