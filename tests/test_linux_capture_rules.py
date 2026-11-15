"""WinDivert capture options -> nftables rules (the Linux side of a preset)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from platforms.linux import capture_rules as cr
from platforms.linux.capture_rules import UnsupportedFilter, translate, translate_raw_part

SRC = Path(__file__).resolve().parents[1] / "src"
BUILTIN_WINWS2 = SRC / "presets" / "builtin" / "winws2"

# The three raw filters the builtin presets use (copies of private_zapretgui/dist/windivert.filter).
WIREGUARD = """udp.PayloadLength=148 and udp.Payload32[0]=0x01000000 or
udp.PayloadLength=92 and udp.Payload32[0]=0x02000000 or
udp.PayloadLength=64 and udp.Payload32[0]=0x03000000"""
STUN = """  outbound and
  udp.PayloadLength>=20 and
  udp.Payload32[1]=0x2112A442 and udp.Payload[0]<0x40"""
DISCORD = (
    "  outbound and ip and\n"
    "  (udp.DstPort>=50000 and udp.DstPort<=50099 or udp.DstPort>=19294 and udp.DstPort<=19344) and\n"
    "  udp.PayloadLength=74 and\n  udp.Payload32[0]=0x00010046 and\n"
    + " and\n".join(f"  udp.Payload32[{i}]=0" for i in range(2, 18))
)
RAW_FILES = {
    "windivert.filter/windivert_part.wireguard.txt": WIREGUARD,
    "windivert.filter/windivert_part.stun.txt": STUN,
    "windivert.filter/windivert_part.discord_media.txt": DISCORD,
}


def read_raw(name: str) -> str:
    return RAW_FILES[name]


def _lines(result, chain: str) -> list[str]:
    inside = False
    found = []
    for line in result.ruleset.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"chain {chain} "):
            inside = True
        elif inside and stripped == "}":
            inside = False
        elif inside and stripped.startswith("meta mark"):
            found.append(stripped)
    return found


def _nft_accepts(ruleset: str) -> tuple[bool, str]:
    if not shutil.which("nft"):
        pytest.skip("nft is not installed")
    with tempfile.NamedTemporaryFile("w", suffix=".nft", delete=False) as handle:
        handle.write(ruleset)
    try:
        done = subprocess.run(["nft", "-c", "-f", handle.name], capture_output=True, text=True)
    finally:
        Path(handle.name).unlink(missing_ok=True)
    if "Operation not permitted" in done.stderr:
        pytest.skip("nft cannot check rules here (no permission)")
    return done.returncode == 0, done.stderr


# --- ports ----------------------------------------------------------------------------

def test_outbound_ports_become_dport_rules_for_both_families():
    result = translate(["--wf-tcp-out=80,443-65535"], queue_num=200)
    post = _lines(result, "post")
    assert len(post) == 2
    assert any("ip daddr != @lan4" in r and "tcp dport { 80, 443-65535 }" in r for r in post)
    assert any("ip6 daddr != @lan6" in r and "tcp dport { 80, 443-65535 }" in r for r in post)
    assert all("ct original packets 1-12 queue num 200 bypass" in r for r in post)
    assert _lines(result, "pre") == []


def test_inbound_ports_use_the_source_port_and_the_reply_counter():
    result = translate(["--wf-udp-in=53"], queue_num=7, packet_limit=5)
    pre = _lines(result, "pre")
    assert len(pre) == 2
    assert all("udp sport { 53 }" in r and "ct reply packets 1-5 queue num 7 bypass" in r for r in pre)
    assert _lines(result, "post") == []


def test_a_leading_tilde_negates_the_list():
    result = translate(["--wf-tcp-out=~80"], queue_num=1)
    assert all("tcp dport != { 80 }" in r for r in _lines(result, "post"))


@pytest.mark.parametrize("bad", ["--wf-tcp-out=abc", "--wf-tcp-out=80,", "--wf-tcp-out=70000", "--wf-tcp-out=443-80", "--wf-tcp-out="])
def test_bad_port_lists_are_unsupported_not_guessed(bad):
    result = translate([bad], queue_num=1)
    assert result.unsupported and not result.ok and result.ruleset == ""


# --- raw WinDivert filters ------------------------------------------------------------------

def test_the_wireguard_filter_is_three_rules_in_both_directions():
    result = translate(["--wf-raw-part=@windivert.filter/windivert_part.wireguard.txt"], queue_num=200, read_raw=read_raw)
    assert result.ok
    for chain, counter in (("post", "original"), ("pre", "reply")):
        rules = _lines(result, chain)
        assert len(rules) == 6  # three filters x IPv4/IPv6
        for length, word in ((156, "0x01000000"), (100, "0x02000000"), (72, "0x03000000")):
            assert any(f"udp length {length} @th,64,32 {word} ct {counter} packets" in r for r in rules)


def test_the_stun_filter_is_outbound_only_and_reads_the_right_offsets():
    result = translate(["--wf-raw-part=@windivert.filter/windivert_part.stun.txt"], queue_num=1, read_raw=read_raw)
    assert _lines(result, "pre") == []
    post = _lines(result, "post")
    assert len(post) == 2
    # payload length >= 20 -> udp length >= 28; word 1 is at bit 96; byte 0 < 0x40 is at bit 64
    assert all("udp length >= 28 @th,96,32 0x2112a442 @th,64,8 < 0x40" in r for r in post)


def test_the_discord_filter_is_ipv4_only_with_both_port_ranges():
    result = translate(["--wf-raw-part=@windivert.filter/windivert_part.discord_media.txt"], queue_num=1, read_raw=read_raw)
    post = _lines(result, "post")
    assert len(post) == 2  # one rule per port range
    assert all("ip daddr != @lan4" in r and "ip6" not in r for r in post)
    assert any("udp dport >= 50000 udp dport <= 50099" in r for r in post)
    assert any("udp dport >= 19294 udp dport <= 19344" in r for r in post)
    assert all("udp length 82 @th,64,32 0x00010046" in r and "@th,608,32 0x00000000" in r for r in post)


def test_and_binds_tighter_than_or_and_brackets_change_it():
    plain = translate_raw_part("udp.DstPort=1 or udp.DstPort=2 and udp.PayloadLength=10")
    assert len(plain) == 2
    assert plain[0].matches == ("udp dport 1",)
    assert plain[1].matches == ("udp dport 2", "udp length 18")
    bracket = translate_raw_part("(udp.DstPort=1 or udp.DstPort=2) and udp.PayloadLength=10")
    assert [r.matches for r in bracket] == [("udp dport 1", "udp length 18"), ("udp dport 2", "udp length 18")]


def test_contradictions_are_dropped_and_an_impossible_filter_is_unsupported():
    assert len(translate_raw_part("inbound and udp.DstPort=1 or outbound and udp.DstPort=2")) == 2
    with pytest.raises(UnsupportedFilter):
        translate_raw_part("inbound and outbound and udp.DstPort=1")


@pytest.mark.parametrize(
    "text",
    ["ip.DstAddr == 1.2.3.4", "not udp", "tcp.PayloadLength=5", "udp.PayloadLength", "(udp.DstPort=1", "udp.DstPort=1 and", "udp.Payload32[0] = x"],
)
def test_filters_outside_the_known_subset_are_refused(text):
    with pytest.raises(UnsupportedFilter):
        translate_raw_part(text)


def test_a_filter_that_branches_too_much_is_refused():
    big = " and ".join("(udp.DstPort=1 or udp.DstPort=2)" for _ in range(8))
    with pytest.raises(UnsupportedFilter):
        translate_raw_part(big)


def test_inline_raw_part_works_and_a_file_without_a_reader_is_unsupported():
    inline = translate(["--wf-raw-part=udp.DstPort=5000"], queue_num=1)
    assert inline.ok and len(_lines(inline, "post")) == 2
    assert translate(["--wf-raw-part=@some/file.txt"], queue_num=1).unsupported


# --- the rest of the preset ---------------------------------------------------------------------

def test_engine_arguments_pass_through_in_order_without_the_wf_options():
    args = ["--lua-init=@lua/a.lua", "--wf-tcp-out=80", "--filter-tcp=443", "--lua-desync=fake", "--wf-udp-out=443"]
    result = translate(args, queue_num=1)
    assert result.engine_args == ("--lua-init=@lua/a.lua", "--filter-tcp=443", "--lua-desync=fake")


def test_windivert_only_options_are_reported_as_ignored_with_a_reason():
    result = translate(["--wf-tcp-out=80", "--wf-tcp-empty=1", "--wf-dup-check=0", "--wf-priority=5", "--wf-save=x"], queue_num=1)
    assert result.ok
    names = [line.split(":")[0] for line in result.ignored]
    assert names == ["--wf-tcp-empty=1", "--wf-dup-check=0", "--wf-priority=5", "--wf-save=x"]
    assert all(": " in line and len(line.split(": ", 1)[1]) > 10 for line in result.ignored)


@pytest.mark.parametrize("option", ["--wf-raw=@x.txt", "--wf-raw-filter=ip", "--wf-iface=3", "--wf-mystery=1"])
def test_other_wf_options_are_unsupported_and_never_dropped_silently(option):
    result = translate(["--wf-tcp-out=80", option], queue_num=1)
    assert not result.ok
    assert any(line.startswith(option) for line in result.unsupported)
    assert option not in result.engine_args


def test_lan_filter_off_removes_the_address_sets_and_merges_the_families():
    result = translate(["--wf-tcp-out=80", "--wf-filter-lan=0"], queue_num=1)
    assert "@lan4" not in result.ruleset and "set lan4" not in result.ruleset
    assert len(_lines(result, "post")) == 1
    assert "meta nfproto" not in _lines(result, "post")[0]


def test_l3_restricts_the_families():
    v4 = translate(["--wf-tcp-out=80", "--wf-l3=ipv4"], queue_num=1)
    assert len(_lines(v4, "post")) == 1 and "ip6" not in v4.ruleset.split("chain post")[1]
    v6_nolan = translate(["--wf-tcp-out=80", "--wf-l3=ipv6", "--wf-filter-lan=0"], queue_num=1)
    assert "meta nfproto ipv6" in _lines(v6_nolan, "post")[0]
    assert translate(["--wf-l3=ipv5"], queue_num=1).unsupported


def test_nothing_to_capture_means_an_empty_ruleset_and_no_kernel_settings():
    result = translate(["--lua-desync=fake"], queue_num=1)
    assert result.ok and result.ruleset == "" and result.sysctls == ()


def test_ruleset_replaces_the_table_atomically_and_marks_the_engine_packets():
    result = translate(["--wf-tcp-out=80"], queue_num=9, table="zt")
    lines = result.ruleset.splitlines()
    assert lines[:3] == ["table inet zt", "delete table inet zt", "table inet zt {"]
    assert "meta mark and 0x40000000 != 0 notrack" in result.ruleset
    assert all("meta mark and 0x40000000 == 0" in r for r in _lines(result, "post"))
    assert ("net.netfilter.nf_conntrack_tcp_be_liberal", "1") in result.sysctls
    assert cr.teardown_ruleset("zt") == "table inet zt\ndelete table inet zt\n"


# --- against the real nft parser and the real presets ------------------------------------------------

@pytest.mark.parametrize(
    "args",
    [
        ["--wf-tcp-out=80,443-65535", "--wf-udp-out=443", "--wf-tcp-in=443", "--wf-udp-in=53,5000-6000"],
        ["--wf-tcp-out=~22", "--wf-filter-lan=0"],
        ["--wf-udp-out=1", "--wf-l3=ipv6", "--wf-filter-lan=0"],
        [f"--wf-raw-part=@{name}" for name in RAW_FILES],
    ],
)
def test_generated_rulesets_pass_the_real_nft_parser(args):
    result = translate(args, queue_num=200, read_raw=read_raw)
    assert result.ok and result.ruleset
    accepted, error = _nft_accepts(result.ruleset)
    assert accepted, error


def test_teardown_is_accepted_by_nft():
    accepted, error = _nft_accepts(cr.teardown_ruleset())
    assert accepted, error


# Presets that cannot be translated yet. Empty on purpose: a new builtin preset with a capture option
# the translator does not know must be handled here (teach the translator, or list it with the reason).
KNOWN_LINUX_GAPS: dict[str, str] = {}


def test_every_builtin_winws2_preset_translates_and_nft_accepts_it():
    from winws_runtime.runners.preset_runner_support import launch_args_from_preset_text

    presets = sorted(BUILTIN_WINWS2.glob("*.txt"))
    assert len(presets) > 50
    failures = []
    for path in presets:
        if path.name in KNOWN_LINUX_GAPS:
            continue
        result = translate(launch_args_from_preset_text(path.read_text(encoding="utf-8")), queue_num=200, read_raw=read_raw)
        if result.unsupported or not result.ruleset:
            failures.append(f"{path.name}: {result.unsupported or 'no capture rules'}")
            continue
        accepted, error = _nft_accepts(result.ruleset)
        if not accepted:
            failures.append(f"{path.name}: nft says {error.strip()[:200]}")
    assert failures == [], "\n".join(failures)


def test_embedded_raw_filter_copies_match_the_real_files_when_they_are_present():
    folder = SRC.parents[1] / "private_zapretgui" / "dist" / "windivert.filter"
    if not folder.is_dir():
        pytest.skip("private repository not present")
    for name, text in RAW_FILES.items():
        real = (folder / Path(name).name).read_text(encoding="utf-8")
        assert [line.strip() for line in real.splitlines() if line.strip()] == [line.strip() for line in text.splitlines() if line.strip()]
