from django.contrib import admin
from .models import Server

@admin.register(Server)
class ServerAdmin(admin.ModelAdmin):
    list_display = ('name', 'ip_address', 'is_active', 'last_seen')
