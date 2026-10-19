"""Админка подсетей."""

from django.contrib import admin

from .models import IpAddress, PingSample, Subnet, SubnetScan


class IpAddressInline(admin.TabularInline):
    model = IpAddress
    extra = 0
    fields = ("ip", "hostname", "state", "is_alive", "is_monitored", "last_rtt_ms")
    readonly_fields = ("last_rtt_ms",)
    show_change_link = True


@admin.register(Subnet)
class SubnetAdmin(admin.ModelAdmin):
    list_display = ("cidr", "name", "gateway_ip", "is_active", "scan_status", "last_scan_at")
    list_filter = ("is_active", "scan_status")
    search_fields = ("cidr", "name", "gateway_ip")
    inlines = [IpAddressInline]


@admin.register(IpAddress)
class IpAddressAdmin(admin.ModelAdmin):
    list_display = ("ip", "subnet", "hostname", "state", "is_alive", "is_monitored", "last_seen_at")
    list_filter = ("state", "is_alive", "is_monitored", "subnet")
    search_fields = ("ip", "hostname", "mac")
    autocomplete_fields = ("subnet",)


@admin.register(PingSample)
class PingSampleAdmin(admin.ModelAdmin):
    list_display = ("ip_address", "ts", "rtt_ms", "is_alive")
    list_filter = ("is_alive",)
    date_hierarchy = "ts"


@admin.register(SubnetScan)
class SubnetScanAdmin(admin.ModelAdmin):
    list_display = ("subnet", "started_at", "finished_at", "status", "hosts_total", "hosts_alive", "duration_ms")
    list_filter = ("status", "subnet")
    date_hierarchy = "started_at"
