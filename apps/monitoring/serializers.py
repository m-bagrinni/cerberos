"""Сериализаторы для API мониторинга."""
from rest_framework import serializers

from .models import Interface, InterfaceVlan, MetricValue, Vlan


class MetricValueSerializer(serializers.ModelSerializer):
    metric_key = serializers.CharField(source="metric.key", read_only=True)
    metric_name = serializers.CharField(source="metric.name", read_only=True)
    unit = serializers.CharField(source="metric.unit", read_only=True)

    class Meta:
        model = MetricValue
        fields = (
            "id", "metric", "metric_key", "metric_name", "unit",
            "ts", "value", "raw_value", "instance",
        )


class InterfaceSerializer(serializers.ModelSerializer):
    oper_status_display = serializers.CharField(source="oper_status_str", read_only=True)
    is_physical = serializers.BooleanField(read_only=True)
    is_vlan = serializers.BooleanField(read_only=True)
    is_svi = serializers.BooleanField(read_only=True)

    class Meta:
        model = Interface
        fields = (
            "id", "if_index", "name", "descr", "alias",
            "if_type", "speed_mbps",
            "admin_status", "oper_status", "oper_status_display",
            "pvid", "is_physical", "is_vlan", "is_svi",
            "updated_at",
        )


class VlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vlan
        fields = ("id", "vlan_id", "name", "if_index")


class InterfaceVlanSerializer(serializers.ModelSerializer):
    vlan_id = serializers.IntegerField(source="vlan.vlan_id", read_only=True)
    vlan_name = serializers.CharField(source="vlan.name", read_only=True)
    interface_name = serializers.CharField(source="interface.name", read_only=True)

    class Meta:
        model = InterfaceVlan
        fields = (
            "id", "interface", "interface_name",
            "vlan", "vlan_id", "vlan_name", "is_tagged",
        )


class InterfaceDetailSerializer(InterfaceSerializer):
    vlan_links = InterfaceVlanSerializer(many=True, read_only=True)

    class Meta(InterfaceSerializer.Meta):
        fields = InterfaceSerializer.Meta.fields + ("vlan_links",)
