from __future__ import annotations

"""Склейка раздачи Zapret 2 GUI с общим ядром: собирается, ничего не запуская."""

from updater.share import wiring
from zapret_update import share_service


def test_default_service_is_built_for_this_product_and_is_a_singleton(monkeypatch) -> None:
    monkeypatch.setattr(wiring, "_default", None)
    service = wiring.default_service()
    assert isinstance(service, share_service.SharingService)
    assert service._product == "zapret-gui" and service._current_version
    assert wiring.default_service() is service
    assert not service.running  # собрана, но не запущена
    assert service._root is None or service._exe_path.name == "Zapret.exe"


def test_service_has_an_internet_side_and_it_follows_its_own_setting(monkeypatch) -> None:
    monkeypatch.setattr(wiring, "_default", None)
    monkeypatch.setattr(wiring, "_book", None)
    service = wiring.default_service()  # не зависает: у книги свой замок
    assert service._internet is not None and not service.internet_running
    assert service._internet_enabled.__name__ == "get_share_updates_internet"


def test_internet_finder_exists_only_when_the_switch_is_on(monkeypatch) -> None:
    from settings import store

    monkeypatch.setattr(store, "get_share_updates_internet", lambda: False)
    assert wiring.internet_finder("1.2.3") is None
    monkeypatch.setattr(store, "get_share_updates_internet", lambda: True)
    assert callable(wiring.internet_finder("1.2.3"))

    def broken():
        raise OSError("db locked")

    monkeypatch.setattr(store, "get_share_updates_internet", broken)
    assert wiring.internet_finder("1.2.3") is None  # не прочиталось — не включено


def test_registry_bases_are_the_wait_addresses_without_the_wait_word(monkeypatch) -> None:
    from types import SimpleNamespace

    from updater.release import watch

    endpoints = (
        SimpleNamespace(name="Forgejo", url="https://git.zapret.moe/api/zapret/wait", verify_ssl=True),
        SimpleNamespace(name="m1", url="https://1.2.3.4:888/api/wait", verify_ssl=False),  # TLS не проверяется — не берём
        SimpleNamespace(name="m2", url="https://5.6.7.8:888/api/wait", verify_ssl=True),
        SimpleNamespace(name="odd", url="https://x/other", verify_ssl=True),
    )
    monkeypatch.setattr(watch, "wait_endpoints", lambda: endpoints)
    assert wiring.registry_bases() == ["https://git.zapret.moe/api/zapret", "https://5.6.7.8:888/api"]


def test_signed_list_of_this_version_is_handed_to_neighbours_from_the_installed_files(tmp_path, monkeypatch) -> None:
    from updater.install import file_update

    monkeypatch.setattr(file_update, "install_root", lambda: tmp_path)
    assert wiring.manifest_blob() is None  # списка ещё нет
    target = tmp_path.joinpath(*file_update.INSTALLED_MANIFEST)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"LIST")
    assert wiring.manifest_blob() is None  # без подписи не отдаём
    target.with_name(target.name + ".sig").write_bytes(b"SIG")
    assert wiring.manifest_blob() == (b"LIST", b"SIG")
    monkeypatch.setattr(file_update, "install_root", lambda: None)
    assert wiring.manifest_blob() is None
    monkeypatch.setattr(wiring, "_default", None)
    assert wiring.default_service()._manifest_blob is wiring.manifest_blob
