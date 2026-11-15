from django.core.exceptions import ValidationError
from django.db import models


class Station(models.Model):
    """
    Станция — физическая точка (город/узел), где стоит оборудование.
    На станции могут быть:
      - Device (D-Link/Eltex коммутаторы) через StationDevice
      - NeDevice (Olencom NE) через StationNe
    Координаты x/y — для drag&drop на SVG-схеме (в условных единицах viewBox).
    """
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True)

    # Координаты для схемы (drag&drop), условные единицы viewBox (например 0..1000)
    x = models.FloatField(default=100.0)
    y = models.FloatField(default=100.0)

    # Гео (опционально, для будущей карты)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("sort_order", "name")
        verbose_name = "Станция"
        verbose_name_plural = "Станции"

    def __str__(self):
        return self.name


class StationDevice(models.Model):
    """Привязка коммутатора (Device) к станции."""
    station = models.ForeignKey(
        Station, on_delete=models.CASCADE, related_name="devices"
    )
    device = models.ForeignKey(
        "devices.Device", on_delete=models.CASCADE, related_name="station_links"
    )
    role = models.CharField(max_length=32, blank=True)  # uplink / access / ...

    class Meta:
        unique_together = ("station", "device")
        verbose_name = "Устройство на станции"
        verbose_name_plural = "Устройства на станциях"

    def __str__(self):
        return f"{self.station.name} · {self.device.name}"


class StationNe(models.Model):
    """Привязка NE-мультиплексора (NeDevice) к станции."""
    station = models.ForeignKey(
        Station, on_delete=models.CASCADE, related_name="nes"
    )
    ne = models.ForeignKey(
        "devices.NeDevice", on_delete=models.CASCADE, related_name="station_links"
    )
    role = models.CharField(max_length=32, blank=True)

    class Meta:
        unique_together = ("station", "ne")
        verbose_name = "NE на станции"
        verbose_name_plural = "NE на станциях"

    def __str__(self):
        return f"{self.station.name} · {self.ne.display_name}"


