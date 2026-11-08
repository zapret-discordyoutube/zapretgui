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
