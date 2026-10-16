"""Сериализаторы для API устройств."""
from rest_framework import serializers

from .models import Device, DeviceProfile, MetricDefinition, Vendor


class VendorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vendor
        fields = ("id", "slug", "name", "enterprise_ids")


class MetricDefinitionSerializer(serializers.ModelSerializer):
    profile_slug = serializers.CharField(source="profile.slug", read_only=True)

    class Meta:
        model = MetricDefinition
        fields = (
            "id", "profile", "profile_slug", "key", "name", "oid",
            "value_type", "scale", "unit", "transform", "transform_params",
            "warn", "crit", "enabled",
        )


class DeviceProfileSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    metrics_count = serializers.IntegerField(source="metrics.count", read_only=True)

    class Meta:
        model = DeviceProfile
        fields = (
            "id", "vendor", "vendor_name", "slug", "name", "parent",
            "match_sysobject_prefixes", "match_sysdescr_regex",
            "match_model_regex", "priority", "metrics_count",
        )


class DeviceListSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    profile_name = serializers.CharField(source="effective_profile.name", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    status_emoji = serializers.CharField(read_only=True)

    class Meta:
        model = Device
        fields = (
            "id", "name", "ip", "description",
            "vendor", "vendor_name",
            "profile", "profile_name",
            "status", "status_display", "status_emoji",
            "sys_object_id", "sys_descr", "ent_model",
            "last_seen", "last_polled_at", "created_at",
        )


class DeviceDetailSerializer(DeviceListSerializer):
    snmp_version = serializers.CharField()
    snmp_community = serializers.CharField()
    snmp_port = serializers.IntegerField()

    class Meta(DeviceListSerializer.Meta):
        fields = DeviceListSerializer.Meta.fields + (
            "snmp_version", "snmp_community", "snmp_port", "profile_override",
        )
