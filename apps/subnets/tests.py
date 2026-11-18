"""
Тесты захвата трафика: парсер вывода tcpdump, проверка BPF, дедупликация, API и live-сессии.

Запуск:  docker compose -f docker-compose.yml -f docker-compose.dev.yml exec web \
             python manage.py test apps.subnets --noinput
"""
import time
from unittest import mock

from django.test import SimpleTestCase, TestCase

from apps.accounts.access import EDIT
from apps.accounts.models import User
from apps.subnets import bpf
from apps.subnets.tcpdump_parser import Deduplicator, parse_line, parse_tcpdump_output

# Реальные строки tcpdump -nn -tttt -e (сняты с enp4s0 / enp4s0.60 / any)
ETH_ARP_REQ = ("2026-11-17 15:57:54.191941 b4:2e:99:15:13:bc > ff:ff:ff:ff:ff:ff, ethertype ARP (0x0806), "
               "length 42: Request who-has 10.70.7.22 tell 10.70.7.77, length 28")
ETH_ARP_REP = ("2026-11-17 15:57:54.639438 f4:1e:57:f8:26:8a > b4:2e:99:15:13:bc, ethertype ARP (0x0806), "
               "length 60: Reply 10.70.7.253 is-at f4:1e:57:f8:26:8a, length 46")
ETH_SNMP = ("2026-11-17 15:57:54.547883 b4:2e:99:15:13:bc > 00:17:c8:dc:5b:3b, ethertype IPv4 (0x0800), "
            "length 88: 10.70.7.77.58592 > 10.70.7.97.161:  GetRequest(31)  .1.3.6.1.2.1.25.3.5.1.1.1")
ETH_ICMP = ("2026-11-17 15:57:54.639450 b4:2e:99:15:13:bc > f4:1e:57:f8:26:8a, ethertype IPv4 (0x0800), "
            "length 60: 10.70.7.77 > 10.70.7.253: ICMP echo request, id 53164, seq 30636, length 26")
ETH_DHCP = ("2026-11-17 15:57:54.607485 d4:d6:df:01:d4:2f > ff:ff:ff:ff:ff:ff, ethertype IPv4 (0x0800), "
            "length 351: 0.0.0.0.68 > 255.255.255.255.67: BOOTP/DHCP, Request from d4:d6:df:01:d4:2f, length 309")
ETH_STP = ("2026-11-17 15:57:57.559655 50:1c:bf:7d:d1:13 > 01:00:0c:cc:cc:cd, 802.3, length 50: LLC, dsap SNAP "
           "(0xaa) Individual, ssap SNAP (0xaa) Command, ctrl 0x03: oui Cisco (0x00000c), pid PVST (0x010b), "
           "length 42: STP 802.1d, Config, Flags [none], bridge-id 803c.50:1c:bf:7d:d1:00.8013, length 42")
VLAN_ARP = ("2026-11-17 15:57:58.873520 74:56:3c:a9:60:6c > ff:ff:ff:ff:ff:ff, ethertype 802.1Q (0x8100), "
            "length 64: vlan 8, p 0, ethertype ARP (0x0806), Request who-has 192.168.8.13 tell 192.168.8.5, length 46")
VLAN_ICMP = ("2026-11-17 15:57:58.879541 40:01:00:00:06:90 > b4:2e:99:15:13:bc, ethertype 802.1Q (0x8100), "
             "length 64: vlan 8, p 0, ethertype IPv4 (0x0800), 192.168.8.109 > 192.168.8.254: ICMP echo reply, "
             "id 7853, seq 64428, length 26")
ANY_TCP_SUB = ("2026-11-17 15:57:43.159988 enp4s0.8 Out ifindex 4 b4:2e:99:15:13:bc ethertype IPv4 (0x0800), "
               "length 148: 192.168.8.254.35126 > 192.168.8.4.4700: Flags [P.], seq 1:89, ack 1, win 989, length 88")
