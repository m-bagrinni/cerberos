from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_user'),
        ('devices', '0009_user_approved_at_user_approved_by_and_more'),  # замените на последнюю миграцию devices!
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            -- Копируем пользователей из devices_user в accounts_user (сохраняя ID)
            INSERT INTO accounts_user (
                id, password, last_login, is_superuser, username, first_name, last_name,
                email, is_staff, is_active, date_joined, telegram_id,
                registration_status, approved_at, approved_by_id, registration_note
            )
            SELECT
                id, password, last_login, is_superuser, username, first_name, last_name,
                email, is_staff, is_active, date_joined, telegram_id,
                registration_status, approved_at, approved_by_id, registration_note
            FROM devices_user
            ON CONFLICT (id) DO NOTHING;
            """,
            reverse_sql="DELETE FROM accounts_user;"
        ),
    ]
