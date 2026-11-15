"""Seed предустановленных ролей и разрешений."""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from apps.accounts.models import Permission, Role, UserRole


# (code, name, group)
PERMISSIONS = [
    # Devices
    ("device.view", "Просмотр устройств", "device"),
    ("device.create", "Создание устройств", "device"),
    ("device.update", "Редактирование устройств", "device"),
    ("device.delete", "Удаление устройств", "device"),
    ("device.poll", "Ручной poll", "device"),
    # Profiles
    ("profile.view", "Просмотр профилей", "profile"),
    ("profile.create", "Создание профилей", "profile"),
    ("profile.update", "Редактирование профилей", "profile"),
    ("profile.delete", "Удаление профилей", "profile"),
    ("profile.clone", "Клонирование профилей", "profile"),
    ("profile.test_oid", "Тест OID", "profile"),
    # Metrics
    ("metric.create", "Создание метрик", "metric"),
    ("metric.update", "Редактирование метрик", "metric"),
    ("metric.delete", "Удаление метрик", "metric"),
    # Vendors
    ("vendor.view", "Просмотр вендоров", "vendor"),
    ("vendor.create", "Создание вендоров", "vendor"),
    ("vendor.update", "Редактирование вендоров", "vendor"),
    ("vendor.delete", "Удаление вендоров", "vendor"),
    # NE
    ("ne.view", "Просмотр NE", "ne"),
    ("ne.rename", "Переименование NE", "ne"),
    # Subnets
    ("subnet.view", "Просмотр подсетей", "subnet"),
    ("subnet.create", "Создание подсетей", "subnet"),
    ("subnet.update", "Редактирование подсетей", "subnet"),
    ("subnet.delete", "Удаление подсетей", "subnet"),
    ("subnet.scan", "Сканирование подсетей", "subnet"),
    # Alerts
    ("alert.view", "Просмотр алертов", "alert"),
    ("alert.resolve", "Разрешение алертов", "alert"),
    # Users
    ("user.view", "Просмотр пользователей", "user"),
    ("user.create", "Создание пользователей", "user"),
    ("user.update", "Редактирование пользователей", "user"),
    ("user.delete", "Удаление пользователей", "user"),
    ("user.manage_roles", "Управление ролями", "user"),
    # System
    ("services.view", "Просмотр служб", "system"),
    ("services.manage", "Управление контейнерами/задачами", "system"),
    ("admin.access", "Доступ в админку", "system"),
]


# (slug, name, permissions_codes)
ROLES = [
    ("admin", "Администратор", "__all__"),
    ("operator", "Оператор", [
        # view
        "device.view", "profile.view", "vendor.view",
        "ne.view", "subnet.view", "alert.view",
        "user.view", "services.view",
        # actions
        "device.create", "device.update", "device.poll",
        "profile.create", "profile.update", "profile.clone", "profile.test_oid",
        "metric.create", "metric.update",
        "vendor.create", "vendor.update",
        "ne.rename",
        "subnet.create", "subnet.update", "subnet.scan",
        "alert.resolve",
        "admin.access",
    ]),
    ("technician", "Техник", [
        # view
        "device.view", "profile.view", "vendor.view",
        "ne.view", "subnet.view", "alert.view",
        # limited
        "device.update", "device.poll",
        "profile.update", "profile.test_oid",
        "metric.update",
        "ne.rename",
        "subnet.update", "subnet.scan",
        "admin.access",
    ]),
    ("viewer", "Наблюдатель", [
        "device.view", "profile.view", "vendor.view",
        "ne.view", "subnet.view", "alert.view",
        "services.view", "admin.access",
    ]),
]


class Command(BaseCommand):
    help = "Создать предустановленные роли и разрешения"

    def add_arguments(self, parser):
        parser.add_argument("--skip-migrate-users", action="store_true",
                            help="Не назначать роли существующим пользователям")

    def handle(self, *args, **opts):
        # 1. Permissions
        self.stdout.write("=== Permissions ===")
        for code, name, group in PERMISSIONS:
            obj, created = Permission.objects.update_or_create(
                code=code,
                defaults={"name": name, "group": group},
            )
            self.stdout.write(f"  {'OK' if created else 'UPD'} {code}")

        # 2. Roles
        self.stdout.write("\n=== Roles ===")
        for slug, name, perms in ROLES:
            role, created = Role.objects.update_or_create(
                slug=slug,
                defaults={"name": name, "is_system": True},
            )
            if perms == "__all__":
                role.permissions.set(Permission.objects.all())
            else:
                role.permissions.set(
                    Permission.objects.filter(code__in=perms)
                )
            self.stdout.write(
                f"  {'OK' if created else 'UPD'} {slug} ({role.permissions.count()} perms)"
            )

        # 3. Назначаем роли существующим пользователям (если нет ролей)
        if not opts["skip_migrate_users"]:
            self.stdout.write("\n=== Migrate existing users ===")
            User = get_user_model()
            for u in User.objects.all():
                if u.user_roles.exists():
                    continue
                if u.is_superuser:
                    role_slug = "admin"
                elif u.is_staff:
                    role_slug = "operator"
                else:
                    role_slug = "viewer"
                role = Role.objects.get(slug=role_slug)
                UserRole.objects.create(user=u, role=role)
                self.stdout.write(f"  {u.username} → {role_slug}")

        self.stdout.write(self.style.SUCCESS("\nDone"))
