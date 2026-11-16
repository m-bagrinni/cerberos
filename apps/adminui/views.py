from django.views import View
from django.urls import reverse_lazy
from .forms import ClosureForm
"""Кастомная админ-панель Cerberus."""
from apps.accounts.permissions import (
    PermissionRequiredMixin, get_user_permissions, has_perm,
)
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import (
    CreateView, DeleteView, ListView, TemplateView, UpdateView,
)

from apps.devices.models import (
    Device, DeviceProfile, MetricDefinition, NeDevice, Vendor, DeviceCategory,
)
from apps.accounts.models import User
from apps.monitoring.models import MetricValue
from apps.alerts.models import Alert
from apps.subnets.models import Subnet, IpAddress

from .forms import (
    DeviceFilterForm,
    DeviceForm,
    MetricDefinitionForm,
    AlertFilterForm,
    ProfileFilterForm,
    ProfileForm,
    SubnetForm,
    VendorForm,
    StationForm,
    DeviceCategoryForm,
)


# ============================================================
# Dashboard
# ============================================================

class DashboardView(PermissionRequiredMixin, TemplateView):
    permission_required = "admin.access"
    template_name = "adminui/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["stats"] = {
            "devices": Device.objects.count(),
            "profiles": DeviceProfile.objects.count(),
            "vendors": Vendor.objects.count(),
            "ne": NeDevice.objects.count(),
            "metrics": MetricValue.objects.count(),
            "alerts_firing": Alert.objects.filter(state="firing").count(),
            "alerts_crit": Alert.objects.filter(state="firing", severity="crit").count(),
            "subnets": Subnet.objects.count(),
            "ips_alive": IpAddress.objects.filter(is_alive=True).count(),
        }
        ctx["recent_alerts"] = Alert.objects.filter(state="firing").order_by("-started_at")[:8]
        ctx["devices"] = Device.objects.order_by("name")[:20]
        return ctx


# ============================================================
# Devices — CRUD
# ============================================================

class DeviceListView(PermissionRequiredMixin, ListView):
    permission_required = "device.view"
    model = Device
    template_name = "adminui/devices/list.html"
    context_object_name = "devices"
    paginate_by = 50

    def get_queryset(self):
        qs = Device.objects.select_related("vendor", "profile").order_by("name")
        form = DeviceFilterForm(self.request.GET or None)
        if form.is_valid():
            q = form.cleaned_data.get("q")
            vendor = form.cleaned_data.get("vendor")
            category = form.cleaned_data.get("category")
            status = form.cleaned_data.get("status")
            if q:
                qs = qs.filter(
                    Q(name__icontains=q) | Q(ip__icontains=q)
                    | Q(ent_model__icontains=q) | Q(sys_descr__icontains=q)
                )
            if vendor:
                qs = qs.filter(vendor=vendor)
            if category:
                qs = qs.filter(profile__device_category=category)
            if status:
                qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter_form"] = DeviceFilterForm(self.request.GET or None)
        ctx["total"] = self.get_queryset().count()
        return ctx


class DeviceDetailView(PermissionRequiredMixin, TemplateView):
    permission_required = "device.view"
    template_name = "adminui/devices/detail.html"

    def get_context_data(self, **kwargs):
        import json
        from datetime import timedelta
        from django.utils import timezone

        ctx = super().get_context_data(**kwargs)
        device = get_object_or_404(Device, pk=kwargs["pk"])
        ctx["device"] = device

        metrics_dict = {}
        if device.profile:
            metrics_dict = device.profile.get_effective_metrics() or {}
        ctx["metrics"] = list(metrics_dict.values())

        since = timezone.now() - timedelta(hours=24)
        rows = (
            MetricValue.objects
            .filter(device=device, ts__gte=since, metric__key__in=list(metrics_dict.keys()))
            .values("metric__key", "ts", "value")
            .order_by("ts")
        )

        charts = {k: [] for k in metrics_dict.keys()}
        for r in rows:
            k = r["metric__key"]
            if k in charts:
                charts[k].append({"ts": int(r["ts"].timestamp() * 1000), "value": r["value"]})

        ctx["charts_json"] = json.dumps(charts, ensure_ascii=False)
        ctx["metrics_meta_json"] = json.dumps({
            k: {"name": m.name, "unit": getattr(m, "unit", "") or ""}
            for k, m in metrics_dict.items()
        }, ensure_ascii=False)
        return ctx


class DeviceCreateView(PermissionRequiredMixin, CreateView):
    permission_required = "device.create"
    model = Device
    form_class = DeviceForm
    template_name = "adminui/devices/form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = "Новое устройство"
        ctx["form_submit"] = "Создать"
        ctx["device"] = None
        return ctx

    def get_success_url(self):
        return reverse("adminui:device_detail", args=[self.object.pk])


class DeviceUpdateView(PermissionRequiredMixin, UpdateView):
    permission_required = "device.update"
    model = Device
    form_class = DeviceForm
    template_name = "adminui/devices/form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = f"Редактирование: {self.object.name}"
        ctx["form_submit"] = "Сохранить"
        ctx["device"] = self.object
        return ctx

    def get_success_url(self):
        return reverse("adminui:device_detail", args=[self.object.pk])


class DeviceDeleteView(PermissionRequiredMixin, DeleteView):
    permission_required = "device.delete"
    model = Device
    template_name = "adminui/devices/delete_confirm.html"

    def get_success_url(self):
        return reverse("adminui:devices")


# ============================================================
# Profiles — list + placeholders (детали — Этап 3.2)
# ============================================================

class ProfileListView(PermissionRequiredMixin, ListView):
    permission_required = "profile.view"
    model = DeviceProfile
    template_name = "adminui/profiles/list.html"
    context_object_name = "profiles"
    paginate_by = 50

    def get_queryset(self):
        qs = DeviceProfile.objects.select_related("vendor", "parent").order_by("vendor__name", "priority", "name")
        form = ProfileFilterForm(self.request.GET or None)
        if form.is_valid():
            q = form.cleaned_data.get("q")
            vendor = form.cleaned_data.get("vendor")
            category = form.cleaned_data.get("category")
            if q:
                qs = qs.filter(Q(name__icontains=q) | Q(slug__icontains=q))
            if vendor:
                qs = qs.filter(vendor=vendor)
            if category:
                qs = qs.filter(device_category=category)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter_form"] = ProfileFilterForm(self.request.GET or None)
        ctx["total"] = self.get_queryset().count()
        return ctx


