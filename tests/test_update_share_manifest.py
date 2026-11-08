from __future__ import annotations

"""Подписанный список выпуска от соседей: подделку, старьё и чужой продукт не берём."""

import base64
import json
import time
from types import SimpleNamespace

import pytest

from zapret_update import share_cert, share_discover, share_manifest, share_serve, share_service
from zapret_update.ed25519 import public_key_from_seed, sign_ed25519

SEED = bytes(range(32))
OTHER_SEED = bytes(range(1, 33))
TRUSTED = {"k": public_key_from_seed(SEED)}
NOW = 1_800_000_000


def signed(version="21.1.7.240", *, seed=SEED, product="zapret-gui", channel="dev", created=NOW - 3600, key="k"):
    payload = {
        "schema": 1, "product": product, "channel": channel, "version": version, "created": created, "key": key,
        "files": [], "installer": {"name": f"Zapret2Setup_{version}.exe", "size": 10, "sha256": "a" * 64},
    }
    raw = json.dumps(payload).encode()
    return raw, base64.b64encode(sign_ed25519(seed, raw))


def fetch(answers, **kw):
    """answers: список ответов соседей по порядку; None — сосед промолчал."""
    neighbours = [SimpleNamespace(host=f"93.184.216.{i + 1}", port=45000, tls=True) for i in range(len(answers))]
    queue = dict(zip((n.host for n in neighbours), answers))
    return share_manifest.fetch_from_peers(
        neighbours, trusted=TRUSTED, product="zapret-gui", channel="dev", current_version="21.1.7.233",
        now=lambda: NOW, ask=lambda n, t: queue[n.host], **kw,
    )


def test_reply_roundtrip_and_garbage() -> None:
    raw, signature = signed()
    assert share_manifest.decode_manifest_reply(share_manifest.encode_manifest_reply(raw, signature)) == (raw, signature)
    for bad in (b"", b"[]", b"not json", b'{"manifest": 5, "signature": "x"}', b'{"manifest": "!!", "signature": "AA=="}',
                b'{"manifest": "", "signature": "AA=="}', b"x" * (share_manifest.MAX_REPLY + 1)):
        assert share_manifest.decode_manifest_reply(bad) is None


def test_genuine_newer_release_is_accepted() -> None:
    release = fetch([signed("21.1.7.240")])
    assert release.manifest.version == "21.1.7.240" and release.source == "93.184.216.1:45000"
    assert release.raw and release.signature


@pytest.mark.parametrize(
    "case",
    ["same_version", "older_version", "wrong_key", "unknown_key_name", "other_product", "other_channel", "too_old", "tampered"],
)
def test_everything_dishonest_or_stale_is_refused(case) -> None:
    raw, sig = {
        "same_version": signed("21.1.7.233"),
        "older_version": signed("21.1.7.200"),
        "wrong_key": signed(seed=OTHER_SEED),
        "unknown_key_name": signed(key="other"),
        "other_product": signed(product="zapret-kvn"),
        "other_channel": signed(channel="stable"),
        "too_old": signed(created=NOW - (share_manifest.MAX_AGE_DAYS + 1) * 86400),
        "tampered": (signed()[0].replace(b"21.1.7.240", b"21.1.7.999"), signed()[1]),
    }[case]
    assert fetch([(raw, sig)]) is None


def test_silent_neighbours_are_skipped_and_the_newest_genuine_release_wins() -> None:
    release = fetch([None, signed("21.1.7.236"), signed("21.1.7.240"), signed("21.1.7.238"), signed("21.1.7.999", seed=OTHER_SEED)])
    assert release.manifest.version == "21.1.7.240"


def test_a_failing_ask_does_not_break_the_others() -> None:
    def ask(neighbour, timeout):
        if neighbour.host.endswith(".1"):
            raise OSError("boom")
        return signed("21.1.7.241")

    neighbours = [SimpleNamespace(host="93.184.216.1", port=45000, tls=True), SimpleNamespace(host="93.184.216.2", port=45000, tls=True)]
    release = share_manifest.fetch_from_peers(
        neighbours, trusted=TRUSTED, product="zapret-gui", channel="dev", current_version="21.1.7.233", now=lambda: NOW, ask=ask
    )
    assert release.manifest.version == "21.1.7.241"


def test_only_a_limited_number_of_neighbours_is_asked() -> None:
    asked = []

    def ask(neighbour, timeout):
        asked.append(neighbour.host)
        return None

    neighbours = [SimpleNamespace(host=f"93.184.216.{i}", port=45000, tls=True) for i in range(1, 30)]
    share_manifest.fetch_from_peers(
        neighbours, trusted=TRUSTED, product="zapret-gui", channel="dev", current_version="1", now=lambda: NOW, ask=ask
    )
    assert len(asked) == share_manifest.ASK_LIMIT


# ── по сети по-настоящему ──────────────────────────────────────────────

def _server(tmp_path, provider, *, tls):
    server = share_serve.ShareServer(
        share_serve.InstalledFiles(tmp_path, []), host="127.0.0.1", allow_loopback=True,
        ssl_context=share_cert.server_context() if tls else None, manifest_provider=provider,
    )
    server.start()
    return server


@pytest.mark.parametrize("tls", [True, False])
def test_neighbour_serves_its_signed_list_and_the_client_verifies_it(tmp_path, tls) -> None:
    raw, sig = signed("21.1.7.240")
    server = _server(tmp_path, lambda: (raw, sig), tls=tls)
    try:
        neighbour = share_discover.Neighbour("127.0.0.1", server.port, tls=tls)
        release = share_manifest.fetch_from_peers(
            [neighbour], trusted=TRUSTED, product="zapret-gui", channel="dev", current_version="21.1.7.233", now=lambda: NOW
        )
        assert release is not None and release.raw == raw and release.manifest.version == "21.1.7.240"
    finally:
        server.stop()


def test_route_is_absent_without_a_provider_and_404_when_the_provider_has_nothing(tmp_path) -> None:
    for provider in (None, lambda: None):
        server = _server(tmp_path, provider, tls=False)
        try:
            neighbour = share_discover.Neighbour("127.0.0.1", server.port)
            assert share_manifest._ask(neighbour, 2) is None
        finally:
            server.stop()


def test_service_hands_the_signed_list_to_both_of_its_servers(tmp_path) -> None:
    raw, sig = signed("21.1.7.233")
    seen = {}

    class Net:
        running = False

        def start(self, files, version, manifest_provider=None, activity=None):
            seen["net"] = manifest_provider
            self.running = True
            return True

        def stop(self):
            self.running = False

    service = share_service.SharingService(
        root=tmp_path, enabled=lambda: True, load_installed=lambda: SimpleNamespace(files=(), version="21.1.7.233"),
        product="zapret-gui", current_version="21.1.7.233", discovery_port=0, host="127.0.0.1", allow_loopback=True,
        internet_enabled=lambda: True, internet=Net(), manifest_blob=lambda: (raw, sig),
    )
    try:
        assert service.refresh() is True
        assert seen["net"]() == (raw, sig)
        neighbour = share_discover.Neighbour("127.0.0.1", service.port)
        assert share_manifest._ask(neighbour, 2) == (raw, sig)  # и сервер по локальной сети тоже отдаёт
    finally:
        service.stop()
