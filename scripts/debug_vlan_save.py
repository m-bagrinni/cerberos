"""Отладка _save: проверка что видит парсер с current таблицами."""
import os
import sys

sys.path.insert(0, "/app")

import django  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cerber.settings")
django.setup()

import asyncio  # noqa: E402

from apps.devices.models import Device  # noqa: E402
from apps.monitoring.management.commands.poll_interfaces import (  # noqa: E402
    Command, OID_VLAN_EGRESS, OID_VLAN_UNTAGGED,
)
from apps.monitoring.snmp import SnmpClient, SnmpTarget  # noqa: E402


async def fetch(ip, community):
    client = SnmpClient(SnmpTarget(host=ip, community=community))
    cmd = Command()
    return {
        "vlan_egress": cmd._to_dict(await client.walk(OID_VLAN_EGRESS)),
        "vlan_untagged": cmd._to_dict(await client.walk(OID_VLAN_UNTAGGED)),
    }


def main():
    device = Device.objects.get(ip="172.16.101.2")
    data = asyncio.run(fetch(device.ip, device.snmp_community or "public"))

    cmd = Command()
    print(f"vlan_egress keys: {sorted(data['vlan_egress'].keys())}")
    print(f"vlan_untagged keys: {sorted(data['vlan_untagged'].keys())}")
    print()

    for vid in [1, 2, 101]:
        print(f"=== VLAN {vid} ===")
        for name in ["vlan_egress", "vlan_untagged"]:
            if vid in data[name]:
                bits = cmd._bitmap_set(data[name][vid])
                print(f"  {name} -> {sorted(bits)[:30]}")
            else:
                print(f"  {name}: ключ {vid} НЕ найден")


if __name__ == "__main__":
    main()
