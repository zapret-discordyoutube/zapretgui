from __future__ import annotations

"""Программа, поставленная установщиком, сама берёт подписанный список своей версии."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from updater.share import adopt, discover, runtime

LIST_PATH = ("system", "update", "installed-manifest.json")


def _signed(version: str):
    return SimpleNamespace(manifest=SimpleNamespace(version=version, files=()), raw=b'{"raw":1}', signature=b"sig")


def _lookup(version: str | None, error: str = ""):
    release = {"version": version} if version else None
    return lambda _channel: SimpleNamespace(release=release, error=error)


def test_matching_newest_release_is_saved_next_to_the_program(tmp_path):
    reports: list[str] = []
    manifest = adopt.adopt_installed_list(
        tmp_path, channel="dev", version="21.1.7.231", list_path=LIST_PATH,
        lookup=_lookup("21.1.7.231"), fetch=lambda release, **kw: _signed(release["version"]), report=reports.append,
    )
    assert manifest.version == "21.1.7.231"
    target = tmp_path.joinpath(*LIST_PATH)
    assert target.read_bytes() == b'{"raw":1}' and target.with_name(target.name + ".sig").read_bytes() == b"sig"
    assert not list(target.parent.glob("*.tmp")) and "сохранён" in reports[-1]


def test_fetch_is_asked_for_the_same_version_with_the_installed_one_as_base(tmp_path):
    seen = {}

    def fetch(release, **kwargs):
        seen.update(kwargs)
        return _signed("1.0")

    adopt.adopt_installed_list(tmp_path, channel="stable", version="1.0", list_path=LIST_PATH,
                               lookup=_lookup("1.0"), fetch=fetch)
    assert seen == {"channel": "stable", "current_version": "1.0", "allow_same": True}


@pytest.mark.parametrize("case", ["no_answer", "newer_exists", "older_exists", "signed_other_version"])
def test_nothing_is_saved_when_the_version_does_not_match(tmp_path, case):
    reports: list[str] = []
    fetched: list[object] = []
    lookup = {"no_answer": _lookup(None, "нет сети"), "newer_exists": _lookup("21.1.7.232"),
              "older_exists": _lookup("21.1.7.230"), "signed_other_version": _lookup("21.1.7.231")}[case]
    returned = "21.1.7.999" if case == "signed_other_version" else "21.1.7.231"
    result = adopt.adopt_installed_list(
        tmp_path, channel="dev", version="21.1.7.231", list_path=LIST_PATH, lookup=lookup,
        fetch=lambda release, **kw: fetched.append(1) or _signed(returned), report=reports.append,
    )
    assert result is None and not (tmp_path / "system").exists() and reports
    if case != "signed_other_version":
        assert not fetched  # лишний список даже не скачивается


def _service(tmp_path, *, installed, adopt_result=None, adopt_error=None, enabled=lambda: True, current="21.1.7.231"):
    calls = {"adopt": 0}

    def adopt_call():
        calls["adopt"] += 1
        if adopt_error:
            raise adopt_error
        return adopt_result

    reports: list[str] = []
    service = runtime.SharingService(
        root=tmp_path, enabled=enabled, load_installed=lambda: installed, product="zapret-gui",
        current_version=current, adopt=adopt_call, discovery_port=0, host="127.0.0.1", allow_loopback=True,
        report=reports.append,
    )
    return service, calls, reports


def _manifest(version: str):
    return SimpleNamespace(files=(), version=version)


def test_missing_list_is_adopted_and_sharing_starts(tmp_path):
    service, calls, _ = _service(tmp_path, installed=None, adopt_result=_manifest("21.1.7.231"))
    try:
        assert service.refresh() is True and calls["adopt"] == 1
    finally:
        service.stop()


def test_list_of_another_version_is_replaced_and_the_right_version_is_announced(tmp_path):
    service, calls, _ = _service(tmp_path, installed=_manifest("21.1.7.200"), adopt_result=_manifest("21.1.7.231"))
    try:
        assert service.refresh() is True
        found = discover.find_neighbours("zapret-gui", "21.1.7.231", timeout=1, sources=["127.0.0.1"],
                                         targets=[("127.0.0.1", service.discovery_port)], allow_loopback=True)
        old = discover.find_neighbours("zapret-gui", "21.1.7.200", timeout=0.4, sources=["127.0.0.1"],
                                       targets=[("127.0.0.1", service.discovery_port)], allow_loopback=True)
        assert found and not old
    finally:
        service.stop()


def test_stale_list_that_cannot_be_replaced_means_no_sharing(tmp_path):
    service, calls, reports = _service(tmp_path, installed=_manifest("21.1.7.200"), adopt_result=None)
    assert service.refresh() is False and not service.running and calls["adopt"] == 1 and reports


def test_fitting_list_is_used_without_any_network_lookup(tmp_path):
    service, calls, _ = _service(tmp_path, installed=_manifest("21.1.7.231"))
    try:
        assert service.refresh() is True and calls["adopt"] == 0
    finally:
        service.stop()


def test_adopt_failure_is_reported_and_does_not_escape(tmp_path):
    service, _, reports = _service(tmp_path, installed=None, adopt_error=OSError("no route"))
    assert service.refresh() is False and any("no route" in text for text in reports)


def test_switch_turned_off_during_the_lookup_wins(tmp_path):
    flag = {"on": True}
    service, calls, _ = _service(tmp_path, installed=None, enabled=lambda: flag["on"],
                                 adopt_result=_manifest("21.1.7.231"))
    original = service._adopt
    service._adopt = lambda: (flag.update(on=False), original())[1]
    assert service.refresh() is False and not service.running
