"""Корреляция при создании алерта «недоступно» — где бы его ни создали."""
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Alert


@receiver(post_save, sender=Alert, dispatch_uid="alerts.correlation.on_created")
def _alert_created(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        from .correlation import on_created
        on_created(instance)
