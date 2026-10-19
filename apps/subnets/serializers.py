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

    class Meta:
        model = IpAddress
        fields = (
            "id", "subnet", "subnet_cidr", "ip", "last_octet",
            "hostname", "mac", "state",
            "is_alive", "is_monitored", "is_reserved",
            "last_seen_at", "last_ping_at",
            "last_rtt_ms", "packet_loss_pct",
        )

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
