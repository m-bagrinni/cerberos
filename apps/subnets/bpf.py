"""
Проверка BPF-фильтра захвата (синтаксис pcap-filter) до передачи в tcpdump.

Задача — пропустить всё, что нужно сетевику (скобки, логика, tcp[tcpflags],
portrange, ether host MAC, vlan, less/greater …), и отсечь:
  • символы вне белого списка cerber-ops (там та же регулярка);
  • имена хостов/сервисов — tcpdump резолвил бы их через DNS из sniffer;
  • явно битые конструкции (несбалансированные скобки, host без адреса, порт > 65535).

Окончательную проверку синтаксиса делает сам tcpdump (`check_with_tcpdump`).
"""
from __future__ import annotations

import ipaddress
import re

MAX_LEN = 300

# Должен совпадать с _BPF в ops/agent.py — иначе ops отвергнет команду.
_ALLOWED_CHARS = re.compile(r"^[A-Za-z0-9 .:/_()!&|<>=\[\]-]*$")

KEYWORDS = frozenset("""
and or not
host net port portrange src dst gateway proto protochain mask len less greater
broadcast multicast inbound outbound ifname on
ether fddi tr wlan link ip ip6 arp rarp atalk aarp decnet iso stp ipx netbeui llc
tcp udp icmp icmp6 igmp igrp pim ah esp vrrp sctp
vlan mpls pppoes pppoed geneve vxlan
tcpflags tcp-fin tcp-syn tcp-rst tcp-push tcp-ack tcp-urg tcp-ece tcp-cwr
icmptype icmpcode icmp-echoreply icmp-unreach icmp-sourcequench icmp-redirect
icmp-echo icmp-routeradvert icmp-routersolicit icmp-timxceed icmp-paramprob
icmp-tstamp icmp-tstampreply icmp-ireq icmp-ireqreply icmp-maskreq icmp-maskreply
icmp6type icmp6code
""".split())

# Слова, после которых обязан идти адрес/число
_NEEDS_ADDR = {"host", "net", "gateway"}
_NEEDS_PORT = {"port"}
_NEEDS_RANGE = {"portrange"}

_TOKEN_RE = re.compile(
    r"\s*(?:"
    r"(?P<op>&&|\|\||!=|<=|>=|==|<<|>>|[()\[\]!<>=&|:-])"
    r"|(?P<word>[A-Za-z0-9_.:/-]+)"
    r")"
)

_OPERATORS = {"&&", "||", "!=", "<=", ">=", "==", "<<", ">>", "!", "<", ">", "=", "&", "|", ":", "-"}
_MAC_RE = re.compile(r"^[0-9a-fA-F]{2}([:.-]?)[0-9a-fA-F]{2}(?:\1[0-9a-fA-F]{2}){4}$")
_NUM_RE = re.compile(r"^(?:0x[0-9a-fA-F]{1,8}|\d{1,10})$")
_RANGE_RE = re.compile(r"^(\d{1,5})-(\d{1,5})$")
_SLICE_RE = re.compile(r"^\d{1,4}:\d$")       # ip[2:2] — смещение:размер
_IFACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,31}$")


class BpfError(ValueError):
    """Фильтр не прошёл проверку. Сообщение — для пользователя."""


def _is_addr(w: str) -> bool:
    try:
        ipaddress.ip_network(w, strict=False)
        return True
    except ValueError:
        pass
    # tcpdump допускает сокращённую сеть: "net 10.70" / "net 192.168.8"
    return bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){0,2}", w))


def _tokenize(expr: str) -> list[str]:
    """
    Слова захватываем жадно (tcp-syn, 10.0.0.0/8, aa:bb:..), а «-» и «:» как операторы
    остаются только когда стоят отдельно (len - 14, ip[2 : 2]).
    """
    tokens: list[str] = []
    pos = 0
    while pos < len(expr):
        m = _TOKEN_RE.match(expr, pos)
        if not m or m.end() == pos:
            if expr[pos:].strip() == "":
                break
            raise BpfError(f"Не разобрать фильтр около «{expr[pos:pos + 12]}»")
        tok = m.group("word") or m.group("op")
        if tok:
            tokens.append(tok)
        pos = m.end()
    return tokens