class OpticalLink(models.Model):
    """
    Оптическая линия между двумя портами.
    Каждый конец — либо NeDevice (Olencom, порты SFP 1..3 / LAN 1..4),
    либо Device (D-Link/Eltex, порт = if_index).

    Кэш DDM (rx_*_dbm, link_*_up, link_status) обновляется Celery-задачей
    poll_optical_links — чтобы UI не дёргал SNMP.
    """
    PORT_TYPE_CHOICES = [
        ("sfp", "SFP"),
        ("lan", "Ethernet LAN"),
    ]

    LINK_STATUS_CHOICES = [
        ("ok", "Норма"),
        ("warn", "Предупреждение"),
        ("degraded", "Деградация сигнала"),
        ("crit", "Авария"),
        ("unknown", "Неизвестно"),
    ]

    # ── Конец A ─────────────────────────────────────────────
    station_a = models.ForeignKey(
        Station, on_delete=models.CASCADE, related_name="links_a"
    )
    ne_a = models.ForeignKey(
        "devices.NeDevice", on_delete=models.CASCADE,
        null=True, blank=True, related_name="links_a",
    )
    device_a = models.ForeignKey(
        "devices.Device", on_delete=models.CASCADE,
        null=True, blank=True, related_name="links_a",
    )
    port_a_type = models.CharField(
        max_length=8, choices=PORT_TYPE_CHOICES, default="sfp"
    )
    port_a_num = models.PositiveSmallIntegerField(default=1)

    # ── Конец B ─────────────────────────────────────────────
    station_b = models.ForeignKey(
        Station, on_delete=models.CASCADE, related_name="links_b"
    )
    ne_b = models.ForeignKey(
        "devices.NeDevice", on_delete=models.CASCADE,
        null=True, blank=True, related_name="links_b",
    )
    device_b = models.ForeignKey(
        "devices.Device", on_delete=models.CASCADE,
        null=True, blank=True, related_name="links_b",
    )
    port_b_type = models.CharField(
        max_length=8, choices=PORT_TYPE_CHOICES, default="sfp"
    )
    port_b_num = models.PositiveSmallIntegerField(default=1)

    # ── Метаданные ──────────────────────────────────────────
    label = models.CharField(max_length=128, blank=True)
    description = models.TextField(blank=True)
    path_geojson = models.JSONField(
        null=True, blank=True,
        help_text="Координаты [[lat, lon], ...] вдоль ж/д (для отрисовки)",
    )

    is_active = models.BooleanField(default=True)

    # ── Кэш DDM ─────────────────────────────────────────────
    rx_a_dbm = models.FloatField(null=True, blank=True)
    tx_a_dbm = models.FloatField(null=True, blank=True)
    temp_a_c = models.FloatField(null=True, blank=True)
    bias_a_ma = models.FloatField(null=True, blank=True)
    volt_a_v = models.FloatField(null=True, blank=True)
    rx_b_dbm = models.FloatField(null=True, blank=True)
    tx_b_dbm = models.FloatField(null=True, blank=True)
    temp_b_c = models.FloatField(null=True, blank=True)
    bias_b_ma = models.FloatField(null=True, blank=True)
    volt_b_v = models.FloatField(null=True, blank=True)
    link_a_up = models.BooleanField(null=True, blank=True)
    link_b_up = models.BooleanField(null=True, blank=True)
    link_status = models.CharField(
        max_length=16, choices=LINK_STATUS_CHOICES, default="unknown"
    )
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("label", "pk")
        verbose_name = "Оптическая линия"
        verbose_name_plural = "Оптические линии"
        indexes = [
            models.Index(fields=("station_a", "station_b")),
            models.Index(fields=("link_status",)),
        ]

    def __str__(self):
        if self.label:
            return self.label
        a = self.station_a.name if self.station_a else "?"
        b = self.station_b.name if self.station_b else "?"
        return f"{a} — {b}"

    # ── Валидация ───────────────────────────────────────────
    def clean(self):
        errors = {}

        # Ровно один из ne/device на каждом конце
        if bool(self.ne_a) == bool(self.device_a):
            errors["ne_a"] = "Укажите ровно одно: NE или Device (конец A)."
        if bool(self.ne_b) == bool(self.device_b):
            errors["ne_b"] = "Укажите ровно одно: NE или Device (конец B)."

        # Станция A должна совпадать со станцией устройства A
        # (проверяем, если устройство уже привязано к станции)
        if self.ne_a and self.station_a_id:
            ok = StationNe.objects.filter(
                station_id=self.station_a_id, ne=self.ne_a
            ).exists()
            if not ok:
                errors["station_a"] = (
                    f"{self.ne_a.display_name} не привязан к станции "
                    f"{self.station_a}."
                )
        if self.device_a and self.station_a_id:
            ok = StationDevice.objects.filter(
                station_id=self.station_a_id, device=self.device_a
            ).exists()
            if not ok:
                errors["station_a"] = (
                    f"{self.device_a.name} не привязан к станции "
                    f"{self.station_a}."
                )
        if self.ne_b and self.station_b_id:
            ok = StationNe.objects.filter(
                station_id=self.station_b_id, ne=self.ne_b
            ).exists()
            if not ok:
                errors["station_b"] = (
                    f"{self.ne_b.display_name} не привязан к станции "
                    f"{self.station_b}."
                )
        if self.device_b and self.station_b_id:
            ok = StationDevice.objects.filter(
                station_id=self.station_b_id, device=self.device_b
            ).exists()
            if not ok:
                errors["station_b"] = (
                    f"{self.device_b.name} не привязан к станции "
                    f"{self.station_b}."
                )

        # Оба конца не должны быть одним и тем же портом
        if (
            self.station_a_id == self.station_b_id
            and self.ne_a_id == self.ne_b_id
            and self.device_a_id == self.device_b_id
            and self.port_a_type == self.port_b_type
            and self.port_a_num == self.port_b_num
        ):
            errors["port_b_num"] = "Конец A и конец B совпадают."

        if errors:
            raise ValidationError(errors)

    # ── Удобные свойства для UI ─────────────────────────────
    @property
    def status_emoji(self):
        return {
            "ok": "🟢", "warn": "🟡", "crit": "🔴", "unknown": "⚪",
        }.get(self.link_status, "⚪")

    @property
    def end_a_label(self):
        if self.ne_a:
            return f"{self.station_a.name} · {self.ne_a.display_name} · {self.port_a_type.upper()}{self.port_a_num}"
        if self.device_a:
            # Для Ethernet-портов показываем тип и номер (LAN14), а не if_index
            if self.port_a_type == "lan":
                return f"{self.station_a.name} · {self.device_a.name} · LAN{self.port_a_num}"
            return f"{self.station_a.name} · {self.device_a.name} · {self.port_a_type.upper()}{self.port_a_num}"
        return f"{self.station_a.name} · ?"

    @property
    def end_b_label(self):
        if self.ne_b:
            return f"{self.station_b.name} · {self.ne_b.display_name} · {self.port_b_type.upper()}{self.port_b_num}"
        if self.device_b:
            if self.port_b_type == "lan":
                return f"{self.station_b.name} · {self.device_b.name} · LAN{self.port_b_num}"
            return f"{self.station_b.name} · {self.device_b.name} · {self.port_b_type.upper()}{self.port_b_num}"
        return f"{self.station_b.name} · ?"

    def recalc_status(self):
        """Возвращает вычисленный link_status на основе кэша DDM и доступности устройств."""
        # 1. Если порт явно упал (link down) — это авария
        if self.link_a_up is False or self.link_b_up is False:
            return "crit"
            
        # 2. Если устройство недоступно по SNMP/ICMP — это потеря сигнала.
        if self.device_a and not self.device_a.is_reachable:
            return "no_signal"
        if self.device_b and not self.device_b.is_reachable:
            return "no_signal"

        # 3. Собираем доступные значения RX (игнорируя None, если на одной из сторон нет DDM)
        rx_values = [rx for rx in (self.rx_a_dbm, self.rx_b_dbm) if rx is not None]
        
        # Если данных нет вообще ни с одной стороны (нет DDM) — это неизвестность
        if not rx_values:
            return "unknown"
            
        # 4. Оцениваем по худшему из доступных RX
        worst = min(rx_values)
        if worst < -25:
            return "crit"
        if worst < -20:
            return "warn"
        return "ok"

    # ── DDM: динамические свойства из MetricValue ─────────────
    def _last_metric(self, ne, key, instance):
        """Последнее значение метрики NE по (key, instance)."""
        if ne is None:
            return None
        from apps.monitoring.models import MetricValue
        mv = (
            MetricValue.objects
            .filter(ne_device=ne, metric__key=key, instance=str(instance))
            .order_by("-ts")
            .values_list("value", flat=True)
            .first()
        )
        return mv

    def ddm_a(self):
        """DDM-метрики для конца A: rx, tx, temp, bias, volt."""
        return self._ddm_for_side("a")

    def ddm_b(self):
        """DDM-метрики для конца B."""
        return self._ddm_for_side("b")

    def _ddm_for_side(self, side):
        """Собирает DDM из MetricValue для ne_X / device_X."""
        ne = getattr(self, f"ne_{side}", None)
        port = getattr(self, f"port_{side}_num")
        if ne is None:
            return None

        # Возраст: последний sfp_ MetricValue для этого NE
        from apps.monitoring.models import MetricValue
        last_ts = (
            MetricValue.objects
            .filter(ne_device=ne, metric__key__startswith="sfp_")
            .order_by("-ts")
            .values_list("ts", flat=True)
            .first()
        )

        return {
            "rx_dbm": self._last_metric(ne, "sfp_rx_dbm", port),
            "tx_dbm": self._last_metric(ne, "sfp_tx_dbm", port),
            "temp_c": self._last_metric(ne, "sfp_temp", port),
            "bias_ma": self._last_metric(ne, "sfp_bias", port),
            "volt_v": self._last_metric(ne, "sfp_volt", port),
            "link":   self._last_metric(ne, "sfp_link", port),
            "updated_at": last_ts.isoformat() if last_ts else None,
        }