ANY_TCP_PARENT = ("2026-11-17 15:57:43.159991 enp4s0 Out ifindex 2 b4:2e:99:15:13:bc ethertype IPv4 (0x0800), "
                  "length 148: 192.168.8.254.35126 > 192.168.8.4.4700: Flags [P.], seq 1:89, ack 1, win 989, length 88")
NO_E_ICMP = "2026-11-17 15:57:54.639450 IP 10.70.7.77 > 10.70.7.253: ICMP echo request, id 1, seq 2, length 64"
NO_E_ARP = "2026-11-17 15:57:54.639450 ARP, Request who-has 10.70.7.1 tell 10.70.7.77, length 28"
IPV6 = ("2026-11-17 15:58:00.000001 aa:bb:cc:dd:ee:01 > 33:33:00:01:00:02, ethertype IPv6 (0x86dd), "
        "length 118: fe80::1.546 > ff02::1:2.547: dhcp6 solicit")


class ParserTests(SimpleTestCase):
    def test_arp_request_with_ethernet_header(self):
        p = parse_line(ETH_ARP_REQ)
        self.assertEqual((p["proto"], p["category"]), ("ARP", "arp"))
        self.assertEqual((p["src"], p["dst"]), ("10.70.7.77", "10.70.7.22"))
        self.assertEqual(p["mac_dst"], "ff:ff:ff:ff:ff:ff")
        self.assertEqual(p["bm"], "broadcast")
        self.assertEqual(p["length"], 42)

    def test_arp_reply_is_unicast(self):
        p = parse_line(ETH_ARP_REP)
        self.assertEqual((p["proto"], p["src"]), ("ARP", "10.70.7.253"))
        self.assertIsNone(p["bm"])

    def test_snmp_over_udp_with_ports(self):
        p = parse_line(ETH_SNMP)
        self.assertEqual((p["src"], p["sport"], p["dst"], p["dport"]), ("10.70.7.77", 58592, "10.70.7.97", 161))
        self.assertEqual((p["proto"], p["l4"], p["category"]), ("SNMP", "UDP", "snmp"))
        self.assertTrue(p["info"].startswith("GetRequest"))

    def test_icmp_without_ports(self):
        p = parse_line(ETH_ICMP)
        self.assertEqual((p["proto"], p["sport"], p["dport"]), ("ICMP", None, None))

    def test_dhcp_broadcast(self):
        p = parse_line(ETH_DHCP)
        self.assertEqual((p["proto"], p["category"], p["bm"]), ("DHCP", "dhcp", "broadcast"))

    def test_stp_over_llc(self):
        p = parse_line(ETH_STP)
        self.assertEqual((p["proto"], p["category"], p["bm"]), ("STP", "stp", "multicast"))

    def test_vlan_tagged(self):
        a = parse_line(VLAN_ARP)
        self.assertEqual((a["vlan"], a["proto"], a["src"]), (8, "ARP", "192.168.8.5"))
        i = parse_line(VLAN_ICMP)
        self.assertEqual((i["vlan"], i["proto"], i["src"], i["dst"]), (8, "ICMP", "192.168.8.109", "192.168.8.254"))

    def test_any_interface_format(self):
        p = parse_line(ANY_TCP_SUB)
        self.assertEqual((p["iface"], p["direction"], p["mac_src"]), ("enp4s0.8", "Out", "b4:2e:99:15:13:bc"))
        self.assertEqual((p["proto"], p["flags"], p["dport"]), ("TCP", "P.", 4700))
        self.assertEqual(p["length"], 148)

    def test_without_ethernet_header(self):
        self.assertEqual(parse_line(NO_E_ICMP)["proto"], "ICMP")
        arp = parse_line(NO_E_ARP)
        self.assertEqual((arp["proto"], arp["src"], arp["bm"]), ("ARP", "10.70.7.77", "broadcast"))

    def test_ipv6_ports_and_multicast(self):
        p = parse_line(IPV6)
        self.assertEqual((p["src"], p["sport"], p["dst"], p["dport"]), ("fe80::1", 546, "ff02::1:2", 547))
        self.assertEqual((p["proto"], p["bm"]), ("DHCPv6", "multicast"))

    def test_service_lines_skipped(self):
        text = "\n".join([
            "tcpdump: verbose output suppressed, use -v[v]... for full protocol decode",
            "listening on enp4s0, link-type EN10MB (Ethernet), snapshot length 262144 bytes",
            ETH_ICMP, "    continuation line", "1 packet captured",
        ])
        self.assertEqual(len(parse_tcpdump_output(text)), 1)

    def test_contract_keys_for_traffic_monitor(self):
        for line in (ETH_ARP_REQ, ETH_SNMP, ETH_STP, ANY_TCP_SUB, NO_E_ICMP):
            p = parse_line(line)
            for key in ("timestamp", "src", "sport", "dst", "dport", "proto", "info",
                        "length", "category", "mac_src", "mac_dst", "bm"):
                self.assertIn(key, p)

    def test_dedup_on_any(self):
        d = Deduplicator()
        out = d.filter([parse_line(ANY_TCP_SUB), parse_line(ANY_TCP_PARENT)])
        self.assertEqual((len(out), d.dropped), (1, 1))
        # внутренний трафик Docker на any отбрасывается
        docker = ANY_TCP_SUB.replace("enp4s0.8 Out", "br-e8cc327919aa Out")
        self.assertTrue(Deduplicator().is_dup(parse_line(docker)))
        self.assertFalse(Deduplicator(skip_docker=False).is_dup(parse_line(docker)))
        # без -i any (нет iface) дубли не ищем
        d2 = Deduplicator()
        self.assertEqual(len(d2.filter([parse_line(ETH_ICMP), parse_line(ETH_ICMP)])), 2)