class ProfileCreateView(PermissionRequiredMixin, CreateView):
    permission_required = "profile.create"
    model = DeviceProfile
    form_class = ProfileForm
    template_name = "adminui/profiles/form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = "Новый профиль"
        ctx["form_submit"] = "Создать"
        ctx["profile"] = None
        return ctx

    def get_success_url(self):
        return reverse("adminui:profile_detail", args=[self.object.pk])


class ProfileDetailView(PermissionRequiredMixin, TemplateView):
    """Карточка профиля + inline-таблица метрик."""
    permission_required = "profile.view"
    template_name = "adminui/profiles/detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        profile = get_object_or_404(DeviceProfile, pk=kwargs["pk"])
        ctx["profile"] = profile
        ctx["profile_form"] = ProfileForm(instance=profile)
        ctx["metrics"] = profile.metrics.order_by("key")
        ctx["devices"] = profile.devices.select_related("vendor").order_by("name")

        # Тестовое устройство (первое из этого профиля) — для кнопки «Тест OID»
        ctx["test_device"] = ctx["devices"].first()

        # Есть ли дочерние профили (тогда удалять нельзя)
        ctx["has_children"] = profile.children.exists()
        return ctx


class ProfileUpdateView(PermissionRequiredMixin, UpdateView):
    permission_required = "profile.update"
    model = DeviceProfile
    form_class = ProfileForm
    template_name = "adminui/profiles/form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = f"Редактирование профиля: {self.object.name}"
        ctx["form_submit"] = "Сохранить"
        ctx["profile"] = self.object
        return ctx

    def get_success_url(self):
        return reverse("adminui:profile_detail", args=[self.object.pk])


class ProfileDeleteView(PermissionRequiredMixin, DeleteView):
    permission_required = "profile.delete"
    model = DeviceProfile
    template_name = "adminui/profiles/delete_confirm.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        profile = self.get_object()
        ctx["has_children"] = profile.children.exists()
        ctx["children"] = profile.children.all()
        ctx["has_devices"] = profile.devices.exists()
        ctx["devices"] = profile.devices.all()
        ctx["can_delete"] = not (ctx["has_children"] or ctx["has_devices"])
        return ctx

    def post(self, request, *args, **kwargs):
        profile = self.get_object()
        if profile.children.exists() or profile.devices.exists():
            from django.contrib import messages
            messages.error(
                request,
                "Нельзя удалить профиль: есть дочерние профили или устройства.",
            )
            return self.get(request, *args, **kwargs)
        return super().post(request, *args, **kwargs)

    def get_success_url(self):
        return reverse("adminui:profiles")


class ProfileCloneView(PermissionRequiredMixin, TemplateView):
    permission_required = "profile.clone"
    template_name = "adminui/profiles/clone.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["source"] = get_object_or_404(DeviceProfile, pk=kwargs["pk"])
        return ctx

    def post(self, request, *args, **kwargs):
        source = get_object_or_404(DeviceProfile, pk=kwargs["pk"])
        new_slug = (request.POST.get("slug") or "").strip()
        new_name = (request.POST.get("name") or "").strip()

        if not new_slug or not new_name:
            from django.contrib import messages
            messages.error(request, "Slug и имя обязательны")
            return self.get(request, *args, **kwargs)

        if DeviceProfile.objects.filter(slug=new_slug).exists():
            from django.contrib import messages
            messages.error(request, f"Slug «{new_slug}» уже занят")
            return self.get(request, *args, **kwargs)

        new_profile = DeviceProfile.objects.create(
            vendor=source.vendor,
            parent=source.parent,
            slug=new_slug,
            name=new_name,
            priority=source.priority,
            device_category=source.device_category,
            match_sysobject_prefixes=source.match_sysobject_prefixes,
            match_sysdescr_regex=source.match_sysdescr_regex,
            match_model_regex=source.match_model_regex,
        )
        for m in source.metrics.all():
            MetricDefinition.objects.create(
                profile=new_profile,
                key=m.key, name=m.name, oid=m.oid,
                value_type=m.value_type, scale=m.scale, unit=m.unit,
                transform=m.transform, transform_params=m.transform_params,
                warn=m.warn, crit=m.crit, enabled=m.enabled,
            )

        from django.contrib import messages
        messages.success(
            request,
            f"Профиль клонирован: {new_profile.name} ({new_profile.metrics.count()} метрик)",
        )
        return redirect("adminui:profile_detail", pk=new_profile.pk)


class ProfilesPlaceholderView(PermissionRequiredMixin, TemplateView):
    template_name = "adminui/placeholder.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["section"] = "Профили"
        ctx["stage"] = "Этап 3"
        return ctx


# ============================================================
# Остальные — пока заглушки
# ============================================================

