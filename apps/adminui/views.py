"""Кастомная админ-панель Cerberus."""
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import (
    CreateView, DeleteView, ListView, TemplateView, UpdateView,
)

from apps.devices.models import (
    Device, DeviceProfile, MetricDefinition, NeDevice, User, Vendor,
)
from apps.monitoring.models import MetricValue
from apps.alerts.models import Alert
from apps.subnets.models import Subnet, IpAddress

from .forms import (
    DeviceFilterForm, DeviceForm, MetricDefinitionForm,
    AlertFilterForm, ProfileFilterForm, ProfileForm, SubnetForm, VendorForm,
)


class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    login_url = "/admin/login/"

    def test_func(self):
        return self.request.user.is_staff


# ============================================================
# Dashboard
# ============================================================

class DashboardView(AdminRequiredMixin, TemplateView):
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

class DeviceListView(AdminRequiredMixin, ListView):
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


class DeviceDetailView(AdminRequiredMixin, TemplateView):
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


class DeviceCreateView(AdminRequiredMixin, CreateView):
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


class DeviceUpdateView(AdminRequiredMixin, UpdateView):
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


class DeviceDeleteView(AdminRequiredMixin, DeleteView):
    model = Device
    template_name = "adminui/devices/delete_confirm.html"

    def get_success_url(self):
        return reverse("adminui:devices")


# ============================================================
# Profiles — list + placeholders (детали — Этап 3.2)
# ============================================================

class ProfileListView(AdminRequiredMixin, ListView):
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


class ProfileCreateView(AdminRequiredMixin, CreateView):
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


class ProfileDetailView(AdminRequiredMixin, TemplateView):
    """Карточка профиля + inline-таблица метрик."""
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


class ProfileUpdateView(AdminRequiredMixin, UpdateView):
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


class ProfileDeleteView(AdminRequiredMixin, DeleteView):
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


class ProfileCloneView(AdminRequiredMixin, TemplateView):
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


class ProfilesPlaceholderView(AdminRequiredMixin, TemplateView):
    template_name = "adminui/placeholder.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["section"] = "Профили"
        ctx["stage"] = "Этап 3"
        return ctx


# ============================================================
# Остальные — пока заглушки
# ============================================================

class MetricCreateView(AdminRequiredMixin, CreateView):
    """Добавление метрики в профиль."""
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


class MetricUpdateView(AdminRequiredMixin, UpdateView):
    """Редактирование метрики."""
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


class MetricDeleteView(AdminRequiredMixin, DeleteView):
    """Удаление метрики."""
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


class VendorListView(AdminRequiredMixin, ListView):
    """Список вендоров."""
    model = Vendor
    template_name = "adminui/vendors/list.html"
    context_object_name = "vendors"
    paginate_by = 50

    def get_queryset(self):
        return Vendor.objects.annotate(
            profiles_count=Count("profiles", distinct=True),
            devices_count=Count("devices", distinct=True),
        ).order_by("name")


class VendorCreateView(AdminRequiredMixin, CreateView):
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


class VendorUpdateView(AdminRequiredMixin, UpdateView):
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


class VendorDeleteView(AdminRequiredMixin, DeleteView):
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


class NeListView(AdminRequiredMixin, ListView):
    """Список NE (Olencom) с фильтром по шлюзу."""
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


class NeDetailView(AdminRequiredMixin, TemplateView):
    """Карточка NE (Olencom) + графики метрик."""
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
        from apps.monitoring.models import MetricValue
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


class SubnetListView(AdminRequiredMixin, ListView):
    """Список подсетей с прогресс-барами."""
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


class SubnetCreateView(AdminRequiredMixin, CreateView):
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


class SubnetUpdateView(AdminRequiredMixin, UpdateView):
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


class SubnetDeleteView(AdminRequiredMixin, DeleteView):
    model = Subnet
    template_name = "adminui/subnets/delete_confirm.html"

    def get_success_url(self):
        return reverse("adminui:subnets")


class AlertListView(AdminRequiredMixin, ListView):
    """Список алертов с фильтрами."""
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


class UserListView(AdminRequiredMixin, ListView):
    """Список пользователей."""
    from apps.devices.models import User as UserModel
    model = UserModel
    template_name = "adminui/users/list.html"
    context_object_name = "users"
    paginate_by = 100

    def get_queryset(self):
        return self.model.objects.all().order_by(
            "-is_superuser", "-is_staff", "username"
        )


class UserCreateView(AdminRequiredMixin, CreateView):
    from apps.devices.models import User as UserModel
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


class UserUpdateView(AdminRequiredMixin, UpdateView):
    from apps.devices.models import User as UserModel
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


class UserDeleteView(AdminRequiredMixin, DeleteView):
    from apps.devices.models import User as UserModel
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


class AlertDetailView(AdminRequiredMixin, TemplateView):
    """Карточка алерта."""
    template_name = "adminui/alerts/detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        alert = get_object_or_404(Alert, pk=kwargs["pk"])
        ctx["alert"] = alert
        return ctx