class BpfTests(SimpleTestCase):
    def test_valid(self):
        for e in ["", "icmp or arp", "(tcp or udp) and host 10.70.7.1",
                  "tcp[tcpflags] & (tcp-syn|tcp-ack) = tcp-syn", "ether host b4:2e:99:15:13:bc",
                  "udp portrange 161-162", "net 10.70.7.0/24 and not port 22", "vlan 50 and arp",
                  "ip[2:2] > 1000", "icmp[icmptype] = icmp-unreach", "host fe80::1",
                  "stp or ether proto 0x88cc or ether dst 01:00:0c:cc:cc:cc", "greater 1000"]:
            with self.subTest(e=e):
                bpf.validate(e)

    def test_normalizes_whitespace(self):
        self.assertEqual(bpf.validate("  icmp   or\tarp "), "icmp or arp")

    def test_invalid(self):
        cases = {
            "host google.com": "имена хостов",
            "port http": "номер порта",
            "(icmp": "Не закрыта",
            "icmp)": "Лишняя",
            "tcp and": "заканчиваться",
            "host": "не хватает",
            "icmp; rm -rf /": "Недопустимые символы",
            "port 70000": "вне диапазона",
            "host aa:bb:cc:dd:ee:ff": "ether host",
            "udp portrange 9-2": "диапазон",
            "foo": "Неизвестное слово",
            "x" * 301: "длиннее",
        }
        for expr, msg in cases.items():
            with self.subTest(expr=expr[:20]):
                with self.assertRaises(bpf.BpfError) as cm:
                    bpf.validate(expr)
                self.assertIn(msg, str(cm.exception))

    def test_compile_error_reported(self):
        res = mock.Mock(returncode=1, stdout="", stderr="tcpdump: can't parse filter expression: syntax error\n")
        with mock.patch("apps.core.ops_client.sniffer_run", return_value=res):
            self.assertIn("syntax error", bpf.check_with_tcpdump("tcp and (", "eth0"))

    def test_compile_ok_ignores_noise(self):
        res = mock.Mock(returncode=124, stdout="", stderr=(
            "tcpdump: WARNING: any: That device doesn't support promiscuous mode\n"
            "(Promiscuous mode not supported on the \"any\" device)\n"
            "listening on any, link-type LINUX_SLL2\n"))
        with mock.patch("apps.core.ops_client.sniffer_run", return_value=res):
            self.assertIsNone(bpf.check_with_tcpdump("icmp", "any"))


