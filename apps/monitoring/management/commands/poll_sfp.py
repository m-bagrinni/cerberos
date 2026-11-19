"""
Опрос DDM (SFP-модули).

Поддерживает три формата DDM у разных вендоров:

1. **Eltex MES2300B** — Radlan rlPhyTestGetTable (.89.90.1.2.1).
   Индекс: <ifIndex>.<testType>, где testType:
       5 = temperature (°C)
       6 = supply voltage (µV)
       7 = tx bias current (µA)
       8 = tx output power (mdBm, милли-дБм)
       9 = rx optical power (mdBm)
   Значения — INTEGER, единицы — в колонке .4.

2. **DGS-3120** — DDM.mib (.171.12.72.2.1.1.1).
   Значения — DisplayString с десятичной точкой ("38.88", "3.318"),
   TxPower/RxPower — в mW.

3. **DGS-3630** — DLINKSW-DDM-MIB (.171.14.72.1.4.1).
   Значения — Integer32 с множителями:
       temperature ×100, voltage ×1000, bias ×1000,
       tx/rx power µW или ddBm×100.

Использование:
    python manage.py poll_sfp <IP>
"""
import asyncio
import logging
import math

from django.core.management.base import BaseCommand

from apps.devices.models import Device
from apps.monitoring.models import Interface, SfpModule
from apps.monitoring.snmp import SnmpClient, SnmpTarget
from apps.monitoring.status import mark_unreachable

log = logging.getLogger(__name__)


# === Eltex (rlPhyTestGetTable, .89.90.1.2.1) ===
OID_ELTEX_RESULT = "1.3.6.1.4.1.89.90.1.2.1.3"
OID_ELTEX_UNITS  = "1.3.6.1.4.1.89.90.1.2.1.4"
OID_ELTEX_ALARM  = "1.3.6.1.4.1.89.90.1.2.1.5"

# TestType enum
T_TEMP    = 5
T_SUPPLY  = 6
T_TXBIAS  = 7
T_TXOUT   = 8
T_RXOPT   = 9

# === DGS-3120 (DDM.mib, .171.12.72.2.1.1.1) ===
# swDdmStatusTable: .2.1.1.1.<col>.<ifIndex>
#   col=2: temperature, 3: voltage, 4: bias, 5: tx, 6: rx  (все DisplayString)
OID_3120_BASE = "1.3.6.1.4.1.171.12.72.2.1.1.1"

# === DGS-3630 (DLINKSW-DDM-MIB, .171.14.72.1.4.1) ===
OID_3630_CFG_STATE =    "1.3.6.1.4.1.171.14.72.1.2.1.1.1"   # dDdmIfCfgState: 1 вкл, 2 выкл
OID_3630_TEMP =         "1.3.6.1.4.1.171.14.72.1.4.1.1.1"
OID_3630_TEMP_STATE =   "1.3.6.1.4.1.171.14.72.1.4.1.1.2"
OID_3630_VOLTAGE =      "1.3.6.1.4.1.171.14.72.1.4.1.1.7"
OID_3630_VOLTAGE_STATE ="1.3.6.1.4.1.171.14.72.1.4.1.1.8"
OID_3630_BIAS =         "1.3.6.1.4.1.171.14.72.1.4.1.1.13"
OID_3630_BIAS_STATE =   "1.3.6.1.4.1.171.14.72.1.4.1.1.14"
OID_3630_TX =           "1.3.6.1.4.1.171.14.72.1.4.1.1.19"
OID_3630_TX_STATE =     "1.3.6.1.4.1.171.14.72.1.4.1.1.20"
OID_3630_RX =           "1.3.6.1.4.1.171.14.72.1.4.1.1.25"
OID_3630_RX_STATE =     "1.3.6.1.4.1.171.14.72.1.4.1.1.26"


