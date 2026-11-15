"""Turns the WinDivert capture options of a preset into an nftables ruleset.

On Windows the engine (``winws2``) captures packets itself and the preset says
what to capture with ``--wf-*`` options. On Linux the engine (``nfqws2``) reads
packets from an NFQUEUE and the kernel must be told, with nftables, which packets
to send there. This module is that translation. It is pure text in, text out:
no root, no system calls, so it is fully testable.

The rules follow the layout of the original zapret2 (``docs/readme.md``,
"Механика обработки трафика"):

* ``meta mark and 0x40000000 == 0``: packets the engine itself sends carry this
  mark and must not be captured again;
* ``ct original packets 1-N`` (outbound) and ``ct reply packets 1-N`` (inbound):
  only the first packets of a connection are queued;
* a ``notrack`` rule for marked output (the engine's fake packets).

Nothing is dropped silently (project rule: the preset is the source of truth):
an option that has no effect on Linux is listed in ``ignored`` with the reason,
and one that cannot be translated is listed in ``unsupported``. The caller must
refuse to start when ``unsupported`` is not empty.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import product
from typing import Callable, Sequence

TABLE_NAME = "zapretgui"
DESYNC_MARK = 0x40000000
DEFAULT_PACKET_LIMIT = 12
PRE_PRIORITY = -101
POST_PRIORITY = 101
PREDEFRAG_PRIORITY = -401

# Not global addresses (WinDivert constructor option --wf-filter-lan, on by default).
LAN_IPV4 = ("0.0.0.0/8", "10.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12", "192.168.0.0/16")
LAN_IPV6 = ("::1", "fc00::/7", "fe80::/10")

SYSCTLS = {"net.netfilter.nf_conntrack_tcp_be_liberal": "1"}

_MAX_CONJUNCTIONS = 64

# Options of the WinDivert constructor that have no meaning for NFQUEUE.
_IGNORED = {
    "--wf-tcp-empty": "Linux: conntrack counts every packet, empty ACKs included, so the counters are exact",
    "--wf-dup-check": "WinDivert only: the same filter may run twice there",
    "--wf-priority": "WinDivert only: the priority of the filter in the driver",
    "--wf-save": "WinDivert only: saves the generated WinDivert filter to a file",
}


class UnsupportedFilter(ValueError):
    """A WinDivert expression the translator cannot turn into nftables."""


@dataclass(frozen=True)
class CaptureTranslation:
    ruleset: str  # nft script (atomic): replaces the table; empty when there is nothing to capture
    engine_args: tuple[str, ...]  # arguments for nfqws2: everything that is not a --wf-* option
    ignored: tuple[str, ...]  # "option: why it has no effect on Linux"
    unsupported: tuple[str, ...]  # "option: why it cannot be translated"; the caller must refuse
    sysctls: tuple[tuple[str, str], ...]  # kernel settings the ruleset needs

    @property
    def ok(self) -> bool:
        return not self.unsupported


# --- ports ---------------------------------------------------------------------

_PORT = re.compile(r"^(\d{1,5})(?:-(\d{1,5}))?$")


def _port_set(value: str) -> tuple[bool, str]:
    """'80,443-65535' -> (negate, '{ 80, 443-65535 }'). A leading '~' negates the whole list."""
    text = value.strip()
    negate = text.startswith("~")
    if negate:
        text = text[1:]
    items: list[str] = []
    for part in text.split(","):
        found = _PORT.match(part.strip())
        if not found:
            raise UnsupportedFilter(f"port list '{value}': '{part.strip()}' is not a port or a range")
        low = int(found.group(1))
        high = int(found.group(2)) if found.group(2) else low
        if not (0 <= low <= high <= 65535):
            raise UnsupportedFilter(f"port list '{value}': '{part.strip()}' is out of range")
        items.append(str(low) if low == high else f"{low}-{high}")
    if not items:
        raise UnsupportedFilter(f"port list '{value}' is empty")
    return negate, "{ " + ", ".join(items) + " }"


# --- the WinDivert filter subset used by raw parts -----------------------------------

_TOKEN = re.compile(
    r"\s*(?:(?P<num>0[xX][0-9A-Fa-f]+|\d+)"
    r"|(?P<op>==|!=|<=|>=|<|>|=)"
    r"|(?P<andor>&&|\|\||\band\b|\bor\b)"
    r"|(?P<paren>[()])"
    r"|(?P<name>[A-Za-z_][A-Za-z0-9_.]*(?:\[\s*\d+\s*\])?))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Atom:
    name: str  # lower case, without the index
    index: int | None
    op: str | None
    value: int | None
    text: str


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    text = text.strip()
    while position < len(text):
        found = _TOKEN.match(text, position)
        if not found or found.end() == position:
            raise UnsupportedFilter(f"cannot read the WinDivert filter near '{text[position:position + 20]}'")
        position = found.end()
        kind = found.lastgroup
        value = found.group(kind)
        if kind == "andor":
            value = "and" if value.lower() in {"and", "&&"} else "or"
        tokens.append((kind, value))
    return tokens


class _Parser:
    """expr := term (or term)*;  term := factor (and factor)*;  factor := '(' expr ')' | atom.

    The result is the filter in disjunctive normal form: a list of conjunctions of atoms.
    nftables cannot OR different matches in one rule, so every conjunction becomes its own rule.
    """

    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.position = 0

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def _take(self) -> tuple[str, str]:
        token = self._peek()
        if token is None:
            raise UnsupportedFilter("the WinDivert filter ends too early")
        self.position += 1
        return token

    def parse(self) -> list[list[_Atom]]:
        result = self._expr()
        if self._peek() is not None:
            raise UnsupportedFilter(f"unexpected '{self._peek()[1]}' in the WinDivert filter")
        return result

    def _expr(self) -> list[list[_Atom]]:
        result = self._term()
        while self._peek() == ("andor", "or"):
            self._take()
            result = result + self._term()
            self._check_size(result)
        return result

    def _term(self) -> list[list[_Atom]]:
        result = self._factor()
        while self._peek() == ("andor", "and"):
            self._take()
            right = self._factor()
            result = [left + extra for left, extra in product(result, right)]
            self._check_size(result)
        return result

    def _factor(self) -> list[list[_Atom]]:
        kind, value = self._take()
        if kind == "paren" and value == "(":
            inner = self._expr()
            if self._take() != ("paren", ")"):
                raise UnsupportedFilter("a bracket is not closed in the WinDivert filter")
            return inner
        if kind != "name":
            raise UnsupportedFilter(f"unexpected '{value}' in the WinDivert filter")
        if value.lower() == "not":
            raise UnsupportedFilter("'not' in a WinDivert filter")
        name, index = value, None
        bracket = re.match(r"^(.*?)\[\s*(\d+)\s*\]$", value)
        if bracket:
            name, index = bracket.group(1), int(bracket.group(2))
        op = number = None
        following = self._peek()
        if following is not None and following[0] == "op":
            self._take()
            op = "==" if following[1] in {"=", "=="} else following[1]
            kind2, value2 = self._take()
            if kind2 != "num":
                raise UnsupportedFilter(f"'{name} {following[1]}' needs a number in the WinDivert filter")
            number = int(value2, 0)
        text = value if op is None else f"{value} {following[1]} {number}"
        return [[_Atom(name.lower(), index, op, number, text)]]

    @staticmethod
    def _check_size(result: list) -> None:
        if len(result) > _MAX_CONJUNCTIONS:
            raise UnsupportedFilter("the WinDivert filter is too branched to translate")


def _compare(op: str | None) -> str:
    return {"==": "", "!=": "!= ", "<": "< ", "<=": "<= ", ">": "> ", ">=": ">= "}[op or "=="]


@dataclass(frozen=True)
class _Rule:
    direction: str  # "out", "in" or "both"
    family: str  # "ipv4", "ipv6" or "" for both
    proto: str  # "udp", "tcp" or ""
    matches: tuple[str, ...]


def _conjunction_to_rule(atoms: Sequence[_Atom]) -> _Rule | None:
    """One conjunction of WinDivert atoms -> one rule. None when it can never match."""
    direction = "both"
    family = ""
    proto = ""
    matches: list[str] = []

    def set_proto(value: str) -> None:
        nonlocal proto
        if proto and proto != value:
            raise _Impossible
        proto = value

    try:
        for atom in atoms:
            name = atom.name
            if name in {"outbound", "inbound"} and atom.op is None:
                wanted = "out" if name == "outbound" else "in"
                if direction not in {"both", wanted}:
                    raise _Impossible
                direction = wanted
            elif name in {"ip", "ipv6"} and atom.op is None:
                wanted = "ipv4" if name == "ip" else "ipv6"
                if family and family != wanted:
                    raise _Impossible
                family = wanted
            elif name in {"udp", "tcp"} and atom.op is None:
                set_proto(name)
            elif name == "udp.payloadlength" and atom.op:
                set_proto("udp")
                matches.append(f"udp length {_compare(atom.op)}{atom.value + 8}")
            elif name == "udp.payload32" and atom.op and atom.index is not None:
                set_proto("udp")
                matches.append(f"@th,{64 + 32 * atom.index},32 {_compare(atom.op)}0x{atom.value:08x}")
            elif name == "udp.payload" and atom.op and atom.index is not None:
                set_proto("udp")
                matches.append(f"@th,{64 + 8 * atom.index},8 {_compare(atom.op)}0x{atom.value:02x}")
            elif name in {"udp.dstport", "udp.srcport"} and atom.op:
                set_proto("udp")
                side = "dport" if name.endswith("dstport") else "sport"
                matches.append(f"udp {side} {_compare(atom.op)}{atom.value}")
            else:
                raise UnsupportedFilter(f"'{atom.text}' in a WinDivert filter")
    except _Impossible:
        return None
    return _Rule(direction, family, proto, tuple(matches))


class _Impossible(Exception):
    """A conjunction that contradicts itself (for example inbound and outbound)."""


def translate_raw_part(text: str) -> list[_Rule]:
    """A raw WinDivert filter -> rules (raises UnsupportedFilter)."""
    plain = "\n".join(line.split("#", 1)[0] for line in str(text or "").splitlines())
    conjunctions = _Parser(_tokenize(plain)).parse()
    rules = [rule for rule in (_conjunction_to_rule(atoms) for atoms in conjunctions) if rule is not None]
    if not rules:
        raise UnsupportedFilter("the WinDivert filter can never match")
    return rules


# --- the whole translation ------------------------------------------------------------

def _split_option(arg: str) -> tuple[str, str | None]:
    name, separator, value = arg.partition("=")
    return name, (value if separator else None)


def translate(
    args: Sequence[str],
    *,
    queue_num: int,
    read_raw: Callable[[str], str] | None = None,
    packet_limit: int = DEFAULT_PACKET_LIMIT,
    table: str = TABLE_NAME,
) -> CaptureTranslation:
    """Translates the capture part of an engine argument list.

    ``read_raw(name)`` returns the text of a ``--wf-raw-part=@name`` file (the name as written in
    the preset, relative to the program folder); without it such options are unsupported.
    """
    engine_args: list[str] = []
    ignored: list[str] = []
    unsupported: list[str] = []
    ports: dict[str, tuple[bool, str]] = {}
    raw_rules: list[_Rule] = []
    families = {"ipv4", "ipv6"}
    filter_lan = True

    for arg in args:
        name, value = _split_option(arg)
        if not name.startswith("--wf-"):
            engine_args.append(arg)
            continue
        try:
            if name in {"--wf-tcp-out", "--wf-tcp-in", "--wf-udp-out", "--wf-udp-in"}:
                ports[name] = _port_set(value or "")
            elif name == "--wf-raw-part":
                if not value:
                    raise UnsupportedFilter("--wf-raw-part without a filter")
                if value.startswith("@"):
                    if read_raw is None:
                        raise UnsupportedFilter(f"cannot read the filter file {value[1:]}")
                    filter_text = read_raw(value[1:])
                else:
                    filter_text = value
                raw_rules.extend(translate_raw_part(filter_text))
            elif name == "--wf-filter-lan":
                filter_lan = (value or "1").strip() not in {"0", "false"}
            elif name == "--wf-l3":
                wanted = (value or "").strip().lower()
                if wanted not in {"ipv4", "ipv6"}:
                    raise UnsupportedFilter(f"--wf-l3={value}")
                families = {wanted}
            elif name in _IGNORED:
                ignored.append(f"{arg}: {_IGNORED[name]}")
            else:
                raise UnsupportedFilter("this WinDivert option has no nftables translation")
        except (UnsupportedFilter, OSError) as error:
            unsupported.append(f"{arg}: {error}")

    rules_post: list[str] = []
    rules_pre: list[str] = []
    marker = f"meta mark and 0x{DESYNC_MARK:08x} == 0"
    queue = f"queue num {int(queue_num)} bypass"

    def emit(direction: str, family: str, proto: str, matches: Sequence[str]) -> None:
        """One capture clause -> rules. With the LAN filter on, IPv4 and IPv6 get a rule each (their address
        matches differ); with it off one rule covers both unless the family is restricted."""
        wanted = [f for f in ("ipv4", "ipv6") if f in families and family in {"", f}]
        for chain in ("out", "in"):
            if direction not in {"both", chain} or not wanted:
                continue
            tail = [f"meta l4proto {proto}"] if proto else []
            tail += list(matches)
            tail += [f"ct {'original' if chain == 'out' else 'reply'} packets 1-{int(packet_limit)}", queue]
            if filter_lan:
                side = "daddr" if chain == "out" else "saddr"
                heads = [[f"ip {side} != @lan4"] if fam == "ipv4" else [f"ip6 {side} != @lan6"] for fam in wanted]
            else:
                heads = [[] if len(wanted) == 2 else [f"meta nfproto {wanted[0]}"]]
            for head in heads:
                (rules_post if chain == "out" else rules_pre).append(" ".join([marker, *head, *tail]))

    for option, (negate, port_set) in ports.items():
        proto = "tcp" if "tcp" in option else "udp"
        outbound = option.endswith("-out")
        side = "dport" if outbound else "sport"
        comparison = "!= " if negate else ""
        emit("out" if outbound else "in", "", proto, [f"{proto} {side} {comparison}{port_set}"])
    for rule in raw_rules:
        emit(rule.direction, rule.family, rule.proto, rule.matches)

    ruleset = ""
    if rules_post or rules_pre:
        ruleset = _render(table, list(dict.fromkeys(rules_post)), list(dict.fromkeys(rules_pre)), filter_lan, families)

    return CaptureTranslation(
        ruleset=ruleset,
        engine_args=tuple(engine_args),
        ignored=tuple(ignored),
        unsupported=tuple(unsupported),
        sysctls=tuple(SYSCTLS.items()) if ruleset else (),
    )


def _render(table: str, post: list[str], pre: list[str], filter_lan: bool, families: set[str]) -> str:
    lines = [
        f"table inet {table}",
        f"delete table inet {table}",
        f"table inet {table} {{",
    ]
    if filter_lan:
        lines.append(f"  set lan4 {{ type ipv4_addr; flags interval; elements = {{ {', '.join(LAN_IPV4)} }} }}")
        lines.append(f"  set lan6 {{ type ipv6_addr; flags interval; elements = {{ {', '.join(LAN_IPV6)} }} }}")
    # The engine's own packets are marked; they must skip connection tracking.
    lines += [
        "  chain predefrag {",
        f"    type filter hook output priority {PREDEFRAG_PRIORITY}; policy accept;",
        f"    meta mark and 0x{DESYNC_MARK:08x} != 0 notrack",
        "  }",
    ]
    if post:
        lines += ["  chain post {", f"    type filter hook postrouting priority {POST_PRIORITY}; policy accept;"]
        lines += [f"    {rule}" for rule in post]
        lines.append("  }")
    if pre:
        lines += ["  chain pre {", f"    type filter hook prerouting priority {PRE_PRIORITY}; policy accept;"]
        lines += [f"    {rule}" for rule in pre]
        lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def teardown_ruleset(table: str = TABLE_NAME) -> str:
    """Removes the table whether it exists or not (declare, then delete: one atomic script)."""
    return f"table inet {table}\ndelete table inet {table}\n"