def validate(expr: str) -> str:
    """
    Возвращает нормализованный фильтр ('' — без фильтра, весь трафик).
    Бросает BpfError с понятным пользователю текстом.
    """
    expr = " ".join((expr or "").split())
    if not expr:
        return ""
    if len(expr) > MAX_LEN:
        raise BpfError(f"Фильтр длиннее {MAX_LEN} символов")
    if not _ALLOWED_CHARS.match(expr):
        bad = sorted({c for c in expr if not _ALLOWED_CHARS.match(c)})
        raise BpfError(f"Недопустимые символы: {' '.join(bad)}")

    tokens = _tokenize(expr)
    depth_paren = depth_br = 0
    prev = ""
    for i, tok in enumerate(tokens):
        low = tok.lower()
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None

        if tok == "(":
            depth_paren += 1
        elif tok == ")":
            depth_paren -= 1
            if depth_paren < 0:
                raise BpfError("Лишняя закрывающая скобка «)»")
        elif tok == "[":
            depth_br += 1
        elif tok == "]":
            depth_br -= 1
            if depth_br < 0:
                raise BpfError("Лишняя скобка «]»")
        elif tok in _OPERATORS:
            pass
        elif low in KEYWORDS:
            pass
        elif _NUM_RE.match(tok):
            if prev in _NEEDS_PORT and int(tok, 0) > 65535:
                raise BpfError(f"Порт {tok} вне диапазона 0–65535")
        elif _MAC_RE.match(tok):
            if "ether" not in (t.lower() for t in tokens[max(0, i - 3):i]):
                raise BpfError(f"MAC-адрес {tok}: используйте «ether host {tok}»")
        elif _RANGE_RE.match(tok) and prev in _NEEDS_RANGE:
            a, b = (int(x) for x in _RANGE_RE.match(tok).groups())
            if not (0 <= a <= b <= 65535):
                raise BpfError(f"Некорректный диапазон портов {tok}")
        elif _SLICE_RE.match(tok) and depth_br > 0:
            pass
        elif _is_addr(tok):
            pass
        elif prev in ("ifname", "on") and _IFACE_RE.match(tok):
            pass
        else:
            if prev in _NEEDS_ADDR:
                raise BpfError(f"После «{prev}» нужен IP-адрес или сеть, а не «{tok}» (имена хостов не поддерживаются)")
            if prev in _NEEDS_PORT:
                raise BpfError(f"После «port» нужен номер порта, а не «{tok}»")
            raise BpfError(f"Неизвестное слово «{tok}»")

        if low in _NEEDS_ADDR | _NEEDS_PORT | _NEEDS_RANGE and nxt is None:
            raise BpfError(f"После «{tok}» не хватает значения")
        prev = low

    if depth_paren:
        raise BpfError("Не закрыта скобка «(»")
    if depth_br:
        raise BpfError("Не закрыта скобка «[»")
    if tokens and tokens[-1].lower() in ("and", "or", "not", "&&", "||", "!"):
        raise BpfError(f"Фильтр не может заканчиваться на «{tokens[-1]}»")
    return expr


def check_with_tcpdump(expr: str, iface: str) -> str | None:
    """
    Компилирует фильтр настоящим tcpdump в sniffer (на ~1 с слушает интерфейс).
    Возвращает текст ошибки компилятора или None, если фильтр корректен.
    """
    import subprocess
    from apps.core import ops_client

    argv = ["timeout", "1s", "tcpdump", "-nn", "-c", "1", "-i", iface]
    if expr:
        argv.append(expr)
    try:
        r = ops_client.sniffer_run(argv, timeout=4)
    except subprocess.TimeoutExpired:
        return None
    err = r.stderr or ""
    markers = ("syntax error", "can't parse", "illegal", "unknown", "no such device", "not supported")
    for line in err.splitlines():
        low = line.lower()
        if "promiscuous" in low:
            continue
        if any(m in low for m in markers):
            return line.replace("tcpdump: ", "").strip()
    return None
