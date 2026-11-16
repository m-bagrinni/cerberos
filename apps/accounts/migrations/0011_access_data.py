"""Шаблоны доступа + перенос текущих (фактических) прав пользователей в новую модель.

Фактические права до переделки:
  - суперпользователь — всё;
  - уровень должности >= 80 — всё (роли не читались). «Пользователи и права» оставляем
    только тем, у кого роль «Администратор»;
  - ниже 80 — модули из должности: «Локальная сеть» только просмотр (запись требовала
    device.update, который давал лишь уровень 80+), «Серверы» — изменение, если должность
    не «только просмотр», «Документы» — изменение.
"""
from django.db import migrations

SECTIONS = ["devices", "alerts", "stations", "metrics", "mux", "subnets", "analytics",
            "servers", "edocs", "services"]
LOCAL = ["devices", "alerts", "stations", "metrics", "mux", "subnets", "analytics"]
ALL_CAPS = ["port_control", "traffic_capture", "snmp_secrets", "announcements", "users_manage"]


def forwards(apps, schema_editor):
    from apps.accounts.access import DEFAULT_PRESETS
    AccessPreset = apps.get_model("accounts", "AccessPreset")
    User = apps.get_model("accounts", "User")
    UserRole = apps.get_model("accounts", "UserRole")
    Position = apps.get_model("accounts", "Position")

    for i, (name, descr, sections, caps) in enumerate(DEFAULT_PRESETS):
        AccessPreset.objects.update_or_create(
            name=name, defaults={"description": descr, "sections": sections, "caps": caps, "sort_order": i},
        )

    for u in User.objects.all():
        pos = Position.objects.filter(pk=u.position_ref_id).first() if u.position_ref_id else None
        level = pos.access_level if pos else 0
        mods = [m.strip() for m in (pos.modules or "").split(",") if m.strip()] if pos else []
        if pos and not mods:
            mods = ["local", "servers", "edocs"]
        readonly = bool(pos and pos.is_readonly)
        roles = set(UserRole.objects.filter(user=u).values_list("role__slug", flat=True))

        sec = {k: 0 for k in SECTIONS}
        caps = []
        if u.is_superuser or level >= 80:
            sec = {k: 2 for k in SECTIONS}
            caps = [c for c in ALL_CAPS if c != "users_manage"]
            if u.is_superuser or "admin" in roles:
                caps.append("users_manage")
        else:
            if "local" in mods:
                for k in LOCAL:
                    sec[k] = 1
            if "servers" in mods:
                sec["servers"] = 1 if readonly else 2
            if "edocs" in mods:
                sec["edocs"] = 2
        u.access_sections = sec
        u.access_caps = caps
        u.save(update_fields=["access_sections", "access_caps"])


class Migration(migrations.Migration):
    dependencies = [("accounts", "0010_user_access")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
