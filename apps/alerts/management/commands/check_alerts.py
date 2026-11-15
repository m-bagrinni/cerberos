import logging
from apps.accounts.telegram_notify import notify_alert_created
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
from apps.subnets.models import IpAddress


logger = logging.getLogger(__name__)


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
            ("crit", 75, "gt", "Критический перегрев SFP"),
            ("warn", 70, "gt", "Перегрев SFP"),
        ],
    }
    NE_INSTANCE_RE = r"^[12]?$"

    # Правила для IpAddress (пинг-мониторинг): metric_key → (severity, threshold_sec)
    # Если IP молчит дольше threshold_sec и is_monitored=True → firing Alert
    IP_RULES = [
        # (severity, silence_seconds, message)
        ("crit", 120, "IP не отвечает более 2 минут"),
        ("warn", 60,  "IP не отвечает более 1 минуты"),
    ]

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

                # === ГИСТЕРЕЗИС: проверяем историю за последние 3 минуты ===
                from datetime import timedelta
                three_min_ago = timezone.now() - timedelta(minutes=3)
                recent_values = list(
                    MetricValue.objects.filter(
                        device=device, metric=metric, ts__gte=three_min_ago
                    ).order_by('-ts')[:30]
                )
                total_recent = len([rv for rv in recent_values if rv.value is not None])
                
                above_count = 0
                below_count = 0
                if threshold is not None:
                    above_count = sum(1 for rv in recent_values if rv.value is not None and rv.value >= threshold)
                    below_count = sum(1 for rv in recent_values if rv.value is not None and rv.value < threshold)
                
                is_stable_above = total_recent >= 3 and above_count / total_recent >= 0.8
                is_stable_below = total_recent >= 3 and below_count / total_recent >= 0.8

                if severity is None:
                    # Закрываем только при устойчивом спаде
                    if active and is_stable_below:
                        if not dry:
                            active.state = Alert.STATE_OK
                            active.resolved_at = timezone.now()
                            active.save(update_fields=["state", "resolved_at", "updated_at"])
                            try:
                                from apps.accounts.telegram_notify import notify_alert_resolved
                                notify_alert_resolved(active)
                            except Exception as _e:
                                logger.warning("TG notify (resolved) failed: %s", _e)
                        resolved += 1
                        self.stdout.write(f"  ✓ {device.name} {metric.key}: {value} → OK (устойчиво)")
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
                    # Создаем только если метрика устойчиво выше порога
                    if not is_stable_above:
                        self.stdout.write(f"  ~ {device.name} {metric.key}: {value} ({severity}) — пропущено (нестабильно)")
                        continue
                    
                    if not dry:
                        alert = Alert.objects.create(
                            device=device,
                            metric=metric,
                            metric_key=metric.key,
                            severity=severity,
                            state=Alert.STATE_FIRING,
                            value=value,
                            threshold=threshold,
                            message=msg,
                        )
                        try:
                            notify_alert_created(alert)
                        except Exception as _e:
                            logger.warning('TG notify failed: %s', _e)
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

        # --- 3. IP-адреса (пинг-мониторинг подсетей) ---
        ip_stats = self._check_ip_addresses(dry=dry)
        created += ip_stats["created"]
        still_firing += ip_stats["still_firing"]
        resolved += ip_stats["resolved"]

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
                                try:
                                    from apps.accounts.telegram_notify import notify_alert_resolved
                                    notify_alert_resolved(active)
                                except Exception as _e:
                                    logger.warning("TG notify (resolved) failed: %s", _e)
                            stats["resolved"] += 1
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
                        stats["still_firing"] += 1
                        self.stdout.write(f"  • NE-{ne.ne_id} {alert_key}: {value} ({severity}) — обновлено")
                    else:
                        if not dry:
                            alert = Alert.objects.create(
                                device=ne.gateway,
                                ne_device=ne,
                                metric_key=alert_key,
                                severity=severity,
                                state=Alert.STATE_FIRING,
                                value=value,
                                threshold=threshold,
                                message=full_msg,
                            )
                            try:
                                notify_alert_created(alert)
                            except Exception as _e:
                                logger.warning('TG notify failed: %s', _e)
                        stats["created"] += 1
                        self.stdout.write(
                            self.style.WARNING(
                                f"  🚨 NE-{ne.ne_id} {alert_key}: {value} ({severity}) — создан алерт"
                            )
                        )

        return stats

    # ============================================================
    # IP-адреса (пинг-мониторинг подсетей)
    # ============================================================

    def _check_ip_addresses(self, dry=False):
        """
        Проверяет все IpAddress с is_monitored=True.

        Логика:
          - Если is_alive=True → закрываем алерт (если был)
          - Если is_alive=False и IP молчит больше порога → создаём firing Alert
          - Пороги: warn=60 сек, crit=120 сек (выбирается самый серьёзный)
        """
        stats = {"created": 0, "resolved": 0, "still_firing": 0}
        now = timezone.now()

        monitored = IpAddress.objects.filter(is_monitored=True).select_related("subnet")

        for ip in monitored:
            alert_key = f"ip_ping:{ip.id}"

            active = Alert.objects.filter(
                ip_address=ip,
                metric_key=alert_key,
                state=Alert.STATE_FIRING,
            ).first()

            # ── Живой → закрываем алерт ──
            if ip.is_alive:
                if active:
                    if not dry:
                        active.state = Alert.STATE_OK
                        active.resolved_at = now
                        active.save(update_fields=["state", "resolved_at", "updated_at"])
                        try:
                            from apps.accounts.telegram_notify import notify_alert_resolved
                            notify_alert_resolved(active)
                        except Exception as _e:
                            logger.warning("TG notify (resolved) failed: %s", _e)
                    stats["resolved"] += 1
                    self.stdout.write(f"  ✓ IP {ip.ip}: OK (был алерт)")
                continue

            # ── Мёртвый → считаем молчание ──
            if not ip.last_seen_at:
                # Никогда не отвечал — не алертим
                continue

            silence = (now - ip.last_seen_at).total_seconds()

            # Определяем severity (берём самое серьёзное сработавшее правило)
            severity = threshold = msg = None
            for sev, thr, message in self.IP_RULES:
                if silence >= thr:
                    severity, threshold, msg = sev, thr, message
                    break

            if severity is None:
                # Ещё рано алертить
                if active:
                    # Алерт был, но теперь восстановился частично — не трогаем
                    pass
                continue

            # ── Формируем сообщение ──
            full_msg = f"IP {ip.ip} ({ip.subnet.cidr}): {msg} ({int(silence)} сек)"

            if active:
                # Обновляем существующий
                if not dry:
                    active.value = silence
                    active.threshold = threshold
                    active.severity = severity
                    active.message = full_msg
                    active.save(update_fields=[
                        "value", "threshold", "severity", "message", "updated_at",
                    ])
                stats["still_firing"] += 1
                self.stdout.write(f"  • IP {ip.ip}: молчит {int(silence)} сек ({severity}) — обновлено")
            else:
                # Создаём новый
                if not dry:
                    alert = Alert.objects.create(
                        ip_address=ip,
                        metric_key=alert_key,
                        severity=severity,
                        state=Alert.STATE_FIRING,
                        value=silence,
                        threshold=threshold,
                        message=full_msg,
                    )
                    try:
                        notify_alert_created(alert)
                    except Exception as _e:
                        logger.warning('TG notify failed: %s', _e)
                stats["created"] += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"  🚨 IP {ip.ip}: молчит {int(silence)} сек ({severity}) — создан алерт"
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
