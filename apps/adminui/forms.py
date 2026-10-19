"""Формы админ-панели."""
from django import forms

from apps.devices.models import Device, DeviceProfile, MetricDefinition, Vendor
from apps.subnets.models import Subnet


class DeviceFilterForm(forms.Form):
    """Фильтр для списка устройств."""
    q = forms.CharField(
        required=False,
        label="Поиск",
        widget=forms.TextInput(attrs={
            "placeholder": "имя, IP, модель…",
            "class": "aui-input",
        }),
    )
    vendor = forms.ModelChoiceField(
        queryset=Vendor.objects.order_by("name"),
        required=False,
        empty_label="Все вендоры",
        widget=forms.Select(attrs={"class": "aui-input"}),
    )
    category = forms.ChoiceField(
        required=False,
        choices=[
            ("", "Все категории"),
            ("switch", "Switch"),
            ("multiplexer", "Multiplexer"),
            ("router", "Router"),
            ("other", "Other"),
        ],
        widget=forms.Select(attrs={"class": "aui-input"}),
    )
    status = forms.ChoiceField(
        required=False,
        choices=[
            ("", "Все статусы"),
            ("ok", "OK"),
            ("error", "Error"),
            ("unknown", "Unknown"),
        ],
        widget=forms.Select(attrs={"class": "aui-input"}),
    )


class DeviceForm(forms.ModelForm):
    """Форма создания/редактирования устройства."""
    class Meta:
        model = Device
        fields = [
            "name", "ip", "snmp_version", "snmp_community",
            "snmp_port", "vendor", "profile", "profile_override",
            "ne_id", "ne_gateway_ip",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "aui-input", "placeholder": "ag.LAZ"}),
            "ip": forms.TextInput(attrs={"class": "aui-input mono", "placeholder": "172.16.101.2"}),
            "snmp_version": forms.Select(attrs={"class": "aui-input"}),
            "snmp_community": forms.TextInput(attrs={"class": "aui-input mono", "placeholder": "private"}),
            "snmp_port": forms.NumberInput(attrs={"class": "aui-input", "min": 1, "max": 65535}),
            "vendor": forms.Select(attrs={"class": "aui-input"}),
            "profile": forms.Select(attrs={"class": "aui-input"}),
            "profile_override": forms.Select(attrs={"class": "aui-input"}),
            "ne_id": forms.NumberInput(attrs={"class": "aui-input", "placeholder": "—"}),
            "ne_gateway_ip": forms.TextInput(attrs={"class": "aui-input mono", "placeholder": "—"}),
        }
        labels = {
            "name": "Имя",
            "ip": "IP-адрес",
            "snmp_version": "SNMP-версия",
            "snmp_community": "Community",
            "snmp_port": "SNMP-порт",
            "vendor": "Вендор",
            "profile": "Профиль",
            "profile_override": "Профиль (override)",
            "ne_id": "NE ID",
            "ne_gateway_ip": "NE Gateway IP",
        }

    def clean_ip(self):
        ip = self.cleaned_data["ip"]
        qs = Device.objects.filter(ip=ip)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Устройство с таким IP уже существует")
        return ip

    def clean_snmp_port(self):
        port = self.cleaned_data.get("snmp_port") or 161
        if not (1 <= port <= 65535):
            raise forms.ValidationError("Порт должен быть в диапазоне 1–65535")
        return port


class ProfileFilterForm(forms.Form):
    """Фильтр для списка профилей."""
    q = forms.CharField(
        required=False,
        label="Поиск",
        widget=forms.TextInput(attrs={"class": "aui-input", "placeholder": "имя, slug, вендор…"}),
    )
    vendor = forms.ModelChoiceField(
        queryset=Vendor.objects.order_by("name"),
        required=False,
        empty_label="Все вендоры",
        widget=forms.Select(attrs={"class": "aui-input"}),
    )
    category = forms.ChoiceField(
        required=False,
        choices=[
            ("", "Все категории"),
            ("switch", "Switch"),
            ("multiplexer", "Multiplexer"),
            ("router", "Router"),
            ("other", "Other"),
        ],
        widget=forms.Select(attrs={"class": "aui-input"}),
    )


