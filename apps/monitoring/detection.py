"""
Определение вендора, модели и профиля устройства по SNMP.

Разделение (важно для Django 5):
  - async gather_snmp_info()       — только SNMP, БЕЗ ORM
  - sync  resolve_vendor_profile() — Django ORM (только sync!)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from apps.devices.models import Device, DeviceProfile, Vendor

from .snmp import SnmpClient, SnmpError, SnmpTarget, SnmpTimeout

log = logging.getLogger(__name__)


@dataclass
class SnmpInfo:
    sys_object_id: str = ""
    sys_descr: str = ""
    sys_name: str = ""
    ent_model: str = ""
    error: str = ""


@dataclass
class DetectionResult:
    vendor: Vendor | None
    profile: DeviceProfile | None
    sys_object_id: str
    sys_descr: str
    sys_name: str
    ent_model: str
    error: str = ""

    @property
    def success(self) -> bool:
        return self.error == "" and bool(self.sys_object_id or self.sys_descr)


async def gather_snmp_info(
    ip: str,
    community: str = "public",
    port: int = 161,
    timeout: float = 2.0,
    retries: int = 1,
) -> SnmpInfo:
    """Только SNMP. Никаких обращений к Django ORM."""
    client = SnmpClient(SnmpTarget(
        host=ip, community=community, port=port,
        timeout=timeout, retries=retries,
    ))

    try:
        sys_info = await client.get_sys_info()
    except SnmpTimeout as e:
        return SnmpInfo(error=f"timeout: {e}")
    except SnmpError as e:
        return SnmpInfo(error=f"snmp error: {e}")
    except Exception as e:
        return SnmpInfo(error=f"unexpected: {e}")

    info = SnmpInfo(
        sys_object_id=(sys_info.get("sysObjectID") or "").strip(),
        sys_descr=(sys_info.get("sysDescr") or "").strip(),
        sys_name=(sys_info.get("sysName") or "").strip(),
    )

    try:
        m = await client.get_ent_model()
        if m:
            info.ent_model = m
    except Exception:
        pass

    return info


def resolve_vendor_profile(info: SnmpInfo) -> DetectionResult:
    """Синхронный ORM-матчинг вендора и профиля."""
    if info.error:
        return DetectionResult(
            None, None, info.sys_object_id, info.sys_descr,
            info.sys_name, info.ent_model, error=info.error,
        )

    vendor = _match_vendor(info.sys_object_id)
    profile = _match_profile(
        info.sys_object_id, info.sys_descr, info.ent_model, vendor,
    )
    return DetectionResult(
        vendor=vendor,
        profile=profile,
        sys_object_id=info.sys_object_id,
        sys_descr=info.sys_descr,
        sys_name=info.sys_name,
        ent_model=info.ent_model,
    )


def _match_vendor(sys_object_id: str) -> Vendor | None:
    if not sys_object_id:
        return None
    for vendor in Vendor.objects.all():
        for eid in (vendor.enterprise_ids or []):
            prefix = f"1.3.6.1.4.1.{eid}."
            if sys_object_id.startswith(prefix):
                return vendor
    return None


def _match_profile(
    sys_object_id: str,
    sys_descr: str,
    ent_model: str,
    vendor: Vendor | None,
) -> DeviceProfile | None:
    qs = DeviceProfile.objects.all()
    if vendor is not None:
        qs = qs.filter(vendor=vendor)
    qs = qs.order_by("-priority")

    for profile in qs:
        if _profile_matches(profile, sys_object_id, sys_descr, ent_model):
            return profile
    return None


def _profile_matches(
    profile: DeviceProfile,
    sys_object_id: str,
    sys_descr: str,
    ent_model: str,
) -> bool:
    prefixes = profile.match_sysobject_prefixes or []
    if prefixes:
        if not any(sys_object_id.startswith(p) for p in prefixes):
            return False

    if profile.match_sysdescr_regex:
        if not sys_descr:
            return False
        if not re.search(profile.match_sysdescr_regex, sys_descr, re.IGNORECASE):
            return False

    if profile.match_model_regex:
        if not ent_model:
            return False
        if not re.search(profile.match_model_regex, ent_model, re.IGNORECASE):
            return False

    return True


def apply_detection(device: Device, result: DetectionResult) -> Device:
    device.sys_object_id = (result.sys_object_id or "")[:255]
    device.sys_descr = result.sys_descr or ""
    device.ent_model = (result.ent_model or "")[:255]

    if result.vendor is not None:
        device.vendor = result.vendor
    if result.profile is not None:
        device.profile = result.profile

    device.save(update_fields=[
        "sys_object_id", "sys_descr", "ent_model", "vendor", "profile",
    ])
    return device
