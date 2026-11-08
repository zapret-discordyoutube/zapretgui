from __future__ import annotations

"""Настройка «делиться обновлениями по сети»: по умолчанию включена, хранится в SQLite."""

import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from settings.normalize import normalize_program


def test_default_is_on_and_garbage_falls_back_to_default() -> None:
    assert normalize_program({})["share_updates_lan"] is True
    assert normalize_program({"share_updates_lan": False})["share_updates_lan"] is False
    assert normalize_program({"share_updates_lan": "maybe"})["share_updates_lan"] is True


def test_value_survives_the_sqlite_store() -> None:
    from settings import store

    with TemporaryDirectory() as temp_dir:
        with patch("settings.store.MAIN_DIRECTORY", str(Path(temp_dir))):
            store.close_settings_database()
            try:
                assert store.get_share_updates_lan() is True
                store.set_share_updates_lan(False)
                assert store.get_share_updates_lan() is False
                store.set_share_updates_lan(True)
                assert store.get_share_updates_lan() is True
            finally:
                store.close_settings_database()


def test_page_setting_reads_and_writes_the_share_flag_off_the_gui_thread() -> None:
    from PyQt6.QtCore import QCoreApplication

    from updater.page_actions import ShareLanSetting

    app = QCoreApplication.instance() or QCoreApplication([])
    done = threading.Event()
    seen: dict[str, object] = {}

    class Feature:
        def is_share_updates_lan(self):
            seen["read_thread"] = threading.current_thread().name
            return False

        def set_share_updates_lan(self, enabled):
            seen["written"] = (enabled, threading.current_thread().name)
            done.set()

        # Чужой выключатель страницы не должен задеваться.
        def set_auto_update_enabled(self, enabled):
            seen["auto_touched"] = True

    loaded: list[bool] = []
    setting = ShareLanSetting(updater_feature=Feature())
    setting.loaded.connect(loaded.append)
    setting.load()
    setting.save(True)
    assert done.wait(5)
    for _ in range(100):  # сигнал из фонового потока доходит, когда главный обрабатывает события
        app.processEvents()
        if loaded:
            break
        threading.Event().wait(0.05)
    assert loaded == [False]
    assert seen["written"][0] is True
    assert seen["written"][1].startswith("updater-share-lan") and seen["read_thread"].startswith("updater-share-lan")
    assert "auto_touched" not in seen and setting.user_changed
