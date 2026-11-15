"""The built-in encrypted DNS is a Windows service: where it cannot exist, nothing offers it and nothing starts it."""
from __future__ import annotations

import pytest

from dns import doh_switch, local_proxy
from platforms import Cap, current
from platforms.stub import make_platform

pytestmark = pytest.mark.real_local_proxy

ENCRYPTED_TILE = {"local_proxy": "doh", "ipv4": ["127.0.0.1"]}
DOH_TILE = {"doh": "https://dns.example/dns-query", "ipv4": ["1.1.1.1"]}


@pytest.fixture()
def without_local_proxy(monkeypatch):
    monkeypatch.setattr("platforms.current", lambda: make_platform(capabilities=frozenset()))
    yield


@pytest.fixture()
def with_local_proxy(monkeypatch):
    monkeypatch.setattr("platforms.current", lambda: make_platform(capabilities=frozenset({Cap.LOCAL_DNS_PROXY})))
    yield


def test_windows_has_the_capability_and_linux_does_not():
    from platforms.linux import PLATFORM as linux
    from platforms.windows import PLATFORM as windows

    assert windows.has(Cap.LOCAL_DNS_PROXY) and not linux.has(Cap.LOCAL_DNS_PROXY)


def test_without_it_nothing_reads_starts_or_repairs(without_local_proxy):
    assert local_proxy.available() is False
    assert local_proxy.active_mode() == "" and local_proxy.active_server() == ""
    result = local_proxy.start("doh")
    assert result.success is False and result.message == local_proxy.NOT_AVAILABLE_TEXT
    assert local_proxy.stop_if_unused(["8.8.8.8"]) is False
    assert local_proxy.repair(["127.0.0.1"]) == ""
    local_proxy.stop()  # does nothing, does not raise


def test_without_it_no_tile_is_sent_through_the_proxy(without_local_proxy):
    assert doh_switch.proxy_mode(ENCRYPTED_TILE, doh_supported=False) == ""
    assert doh_switch.proxy_mode(DOH_TILE, doh_supported=False, doh_enabled=True) == ""


def test_with_it_the_proxy_decisions_are_the_old_ones(with_local_proxy):
    assert local_proxy.available() is True
    assert doh_switch.proxy_mode(ENCRYPTED_TILE, doh_supported=False) != ""
    assert doh_switch.proxy_mode(DOH_TILE, doh_supported=False, doh_enabled=False) == ""


def test_a_test_supplied_system_is_always_available():
    assert local_proxy.available(object()) is True
