"""DRF-сериализаторы для приложения subnets."""

import ipaddress

from rest_framework import serializers

from .models import IpAddress, PingSample, Subnet, SubnetScan


class SubnetListSerializer(serializers.ModelSerializer):
    """Для списка + создания подсети."""
    stats = serializers.SerializerMethodField()
    populate_ips = serializers.BooleanField(write_only=True, required=False, default=True)

    class Meta:
        model = Subnet
        fields = (
            "id", "cidr", "name", "description", "gateway_ip", "vlan_id",
            "is_active", "scan_status", "last_scan_at", "scan_interval_min",
            "last_scan_error", "stats", "populate_ips",
        )
        read_only_fields = ("scan_status", "last_scan_at", "last_scan_error")

    def get_stats(self, obj):
        return obj.stats()

    def validate_cidr(self, value):
        """Проверяем, что CIDR валидный и не пересекается с существующими."""
        try:
            net = ipaddress.ip_network(value, strict=False)
        except ValueError as e:
            raise serializers.ValidationError(f"Неверный CIDR: {e}")

        # Проверка пересечений с другими подсетями
        instance = self.instance
        for existing in Subnet.objects.exclude(pk=instance.pk if instance else None):
            try:
                other = ipaddress.ip_network(existing.cidr, strict=False)
            except ValueError:
                continue
            if net.overlaps(other):
                raise serializers.ValidationError(
                    f"Пересекается с {existing.cidr} ({existing.name})"
                )

        # Нормализуем: 192.168.7.5/24 → 192.168.7.0/24
        return str(net)

    def create(self, validated_data):
        populate = validated_data.pop("populate_ips", True)
        subnet = Subnet.objects.create(**validated_data)

        if populate:
            try:
                net = ipaddress.ip_network(subnet.cidr, strict=False)
            except ValueError:
                net = None

            if net:
                hosts = list(net.hosts()) or [net.network_address]
                bulk = [
                    IpAddress(subnet=subnet, ip=str(h), state=IpAddress.STATE_UNKNOWN)
                    for h in hosts
                ]
                if bulk:
                    IpAddress.objects.bulk_create(bulk, batch_size=500)

        return subnet


class IpAddressSerializer(serializers.ModelSerializer):
    subnet_cidr = serializers.CharField(source="subnet.cidr", read_only=True)
    last_octet = serializers.SerializerMethodField()
    mac = serializers.SerializerMethodField()
    station_name = serializers.CharField(source="station.name", read_only=True)
    arp_mac = serializers.SerializerMethodField()
    arp_device = serializers.SerializerMethodField()
    arp_interface = serializers.SerializerMethodField()
    is_device_ip = serializers.SerializerMethodField()
    device_name = serializers.SerializerMethodField()
    fdb_port = serializers.SerializerMethodField()
    fdb_device = serializers.SerializerMethodField()
    fdb_sure = serializers.SerializerMethodField()

    class Meta:
        model = IpAddress
        fields = (
            "id", "subnet", "subnet_cidr", "ip", "last_octet",
            "hostname", "mac", "state", "station", "station_name", "description",
            "is_alive", "is_monitored", "is_reserved",
            "last_seen_at", "last_ping_at",
            "last_rtt_ms", "packet_loss_pct",
            "arp_mac", "arp_device", "arp_interface", "is_device_ip", "device_name", "fdb_port", "fdb_device", "fdb_sure",
        )




    def get_device_name(self, obj):
        from apps.devices.models import Device
        dev = Device.objects.filter(ip=obj.ip).first()
        return dev.name if dev else None


    def _fdb(self, obj):
        """(порт, уверенно) — где подключён узел по FDB (apps/monitoring/fdb.py); кэш на объект."""
        cache = self.__dict__.setdefault("_fdb_cache", {})
        if obj.pk not in cache:
            from apps.monitoring.fdb import locate
            from apps.monitoring.models import ArpEntry
            arp = ArpEntry.objects.filter(ip=obj.ip).order_by("-updated_at").first()
            mac = (arp.mac if arp else None) or obj.mac
            cache[obj.pk] = locate(mac) if mac else (None, False)
        return cache[obj.pk]

    def get_fdb_port(self, obj):
        port, _ = self._fdb(obj)
        if not port:
            return None
        return f"{port.port} · VLAN {port.vlan}" if port.vlan else port.port

    def get_fdb_device(self, obj):
        port, _ = self._fdb(obj)
        return port.device if port else None

    def get_fdb_sure(self, obj):
        """False — узел виден только на аплинках (его коммутатор доступа не опрашивается)."""
        port, sure = self._fdb(obj)
        return sure if port else None

    def get_is_device_ip(self, obj):
        from apps.devices.models import Device
        return Device.objects.filter(ip=obj.ip).exists()


    def get_mac(self, obj):
        # Если mac заполнен — возвращаем его
        if obj.mac:
            return obj.mac
        # Иначе ищем в ArpEntry
        from apps.monitoring.models import ArpEntry
        arp = ArpEntry.objects.filter(ip=obj.ip).first()
        return arp.mac if arp else ''

    def get_arp_mac(self, obj):
        from apps.monitoring.models import ArpEntry
        entry = ArpEntry.objects.filter(ip=obj.ip).first()
        return entry.mac if entry else None

    def get_arp_device(self, obj):
        from apps.monitoring.models import ArpEntry
        entry = ArpEntry.objects.filter(ip=obj.ip).select_related('device').first()
        return entry.device.name if entry else None

    def get_arp_interface(self, obj):
        from apps.monitoring.models import ArpEntry
        entry = ArpEntry.objects.filter(ip=obj.ip).first()
        if not entry:
            return None
        # Если в interface_name есть "VLAN" — извлекаем номер
        if entry.interface_name and 'VLAN' in entry.interface_name.upper():
            import re
            match = re.search(r'VLAN\s*(\d+)', entry.interface_name, re.IGNORECASE)
            if match:
                return f"VLAN {match.group(1)}"
        # Иначе показываем interface_name
        return entry.interface_name or None

    def get_last_octet(self, obj):
        try:
            return int(obj.ip.split(".")[-1])
        except Exception:
            return None


class IpGridSerializer(serializers.ModelSerializer):
    """Минимум полей для карты IP."""
    class Meta:
        model = IpAddress
        fields = (
            "id", "ip", "state", "is_alive", "is_monitored",
            "is_reserved", "hostname", "last_rtt_ms",
        )


class SubnetScanSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubnetScan
        fields = (
            "id", "subnet", "started_at", "finished_at", "status",
            "hosts_total", "hosts_alive", "duration_ms", "error",
        )


class PingSampleSerializer(serializers.ModelSerializer):
    class Meta:
        model = PingSample
        fields = ("id", "ts", "rtt_ms", "is_alive")