IFACES = [{"name": "enp4s0.60", "ip": "10.70.7.77", "cidrs": ["10.70.7.77/24"], "up": True, "vlan": 60, "parent": "enp4s0"}]


class _FakeStream:
    """Ответ ops /sniffer/stream: отдаёт строки, затем EOF (или ждёт, пока его не оборвут)."""

    def __init__(self, lines, hold=False):
        self._lines = [(ln + "\n").encode() for ln in lines]
        self._hold = hold
        self._closed = False

    def readline(self):
        if self._lines:
            return self._lines.pop(0)
        while self._hold and not self._closed:
            time.sleep(0.05)
        return b""

    def close(self):
        self._closed = True


class CaptureApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="net", password="pass-12345", is_active=True)
        self.user.access_sections = {"subnets": EDIT}
        self.user.access_caps = ["traffic_capture"]
        self.user.save()
        self.client.force_login(self.user)
        p = mock.patch("apps.subnets.tcpdump.list_ifaces", return_value=IFACES)
        p.start()
        self.addCleanup(p.stop)
        # отдельное пространство ключей в Redis для тестов
        p2 = mock.patch("apps.subnets.capture_live._PREFIX", f"cerber:test-capture:{time.time_ns()}")
        p2.start()
        self.addCleanup(p2.stop)

    def test_requires_capability(self):
        other = User.objects.create_user(username="viewer", password="pass-12345", is_active=True)
        other.access_sections = {"subnets": EDIT}
        other.save()
        self.client.force_login(other)
        for url in ("/api/v1/subnets/tcpdump/validate/", "/api/v1/subnets/tcpdump/live/"):
            self.assertEqual(self.client.post(url, {}, content_type="application/json").status_code, 403)

    def test_validate_endpoint(self):
        r = self.client.post("/api/v1/subnets/tcpdump/validate/",
                             {"bpf": "host example.com", "iface": "any"}, content_type="application/json")
        self.assertEqual(r.json()["valid"], False)
        r = self.client.post("/api/v1/subnets/tcpdump/validate/",
                             {"bpf": "icmp", "iface": "enp4s0.60", "compile": False}, content_type="application/json")
        self.assertEqual(r.json(), {"ok": True, "valid": True, "bpf": "icmp"})

    def test_start_rejects_unknown_iface_and_bad_filter(self):
        r = self.client.post("/api/v1/subnets/tcpdump/live/", {"iface": "eth9"}, content_type="application/json")
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/v1/subnets/tcpdump/live/", {"iface": "any", "bpf": "port http"},
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def _wait_stopped(self, sid, cursor=0, timeout=5.0):
        deadline = time.time() + timeout
        got = []
        while time.time() < deadline:
            r = self.client.get(f"/api/v1/subnets/tcpdump/live/{sid}/?cursor={cursor}").json()
            got += r["packets"]
            cursor = r["cursor"]
            if r["status"] == "stopped" and not r["more"]:
                return r, got
            time.sleep(0.2)
        self.fail("сессия не остановилась")

    def test_live_session_end_to_end(self):
        lines = ["listening on enp4s0.60, link-type EN10MB (Ethernet), snapshot length 262144 bytes",
                 ETH_ARP_REQ, ETH_SNMP, ETH_ICMP]
        with mock.patch("apps.core.ops_client.sniffer_stream_open", return_value=_FakeStream(lines)) as op:
            r = self.client.post("/api/v1/subnets/tcpdump/live/",
                                 {"iface": "enp4s0.60", "bpf": "arp or icmp or udp", "duration": 60},
                                 content_type="application/json")
            self.assertEqual(r.status_code, 200, r.content)
            sid = r.json()["sid"]
            final, packets = self._wait_stopped(sid)
        argv = op.call_args[0][0]
        self.assertEqual(argv[:2], ["timeout", "60s"])
        self.assertEqual(argv[-1], "arp or icmp or udp")
        self.assertEqual([p["n"] for p in packets], [1, 2, 3])
        self.assertEqual([p["proto"] for p in packets], ["ARP", "SNMP", "ICMP"])
        self.assertEqual((final["count"], final["stop_reason"], final["linktype"]), (3, "ended", "EN10MB"))

    def test_live_stop_and_ownership(self):
        stream = _FakeStream([ETH_ICMP], hold=True)
        with mock.patch("apps.core.ops_client.sniffer_stream_open", return_value=stream), \
                mock.patch("apps.core.ops_client.abort_stream", side_effect=lambda resp: resp.close()):
            sid = self.client.post("/api/v1/subnets/tcpdump/live/", {"iface": "enp4s0.60"},
                                   content_type="application/json").json()["sid"]
            # чужой пользователь не видит и не останавливает сессию
            other = User.objects.create_user(username="other", password="pass-12345", is_active=True)
            other.access_sections = {"subnets": EDIT}
            other.access_caps = ["traffic_capture"]
            other.save()
            self.client.force_login(other)
            self.assertEqual(self.client.get(f"/api/v1/subnets/tcpdump/live/{sid}/").status_code, 404)
            self.assertEqual(self.client.post(f"/api/v1/subnets/tcpdump/live/{sid}/stop/").status_code, 404)

            self.client.force_login(self.user)
            self.assertEqual(self.client.post(f"/api/v1/subnets/tcpdump/live/{sid}/stop/").status_code, 200)
            final, packets = self._wait_stopped(sid)
        self.assertEqual(final["stop_reason"], "user")
        self.assertEqual(len(packets), 1)

    def test_packet_limit(self):
        lines = [ETH_ICMP] * 10
        with mock.patch("apps.core.ops_client.sniffer_stream_open", return_value=_FakeStream(lines, hold=True)), \
                mock.patch("apps.core.ops_client.abort_stream", side_effect=lambda resp: resp.close()):
            sid = self.client.post("/api/v1/subnets/tcpdump/live/",
                                   {"iface": "enp4s0.60", "max_packets": 4},
                                   content_type="application/json").json()["sid"]
            final, packets = self._wait_stopped(sid)
        self.assertEqual((final["stop_reason"], final["count"], len(packets)), ("limit", 4, 4))

    def test_snapshot_honours_packet_count(self):
        res = mock.Mock(returncode=0, stdout="\n".join([ETH_ARP_REQ, ETH_ICMP]),
                        stderr="listening on enp4s0.60, link-type EN10MB\n2 packets captured\n"
                               "5 packets received by filter\n0 packets dropped by kernel\n")
        with mock.patch("apps.core.ops_client.sniffer_run", return_value=res) as run:
            r = self.client.post("/api/v1/subnets/tcpdump/",
                                 {"iface": "enp4s0.60", "bpf": "arp or icmp", "packets": 2},
                                 content_type="application/json").json()
        argv = run.call_args[0][0]
        self.assertIn("-c", argv)
        self.assertEqual(argv[argv.index("-c") + 1], "2")
        self.assertEqual((r["ok"], r["packets_captured"], r["kernel_dropped"]), (True, 2, 0))
        self.assertEqual([p["proto"] for p in r["packets"]], ["ARP", "ICMP"])