class Command(BaseCommand):
    help = "Опрос DDM SFP-модулей (Eltex / DGS-3120 / DGS-3630)"

    def add_arguments(self, parser):
        parser.add_argument("ip")

    def handle(self, *args, **opts):
        try:
            device = Device.objects.get(ip=opts["ip"])
        except Device.DoesNotExist:
            self.stderr.write(f"Устройство {opts['ip']} не найдено")
            return

        self.stdout.write(f"Опрос SFP {device.name} ({device.ip})...")

        try:
            data = asyncio.run(self._fetch(device))
        except Exception as e:
            self.stderr.write(f"SNMP ошибка: {e}")
            mark_unreachable(device, reason=str(e))
            return

        self._save(device, data)
        self.stdout.write(self.style.SUCCESS("OK"))

    async def _fetch(self, device) -> dict:
        client = SnmpClient(SnmpTarget(
            host=device.ip,
            community=device.snmp_community or "public",
            port=device.snmp_port,
        ))

        # 1. Eltex (rlPhyTestGetTable)
        d = await self._fetch_eltex(client)
        if d["modules"]:
            self.stdout.write("  формат: Eltex rlPhyTestGetTable (.89.90.1.2)")
            return d

        # 2. DGS-3120 (DDM.mib)
        d = await self._fetch_3120(client)
        if d["modules"]:
            self.stdout.write("  формат: DGS-3120 DDM.mib (.171.12.72)")
            return d

        # 3. DGS-3630 (DLINKSW-DDM-MIB)
        d = await self._fetch_3630(client)
        if d["modules"]:
            self.stdout.write("  формат: DGS-3630 DLINKSW-DDM-MIB (.171.14.72)")
        else:
            # DGS-3630: таблица настроек есть, а измерений нет — DDM выключен на портах
            # (dDdmIfCfgState: 1 — включён, 2 — выключен). Так было на ac.01.ukjd-old.
            off = [oid.rsplit(".", 1)[-1] for oid, v in await client.walk(OID_3630_CFG_STATE)
                   if self._int(v) == 2]
            if off:
                msg = f"DDM выключен на коммутаторе (порты if_index {', '.join(off)}) — включите мониторинг SFP"
                self.stdout.write("  " + msg)
                log.warning("poll_sfp %s (%s): %s", device.name, device.ip, msg)
            else:
                self.stdout.write("  DDM не найден ни в одном формате")
        return d

    # ----------------------------------------------------------------
    # Eltex MES2300B (.89.90.1.2.1)
    # ----------------------------------------------------------------
    async def _fetch_eltex(self, client) -> dict:
        results = await client.walk(OID_ELTEX_RESULT)
        if not results:
            return {"modules": {}}
        units = self._to_dict_2key(await client.walk(OID_ELTEX_UNITS))
        alarms = self._to_dict_2key(await client.walk(OID_ELTEX_ALARM))

        # by_iface[ifIndex] = {field: value}
        by_iface: dict[int, dict] = {}
        for oid, val in results:
            parsed = self._parse_2key_index(oid)
            if parsed is None:
                continue
            if_index, test_type = parsed
            try:
                v = int(val)
            except (TypeError, ValueError):
                continue

            m = by_iface.setdefault(if_index, {})

            if test_type == T_TEMP:
                m["temperature_c"] = float(v)
            elif test_type == T_SUPPLY:
                m["voltage_v"] = v / 1_000_000.0        # µV → V
            elif test_type == T_TXBIAS:
                m["bias_current_ma"] = v / 1000.0       # µA → mA
            elif test_type == T_TXOUT:
                m["tx_power_dbm"] = v / 1000.0          # mdBm → dBm
            elif test_type == T_RXOPT:
                m["rx_power_dbm"] = v / 1000.0          # mdBm → dBm

            # Alarm state (5-колоночная таблица .5)
            alarm = alarms.get((if_index, test_type))
            state = self._alarm_to_state(alarm)
            if test_type == T_TEMP:
                m["temp_state"] = state
            elif test_type == T_SUPPLY:
                m["voltage_state"] = state
            elif test_type == T_TXBIAS:
                m["bias_state"] = state
            elif test_type == T_TXOUT:
                m["tx_state"] = state
            elif test_type == T_RXOPT:
                m["rx_state"] = state

        # Оставляем только те ifIndex, где есть хоть один DDM-параметр
        modules = {}
        for if_index, m in by_iface.items():
            if not any(k in m for k in (
                "temperature_c", "voltage_v", "bias_current_ma",
                "tx_power_dbm", "rx_power_dbm",
            )):
                continue
            modules[if_index] = {
                "temperature_c": m.get("temperature_c"),
                "voltage_v": m.get("voltage_v"),
                "bias_current_ma": m.get("bias_current_ma"),
                "tx_power_dbm": m.get("tx_power_dbm"),
                "rx_power_dbm": m.get("rx_power_dbm"),
                "temp_state": m.get("temp_state", ""),
                "voltage_state": m.get("voltage_state", ""),
                "bias_state": m.get("bias_state", ""),
                "tx_state": m.get("tx_state", ""),
                "rx_state": m.get("rx_state", ""),
            }
        return {"modules": modules}

    # ----------------------------------------------------------------
    # DGS-3120 (.171.12.72.2.1.1.1)
    # ----------------------------------------------------------------
    async def _fetch_3120(self, client) -> dict:
        rows = await client.walk(OID_3120_BASE)
        by_iface: dict[int, dict] = {}
        for oid, val in rows:
            parts = oid.split(".")
            if len(parts) < 2:
                continue
            try:
                col = int(parts[-2])
                idx = int(parts[-1])
            except (ValueError, IndexError):
                continue
            by_iface.setdefault(idx, {})[col] = val

        modules = {}
        for idx, cols in by_iface.items():
            temp = self._str_to_float(cols.get(2))
            voltage = self._str_to_float(cols.get(3))
            bias = self._str_to_float(cols.get(4))
            tx_mw = self._str_to_float(cols.get(5))
            rx_mw = self._str_to_float(cols.get(6))
            if all(v is None for v in (temp, voltage, bias, tx_mw, rx_mw)):
                continue
            modules[idx] = {
                "temperature_c": temp,
                "voltage_v": voltage,
                "bias_current_ma": bias,
                "tx_power_dbm": self._mw_to_dbm(tx_mw),
                "rx_power_dbm": self._mw_to_dbm(rx_mw),
                "temp_state": "", "voltage_state": "",
                "bias_state": "", "tx_state": "", "rx_state": "",
            }
        return {"modules": modules}

    # ----------------------------------------------------------------
    # DGS-3630 (.171.14.72.1.4.1)
    # ----------------------------------------------------------------
    async def _fetch_3630(self, client) -> dict:
        temp = self._to_dict(await client.walk(OID_3630_TEMP))
        temp_state = self._to_dict(await client.walk(OID_3630_TEMP_STATE))
        voltage = self._to_dict(await client.walk(OID_3630_VOLTAGE))  # OID 7 (3.358 V -> 335)
        voltage_state = self._to_dict(await client.walk(OID_3630_VOLTAGE_STATE))
        bias = self._to_dict(await client.walk("1.3.6.1.4.1.171.14.72.1.4.1.1.32"))  # OID 32 (30.140 mA -> 30140)
        bias_state = self._to_dict(await client.walk(OID_3630_BIAS_STATE))
        tx = self._to_dict(await client.walk(OID_3630_TX))
        tx_state = self._to_dict(await client.walk(OID_3630_TX_STATE))
        rx = self._to_dict(await client.walk(OID_3630_RX))
        rx_state = self._to_dict(await client.walk(OID_3630_RX_STATE))

        modules = {}
        all_idx = set(temp) | set(tx) | set(rx)
        for idx in all_idx:
            t = self._int(temp.get(idx))
            v = self._int(voltage.get(idx))
            b = self._int(bias.get(idx))
            txv = self._int(tx.get(idx))
            rxv = self._int(rx.get(idx))
            if t is None and txv is None and rxv is None:
                continue
            modules[idx] = {
                "temperature_c": t / 1000.0 if t is not None else None,       # 41969 -> 41.969
                "voltage_v": v / 100.0 if v is not None else None,             # 335 -> 3.35
                "bias_current_ma": b / 1000.0 if b is not None else None,      # 30140 -> 30.14
                "tx_power_dbm": self._to_dbm(txv),                             # 5789 -> -2.36
                "rx_power_dbm": self._to_dbm(rxv),                             # 522 -> -12.82
                "temp_state": self._state_str(temp_state.get(idx)),
                "voltage_state": self._state_str(voltage_state.get(idx)),
                "bias_state": self._state_str(bias_state.get(idx)),
                "tx_state": self._state_str(tx_state.get(idx)),
                "rx_state": self._state_str(rx_state.get(idx)),
            }
        return {"modules": modules}

    # ----------------------------------------------------------------
    # Save
    # ----------------------------------------------------------------
    def _save(self, device, data):
        from apps.devices.models import MetricDefinition
        from apps.monitoring.models import MetricValue
        from django.utils import timezone

        ifaces = {i.if_index: i for i in Interface.objects.filter(device=device)}
        saved = 0
        skipped = 0

        # Находим метрики
        metrics = {
            'sfp_rx_dbm': MetricDefinition.objects.filter(key='sfp_rx_dbm').first(),
            'sfp_tx_dbm': MetricDefinition.objects.filter(key='sfp_tx_dbm').first(),
            'sfp_temp': MetricDefinition.objects.filter(key='sfp_temp').first(),
        }

        for idx, m in data["modules"].items():
            if idx not in ifaces:
                skipped += 1
                continue
            SfpModule.objects.update_or_create(
                interface=ifaces[idx],
                defaults=m,
            )
            saved += 1

            # Записываем в MetricValue для графиков
            now = timezone.now()
            if metrics['sfp_rx_dbm'] and m.get('rx_power_dbm') is not None:
                MetricValue.objects.create(
                    device=device,
                    metric=metrics['sfp_rx_dbm'],
                    instance=str(idx),
                    ts=now,
                    value=m['rx_power_dbm']
                )
            if metrics['sfp_tx_dbm'] and m.get('tx_power_dbm') is not None:
                MetricValue.objects.create(
                    device=device,
                    metric=metrics['sfp_tx_dbm'],
                    instance=str(idx),
                    ts=now,
                    value=m['tx_power_dbm']
                )
            if metrics['sfp_temp'] and m.get('temperature_c') is not None:
                MetricValue.objects.create(
                    device=device,
                    metric=metrics['sfp_temp'],
                    instance=str(idx),
                    ts=now,
                    value=m['temperature_c']
                )

        self.stdout.write(f"  сохранено SFP: {saved}")
        if skipped:
            self.stdout.write(f"  ifIndex без Interface в БД: {skipped}")

    # ----------------------------------------------------------------
    # Утилиты
    # ----------------------------------------------------------------
    def _parse_2key_index(self, oid: str):
        """Парсит OID .<base>.<ifIndex>.<testType> → (ifIndex, testType)."""
        parts = oid.split(".")
        if len(parts) < 2:
            return None
        try:
            return int(parts[-2]), int(parts[-1])
        except (ValueError, IndexError):
            return None

    def _to_dict_2key(self, rows):
        """rows → {(ifIndex, testType): value}."""
        out = {}
        for oid, val in rows:
            key = self._parse_2key_index(oid)
            if key is not None:
                out[key] = val
        return out

    def _alarm_to_state(self, val):
        """rlPhyTestGetAlarm: 1=notRelevant, 2=noAlarm, 3=lowWarn,
           4=highWarn, 5=lowAlarm, 6=highAlarm → ok/warning/alarm."""
        if val is None:
            return ""
        try:
            v = int(val)
        except (TypeError, ValueError):
            return ""
        return {1: "", 2: "ok", 3: "warning", 4: "warning",
                5: "alarm", 6: "alarm"}.get(v, "")

    def _mw_to_dbm(self, mw):
        if mw is None or mw <= 0:
            return None
        return round(10 * math.log10(mw), 2)

    def _to_dbm(self, val):
        """Для 3630: сырое значение (µW * 100) -> dBm."""
        if val is None or val == 0:
            return None
        # Формула: 10 * log10(val / 10000)
        return round(10 * math.log10(val / 10000.0), 2)

    def _state_str(self, val):
        if val is None:
            return ""
        try:
            return {1: "ok", 2: "warning", 3: "alarm"}.get(int(val), "")
        except Exception:
            return ""

    def _str_to_float(self, v):
        """pysnmp → float из строки ('39.88', '  -')."""
        if v is None:
            return None
        try:
            s = v.prettyPrint().strip()
        except Exception:
            s = str(v).strip()
        if not s or s == "-":
            return None
        try:
            return float(s)
        except ValueError:
            return None

    def _to_dict(self, rows):
        out = {}
        for oid, val in rows:
            try:
                idx = int(oid.rsplit(".", 1)[1])
            except (ValueError, IndexError):
                continue
            out[idx] = val
        return out

    def _int(self, v, default=None):
        if v is None:
            return default
        try:
            return int(v)
        except Exception:
            return default
