"""Модели мониторинга: значения метрик, интерфейсы, VLAN."""
from django.db import models


# ============================================================
# Значения метрик
# ============================================================

class MetricValue(models.Model):
    """Одно измерение метрики на устройстве."""
    device = models.ForeignKey(
        "devices.Device", on_delete=models.CASCADE, related_name="metric_values",
    )
    ne_device = models.ForeignKey(
        "devices.NeDevice", null=True, blank=True,
        on_delete=models.CASCADE, related_name="metric_values",
        verbose_name="NE-устройство",
        help_text="Заполнено для метрик подчинённых NE (мультиплексоры)",
    )
    metric = models.ForeignKey(
        "devices.MetricDefinition", on_delete=models.CASCADE, related_name="values",
    )
    ts = models.DateTimeField(auto_now_add=True, db_index=True)
    value = models.FloatField(null=True)
    raw_value = models.CharField(max_length=512, blank=True)
    instance = models.CharField(max_length=64, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["device", "metric", "-ts"]),
            # «последнее значение метрики NE по порту» — DDM линков, мультиплексоры
            models.Index(fields=["ne_device", "metric", "instance", "-ts"],
                         name="mv_ne_metric_inst_ts"),
            models.Index(fields=["-ts"]),
        ]
        verbose_name = "Значение метрики"
        verbose_name_plural = "Значения метрик"
        ordering = ("-ts",)

    def __str__(self):
        return f"{self.device.name} {self.metric.key}={self.value}"


# ============================================================
# Интерфейсы
# ============================================================

class Interface(models.Model):
    """Сетевой интерфейс устройства (физический, VLAN, SVI, mgmt, CPU)."""
    IF_TYPE_PHYSICAL = 6
    # Физические Ethernet-типы по IF-MIB (1.3.6.1.2.1.2.2.1.3):
    #   6   — ethernetCsmacd (DGS-3630 copper/SFP)
    #   62  — fastEthernet
    #   71  — fastEthernetFX
    #   117 — gigabitEthernet (DGS-3120 copper)
    PHYSICAL_IF_TYPES = (6, 62, 71, 117)
    IF_TYPE_L2VLAN = 135
    IF_TYPE_SVI = 142
    IF_TYPE_CPU = 53
    IF_TYPE_OTHER = 1

    device = models.ForeignKey(
        "devices.Device", on_delete=models.CASCADE, related_name="interfaces",
    )
    if_index = models.PositiveIntegerField()
    name = models.CharField(max_length=128, blank=True)
    descr = models.CharField(max_length=255, blank=True)
    alias = models.CharField(max_length=255, blank=True)
    if_type = models.IntegerField(default=0)
    speed_mbps = models.BigIntegerField(null=True, blank=True)
    admin_status = models.SmallIntegerField(default=1)
    oper_status = models.SmallIntegerField(default=2)
    pvid = models.IntegerField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("device", "if_index")
        indexes = [
            models.Index(fields=["device", "if_type"]),
            models.Index(fields=["device", "oper_status"]),
        ]
        verbose_name = "Интерфейс"
        verbose_name_plural = "Интерфейсы"
        ordering = ("device", "if_index")

    def __str__(self):
        return f"{self.device.name}:{self.name or self.if_index}"

    @property
    def is_physical(self):
        """
        Физический Ethernet-порт (copper или SFP).

        if_index < 150:
            - D-Link:  физика 1..26,    VLAN/SVI 1024+ → отсекается
            - Eltex:   Unit 1 физика 49..108, фантомные Unit 2..8 (157+)
                       и VLAN (1000+) → отсекается
        """
        return self.if_type in self.PHYSICAL_IF_TYPES and self.if_index < 150

    @property
    def is_vlan(self):
        return self.if_type == self.IF_TYPE_L2VLAN

    @property
    def is_svi(self):
        return self.if_type == self.IF_TYPE_SVI

    @property
    def is_cpu(self):
        return self.if_type == self.IF_TYPE_CPU

    @property
    def oper_status_str(self):
        return {1: "up", 2: "down", 3: "testing"}.get(self.oper_status, "unknown")

    @property
    def admin_status_str(self):
        return {1: "up", 2: "down"}.get(self.admin_status, "unknown")


# ============================================================
# VLAN
# ============================================================

class Vlan(models.Model):
    """VLAN на устройстве."""
    device = models.ForeignKey(
        "devices.Device", on_delete=models.CASCADE, related_name="vlans",
    )
    vlan_id = models.PositiveIntegerField()
    name = models.CharField(max_length=64, blank=True)
    if_index = models.PositiveIntegerField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("device", "vlan_id")
        verbose_name = "VLAN"
        verbose_name_plural = "VLAN-ы"
        ordering = ("device", "vlan_id")

    def __str__(self):
        return f"{self.device.name}:VLAN{self.vlan_id}"


