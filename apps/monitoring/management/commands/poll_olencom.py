"""
Poll Olencom RP1000 (PDH Multiplexer) via SNMP v1.

Каждое NE — отдельное NeDevice. Метрики NE пишутся:
  - device   = шлюз (для обратной совместимости и запросов)
  - ne_device = конкретное NE
  - instance  = номер порта ("1", "2", "3".."6") для SFP/Ethernet
  - instance  = "" для метаданных самого NE (ne_status, ne_name)

Использование:
    python manage.py poll_olencom 172.16.101.12
    python manage.py poll_olencom 172.16.101.12 --ne 2 4 6
    python manage.py poll_olencom 172.16.101.12 --verbose
"""

import re
import struct
import subprocess
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.devices.models import Device, NeDevice, MetricDefinition
from apps.monitoring.models import MetricValue


SFP_PORTS = (1, 2, 3)
ETH_PORTS = (1, 2, 3, 4, 5, 6)

OID_BASE = "1.3.6.1.4.1.3399.2.3.3"

OID_NE_STATUS = f"{OID_BASE}.2.8.1.5"
OID_NE_NAME   = f"{OID_BASE}.2.8.1.20"
OID_NE_IP     = f"{OID_BASE}.1.2.1.1"

OID_SFP_PRESENT = f"{OID_BASE}.2.11.1.2"
OID_SFP_INFO    = f"{OID_BASE}.2.11.1.4"
OID_SFP_DDM     = f"{OID_BASE}.2.11.1.5"

OID_ETH_ENABLE = f"{OID_BASE}.2.1.1.2"
OID_ETH_PVID   = f"{OID_BASE}.2.1.1.6"
OID_ETH_MODE   = f"{OID_BASE}.2.1.1.7"
OID_ETH_STATUS = f"{OID_BASE}.2.1.1.11"
OID_ETH_CNT1   = f"{OID_BASE}.2.1.1.13"
OID_ETH_CNT2   = f"{OID_BASE}.2.1.1.20"


_re_int = re.compile(r'INTEGER:\s*(-?\d+)')
_re_hex = re.compile(r'Hex-STRING:\s*([0-9A-Fa-f\s]+)')
_re_ip  = re.compile(r'IpAddress:\s*(\d+\.\d+\.\d+\.\d+)')


def parse_int(out):
    m = _re_int.search(out)
    return int(m.group(1)) if m else None


def parse_hex(out):
    m = _re_hex.search(out)
    if not m:
        return None
    try:
        return [int(b, 16) for b in m.group(1).split()]
    except ValueError:
        return None


def parse_ip(out):
    m = _re_ip.search(out)
    return m.group(1) if m else None


def parse_ascii(byte_list):
    return "".join(chr(b) for b in byte_list if 32 <= b <= 126).strip()


def clean_ne_name(hex_bytes):
    if not hex_bytes:
        return ""
    raw = bytes(b for b in hex_bytes if b != 0)
    return "".join(chr(b) for b in raw if 32 <= b <= 126).strip()


def parse_ddm(hex_bytes):
    if not hex_bytes or len(hex_bytes) < 24:
        return None
    return {
        "temp_c":    struct.unpack(">H", bytes(hex_bytes[2:4]))[0] / 1000.0,
        "bias_ma":   struct.unpack(">H", bytes(hex_bytes[6:8]))[0] / 1000.0,
        "link":      1 if hex_bytes[8] == 1 else 0,
        "rx_dbm":    -struct.unpack(">H", bytes(hex_bytes[14:16]))[0] / 100.0,
        "tx_sign":   hex_bytes[16],
        "tx_dbm":    (-1 if hex_bytes[16] == 1 else 1)
                     * struct.unpack(">H", bytes(hex_bytes[18:20]))[0] / 100.0,
        "voltage_v": struct.unpack(">H", bytes(hex_bytes[22:24]))[0] / 10000.0,
    }


def parse_sfp_info(hex_bytes):
    if not hex_bytes or len(hex_bytes) < 48:
        return None
    return {
        "vendor": parse_ascii(hex_bytes[0:16]),
        "pn":     parse_ascii(hex_bytes[16:32]),
        "sn":     parse_ascii(hex_bytes[32:48]),
    }


