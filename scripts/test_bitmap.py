"""Тест парсинга VLAN bitmap. Запуск: python scripts/test_bitmap.py"""
import os
import sys

sys.path.insert(0, "/app")

import django  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cerber.settings")
django.setup()

import asyncio  # noqa: E402

from apps.monitoring.management.commands.poll_interfaces import Command  # noqa: E402
from apps.monitoring.snmp import SnmpClient, SnmpTarget  # noqa: E402


async def main():
    c = SnmpClient(SnmpTarget(host="172.16.101.2", community="public"))
    rows = await c.walk("1.3.6.1.2.1.17.7.1.4.3.1.4", max_rows=3)
    cmd = Command()
    print(f"Walk вернул {len(rows)} строк\n")

    for oid, val in rows:
        print(f"OID: {oid}")
        print(f"  type: {type(val).__name__}")
        print(f"  prettyPrint(): {val.prettyPrint()[:80]}")

        try:
            print(f"  asOctets(): {val.asOctets().hex()[:80]}")
        except Exception as e:
            print(f"  asOctets ERROR: {type(e).__name__}: {e}")

        try:
            b = bytes(val)
            print(f"  bytes(val): {b.hex()[:80]} | len={len(b)}")
        except Exception as e:
            print(f"  bytes ERROR: {type(e).__name__}: {e}")

        bits = cmd._bitmap_set(val)
        print(f"  -> _bitmap_set: {sorted(bits)[:40]}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