class MetricCreateView(PermissionRequiredMixin, CreateView):
    """Добавление метрики в профиль."""
    permission_required = "metric.create"
    model = MetricDefinition
    form_class = MetricDefinitionForm
    template_name = "adminui/metrics/form.html"

    def dispatch(self, request, *args, **kwargs):
        self.profile = get_object_or_404(DeviceProfile, pk=kwargs["profile_pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = f"Новая метрика: {self.profile.name}"
        ctx["form_submit"] = "Создать"
        ctx["profile"] = self.profile
        ctx["metric"] = None
        return ctx

    def form_valid(self, form):
        form.instance.profile = self.profile
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("adminui:profile_detail", args=[self.profile.pk])


class MetricUpdateView(PermissionRequiredMixin, UpdateView):
    """Редактирование метрики."""
    permission_required = "metric.update"
    model = MetricDefinition
    form_class = MetricDefinitionForm
    template_name = "adminui/metrics/form.html"

    def dispatch(self, request, *args, **kwargs):
        self.profile = get_object_or_404(DeviceProfile, pk=kwargs["profile_pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return MetricDefinition.objects.filter(profile=self.profile)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = f"Метрика: {self.object.key}"
        ctx["form_submit"] = "Сохранить"
        ctx["profile"] = self.profile
        ctx["metric"] = self.object
        return ctx

    def get_success_url(self):
        return reverse("adminui:profile_detail", args=[self.profile.pk])


class MetricDeleteView(PermissionRequiredMixin, DeleteView):
    """Удаление метрики."""
    permission_required = "metric.delete"
    model = MetricDefinition
    template_name = "adminui/metrics/delete_confirm.html"

    def dispatch(self, request, *args, **kwargs):
        self.profile = get_object_or_404(DeviceProfile, pk=kwargs["profile_pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return MetricDefinition.objects.filter(profile=self.profile)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["profile"] = self.profile
        return ctx

    def get_success_url(self):
        return reverse("adminui:profile_detail", args=[self.profile.pk])


class VendorListView(PermissionRequiredMixin, ListView):
    """Список вендоров."""
    permission_required = "vendor.view"
    model = Vendor
    template_name = "adminui/vendors/list.html"
    context_object_name = "vendors"
    paginate_by = 50

    def get_queryset(self):
        return Vendor.objects.annotate(
            profiles_count=Count("profiles", distinct=True),
            devices_count=Count("devices", distinct=True),
        ).order_by("name")


class VendorCreateView(PermissionRequiredMixin, CreateView):
    permission_required = "vendor.create"
    model = Vendor
    form_class = VendorForm
    template_name = "adminui/vendors/form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = "Новый вендор"
        ctx["form_submit"] = "Создать"
        ctx["vendor"] = None
        return ctx

    def get_success_url(self):
        return reverse("adminui:vendors")


class VendorUpdateView(PermissionRequiredMixin, UpdateView):
    permission_required = "vendor.update"
    model = Vendor
    form_class = VendorForm
    template_name = "adminui/vendors/form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = f"Редактирование: {self.object.name}"
        ctx["form_submit"] = "Сохранить"
        ctx["vendor"] = self.object
        return ctx

    def get_success_url(self):
        return reverse("adminui:vendors")


class VendorDeleteView(PermissionRequiredMixin, DeleteView):
    permission_required = "vendor.delete"
    model = Vendor
    template_name = "adminui/vendors/delete_confirm.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        v = self.get_object()
        ctx["has_profiles"] = v.profiles.exists()
        ctx["has_devices"] = v.devices.exists()
        ctx["can_delete"] = not (ctx["has_profiles"] or ctx["has_devices"])
        return ctx

    def post(self, request, *args, **kwargs):
        v = self.get_object()
        if v.profiles.exists() or v.devices.exists():
            from django.contrib import messages
            messages.error(request, "Нельзя удалить вендора: есть профили или устройства.")
            return self.get(request, *args, **kwargs)
        return super().post(request, *args, **kwargs)

    def get_success_url(self):
        return reverse("adminui:vendors")


class NeListView(PermissionRequiredMixin, ListView):
    """Список NE (Olencom) с фильтром по шлюзу."""
    permission_required = "ne.view"
    model = NeDevice
    template_name = "adminui/ne/list.html"
    context_object_name = "ne_list"
    paginate_by = 200

    def get_queryset(self):
        qs = NeDevice.objects.select_related("gateway").order_by(
            "gateway__name", "sort_order", "ne_id"
        )
        gateway_pk = self.request.GET.get("gateway")
        if gateway_pk:
            qs = qs.filter(gateway_id=gateway_pk)
        q = self.request.GET.get("q")
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(raw_name__icontains=q))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["gateways"] = (
            Device.objects
            .filter(ne_devices__isnull=False)
            .annotate(ne_count=Count("ne_devices", distinct=True))
            .order_by("name")
        )
        ctx["current_gateway"] = self.request.GET.get("gateway", "")
        ctx["search_q"] = self.request.GET.get("q", "")
        ctx["total"] = self.get_queryset().count()
        return ctx


class NeDetailView(PermissionRequiredMixin, TemplateView):
    """Карточка NE (Olencom) + графики метрик."""
    permission_required = "ne.view"
    template_name = "adminui/ne/detail.html"

    def get_context_data(self, **kwargs):
        import json
        from datetime import timedelta
        from django.utils import timezone

        ctx = super().get_context_data(**kwargs)
        ne = get_object_or_404(NeDevice, pk=kwargs["pk"])
        ctx["ne"] = ne
        ctx["gateway"] = ne.gateway

        # Метрики NE: берём из MetricValue с этим ne_device
        # (не из profile — у NE своя структура, но фактически метрики лежат в MetricValue)
        metric_rows = (
            MetricValue.objects
            .filter(ne_device=ne)
            .values_list("metric__key", flat=True)
            .distinct()
        )
        metric_keys = sorted(set(metric_rows))
        ctx["metric_keys"] = metric_keys

        # Названия и единицы метрик — из MetricDefinition
        from apps.devices.models import MetricDefinition
        meta = {}
        for k in metric_keys:
            md = MetricDefinition.objects.filter(key=k).first()
            if md:
                meta[k] = {"name": md.name, "unit": md.unit or ""}
            else:
                meta[k] = {"name": k, "unit": ""}
        ctx["metrics_meta"] = meta

        # Retention = 2 часа, значит смотрим за 2ч
        since = timezone.now() - timedelta(hours=2)
        rows = (
            MetricValue.objects
            .filter(ne_device=ne, ts__gte=since)
            .values("metric__key", "ts", "value", "instance")
            .order_by("ts")
        )

        # Группируем по (key, instance) — отдельный график для каждого порта
        charts = {}
        for r in rows:
            k = r["metric__key"]
            inst = r["instance"] or "0"
            chart_key = f"{k}:{inst}" if inst and inst != "0" else k
            if chart_key not in charts:
                charts[chart_key] = []
            charts[chart_key].append({
                "ts": int(r["ts"].timestamp() * 1000),
                "value": r["value"],
            })

        # Мета: для ключей с instance — расширяем name (добавляем :N)
        meta2 = {}
        for chart_key in charts.keys():
            if ":" in chart_key:
                base_key, inst = chart_key.rsplit(":", 1)
                base_meta = meta.get(base_key, {"name": base_key, "unit": ""})
                meta2[chart_key] = {
                    "name": f"{base_meta['name']} :{inst}",
                    "unit": base_meta["unit"],
                }
            else:
                meta2[chart_key] = meta.get(chart_key, {"name": chart_key, "unit": ""})

        ctx["metric_keys"] = sorted(charts.keys())
        ctx["charts_json"] = json.dumps(charts, ensure_ascii=False)
        ctx["metrics_meta_json"] = json.dumps(meta2, ensure_ascii=False)
        return ctx


class SubnetListView(PermissionRequiredMixin, ListView):
    """Список подсетей с прогресс-барами."""
    permission_required = "subnet.view"
    model = Subnet
    template_name = "adminui/subnets/list.html"
    context_object_name = "subnets"
    paginate_by = 100

    def get_queryset(self):
        return Subnet.objects.all().order_by("name")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Считаем stats для каждой подсети
        subnets_with_stats = []
        for s in ctx["subnets"]:
            subnets_with_stats.append({"subnet": s, "stats": s.stats()})
        ctx["subnets_stats"] = subnets_with_stats
        ctx["total"] = len(subnets_with_stats)
        return ctx


class SubnetCreateView(PermissionRequiredMixin, CreateView):
    permission_required = "subnet.create"
    model = Subnet
    form_class = SubnetForm
    template_name = "adminui/subnets/form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = "Новая подсеть"
        ctx["form_submit"] = "Создать"
        ctx["subnet"] = None
        return ctx

    def get_success_url(self):
        return reverse("adminui:subnets")


class SubnetUpdateView(PermissionRequiredMixin, UpdateView):
    permission_required = "subnet.update"
    model = Subnet
    form_class = SubnetForm
    template_name = "adminui/subnets/form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = f"Подсеть: {self.object.name}"
        ctx["form_submit"] = "Сохранить"
        ctx["subnet"] = self.object
        return ctx

    def get_success_url(self):
        return reverse("adminui:subnets")


class SubnetDeleteView(PermissionRequiredMixin, DeleteView):
    permission_required = "subnet.delete"
    model = Subnet
    template_name = "adminui/subnets/delete_confirm.html"

    def get_success_url(self):
        return reverse("adminui:subnets")


class AlertListView(PermissionRequiredMixin, ListView):
    """Список алертов с фильтрами."""
    permission_required = "alert.view"
    model = Alert
    template_name = "adminui/alerts/list.html"
    context_object_name = "alerts"
    paginate_by = 100

    def get_queryset(self):
        qs = (
            Alert.objects
            .select_related("device", "ne_device", "ip_address")
            .order_by("-started_at")
        )
        form = AlertFilterForm(self.request.GET or None)
        if form.is_valid():
            q = form.cleaned_data.get("q")
            severity = form.cleaned_data.get("severity")
            state = form.cleaned_data.get("state")
            entity = form.cleaned_data.get("entity")

            if q:
                qs = qs.filter(
                    Q(metric_key__icontains=q)
                    | Q(message__icontains=q)
                )
            if severity:
                qs = qs.filter(severity=severity)
            if state:
                qs = qs.filter(state=state)
            if entity == "device":
                qs = qs.filter(device__isnull=False)
            elif entity == "ne":
                qs = qs.filter(ne_device__isnull=False)
            elif entity == "ip":
                qs = qs.filter(ip_address__isnull=False)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter_form"] = AlertFilterForm(self.request.GET or None)
        ctx["total"] = self.get_queryset().count()

        # Счётчики
        qs_all = Alert.objects.all()
        ctx["counts"] = {
            "firing": qs_all.filter(state="firing").count(),
            "firing_crit": qs_all.filter(state="firing", severity="crit").count(),
            "firing_warn": qs_all.filter(state="firing", severity="warn").count(),
            "total": qs_all.count(),
        }
        return ctx


class UserListView(PermissionRequiredMixin, ListView):
    """Список пользователей."""
    permission_required = "user.view"
    from apps.accounts.models import User as UserModel
    model = UserModel
    template_name = "adminui/users/list.html"
    context_object_name = "users"
    paginate_by = 100

    def get_queryset(self):
        return self.model.objects.all().order_by(
            "-is_superuser", "-is_staff", "username"
        )


class UserCreateView(PermissionRequiredMixin, CreateView):
    permission_required = "user.create"
    from apps.accounts.models import User as UserModel
    model = UserModel
    template_name = "adminui/users/form.html"

    def get_form_class(self):
        from .forms import UserCreateForm
        return UserCreateForm

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = "Новый пользователь"
        ctx["form_submit"] = "Создать"
        ctx["user_obj"] = None
        return ctx

    def get_success_url(self):
        return reverse("adminui:users")


class UserUpdateView(PermissionRequiredMixin, UpdateView):
    permission_required = "user.update"
    from apps.accounts.models import User as UserModel
    model = UserModel
    template_name = "adminui/users/form.html"

    def get_form_class(self):
        from .forms import UserForm
        return UserForm

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = f"Пользователь: {self.object.username}"
        ctx["form_submit"] = "Сохранить"
        ctx["user_obj"] = self.object
        return ctx

    def get_success_url(self):
        return reverse("adminui:users")


class UserDeleteView(PermissionRequiredMixin, DeleteView):
    permission_required = "user.delete"
    from apps.accounts.models import User as UserModel
    model = UserModel
    template_name = "adminui/users/delete_confirm.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["is_self"] = self.get_object().pk == self.request.user.pk
        return ctx

    def post(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.pk == request.user.pk:
            from django.contrib import messages
            messages.error(request, "Нельзя удалить самого себя")
            return self.get(request, *args, **kwargs)
        return super().post(request, *args, **kwargs)

    def get_success_url(self):
        return reverse("adminui:users")


class RoleListView(PermissionRequiredMixin, ListView):
    """Список ролей."""
    permission_required = "admin.access"
    from apps.accounts.models import Role as RoleModel
    model = RoleModel
    template_name = "adminui/roles/list.html"
    context_object_name = "roles"
    paginate_by = 50

    def get_queryset(self):
        from django.db.models import Count
        return (
            self.model.objects
            .prefetch_related("permissions")
            .annotate(users_count=Count("userrole", distinct=True))
            .order_by("name")
        )


class RoleDetailView(PermissionRequiredMixin, TemplateView):
    """Карточка роли — список разрешений."""
    permission_required = "admin.access"
    template_name = "adminui/roles/detail.html"

    def get_context_data(self, **kwargs):
        from apps.accounts.models import Role
        ctx = super().get_context_data(**kwargs)
        ctx["role"] = get_object_or_404(Role, pk=kwargs["pk"])
        # Группируем permissions по group
        from django.db.models import Count
        perms = ctx["role"].permissions.all().order_by("group", "code")
        groups = {}
        for p in perms:
            groups.setdefault(p.group, []).append(p)
        ctx["perm_groups"] = groups
        ctx["users"] = (
            User.objects
            .filter(user_roles__role=ctx["role"])
            .order_by("username")
        )
        return ctx


class UserRolesView(PermissionRequiredMixin, UpdateView):
    """Назначение ролей пользователю."""
    permission_required = "user.manage_roles"
    model = User
    template_name = "adminui/users/roles.html"
    fields = []

    def get_context_data(self, **kwargs):
        from apps.accounts.models import Role, UserRole
        ctx = super().get_context_data(**kwargs)
        user = self.object
        ctx["user_obj"] = user
        ctx["roles"] = Role.objects.all().order_by("name")
        ctx["user_role_ids"] = set(
            user.user_roles.values_list("role_id", flat=True)
        )
        return ctx

    def post(self, request, *args, **kwargs):
        from apps.accounts.models import Role, UserRole
        user = self.get_object()
        selected_ids = request.POST.getlist("roles")
        # Удаляем старые
        UserRole.objects.filter(user=user).delete()
        # Добавляем новые
        for rid in selected_ids:
            try:
                role = Role.objects.get(pk=rid)
                UserRole.objects.create(
                    user=user, role=role, assigned_by=request.user,
                )
            except Role.DoesNotExist:
                pass
        from django.contrib import messages
        messages.success(
            request,
            f"Роли обновлены: {user.user_roles.count()} назначено",
        )
        return redirect("adminui:users")


class AlertDetailView(PermissionRequiredMixin, TemplateView):
    """Карточка алерта."""
    permission_required = "alert.view"
    template_name = "adminui/alerts/detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        alert = get_object_or_404(Alert, pk=kwargs["pk"])
        ctx["alert"] = alert
        return ctx


# ============================================================
# Modules (платформенные)
# ============================================================

class ModuleListView(PermissionRequiredMixin, ListView):
    """Список модулей."""
    permission_required = "admin.access"
    from apps.core.models import Module as ModuleModel
    model = ModuleModel
    template_name = "adminui/modules/list.html"
    context_object_name = "modules"
    paginate_by = 50

    def get_queryset(self):
        return self.model.objects.all().order_by("sort_order", "name")


class ModuleCreateView(PermissionRequiredMixin, CreateView):
    """Создание модуля."""
    permission_required = "admin.access"
    from apps.core.models import Module as ModuleModel
    model = ModuleModel
    fields = [
        "slug", "name", "description", "icon", "color",
        "url_prefix", "is_active", "is_installed", "sort_order",
    ]
    template_name = "adminui/modules/form.html"
    success_url = "/admin-ui/modules/"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = "Новый модуль"
        ctx["form_submit"] = "Создать"
        ctx["module"] = None
        return ctx


class ModuleUpdateView(PermissionRequiredMixin, UpdateView):
    """Редактирование модуля."""
    permission_required = "admin.access"
    from apps.core.models import Module as ModuleModel
    model = ModuleModel
    fields = [
        "slug", "name", "description", "icon", "color",
        "url_prefix", "is_active", "is_installed", "sort_order",
    ]
    template_name = "adminui/modules/form.html"
    success_url = "/admin-ui/modules/"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = f"Модуль: {self.object.name}"
        ctx["form_submit"] = "Сохранить"
        ctx["module"] = self.object
        return ctx


# ── Оптические муфты ─────────────────────────────────────────
from apps.stations.models import OpticalClosure, OpticalLink

class ClosureListView(PermissionRequiredMixin, ListView):
    model = OpticalClosure
    template_name = "adminui/closures/list.html"
    context_object_name = "closures"
    paginate_by = 50
    permission_required = "device.view"  # или можно сделать отдельное право

    def get_queryset(self):
        qs = super().get_queryset().select_related("link")
        q = self.request.GET.get("q", "").strip()
        link_id = self.request.GET.get("link", "").strip()
        if q:
            qs = qs.filter(name__icontains=q)
        if link_id:
            qs = qs.filter(link_id=link_id)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["links"] = OpticalLink.objects.all()
        ctx["q"] = self.request.GET.get("q", "")
        ctx["selected_link"] = self.request.GET.get("link", "")
        return ctx


class ClosureCreateView(PermissionRequiredMixin, CreateView):
    model = OpticalClosure
    form_class = ClosureForm
    template_name = "adminui/closures/form.html"
    success_url = reverse_lazy("adminui:closures")
    permission_required = "device.create"


class ClosureUpdateView(PermissionRequiredMixin, UpdateView):
    model = OpticalClosure
    form_class = ClosureForm
    template_name = "adminui/closures/form.html"
    success_url = reverse_lazy("adminui:closures")
    permission_required = "device.update"


class ClosureDeleteView(PermissionRequiredMixin, DeleteView):
    model = OpticalClosure
    template_name = "adminui/closures/confirm_delete.html"
    success_url = reverse_lazy("adminui:closures")
    permission_required = "device.delete"

# ── Станции ─────────────────────────────────────────────────────
from apps.stations.models import Station, OpticalLink

class StationListView(PermissionRequiredMixin, ListView):
    model = Station
    template_name = "adminui/stations/list.html"
    context_object_name = "stations"
    paginate_by = 50
    permission_required = "device.view"

    def get_queryset(self):
        qs = super().get_queryset()
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(name__icontains=q)
        return qs

class StationCreateView(PermissionRequiredMixin, CreateView):
    model = Station
    form_class = StationForm
    template_name = "adminui/stations/form.html"
    success_url = reverse_lazy("adminui:stations")
    permission_required = "device.create"

    def form_valid(self, form):
        response = super().form_valid(form)
        # Сохраняем связи StationDevice
        self.save_station_devices()
        return response

    def save_station_devices(self):
        from apps.stations.models import StationDevice
        station = self.object
        devices = self.request.POST.getlist('devices')
        # Удаляем старые связи
        StationDevice.objects.filter(station=station).delete()
        # Создаём новые
        for device_id in devices:
            try:
                StationDevice.objects.create(station=station, device_id=int(device_id))
            except Exception:
                pass

class StationUpdateView(PermissionRequiredMixin, UpdateView):
    model = Station
    form_class = StationForm
    template_name = "adminui/stations/form.html"
    success_url = reverse_lazy("adminui:stations")
    permission_required = "device.update"

    def form_valid(self, form):
        response = super().form_valid(form)
        self.save_station_devices()
        return response

    def save_station_devices(self):
        from apps.stations.models import StationDevice
        station = self.object
        devices = self.request.POST.getlist('devices')
        StationDevice.objects.filter(station=station).delete()
        for device_id in devices:
            try:
                StationDevice.objects.create(station=station, device_id=int(device_id))
            except Exception:
                pass

class StationDeleteView(PermissionRequiredMixin, DeleteView):
    model = Station
    template_name = "adminui/stations/confirm_delete.html"
    success_url = reverse_lazy("adminui:stations")
    permission_required = "device.delete"


# ── Оптические линии ────────────────────────────────────────────
class OpticalLinkListView(PermissionRequiredMixin, ListView):
    model = OpticalLink
    template_name = "adminui/links/list.html"
    context_object_name = "links"
    paginate_by = 50
    permission_required = "device.view"

    def get_queryset(self):
        qs = super().get_queryset().select_related("station_a", "station_b")
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(label__icontains=q) | qs.filter(station_a__name__icontains=q) | qs.filter(station_b__name__icontains=q)
        return qs

class OpticalLinkCreateView(PermissionRequiredMixin, CreateView):
    model = OpticalLink
    fields = ("label", "station_a", "station_b", "device_a", "ne_a", "port_a_type", "port_a_num", "device_b", "ne_b", "port_b_type", "port_b_num", "is_active")
    template_name = "adminui/links/form.html"
    success_url = reverse_lazy("adminui:links")
    permission_required = "device.create"

class OpticalLinkUpdateView(PermissionRequiredMixin, UpdateView):
    model = OpticalLink
    fields = ("label", "station_a", "station_b", "device_a", "ne_a", "port_a_type", "port_a_num", "device_b", "ne_b", "port_b_type", "port_b_num", "is_active")
    template_name = "adminui/links/form.html"
    success_url = reverse_lazy("adminui:links")
    permission_required = "device.update"

class OpticalLinkDuplicateView(PermissionRequiredMixin, View):
    """Дублирование оптической линии (создаёт копию и открывает форму редактирования)."""
    permission_required = "device.create"

    def get(self, request, pk):
        from django.shortcuts import redirect
        from apps.stations.models import OpticalLink
        link = get_object_or_404(OpticalLink, pk=pk)
        
        # Копируем все поля, кроме id, created_at, updated_at
        new_link = OpticalLink.objects.create(
            label=f"{link.label} (копия)" if link.label else "",
            description=link.description,
            station_a=link.station_a,
            station_b=link.station_b,
            device_a=link.device_a,
            ne_a=link.ne_a,
            port_a_type=link.port_a_type,
            port_a_num=link.port_a_num,
            device_b=link.device_b,
            ne_b=link.ne_b,
            port_b_type=link.port_b_type,
            port_b_num=link.port_b_num,
            path_geojson=link.path_geojson,
            is_active=False,  # копия неактивна, пока не настроена
        )
        
        return redirect("adminui:link_update", pk=new_link.pk)


class OpticalLinkDeleteView(PermissionRequiredMixin, DeleteView):
    model = OpticalLink
    template_name = "adminui/links/confirm_delete.html"
    success_url = reverse_lazy("adminui:links")
    permission_required = "device.delete"

# ── Рисование маршрута в кастомной админке ──────────────────────
from django.shortcuts import get_object_or_404, render
from django.contrib.auth.decorators import login_required

@login_required  # права — AccessMiddleware: /admin-ui/links/ → «Диспетчерская и станции» (изменение)
def draw_path_adminui(request, link_id):
    link = get_object_or_404(OpticalLink, id=link_id)
    
    points = []
    if link.station_a and link.station_a.latitude and link.station_a.longitude:
        points.append({
            'lat': float(link.station_a.latitude),
            'lon': float(link.station_a.longitude),
            'name': link.station_a.name,
            'type': 'station_a'
        })
    if link.station_b and link.station_b.latitude and link.station_b.longitude:
        points.append({
            'lat': float(link.station_b.latitude),
            'lon': float(link.station_b.longitude),
            'name': link.station_b.name,
            'type': 'station_b'
        })
    
    return render(request, 'adminui/links/draw.html', {
        'link': link,
        'stations': points
    })

# ── Управление ролями (RBAC) ────────────────────────────────────
from apps.accounts.models import Role, Permission
from django import forms

class RoleForm(forms.ModelForm):
    class Meta:
        model = Role
        fields = ("name", "slug", "permissions")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-input", "placeholder": "Название роли"}),
            "slug": forms.TextInput(attrs={"class": "form-input", "placeholder": "slug (латиницей)"}),
            "permissions": forms.CheckboxSelectMultiple(),
        }

class RoleListView(PermissionRequiredMixin, ListView):
    model = Role
    template_name = "adminui/roles/list.html"
    context_object_name = "roles"
    permission_required = "system.admin.access"

    def get_queryset(self):
        return super().get_queryset().prefetch_related("permissions", "userrole_set")

class RoleCreateView(PermissionRequiredMixin, CreateView):
    model = Role
    form_class = RoleForm
    template_name = "adminui/roles/form.html"
    success_url = reverse_lazy("adminui:roles")
    permission_required = "system.admin.access"

class RoleUpdateView(PermissionRequiredMixin, UpdateView):
    model = Role
    form_class = RoleForm
    template_name = "adminui/roles/form.html"
    success_url = reverse_lazy("adminui:roles")
    permission_required = "system.admin.access"

class RoleDeleteView(PermissionRequiredMixin, DeleteView):
    model = Role
    template_name = "adminui/roles/confirm_delete.html"
    success_url = reverse_lazy("adminui:roles")
    permission_required = "system.admin.access"


# ─── Категории устройств ──────────────────────────────────

class CategoryListView(PermissionRequiredMixin, ListView):
    permission_required = "profile.view"
    model = DeviceCategory
    template_name = "adminui/categories/list.html"
    context_object_name = "categories"
    paginate_by = 50


class CategoryCreateView(PermissionRequiredMixin, CreateView):
    permission_required = "profile.create"
    model = DeviceCategory
    form_class = DeviceCategoryForm
    template_name = "adminui/categories/form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = "Новая категория"
        ctx["form_submit"] = "Создать"
        return ctx

    def get_success_url(self):
        return reverse("adminui:categories")


class CategoryUpdateView(PermissionRequiredMixin, UpdateView):
    permission_required = "profile.update"
    model = DeviceCategory
    form_class = DeviceCategoryForm
    template_name = "adminui/categories/form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = f"Категория: {self.object.name}"
        ctx["form_submit"] = "Сохранить"
        return ctx

    def get_success_url(self):
        return reverse("adminui:categories")


class CategoryDeleteView(PermissionRequiredMixin, DeleteView):
    permission_required = "profile.delete"
    model = DeviceCategory
    template_name = "adminui/categories/delete_confirm.html"

    def get_success_url(self):
        return reverse("adminui:categories")


# ─── API для категорий (переименование, удаление) ──────────

from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect
import json

def _api_perm(perm):
    """JSON-API админки: вход + право (как у соответствующих CBV). Без него — 401/403 JSON."""
    import functools

    def deco(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return JsonResponse({"ok": False, "error": "authentication required"}, status=401)
            if not has_perm(request.user, perm):
                return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
            return view(request, *args, **kwargs)
        return wrapper
    return deco


@require_POST
@csrf_protect
@_api_perm("profile.update")
def category_rename_api(request):
    from apps.devices.models import DeviceCategory
    try:
        data = json.loads(request.body)
        slug = data.get("slug")
        name = data.get("name")
        if not slug or not name:
            return JsonResponse({"ok": False, "error": "slug и name обязательны"}, status=400)
        try:
            cat = DeviceCategory.objects.get(slug=slug)
        except DeviceCategory.DoesNotExist:
            return JsonResponse({"ok": False, "error": "Категория не найдена"}, status=404)
        cat.name = name
        cat.save(update_fields=["name"])
        return JsonResponse({"ok": True, "slug": slug, "name": name})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@require_POST
@csrf_protect
@_api_perm("profile.create")
def subcategory_create_api(request):
    from apps.devices.models import DeviceCategory, DeviceSubcategory
    from django.utils.text import slugify
    try:
        data = json.loads(request.body)
        category_slug = data.get("category_slug")
        name = data.get("name")
        if not category_slug or not name:
            return JsonResponse({"ok": False, "error": "category_slug и name обязательны"}, status=400)
        try:
            cat = DeviceCategory.objects.get(slug=category_slug)
        except DeviceCategory.DoesNotExist:
            return JsonResponse({"ok": False, "error": "Категория не найдена"}, status=404)
        
        # Генерируем slug
        base_slug = slugify(name) or "subcategory"
        slug = base_slug
        counter = 1
        while DeviceSubcategory.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        
        sub = DeviceSubcategory.objects.create(
            category=cat,
            slug=slug,
            name=name,
        )
        return JsonResponse({"ok": True, "slug": slug, "name": name, "id": sub.id})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@require_POST
@csrf_protect
@_api_perm("profile.delete")
def category_delete_api(request):
    from apps.devices.models import DeviceCategory
    try:
        data = json.loads(request.body)
        slug = data.get("slug")
        if not slug:
            return JsonResponse({"ok": False, "error": "slug обязателен"}, status=400)
        try:
            cat = DeviceCategory.objects.get(slug=slug)
        except DeviceCategory.DoesNotExist:
            return JsonResponse({"ok": False, "error": "Категория не найдена"}, status=404)
        cat.delete()
        return JsonResponse({"ok": True, "slug": slug})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)



# ========== Объявления на главной ==========
from apps.core.models import Announcement
from .forms import AnnouncementForm


class AnnouncementListView(PermissionRequiredMixin, ListView):
    permission_required = "announcement.manage"
    model = Announcement
    template_name = "adminui/announcements/list.html"
    context_object_name = "announcements"
    paginate_by = 50

    def get_queryset(self):
        return Announcement.objects.select_related("created_by").order_by("-is_pinned", "-starts_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["visible_ids"] = set(Announcement.visible().values_list("id", flat=True))
        return ctx


class _AnnouncementFormMixin(PermissionRequiredMixin):
    permission_required = "announcement.manage"
    model = Announcement
    form_class = AnnouncementForm
    template_name = "adminui/announcements/form.html"

    def get_success_url(self):
        return reverse("adminui:announcements")


class AnnouncementCreateView(_AnnouncementFormMixin, CreateView):
    def form_valid(self, form):
        form.instance.created_by = self.request.user
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(form_title="Новое объявление", form_submit="Опубликовать", announcement=None)
        return ctx


class AnnouncementUpdateView(_AnnouncementFormMixin, UpdateView):
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(form_title=f"Редактирование: {self.object.title}", form_submit="Сохранить",
                   announcement=self.object)
        return ctx


class AnnouncementDeleteView(PermissionRequiredMixin, DeleteView):
    permission_required = "announcement.manage"
    model = Announcement
    template_name = "adminui/announcements/delete_confirm.html"

    def get_success_url(self):
        return reverse("adminui:announcements")


# ═══════════════════════════════════════════════════════════════════════════
# Права доступа пользователей: матрица «раздел × уровень» + возможности
# (apps/accounts/access.py). Используется в карточке пользователя,
# при подтверждении регистрации и в шаблонах доступа.
# ═══════════════════════════════════════════════════════════════════════════
import json as _json

from django.contrib import messages as _messages
from django.http import Http404
from django.shortcuts import redirect as _redirect


def access_form_context(sections=None, caps=None):
    from apps.accounts.access import (CAPABILITIES, GROUP_LABELS, LEVEL_LABELS, SECTIONS,
                                      clean_caps, clean_sections)
    from apps.accounts.models import AccessPreset
    sections = clean_sections(sections)
    caps = set(clean_caps(caps))
    groups = []
    for g, label in GROUP_LABELS.items():
        rows = [{"key": s.key, "name": s.name, "hint": s.hint, "level": sections[s.key]}
                for s in SECTIONS if s.group == g]
        if rows:
            groups.append({"label": label, "rows": rows})
    presets = list(AccessPreset.objects.all())
    return {
        "acc_groups": groups,
        "acc_levels": list(LEVEL_LABELS.items()),
        "acc_caps": [{"key": c.key, "name": c.name, "hint": c.hint, "needs": c.needs, "on": c.key in caps}
                     for c in CAPABILITIES],
        "acc_presets": presets,
        "acc_presets_json": _json.dumps({p.pk: {"sections": clean_sections(p.sections), "caps": clean_caps(p.caps)}
                                         for p in presets}),
    }


def access_from_post(post):
    from apps.accounts.access import SECTIONS, clean_caps, clean_sections
    sections = clean_sections({s.key: post.get(f"acc_{s.key}", 0) for s in SECTIONS})
    caps = clean_caps(post.getlist("acc_caps"))
    return sections, caps


def apply_access(editor, target, post):
    """Сохраняет права из формы. Возвращает текст ошибки или ''."""
    sections, caps = access_from_post(post)
    if target.pk == editor.pk and not editor.is_superuser and "users_manage" not in caps:
        return "Нельзя снять с себя право «Пользователи и права» — иначе вы потеряете доступ к этой странице."
    target.access_sections = sections
    target.access_caps = caps
    target.save(update_fields=["access_sections", "access_caps"])
    return ""


def _guard_superuser_target(request, target):
    """Учётку суперпользователя меняет только суперпользователь (иначе — смена его пароля = захват)."""
    if target.is_superuser and not request.user.is_superuser:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Учётную запись суперпользователя может менять только суперпользователь.")


class UserAccessMixin:
    """Подмешивает матрицу прав в формы создания/редактирования пользователя."""

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        kw["request_user"] = self.request.user
        return kw

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        obj = getattr(self, "object", None)
        if self.request.method == "POST":
            s, c = access_from_post(self.request.POST)
        else:
            s, c = (obj.access_sections, obj.access_caps) if obj and obj.pk else ({}, [])
        ctx.update(access_form_context(s, c))
        ctx["target_is_superuser"] = bool(obj and obj.pk and obj.is_superuser)
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        err = apply_access(self.request.user, self.object, self.request.POST)
        if err:
            _messages.error(self.request, err)
        else:
            _messages.success(self.request, f"Пользователь {self.object.username} сохранён.")
        return response


class UserApproveView(PermissionRequiredMixin, TemplateView):
    """Подтверждение регистрации: администратор сразу задаёт права."""
    permission_required = "user.manage_roles"
    template_name = "adminui/users/approve.html"

    def get_target(self):
        from apps.accounts.models import User as U
        return get_object_or_404(U, pk=self.kwargs["pk"])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        target = self.get_target()
        ctx["target"] = target
        ctx.update(access_form_context(target.access_sections, target.access_caps))
        from apps.accounts.models import Position
        ctx["positions"] = Position.objects.filter(is_active=True).order_by("name")
        return ctx

    def post(self, request, *args, **kwargs):
        from django.utils import timezone as _tz
        from apps.accounts.models import Position
        target = self.get_target()
        _guard_superuser_target(request, target)
        sections, caps = access_from_post(request.POST)
        target.access_sections = sections
        target.access_caps = caps
        pos_id = request.POST.get("position_ref", "")
        if pos_id.isdigit():
            target.position_ref = Position.objects.filter(pk=pos_id).first()
        target.is_active = True
        target.registration_status = "approved"
        target.approved_at = _tz.now()
        target.approved_by = request.user
        target.save()
        _messages.success(request, f"Пользователь {target.username} подтверждён, права заданы.")
        return _redirect("adminui:users_pending")


class RolesMovedView(PermissionRequiredMixin, View):
    """Старые страницы ролей → шаблоны доступа / карточка пользователя."""
    permission_required = "user.manage_roles"

    def get(self, request, *args, **kwargs):
        if "pk" in kwargs and "users/" in request.path:
            return _redirect("adminui:user_update", pk=kwargs["pk"])
        return _redirect("adminui:presets")

    post = get


# ── Шаблоны доступа ──
class PresetListView(PermissionRequiredMixin, TemplateView):
    permission_required = "user.manage_roles"
    template_name = "adminui/presets/list.html"

    def get_context_data(self, **kwargs):
        from apps.accounts.access import CAP_BY_KEY, SECTIONS, clean_caps, clean_sections
        from apps.accounts.models import AccessPreset
        ctx = super().get_context_data(**kwargs)
        rows = []
        for p in AccessPreset.objects.all():
            sec = clean_sections(p.sections)
            rows.append({
                "obj": p,
                "edit": [s.name for s in SECTIONS if sec[s.key] == 2],
                "view": [s.name for s in SECTIONS if sec[s.key] == 1],
                "caps": [CAP_BY_KEY[c].name for c in clean_caps(p.caps)],
            })
        ctx["rows"] = rows
        return ctx


class PresetEditView(PermissionRequiredMixin, TemplateView):
    permission_required = "user.manage_roles"
    template_name = "adminui/presets/form.html"

    def get_object(self):
        from apps.accounts.models import AccessPreset
        pk = self.kwargs.get("pk")
        return get_object_or_404(AccessPreset, pk=pk) if pk else AccessPreset()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        obj = kwargs.get("obj") or self.get_object()
        ctx["obj"] = obj
        ctx["error"] = kwargs.get("error", "")
        s, c = kwargs.get("access") or (obj.sections, obj.caps)
        ctx.update(access_form_context(s, c))
        ctx["acc_presets"] = []  # в самом шаблоне выбор шаблона не нужен
        return ctx

    def post(self, request, *args, **kwargs):
        from apps.accounts.models import AccessPreset
        obj = self.get_object()
        obj.name = request.POST.get("name", "").strip()[:100]
        obj.description = request.POST.get("description", "").strip()[:255]
        obj.sections, obj.caps = access_from_post(request.POST)
        if not obj.name:
            return self.render_to_response(self.get_context_data(obj=obj, error="Укажите название.",
                                                                 access=(obj.sections, obj.caps)))
        if AccessPreset.objects.filter(name=obj.name).exclude(pk=obj.pk).exists():
            return self.render_to_response(self.get_context_data(obj=obj, error="Шаблон с таким названием уже есть.",
                                                                 access=(obj.sections, obj.caps)))
        obj.save()
        _messages.success(request, f"Шаблон «{obj.name}» сохранён. Права уже назначенных пользователей не меняются.")
        return _redirect("adminui:presets")


class PresetDeleteView(PermissionRequiredMixin, View):
    permission_required = "user.manage_roles"

    def post(self, request, pk):
        from apps.accounts.models import AccessPreset
        obj = get_object_or_404(AccessPreset, pk=pk)
        obj.delete()
        _messages.success(request, f"Шаблон «{obj.name}» удалён.")
        return _redirect("adminui:presets")


# Переопределяем формы пользователя: + матрица прав, + защита учёток суперпользователей.
# (Классы выше объявлены раньше миксина — переназначаем имена, urls берут уже эти.)
class UserCreateView(UserAccessMixin, UserCreateView):  # noqa: F811
    pass


class UserUpdateView(UserAccessMixin, UserUpdateView):  # noqa: F811
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            _guard_superuser_target(request, self.get_object())
        return super().dispatch(request, *args, **kwargs)


class UserDeleteView(UserDeleteView):  # noqa: F811
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            _guard_superuser_target(request, self.get_object())
        return super().dispatch(request, *args, **kwargs)