class InterfaceVlan(models.Model):
    """Связь интерфейса и VLAN (tagged/untagged)."""
    interface = models.ForeignKey(
        Interface, on_delete=models.CASCADE, related_name="vlan_links",
    )
    vlan = models.ForeignKey(
        Vlan, on_delete=models.CASCADE, related_name="interface_links",
    )
    is_tagged = models.BooleanField(default=True)

    class Meta:
        unique_together = ("interface", "vlan")
        verbose_name = "VLAN на порту"
        verbose_name_plural = "VLAN-ы на портах"

    def __str__(self):
        tag = "T" if self.is_tagged else "U"
        return f"{self.interface.name} {tag} VLAN{self.vlan.vlan_id}"

# ============================================================
# Трафик и ошибки на интерфейсах
# ============================================================

class InterfaceSample(models.Model):
    """Замер трафика/ошибок на интерфейсе (rate считается из двух соседних сэмплов)."""
    interface = models.ForeignKey(
        Interface, on_delete=models.CASCADE, related_name="samples",
    )
    ts = models.DateTimeField(db_index=True)
    in_octets = models.BigIntegerField(null=True)
    out_octets = models.BigIntegerField(null=True)
    in_errors = models.BigIntegerField(null=True)
    out_errors = models.BigIntegerField(null=True)
    in_bps = models.BigIntegerField(null=True, help_text="Входящий rate, бит/с")
    out_bps = models.BigIntegerField(null=True, help_text="Исходящий rate, бит/с")

    class Meta:
        indexes = [
            models.Index(fields=["interface", "-ts"]),
            models.Index(fields=["-ts"]),
        ]
        verbose_name = "Замер интерфейса"
        verbose_name_plural = "Замеры интерфейсов"
        ordering = ("-ts",)

    def __str__(self):
        return f"{self.interface} @ {self.ts:%Y-%m-%d %H:%M:%S}"


# ============================================================
# SFP-модули (DDM)
# ============================================================

class SfpModule(models.Model):
    """DDM-данные SFP-модуля на интерфейсе."""
    interface = models.OneToOneField(
        Interface, on_delete=models.CASCADE, related_name="sfp",
    )
    vendor = models.CharField(max_length=64, blank=True)
    serial = models.CharField(max_length=64, blank=True)
    part_number = models.CharField(max_length=64, blank=True)
    wavelength_nm = models.IntegerField(null=True, blank=True)

    temperature_c = models.FloatField(null=True, blank=True)
    voltage_v = models.FloatField(null=True, blank=True)
    bias_current_ma = models.FloatField(null=True, blank=True)
    tx_power_dbm = models.FloatField(null=True, blank=True)
    rx_power_dbm = models.FloatField(null=True, blank=True)

    temp_state = models.CharField(max_length=16, blank=True)
    voltage_state = models.CharField(max_length=16, blank=True)
    bias_state = models.CharField(max_length=16, blank=True)
    tx_state = models.CharField(max_length=16, blank=True)
    rx_state = models.CharField(max_length=16, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "SFP-модуль"
        verbose_name_plural = "SFP-модули"

    def __str__(self):
        return f"SFP {self.interface.name}"


class ArpEntry(models.Model):
    """ARP-запись с коммутатора (IP ↔ MAC)."""
    device = models.ForeignKey(
        "devices.Device", on_delete=models.CASCADE, related_name="arp_entries",
    )
    ip = models.GenericIPAddressField()
    mac = models.CharField(max_length=17)  # AA:BB:CC:DD:EE:FF
    vlan_id = models.IntegerField(null=True, blank=True)
    interface_index = models.IntegerField(null=True, blank=True)  # if_index порта
    interface_name = models.CharField(max_length=128, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "ARP-запись"
        verbose_name_plural = "ARP-записи"
        unique_together = ("device", "ip", "vlan_id")
        indexes = [
            models.Index(fields=["ip"]),
            models.Index(fields=["mac"]),
        ]

    def __str__(self):
        return f"{self.ip} → {self.mac} (на {self.device.name})"


class FdbEntry(models.Model):
    """FDB-запись (MAC ↔ порт) с коммутатора."""
    device = models.ForeignKey(
        "devices.Device", on_delete=models.CASCADE, related_name="fdb_entries",
    )
    mac = models.CharField(max_length=17)  # AA:BB:CC:DD:EE:FF
    bridge_port = models.IntegerField()  # dot1dBasePort (внутренний порт моста)
    if_index = models.IntegerField(null=True, blank=True)  # if_index физического порта
    interface_name = models.CharField(max_length=128, blank=True)
    vlan_id = models.IntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "FDB-запись"
        verbose_name_plural = "FDB-записи"
        unique_together = ("device", "mac", "vlan_id", "bridge_port")
        indexes = [
            models.Index(fields=["mac"]),
            models.Index(fields=["device", "bridge_port"]),
        ]

    def __str__(self):
        return f"{self.mac} → порт {self.bridge_port} (на {self.device.name})"


class InterfaceStatusHistory(models.Model):
    """
    История изменений oper_status интерфейса.
    """
    interface = models.ForeignKey(
        Interface, on_delete=models.CASCADE,
        related_name="status_history",
    )
    old_status = models.SmallIntegerField(null=True, blank=True)
    new_status = models.SmallIntegerField()
    changed_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-changed_at"]
        indexes = [
            models.Index(fields=["interface", "-changed_at"]),
        ]

    def __str__(self):
        return f"{self.interface.name}: {self.old_status} → {self.new_status} @ {self.changed_at}"