class IdentifyTests(TestCase):
    """«Кто это?»: MAC/OUI, база Cerber, FDB коммутаторов."""

    OUI = "B42E99 Giga-byte Technology\nE89C25 ASUSTek Computer\n0050C2123 Tiny Vendor\n"

    def setUp(self):
        from django.core.cache import cache
        from apps.subnets import identify
        identify._oui_mem = None
        cache.delete(identify.OUI_CACHE_KEY)
        self.addCleanup(lambda: (setattr(identify, "_oui_mem", None), cache.delete(identify.OUI_CACHE_KEY)))

        def fake_run(argv, timeout=30):
            if argv[0] == "cat":
                return mock.Mock(returncode=0, stdout=self.OUI, stderr="")
            if argv[:2] == ["ip", "neigh"]:
                return mock.Mock(returncode=0, stdout="10.70.7.30 dev enp4s0.60 lladdr e8:9c:25:7a:79:10 REACHABLE\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")
        p = mock.patch("apps.core.ops_client.sniffer_run", side_effect=fake_run)
        p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch("apps.subnets.identify._reverse_dns", return_value={})
        p2.start()
        self.addCleanup(p2.stop)

    def test_normalize_and_kind(self):
        from apps.subnets.identify import mac_kind, normalize_mac, well_known
        for raw in ("E8-9C-25-7A-79-10", "e89c.257a.7910", "e8:9c:25:7a:79:10", "e89c257a7910"):
            self.assertEqual(normalize_mac(raw), "e8:9c:25:7a:79:10")
        self.assertIsNone(normalize_mac("e8:9c:25"))
        self.assertIsNone(normalize_mac("zz:9c:25:7a:79:10"))
        self.assertTrue(mac_kind("da:a1:19:00:00:01")["local"])          # случайный MAC телефона
        self.assertTrue(mac_kind("01:80:c2:00:00:00")["multicast"])
        self.assertEqual(well_known("01:00:0c:cc:cc:cd"), "Cisco PVST+")
        self.assertEqual(well_known("01:00:5e:7f:ff:fa"), "IPv4 multicast")

    def test_vendor_longest_prefix(self):
        from apps.subnets.identify import vendor_of
        self.assertEqual(vendor_of("b4:2e:99:15:13:bc"), "Giga-byte Technology")
        self.assertEqual(vendor_of("00:50:c2:12:34:56"), "Tiny Vendor")
        self.assertIsNone(vendor_of("00:11:22:33:44:55"))

    def test_identify_by_mac_db_arp_and_fdb(self):
        from apps.devices.models import Device
        from apps.monitoring.models import FdbEntry
        from apps.subnets.identify import identify
        from apps.subnets.models import IpAddress, Subnet

        net = Subnet.objects.create(cidr="10.70.7.0/24")
        IpAddress.objects.create(subnet=net, ip="10.70.7.30", mac="E8:9C:25:7A:79:10", hostname="PC-067", is_alive=True)
        access = Device.objects.create(name="ag.LAZ", ip="172.16.101.2")
        core = Device.objects.create(name="opt.LAZ", ip="172.16.101.4")
        FdbEntry.objects.create(device=access, mac="E8:9C:25:7A:79:10", bridge_port=4, interface_name="eth1/0/4")
        FdbEntry.objects.create(device=core, mac="E8:9C:25:7A:79:10", bridge_port=1, interface_name="gi1/0/1")
        for i in range(20):  # за аплинком много MAC
            FdbEntry.objects.create(device=core, mac=f"00:00:00:00:00:{i:02x}", bridge_port=1)

        r = identify(mac="e8:9c:25:7a:79:10")
        self.assertEqual(r["vendor"], "ASUSTek Computer")
        self.assertEqual(r["names"][0], {"name": "PC-067", "source": "база Cerber", "ip": "10.70.7.30"})
        ip = r["ips"][0]
        self.assertEqual((ip["ip"], ip["subnet"], ip["is_alive"]), ("10.70.7.30", "10.70.7.0/24", True))
        self.assertIn("ARP сервера", ip["sources"])
        self.assertEqual((r["ports"][0]["device"], r["ports"][0]["port"], r["ports"][0]["access"]), ("ag.LAZ", "eth1/0/4", True))
        self.assertFalse(r["ports"][1]["access"])

    def test_identify_by_ip_finds_mac_via_arp(self):
        from apps.subnets.identify import identify
        r = identify(ips=["10.70.7.30"])
        self.assertEqual((r["mac"], r["mac_from_ip"], r["vendor"]), ("e8:9c:25:7a:79:10", True, "ASUSTek Computer"))

    def test_api(self):
        user = User.objects.create_user(username="net2", password="pass-12345", is_active=True)
        user.access_sections = {"subnets": EDIT}
        user.access_caps = ["traffic_capture"]
        user.save()
        self.client.force_login(user)
        r = self.client.get("/api/v1/subnets/tcpdump/identify/?mac=B4-2E-99-15-13-BC").json()
        self.assertEqual((r["ok"], r["mac"], r["vendor"]), (True, "b4:2e:99:15:13:bc", "Giga-byte Technology"))
        self.assertEqual(self.client.get("/api/v1/subnets/tcpdump/identify/?mac=nope").status_code, 400)
        self.assertEqual(self.client.get("/api/v1/subnets/tcpdump/identify/").status_code, 400)