class MetricDefinitionForm(forms.ModelForm):
    """Форма метрики профиля."""
    class Meta:
        model = MetricDefinition
        fields = [
            "key", "name", "oid", "value_type", "scale", "unit",
            "transform", "transform_params", "warn", "crit", "enabled",
        ]
        widgets = {
            "key": forms.TextInput(attrs={"class": "aui-input mono", "placeholder": "cpu_1m"}),
            "name": forms.TextInput(attrs={"class": "aui-input", "placeholder": "CPU 1 мин"}),
            "oid": forms.TextInput(attrs={"class": "aui-input mono", "placeholder": "1.3.6.1.4.1..."}),
            "value_type": forms.Select(attrs={"class": "aui-input"}),
            "scale": forms.Select(attrs={"class": "aui-input"}),
            "unit": forms.TextInput(attrs={"class": "aui-input", "placeholder": "%"}),
            "transform": forms.TextInput(attrs={"class": "aui-input mono", "placeholder": "(оставь пустым)"}),
            "transform_params": forms.Textarea(attrs={"class": "aui-input mono", "rows": 2, "placeholder": "{}"}),
            "warn": forms.NumberInput(attrs={"class": "aui-input", "step": "any"}),
            "crit": forms.NumberInput(attrs={"class": "aui-input", "step": "any"}),
            "enabled": forms.CheckboxInput(attrs={"class": "aui-checkbox"}),
        }
        labels = {
            "key": "Key",
            "name": "Название",
            "oid": "OID",
            "value_type": "Тип значения",
            "scale": "Режим опроса",
            "unit": "Единица",
            "transform": "Transform (необязательно)",
            "transform_params": "Параметры transform",
            "warn": "Warn",
            "crit": "Crit",
            "enabled": "Включена",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # transform — обязательное поле в модели, но для пользователя — нет
        # Если пусто — сохраняем как пустую строку
        if "transform" in self.fields:
            self.fields["transform"].required = False

    def clean_transform(self):
        return self.cleaned_data.get("transform") or ""

    def clean_oid(self):
        oid = self.cleaned_data["oid"].strip()
        if not oid:
            raise forms.ValidationError("OID обязателен")
        if not all(p.isdigit() for p in oid.lstrip(".").split(".")):
            raise forms.ValidationError("OID должен быть числовым: 1.3.6.1.4.1...")
        return oid.lstrip(".")

    def clean_key(self):
        key = self.cleaned_data["key"].strip()
        if not key:
            raise forms.ValidationError("Key обязателен")
        return key


class ProfileForm(forms.ModelForm):
    """Форма создания/редактирования профиля."""
    class Meta:
        model = DeviceProfile
        fields = [
            "vendor", "parent", "slug", "name", "priority",
            "device_category", "match_sysobject_prefixes", "match_model_regex", "match_sysdescr_regex",
        ]
        widgets = {
            "vendor": forms.Select(attrs={"class": "aui-input"}),
            "parent": forms.Select(attrs={"class": "aui-input"}),
            "slug": forms.TextInput(attrs={"class": "aui-input mono", "placeholder": "dgs-3630-28tc"}),
            "name": forms.TextInput(attrs={"class": "aui-input", "placeholder": "D-Link DGS-3630"}),
            "priority": forms.NumberInput(attrs={"class": "aui-input", "min": 0}),
            "device_category": forms.Select(attrs={"class": "aui-input"}),
            "match_sysobject_prefixes": forms.TextInput(attrs={"class": "aui-input mono"}),
            "match_model_regex": forms.TextInput(attrs={"class": "aui-input"}),
            "match_sysdescr_regex": forms.TextInput(attrs={"class": "aui-input"}),
        }
        labels = {
            "vendor": "Вендор",
            "parent": "Родительский профиль",
            "slug": "Slug",
            "name": "Название",
            "priority": "Приоритет",
            "device_category": "Категория",
            "match_sysobject_prefixes": "Match: sysObjectID",
            "match_model_regex": "Match: entModel",
            "match_sysdescr_regex": "Match: sysDescr",
        }

    def clean_slug(self):
        slug = self.cleaned_data["slug"].strip()
        if not slug:
            raise forms.ValidationError("Slug обязателен")
        qs = DeviceProfile.objects.filter(slug=slug)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Профиль с таким slug уже существует")
        return slug


class VendorForm(forms.ModelForm):
    """Форма вендора."""
    class Meta:
        model = Vendor
        fields = ["slug", "name", "enterprise_ids"]
        widgets = {
            "slug": forms.TextInput(attrs={"class": "aui-input mono", "placeholder": "d-link"}),
            "name": forms.TextInput(attrs={"class": "aui-input", "placeholder": "D-Link"}),
            "enterprise_ids": forms.Textarea(attrs={
                "class": "aui-input mono", "rows": 2,
                "placeholder": "[171, 12345]",
            }),
        }
        labels = {
            "slug": "Slug",
            "name": "Название",
            "enterprise_ids": "Enterprise IDs (JSON-массив)",
        }

    def clean_slug(self):
        slug = self.cleaned_data["slug"].strip()
        qs = Vendor.objects.filter(slug=slug)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Вендор с таким slug уже существует")
        return slug


class SubnetForm(forms.ModelForm):
    """Форма подсети."""
    class Meta:
        model = Subnet
        fields = [
            "cidr", "name", "description", "gateway_ip",
            "vlan_id", "is_active", "scan_interval_min",
        ]
        widgets = {
            "cidr": forms.TextInput(attrs={"class": "aui-input mono", "placeholder": "172.16.101.0/24"}),
            "name": forms.TextInput(attrs={"class": "aui-input", "placeholder": "MANAGEMENT"}),
            "description": forms.Textarea(attrs={"class": "aui-input", "rows": 2}),
            "gateway_ip": forms.TextInput(attrs={"class": "aui-input mono", "placeholder": "172.16.101.1"}),
            "vlan_id": forms.NumberInput(attrs={"class": "aui-input", "min": 1, "max": 4094}),
            "is_active": forms.CheckboxInput(attrs={"class": "aui-checkbox"}),
            "scan_interval_min": forms.NumberInput(attrs={"class": "aui-input", "min": 1, "max": 1440}),
        }
        labels = {
            "cidr": "CIDR",
            "name": "Название",
            "description": "Описание",
            "gateway_ip": "Gateway IP",
            "vlan_id": "VLAN ID",
            "is_active": "Активна",
            "scan_interval_min": "Интервал скана (мин)",
        }


# ============================================================
# Users
# ============================================================

class UserForm(forms.ModelForm):
    """Форма редактирования пользователя."""
    new_password = forms.CharField(
        required=False,
        label="Новый пароль",
        widget=forms.PasswordInput(attrs={
            "class": "aui-input",
            "placeholder": "оставь пустым — не менять",
        }),
    )

    class Meta:
        from apps.devices.models import User
        model = User
        fields = [
            "username", "email", "first_name", "last_name",
            "telegram_id", "is_staff", "is_active", "is_superuser",
        ]
        widgets = {
            "username": forms.TextInput(attrs={"class": "aui-input"}),
            "email": forms.EmailInput(attrs={"class": "aui-input"}),
            "first_name": forms.TextInput(attrs={"class": "aui-input"}),
            "last_name": forms.TextInput(attrs={"class": "aui-input"}),
            "telegram_id": forms.NumberInput(attrs={"class": "aui-input", "placeholder": "123456789"}),
            "is_staff": forms.CheckboxInput(attrs={"class": "aui-checkbox"}),
            "is_active": forms.CheckboxInput(attrs={"class": "aui-checkbox"}),
            "is_superuser": forms.CheckboxInput(attrs={"class": "aui-checkbox"}),
        }
        labels = {
            "username": "Username",
            "email": "Email",
            "first_name": "Имя",
            "last_name": "Фамилия",
            "telegram_id": "Telegram ID",
            "is_staff": "Staff (доступ в админку)",
            "is_active": "Активен",
            "is_superuser": "Superuser",
        }

    def save(self, commit=True):
        user = super().save(commit=False)
        pwd = self.cleaned_data.get("new_password")
        if pwd:
            user.set_password(pwd)
        if commit:
            user.save()
        return user


class UserCreateForm(UserForm):
    """Форма создания пользователя (пароль обязателен)."""
    new_password = forms.CharField(
        required=True,
        label="Пароль",
        widget=forms.PasswordInput(attrs={"class": "aui-input"}),
    )


class AlertFilterForm(forms.Form):
    """Фильтр для списка алертов."""
    q = forms.CharField(
        required=False,
        label="Поиск",
        widget=forms.TextInput(attrs={
            "class": "aui-input",
            "placeholder": "метрика, сообщение…",
        }),
    )
    severity = forms.ChoiceField(
        required=False,
        choices=[
            ("", "Все severity"),
            ("crit", "Crit"),
            ("warn", "Warn"),
        ],
        widget=forms.Select(attrs={"class": "aui-input"}),
    )
    state = forms.ChoiceField(
        required=False,
        choices=[
            ("", "Все состояния"),
            ("firing", "Firing"),
            ("ok", "Ok"),
        ],
        widget=forms.Select(attrs={"class": "aui-input"}),
    )
    entity = forms.ChoiceField(
        required=False,
        choices=[
            ("", "Все объекты"),
            ("device", "Устройство"),
            ("ne", "NE"),
            ("ip", "IP"),
        ],
        widget=forms.Select(attrs={"class": "aui-input"}),
    )
