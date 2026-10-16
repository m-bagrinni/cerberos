from rest_framework import serializers

from apps.devices.models import Device, NeDevice

from .models import OpticalLink, Station, StationDevice, StationNe


# ── Оборудование на станции ────────────────────────────────
class StationDeviceSerializer(serializers.ModelSerializer):
    device_name = serializers.CharField(source="device.name", read_only=True)
    device_ip = serializers.CharField(source="device.ip", read_only=True)
    device_status = serializers.CharField(source="device.status", read_only=True)
    vendor = serializers.CharField(source="device.vendor.name", read_only=True, default="")

    class Meta:
        model = StationDevice
        fields = (
            "id", "device", "device_name", "device_ip", "device_status",
            "vendor", "role",
        )


class StationNeSerializer(serializers.ModelSerializer):
    ne_display_name = serializers.CharField(source="ne.display_name", read_only=True)
    ne_status = serializers.CharField(source="ne.status", read_only=True)
    gateway_ip = serializers.CharField(source="ne.gateway.ip", read_only=True)

    class Meta:
        model = StationNe
        fields = (
            "id", "ne", "ne_display_name", "ne_status", "gateway_ip", "role",
        )


# ── Станция ────────────────────────────────────────────────
class StationSerializer(serializers.ModelSerializer):
    devices = StationDeviceSerializer(many=True, read_only=True)
    nes = StationNeSerializer(many=True, read_only=True)
    links_count = serializers.SerializerMethodField()

    class Meta:
        model = Station
        fields = (
            "id", "slug", "name", "description",
            "x", "y", "latitude", "longitude", "sort_order",
            "devices", "nes", "links_count", "created_at",
        )
        read_only_fields = ("created_at",)

    def get_links_count(self, obj):
        return obj.links_a.count() + obj.links_b.count()


class StationListSerializer(serializers.ModelSerializer):
    devices_count = serializers.SerializerMethodField()
    nes_count = serializers.SerializerMethodField()

    class Meta:
        model = Station
        fields = ("id", "slug", "name", "x", "y", "sort_order",
                  "devices_count", "nes_count")

    def get_devices_count(self, obj):
        return obj.devices.count()

    def get_nes_count(self, obj):
        return obj.nes.count()


# ── Порт (для формы создания линии) ────────────────────────
class PortSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["ne", "device"])
    owner_id = serializers.IntegerField()
    owner_label = serializers.CharField()
    port_type = serializers.ChoiceField(choices=["sfp", "lan", "if"])
    port_num = serializers.IntegerField()
    port_label = serializers.CharField()
    link_up = serializers.BooleanField(allow_null=True)
    rx_dbm = serializers.FloatField(allow_null=True)
    temp_c = serializers.FloatField(allow_null=True)
    is_used = serializers.BooleanField()


# ── Оптическая линия ───────────────────────────────────────
class OpticalLinkSerializer(serializers.ModelSerializer):
    """
    ВАЖНО: station_a / station_b принимаются как slug (строка),
    а не как id — так удобнее для UI. SlugRelatedField сам найдёт объект.
    """
    station_a = serializers.SlugRelatedField(
        slug_field="slug",
        queryset=Station.objects.all(),
    )
    station_b = serializers.SlugRelatedField(
        slug_field="slug",
        queryset=Station.objects.all(),
    )

    end_a_label = serializers.CharField(read_only=True)
    ddm_a = serializers.SerializerMethodField()
    ddm_b = serializers.SerializerMethodField()
    end_b_label = serializers.CharField(read_only=True)
    status_emoji = serializers.CharField(read_only=True)

    class Meta:
        model = OpticalLink
        fields = (
            "id", "label", "description", "is_active",
            "station_a", "ne_a", "device_a", "port_a_type", "port_a_num",
            "station_b", "ne_b", "device_b", "port_b_type", "port_b_num",
            "rx_a_dbm", "rx_b_dbm", "link_a_up", "link_b_up",
            "link_status", "status_emoji",
            "end_a_label", "end_b_label",
            "ddm_a", "ddm_b",
            "updated_at", "created_at",
        )
        read_only_fields = (
            "rx_a_dbm", "rx_b_dbm", "link_a_up", "link_b_up",
            "link_status", "updated_at", "created_at",
        )

    def validate(self, attrs):
        """
        Собираем инстанс для full_clean — ловим наши кастомные
        проверки (NE ↔ Station, конец A ≠ конец B и т.п.).
        """
        instance = self.instance or OpticalLink()

        # station_* приходят как объекты Station из SlugRelatedField
        for k, v in attrs.items():
            setattr(instance, k, v)
        # На PATCH часть полей могла не прийти — берём из self.instance
        if self.instance:
            for field in self.Meta.fields:
                if field not in attrs and hasattr(self.instance, field):
                    setattr(instance, field, getattr(self.instance, field))

        try:
            instance.full_clean(exclude=["id"])
        except Exception as e:
            msg = e.message_dict if hasattr(e, "message_dict") else str(e)
            raise serializers.ValidationError(msg)
        return attrs


    def get_ddm_a(self, obj):
        return obj.ddm_a()

    def get_ddm_b(self, obj):
        return obj.ddm_b()