class DeviceTypeTests(TestCase):
    """Тип устройства «плюс-минус» для сетки IP."""

    OUI = ("E89C25 ASUSTek Computer\n0017C8 Kyocera Display\n4C1FCC Hangzhou Hikvision Digital Technology\n"
           "D4CA6D Routerboard.com\nB0A7B9 TP-Link Systems\nF48CEB D-Link International\n")

    def setUp(self):
        from django.core.cache import cache
        from apps.subnets import identify
        identify._oui_mem = None
        cache.clear()
        self.addCleanup(cache.clear)
        self.addCleanup(lambda: setattr(identify, "_oui_mem", None))
        run = mock.Mock(side_effect=lambda argv, timeout=30: mock.Mock(
            returncode=0, stderr="", stdout=self.OUI if argv[0] == "cat" else ""))
        p = mock.patch("apps.core.ops_client.sniffer_run", run)
        p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch("apps.subnets.tcpdump.list_ifaces", return_value=[{"name": "eth0", "ip": "10.0.0.250"}])
        p2.start()
        self.addCleanup(p2.stop)

    def test_classify_sources_and_priority(self):
        from apps.subnets.devtype import classify
        self.assertEqual(classify(ip="10.0.0.1", gateway_ip="10.0.0.1")["dtype"], "gateway")
        self.assertEqual(classify(ip="10.0.0.5", hostname="PC-067")["dtype"], "pc")
        self.assertIsNone(classify(ip="10.0.0.5", hostname="PCHE"))
        self.assertEqual(classify(ip="10.0.0.5", hostname="idrac-6WGRPD4")["dtype"], "server")
        r = classify(ip="10.0.0.6", mac="00:17:c8:dc:5b:3b")
        self.assertEqual((r["dtype"], r["dconf"], r["dwhy"]), ("printer", "medium", "производитель Kyocera Display"))
        self.assertEqual(classify(ip="10.0.0.7", mac="4c:1f:cc:00:00:01")["dtype"], "camera")
        self.assertEqual(classify(ip="10.0.0.8", mac="52:54:00:12:34:56")["dtype"], "server")
        self.assertEqual(classify(ip="10.0.0.9", mac="da:a1:19:00:00:01")["dtype"], "mobile")
        # имя важнее производителя
        self.assertEqual(classify(ip="10.0.0.10", hostname="prn-buh", mac="e8:9c:25:00:00:01")["dtype"], "printer")
        self.assertIsNone(classify(ip="10.0.0.11"))

    def test_ip_grid_carries_types(self):
        from apps.devices.models import Device
        from apps.monitoring.models import ArpEntry
        from apps.subnets.models import IpAddress, Subnet

        user = User.objects.create_user(username="v", password="pass-12345", is_active=True)
        user.access_sections = {"subnets": EDIT}
        user.save()
        self.client.force_login(user)
        net = Subnet.objects.create(cidr="10.0.0.0/24", gateway_ip="10.0.0.1")
        for i, (host, mac) in enumerate([("", ""), ("PC-1", ""), ("", "00:17:C8:00:00:01"),
                                         ("", ""), ("", ""), ("", "")], start=1):
            IpAddress.objects.create(subnet=net, ip=f"10.0.0.{i}", hostname=host, mac=mac, is_alive=True)
        IpAddress.objects.create(subnet=net, ip="10.0.0.250", is_alive=True)
        sw = Device.objects.create(name="ag.LAZ", ip="10.0.0.4")
        ArpEntry.objects.create(device=sw, ip="10.0.0.5", mac="D4:CA:6D:00:00:01")
        rows = {r["ip"]: r for r in self.client.get(f"/api/v1/subnets/{net.pk}/ip-grid/").json()["ips"]}
        self.assertEqual(rows["10.0.0.1"]["dtype"], "gateway")
        self.assertEqual(rows["10.0.0.2"]["dtype"], "pc")
        self.assertEqual(rows["10.0.0.3"]["dtype"], "printer")
        self.assertEqual(rows["10.0.0.4"]["dtype"], "network")          # устройство Cerber
        self.assertEqual(rows["10.0.0.5"]["dwhy"], "производитель Routerboard.com")  # MAC из ARP коммутатора
        self.assertNotIn("dtype", rows["10.0.0.6"])
        self.assertEqual(rows["10.0.0.250"]["dwhy"], "это сервер Cerber")

    def test_ambiguous_vendor_needs_corroboration(self):
        """TP-Link — и коммутатор, и сетевая карта ПК: решают NetBIOS и TTL."""
        from apps.subnets.devtype import classify
        tp = "b0:a7:b9:0f:97:10"
        alone = classify(ip="10.0.0.20", mac=tp)
        self.assertIsNone(alone["dtype"])
        self.assertIn("TP-Link", alone["dwhy"])
        win = classify(ip="10.0.0.20", mac=tp, fingerprint={
            "ttl": 127, "ttl0": 128, "nb": {"name": "PC-386", "domain": "RAILWAY", "server": True}})
        self.assertEqual((win["dtype"], win["dconf"]), ("pc", "high"))
        self.assertIn("NetBIOS", win["dwhy"])
        same = classify(ip="10.0.0.20", hostname="PC-386", mac=tp, fingerprint={
            "ttl": 127, "ttl0": 128, "nb": {"name": "PC-386", "server": True}})
        self.assertEqual(same["dwhy"].count("имя «PC-386»"), 1)
        linux = classify(ip="10.0.0.21", mac=tp, fingerprint={"ttl": 63, "ttl0": 64, "nb": None})
        self.assertEqual(linux["dtype"], "network")
        sw = classify(ip="10.0.0.22", hostname="PCHE", mac="f4:8c:eb:5b:6b:80",
                      fingerprint={"ttl": 253, "ttl0": 255, "nb": None})
        self.assertEqual((sw["dtype"], sw["dconf"]), ("network", "high"))
        dc = classify(ip="10.0.0.23", fingerprint={"ttl": 63, "ttl0": 64,
                                                   "nb": {"name": "DC1", "dc": True, "samba": True}})
        self.assertEqual(dc["dtype"], "server")