class OpticalClosure(models.Model):
    """Оптическая муфта на линии (пассивная, только для визуализации)."""
    link = models.ForeignKey(
        OpticalLink, on_delete=models.CASCADE, related_name="closures",
        help_text="Оптическая линия, на которой находится муфта"
    )
    name = models.CharField(max_length=128, help_text="Название муфты")
    lat = models.FloatField(help_text="Широта")
    lon = models.FloatField(help_text="Долгота")
    description = models.TextField(blank=True)
    expected_rx_dbm = models.FloatField(null=True, blank=True, help_text="Ожидаемый уровень сигнала (расчетный)")
    total_loss_db = models.FloatField(null=True, blank=True, help_text="Суммарные потери от станции А (дБ)")
    expected_rx_b_dbm = models.FloatField(null=True, blank=True, help_text="Ожидаемый RX от станции Б (дБм)")
    total_loss_b_db = models.FloatField(null=True, blank=True, help_text="Суммарные потери от станции Б (дБ)")
    segment_loss_db = models.FloatField(null=True, blank=True, help_text="Потери на участке от предыдущей муфты (дБ)")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("link", "name")
        verbose_name = "Оптическая муфта"
        verbose_name_plural = "Оптические муфты"

    def __str__(self):
        return f"{self.name} ({self.link})"