def parse_counter_pair(hex_bytes):
    if not hex_bytes or len(hex_bytes) < 16:
        return None
    return {
        "c1": int.from_bytes(bytes(hex_bytes[0:8]), "little"),
        "c2": int.from_bytes(bytes(hex_bytes[8:16]), "little"),
    }


def snmpget(ip, community, oid, timeout=2, retries=1):
    cmd = ["snmpget", "-v", "1", "-c", community,
           "-On", "-t", str(timeout), "-r", str(retries), ip, oid]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 3)
        out = r.stdout.strip()
        if "noSuchName" in out or "No Such" in out or "Timeout" in out:
            return ""
        return out
    except Exception:
        return ""


class Command(BaseCommand):
    help = "Poll Olencom RP1000: шлюз + все его NeDevice через SNMP"

    def add_arguments(self, parser):
        parser.add_argument("gateway_ip", type=str)
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--ne", nargs="*", type=int,
                            help="Опрашивать только эти NE-ID")

    def handle(self, *args, **opts):
        gw_ip = opts["gateway_ip"]
        verbose = opts["verbose"]

        try:
            gateway = Device.objects.get(ip=gw_ip)
        except Device.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Gateway {gw_ip} not found"))
            return

        community = gateway.snmp_community or "public"
        profile = gateway.effective_profile
        if not profile:
            self.stderr.write(self.style.ERROR(f"Gateway {gw_ip} has no profile"))
            return

        metrics = {m.key: m for m in MetricDefinition.objects.filter(profile=profile, enabled=True)}
        if not metrics:
            self.stderr.write(self.style.ERROR(
                f"Profile {profile.slug} has no metrics. Run: seed_olencom"
            ))
            return

        # Список NE из БД (или из --ne)
        if opts.get("ne"):
            nes = list(NeDevice.objects.filter(gateway=gateway, ne_id__in=opts["ne"]))
        else:
            nes = list(NeDevice.objects.filter(gateway=gateway).order_by("ne_id"))

        if not nes:
            self.stderr.write(self.style.WARNING(
                "Нет NeDevice для этого шлюза. Запустите: discover_olencom_nes"
            ))
            return

        now = timezone.now()
        saved = 0

        def save(metric_key, ne_device, instance, value, raw=""):
            nonlocal saved
            m = metrics.get(metric_key)
            if m is None:
                return
            MetricValue.objects.create(
                device=gateway,
                ne_device=ne_device,
                metric=m,
                value=float(value) if value is not None else None,
                raw_value=str(raw)[:512],
                instance=str(instance)[:64],
            )
            saved += 1

        self.stdout.write(f"[POLL] {gw_ip} — {len(nes)} NE")

        for ne in nes:
            ne_id = ne.ne_id

            # 1. Метаданные NE
            s_out = snmpget(gw_ip, community, f"{OID_NE_STATUS}.{ne_id}")
            s_val = parse_int(s_out)
            if s_val is not None:
                ne.status = NeDevice.STATUS_OK if s_val == 1 else NeDevice.STATUS_UNREACHABLE
                save("ne_status", ne, "", s_val, s_out)

            n_out = snmpget(gw_ip, community, f"{OID_NE_NAME}.{ne_id}")
            n_hex = parse_hex(n_out)
            if n_hex:
                clean = clean_ne_name(n_hex)
                if clean and not ne.name:
                    ne.name = clean[:128]
                save("ne_name", ne, "", None, clean)

            ip_out = snmpget(gw_ip, community, f"{OID_NE_IP}.{ne_id}")
            ip_val = parse_ip(ip_out)
            if ip_val:
                ne.ne_ip = ip_val
                save("ne_ip", ne, "", None, ip_val)

            ne.last_seen = now
            ne.last_polled_at = now
            ne.save()

            # 2. SFP порты 1, 2
            for port in SFP_PORTS:
                p_out = snmpget(gw_ip, community, f"{OID_SFP_PRESENT}.{ne_id}.{port}")
                p_hex = parse_hex(p_out)
                if p_hex:
                    save("sfp_present_active", ne, port, None,
                         " ".join(f"{b:02X}" for b in p_hex))

                i_out = snmpget(gw_ip, community, f"{OID_SFP_INFO}.{ne_id}.{port}")
                i_hex = parse_hex(i_out)
                if i_hex and len(i_hex) >= 48 and any(i_hex[:48]):
                    info = parse_sfp_info(i_hex)
                    if info["vendor"]:
                        save("sfp_vendor", ne, port, None, info["vendor"])
                    if info["pn"]:
                        save("sfp_pn", ne, port, None, info["pn"])
                    if info["sn"]:
                        save("sfp_sn", ne, port, None, info["sn"])

                d_out = snmpget(gw_ip, community, f"{OID_SFP_DDM}.{ne_id}.{port}")
                d_hex = parse_hex(d_out)
                if d_hex and len(d_hex) >= 24 and any(d_hex):
                    ddm = parse_ddm(d_hex)
                    save("sfp_temp",    ne, port, ddm["temp_c"],    d_out)
                    save("sfp_bias",    ne, port, ddm["bias_ma"],   d_out)
                    save("sfp_link",    ne, port, ddm["link"],      d_out)
                    save("sfp_rx_dbm",  ne, port, ddm["rx_dbm"],    d_out)
                    save("sfp_tx_dbm",  ne, port, ddm["tx_dbm"],    d_out)
                    save("sfp_voltage", ne, port, ddm["voltage_v"], d_out)

            # 3. Ethernet порты 3..6
            for port in ETH_PORTS:
                e_out = snmpget(gw_ip, community, f"{OID_ETH_ENABLE}.{ne_id}.{port}")
                e_val = parse_int(e_out)
                if e_val is not None:
                    save("eth_enable", ne, port, e_val, e_out)

                pv_out = snmpget(gw_ip, community, f"{OID_ETH_PVID}.{ne_id}.{port}")
                pv_val = parse_int(pv_out)
                if pv_val is not None:
                    save("eth_pvid", ne, port, pv_val, pv_out)

                m_out = snmpget(gw_ip, community, f"{OID_ETH_MODE}.{ne_id}.{port}")
                m_val = parse_int(m_out)
                if m_val is not None:
                    save("eth_mode", ne, port, m_val, m_out)

                st_out = snmpget(gw_ip, community, f"{OID_ETH_STATUS}.{ne_id}.{port}")
                st_val = parse_int(st_out)
                if st_val is not None:
                    save("eth_status", ne, port, st_val, st_out)

                # .20 = [RxAllBytes uint64 LE (8B)] [something 8B]
                c2_out = snmpget(gw_ip, community, f"{OID_ETH_CNT2}.{ne_id}.{port}")
                c2_hex = parse_hex(c2_out)
                if c2_hex and len(c2_hex) >= 8:
                    rx_bytes = int.from_bytes(bytes(c2_hex[0:8]), "little")
                    save("eth_rx_bytes", ne, port, rx_bytes, c2_out)

                # .13 = [RxAllPkts uint32 LE] [0] [мл.32 RxAllBytes] [flags]
                c1_out = snmpget(gw_ip, community, f"{OID_ETH_CNT1}.{ne_id}.{port}")
                c1_hex = parse_hex(c1_out)
                if c1_hex and len(c1_hex) >= 4:
                    rx_pkts = int.from_bytes(bytes(c1_hex[0:4]), "little")
                    save("eth_rx_pkts", ne, port, rx_pkts, c1_out)

            if verbose:
                self.stdout.write(f"  NE-{ne_id}: polled")

        # Шлюз
        gateway.status = Device.STATUS_OK
        gateway.last_polled_at = now
        gateway.last_seen = now
        gateway.save(update_fields=["status", "last_polled_at", "last_seen"])

        self.stdout.write(self.style.SUCCESS(
            f"Done. MetricValue saved={saved}. NE polled: {len(nes)}"
        ))
