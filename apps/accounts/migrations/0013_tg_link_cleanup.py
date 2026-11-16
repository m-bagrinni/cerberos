"""Коды привязки теперь хранятся хэшем — старые (открытым текстом) удаляем.
telegram_id приводим к фактическим привязкам: вписанный вручную ID без привязки сбрасывается
(по нему СЭД отправлял документы — это был обход привязки)."""
from django.db import migrations


def forwards(apps, schema_editor):
    TelegramLinkCode = apps.get_model("accounts", "TelegramLinkCode")
    TelegramAccount = apps.get_model("accounts", "TelegramAccount")
    User = apps.get_model("accounts", "User")
    TelegramLinkCode.objects.all().delete()
    linked = dict(TelegramAccount.objects.filter(is_active=True).values_list("user_id", "tg_id"))
    for u in User.objects.all():
        want = linked.get(u.pk)
        if u.telegram_id != want:
            u.telegram_id = None
            u.save(update_fields=["telegram_id"])
    for uid, tg in linked.items():
        User.objects.filter(pk=uid).update(telegram_id=tg)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0012_tg_link_hash")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
