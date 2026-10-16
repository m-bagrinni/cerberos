"""
check_alerts — проверка порогов метрик на всех устройствах.

Обрабатывает:
- Обычные устройства (D-Link, Eltex) — CPU, RAM, temperature, uptime
- NE-мультиплексоры (Olencom) — sfp_link, sfp_rx_dbm, sfp_temp, ne_status

Использование:
    manage.py check_alerts
    manage.py check_alerts --device 172.16.101.2
    manage.py check_alerts --dry-run
"""

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from apps.alerts.models import Alert
from apps.devices.models import Device, MetricDefinition, NeDevice
from apps.monitoring.models import MetricValue


class Command(BaseCommand):
    help = "Проверить метрики и создать/закрыть алерты"

    # Метрики, обрабатываемые ТОЛЬКО для NeDevice (Olencom)
    NE_ONLY_METRICS = {
        "ne_status", "sfp_link", "sfp_rx_dbm", "sfp_tx_dbm",
        "sfp_bias", "sfp_voltage", "sfp_temp",
        "ne_name", "ne_ip",
    }

    # Правила для NeDevice: metric_key → [(severity, threshold, direction, message)]
    # direction: 'lt' (меньше порога = плохо), 'gt' (больше = плохо), 'ne' (не равно)
    NE_RULES = {
        "ne_status": [
            ("crit", 1, "ne", "NE недоступен"),
        ],
        "sfp_link": [
            ("crit", 1, "ne", "Разрыв линии SFP"),
        ],
        "sfp_rx_dbm": [
            ("crit", -25, "lt", "Критически низкий уровень RX"),
            ("warn", -20, "lt", "Низкий уровень RX"),
        ],
        "sfp_temp": [
            ("crit", 70, "gt", "Критический перегрев SFP"),
            ("warn", 65, "gt", "Перегрев SFP"),
        ],
    }
    NE_INSTANCE_RE = r"^[12]?$"

    def add_arguments(self, parser):
        parser.add_argument("--device", help="Только для одного устройства (IP)")
        parser.add_argument("--dry-run", action="store_true",
                            help="Не сохранять, только показать")

    def handle(self, *args, **options):
        device_filter = options.get("device")
        dry = options.get("dry_run")

        created = 0
        resolved = 0
        still_firing = 0

        # --- 1. Обычные устройства (D-Link, Eltex, ...) ---
        metrics_with_thresholds = MetricDefinition.objects.filter(
            Q(warn__isnull=False) | Q(crit__isnull=False),
            enabled=True,
        )

        devices = Device.objects.all()
        if device_filter:
            devices = devices.filter(ip=device_filter)

        for device in devices:
            effective = device.effective_profile
            if not effective:
                continue

            is_mux = (effective.device_category == "multiplexer")
            eff_metrics = effective.get_effective_metrics()

            for metric in metrics_with_thresholds:
                # Пропускаем NE-метрики для мультиплексоров — их обрабатывает _check_ne_devices
                if is_mux and metric.key in self.NE_ONLY_METRICS:
                    continue

                if metric.key not in eff_metrics:
                    continue

                mv = (
                    MetricValue.objects
                    .filter(device=device, metric=metric)
                    .order_by("-ts")
                    .first()
                )
                if mv is None or mv.value is None:
                    continue

                value = float(mv.value)
                severity, threshold = self._classify(value, metric.warn, metric.crit)

                active = Alert.objects.filter(
                    device=device,
                    ne_device__isnull=True,
                    metric_key=metric.key,
                    state=Alert.STATE_FIRING,
                ).first()

                if severity is None:
                    if active:
                        if not dry:
                            active.state = Alert.STATE_OK
                            active.resolved_at = timezone.now()
                            active.save(update_fields=["state", "resolved_at", "updated_at"])
                        resolved += 1
                        self.stdout.write(f"  ✓ {device.name} {metric.key}: {value} → OK")
                    continue

                msg = self._msg(device, metric, value, threshold, severity)

                if active:
                    if not dry:
                        active.value = value
                        active.threshold = threshold
                        active.severity = severity
                        active.message = msg
                        active.save(update_fields=["value", "threshold", "severity", "message", "updated_at"])
                    still_firing += 1
                    self.stdout.write(f"  • {device.name} {metric.key}: {value} ({severity}) — обновлено")
                else:
                    if not dry:
                        Alert.objects.create(
                            device=device,
                            metric=metric,
                            metric_key=metric.key,
                            severity=severity,
                            state=Alert.STATE_FIRING,
                            value=value,
                            threshold=threshold,
                            message=msg,
                        )
                    created += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"  🚨 {device.name} {metric.key}: {value} ({severity}) — создан алерт"
                        )
                    )

        # --- 2. Мультиплексоры (NeDevice) ---
        ne_stats = self._check_ne_devices(dry=dry, device_ip=device_filter)
        created += ne_stats["created"]
        still_firing += ne_stats["still_firing"]
        resolved += ne_stats["resolved"]

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Итого: создано={created}, обновлено={still_firing}, закрыто={resolved}"
            + (" (dry-run)" if dry else "")
        ))

    # ============================================================
    # NeDevice (мультиплексоры)
    # ============================================================

    def _check_ne_devices(self, dry=False, device_ip=None):
        """Проверка порогов для всех NeDevice."""
        import re

        stats = {"created": 0, "resolved": 0, "still_firing": 0}

        qs = NeDevice.objects.select_related("gateway").all()
        if device_ip:
            qs = qs.filter(gateway__ip=device_ip)

        for ne in qs:
            for metric_key, rules in self.NE_RULES.items():
                # Последнее значение по каждому instance
                latest = {}
                mv_qs = (
                    MetricValue.objects
                    .filter(ne_device=ne, metric__key=metric_key)
                    .order_by("instance", "-ts")
                )
                for mv in mv_qs:
                    if mv.instance in latest:
                        continue
                    if not re.match(self.NE_INSTANCE_RE, mv.instance or ""):
                        continue
                    latest[mv.instance] = mv

                for instance, mv in latest.items():
                    if mv.value is None:
                        continue
                    value = float(mv.value)

                    # Ищем самое серьёзное правило (первое сработавшее)
                    severity = threshold = msg = None
                    for sev, thr, direction, message in rules:
                        if self._ne_rule_fired(value, thr, direction):
                            severity, threshold, msg = sev, thr, message
                            break

                    alert_key = f"{metric_key}:{instance}" if instance else metric_key

                    active = Alert.objects.filter(
                        ne_device=ne,
                        metric_key=alert_key,
                        state=Alert.STATE_FIRING,
                    ).first()

                    if severity is None:
                        if active:
                            if not dry:
                                active.state = Alert.STATE_OK
                                active.resolved_at = timezone.now()
                                active.save(update_fields=["state", "resolved_at", "updated_at"])
                            resolved += 1
                            self.stdout.write(f"  ✓ NE-{ne.ne_id} {alert_key}: {value} → OK")
                        continue

                    full_msg = f"{ne.display_name}: {msg}"
                    if instance:
                        full_msg += f" (порт {instance})"

                    if active:
                        if not dry:
                            active.value = value
                            active.threshold = threshold
                            active.severity = severity
                            active.message = full_msg
                            active.save(update_fields=["value", "threshold", "severity", "message", "updated_at"])
                        still_firing += 1
                        self.stdout.write(f"  • NE-{ne.ne_id} {alert_key}: {value} ({severity}) — обновлено")
                    else:
                        if not dry:
                            Alert.objects.create(
                                device=ne.gateway,
                                ne_device=ne,
                                metric_key=alert_key,
                                severity=severity,
                                state=Alert.STATE_FIRING,
                                value=value,
                                threshold=threshold,
                                message=full_msg,
                            )
                        created += 1
                        self.stdout.write(
                            self.style.WARNING(
                                f"  🚨 NE-{ne.ne_id} {alert_key}: {value} ({severity}) — создан алерт"
                            )
                        )

        return stats

    # ============================================================
    # Утилиты
    # ============================================================

    @staticmethod
    def _classify(value, warn, crit):
        """Для обычных устройств: value >= порог → алерт."""
        if crit is not None and value >= crit:
            return (Alert.SEVERITY_CRIT, crit)
        if warn is not None and value >= warn:
            return (Alert.SEVERITY_WARN, warn)
        return (None, None)

    @staticmethod
    def _msg(device, metric, value, threshold, severity):
        unit = metric.unit or ""
        return f"{metric.name}: {value:.2f}{unit} ≥ {threshold:.2f}{unit}"

    @staticmethod
    def _ne_rule_fired(value, threshold, direction):
        if direction == "lt":
            return value < threshold
        if direction == "gt":
            return value > threshold
        if direction == "ne":
            return value != threshold
        return False
