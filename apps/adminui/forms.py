from apps.stations.models import OpticalClosure
"""Формы админ-панели."""
from django import forms

from apps.devices.models import Device, DeviceProfile, MetricDefinition, Vendor
from apps.subnets.models import Subnet
from apps.core.models import Announcement


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
            "name", "ip", "category", "snmp_version", "snmp_community",
            "snmp_port", "vendor", "profile", "profile_override",
            "ne_id", "ne_gateway_ip",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "aui-input", "placeholder": "ag.LAZ"}),
            "ip": forms.TextInput(attrs={"class": "aui-input mono", "placeholder": "172.16.101.2"}),
            "category": forms.Select(attrs={"class": "aui-input"}),
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
            "category": "Категория",
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
    """Форма пользователя. Права задаются отдельно — матрицей доступа (см. views + access.py).

    «Суперпользователь» и «Staff» видит и меняет только суперпользователь: иначе любой,
    кто управляет пользователями, мог бы сделать себя суперпользователем.
    """
    new_password = forms.CharField(
        required=False,
        label="Новый пароль",
        widget=forms.PasswordInput(attrs={
            "class": "aui-input",
            "placeholder": "оставь пустым — не менять",
            "autocomplete": "new-password",
        }),
    )

    class Meta:
        from apps.accounts.models import User
        model = User
        fields = [
            "username", "email", "last_name", "first_name", "middle_name",
            "position_ref", "department", "work_phone", "mobile_phone",
            "is_active", "is_staff", "is_superuser",
        ]
        widgets = {
            "username": forms.TextInput(attrs={"class": "aui-input"}),
            "email": forms.EmailInput(attrs={"class": "aui-input"}),
            "first_name": forms.TextInput(attrs={"class": "aui-input"}),
            "last_name": forms.TextInput(attrs={"class": "aui-input"}),
            "middle_name": forms.TextInput(attrs={"class": "aui-input"}),
            "position_ref": forms.Select(attrs={"class": "aui-input"}),
            "department": forms.TextInput(attrs={"class": "aui-input"}),
            "work_phone": forms.TextInput(attrs={"class": "aui-input"}),
            "mobile_phone": forms.TextInput(attrs={"class": "aui-input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "aui-checkbox"}),
            "is_staff": forms.CheckboxInput(attrs={"class": "aui-checkbox"}),
            "is_superuser": forms.CheckboxInput(attrs={"class": "aui-checkbox"}),
        }
        labels = {
            "username": "Логин",
            "email": "Email",
            "first_name": "Имя",
            "last_name": "Фамилия",
            "position_ref": "Должность",
            "is_active": "Активен (может входить)",
            "is_staff": "Staff (вход в /admin/ Django)",
            "is_superuser": "Суперпользователь (все права без ограничений)",
        }

    def __init__(self, *args, request_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["position_ref"].help_text = "Название должности — для справочника сотрудников. На права не влияет."
        if not (request_user and request_user.is_superuser):
            self.fields.pop("is_staff")
            self.fields.pop("is_superuser")
        if request_user and self.instance.pk == getattr(request_user, "pk", None):
            # Себя не деактивировать и не снять с себя суперпользователя случайно
            self.fields.pop("is_active", None)
            self.fields.pop("is_superuser", None)

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
        widget=forms.PasswordInput(attrs={"class": "aui-input", "autocomplete": "new-password"}),
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


class ClosureForm(forms.ModelForm):
    class Meta:
        model = OpticalClosure
        fields = ("link", "name", "lat", "lon", "description")
        widgets = {
            "link": forms.Select(attrs={"class": "form-select"}),
            "name": forms.TextInput(attrs={"class": "form-input", "placeholder": "Например, Муфта 1"}),
            "lat": forms.NumberInput(attrs={"class": "form-input", "step": "0.00001", "placeholder": "42.45678"}),
            "lon": forms.NumberInput(attrs={"class": "form-input", "step": "0.00001", "placeholder": "76.12345"}),
            "description": forms.Textarea(attrs={"class": "form-input", "rows": 3}),
        }


class StationForm(forms.ModelForm):
    """Форма станции с выбором устройств."""
    devices = forms.ModelMultipleChoiceField(
        queryset=Device.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "aui-input", "size": 10}),
        label="Устройства на станции",
        help_text="Выберите устройства, которые физически находятся на этой станции",
    )

    class Meta:
        from apps.stations.models import Station
        model = Station
        fields = ("name", "slug", "latitude", "longitude", "sort_order", "devices")
        widgets = {
            "name": forms.TextInput(attrs={"class": "aui-input"}),
            "slug": forms.TextInput(attrs={"class": "aui-input"}),
            "latitude": forms.NumberInput(attrs={"class": "aui-input", "step": "0.000001"}),
            "longitude": forms.NumberInput(attrs={"class": "aui-input", "step": "0.000001"}),
            "sort_order": forms.NumberInput(attrs={"class": "aui-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Если редактируем станцию — предзаполняем devices
        if self.instance and self.instance.pk:
            self.fields['devices'].initial = [
                sd.device for sd in self.instance.devices.all()
            ]


class DeviceCategoryForm(forms.ModelForm):
    """Форма для категории устройств."""
    class Meta:
        from apps.devices.models import DeviceCategory
        model = DeviceCategory
        fields = ["slug", "name", "icon", "sort_order", "is_active"]
        widgets = {
            "slug": forms.TextInput(attrs={"class": "aui-input", "placeholder": "core"}),
            "name": forms.TextInput(attrs={"class": "aui-input", "placeholder": "Верхний уровень"}),
            "icon": forms.HiddenInput(),
            "sort_order": forms.NumberInput(attrs={"class": "aui-input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "aui-checkbox"}),
        }
        labels = {
            "slug": "Slug",
            "name": "Название",
            "icon": "Иконка (Bootstrap Icons)",
            "sort_order": "Порядок сортировки",
            "is_active": "Активна",
        }



class AnnouncementForm(forms.ModelForm):
    """Объявление на главной."""

    class Meta:
        model = Announcement
        fields = ["title", "text", "level", "is_pinned", "is_active", "starts_at", "ends_at"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "aui-input", "placeholder": "Плановые работы на ст. …"}),
            "text": forms.Textarea(attrs={"class": "aui-input", "rows": 5}),
            "level": forms.Select(attrs={"class": "aui-input"}),
            "is_pinned": forms.CheckboxInput(attrs={"class": "aui-checkbox"}),
            "is_active": forms.CheckboxInput(attrs={"class": "aui-checkbox"}),
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local", "class": "aui-input"},
                                             format="%Y-%m-%dT%H:%M"),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local", "class": "aui-input"},
                                           format="%Y-%m-%dT%H:%M"),
        }

    def clean(self):
        data = super().clean()
        s, e = data.get("starts_at"), data.get("ends_at")
        if s and e and e <= s:
            self.add_error("ends_at", "Окончание должно быть позже начала")
        return data