class FingerprintTests(SimpleTestCase):
    def test_initial_ttl(self):
        from apps.subnets.fingerprint import initial_ttl
        self.assertEqual([initial_ttl(x) for x in (63, 64, 127, 126, 253, 30, None)], [64, 64, 128, 128, 255, 32, None])

    def test_parse_nbstat(self):
        import struct
        from apps.subnets.fingerprint import parse_nbstat
        hdr = struct.pack(">HHHHHH", 0xCE7B, 0x8400, 0, 1, 0, 0)
        name = b"\x20" + b"CK" + b"A" * 30 + b"\x00"
        entries = [("PC-067", 0x00, 0x0400), ("RAILWAY", 0x00, 0x8400), ("PC-067", 0x20, 0x0400)]
        body = bytes([len(entries)]) + b"".join(
            n.encode().ljust(15) + bytes([suf]) + struct.pack(">H", fl) for n, suf, fl in entries)
        body += bytes.fromhex("e89c257a7910") + b"\x00" * 40
        pkt = hdr + name + struct.pack(">HHIH", 0x21, 1, 0, len(body)) + body
        r = parse_nbstat(pkt)
        self.assertEqual((r["name"], r["domain"], r["mac"], r["server"], r["dc"]),
                         ("PC-067", "RAILWAY", "e8:9c:25:7a:79:10", True, False))
        self.assertIsNone(parse_nbstat(b"\x00" * 5))
