from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from PyQt6.QtWidgets import QApplication

from presets.ui_store import PresetUiStore
from settings.mode import ENGINE_WINWS2


class PresetUiStoreGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_content_change_signal_emits_without_filesystem_deduplication(self) -> None:
        with TemporaryDirectory() as temp_dir:
            preset_path = Path(temp_dir) / "Default v5.txt"
            preset_path.write_text("--new\n--filter-tcp=443\n", encoding="utf-8")

            class _PresetFileStore:
                def get_source_path(self, _engine, _file_name):
                    return preset_path

                def list_manifests(self, _engine):
                    return []

            store = PresetUiStore(
                ENGINE_WINWS2,
                _PresetFileStore(),
                selection_service=object(),
            )
            emitted: list[str] = []
            store.preset_content_changed.connect(lambda file_name: emitted.append(file_name))

            store.notify_preset_content_changed("Default v5.txt")
            store.notify_preset_content_changed("Default v5.txt")
            preset_path.write_text("--new\n--filter-tcp=80\n", encoding="utf-8")
            store.notify_preset_content_changed("Default v5.txt")

        self.assertEqual(emitted, ["Default v5.txt", "Default v5.txt", "Default v5.txt"])

    def test_duplicate_preset_switch_signal_is_not_emitted_for_same_file_name(self) -> None:
        store = PresetUiStore(
            ENGINE_WINWS2,
            preset_file_store=object(),
            selection_service=object(),
        )
        emitted: list[str] = []
        store.preset_switched.connect(lambda file_name: emitted.append(file_name))

        store.notify_preset_switched("Default v5.txt")
        store.notify_preset_switched("default V5.TXT")
        store.notify_preset_switched("Other.txt")

        self.assertEqual(emitted, ["Default v5.txt", "Other.txt"])

    def test_identity_change_signal_emits_without_filesystem_deduplication(self) -> None:
        with TemporaryDirectory() as temp_dir:
            preset_path = Path(temp_dir) / "Default v5.txt"
            preset_path.write_text("# Preset: Default v5\n--new\n", encoding="utf-8")

            class _PresetFileStore:
                def get_source_path(self, _engine, _file_name):
                    return preset_path

                def list_manifests(self, _engine):
                    return []

            store = PresetUiStore(
                ENGINE_WINWS2,
                _PresetFileStore(),
                selection_service=object(),
            )
            emitted: list[str] = []
            store.preset_identity_changed.connect(lambda file_name: emitted.append(file_name))

            store.notify_preset_identity_changed("Default v5.txt")
            store.notify_preset_identity_changed("default V5.TXT")
            preset_path.write_text("# Preset: Renamed\n--new\n", encoding="utf-8")
            store.notify_preset_identity_changed("Default v5.txt")

        self.assertEqual(emitted, ["Default v5.txt", "default V5.TXT", "Default v5.txt"])

    def test_content_and_identity_notifications_do_not_stat_source_file(self) -> None:
        class _PresetFileStore:
            source_path_calls = 0

            def get_source_path(self, _engine, _file_name):
                self.source_path_calls += 1
                raise AssertionError("preset notify signal must not touch source file path")

            def list_manifests(self, _engine):
                return []

        preset_file_store = _PresetFileStore()
        store = PresetUiStore(
            ENGINE_WINWS2,
            preset_file_store,
            selection_service=object(),
        )
        content_emitted: list[str] = []
        identity_emitted: list[str] = []
        store.preset_content_changed.connect(lambda file_name: content_emitted.append(file_name))
        store.preset_identity_changed.connect(lambda file_name: identity_emitted.append(file_name))

        store.notify_preset_content_changed("Default v5.txt")
        store.notify_preset_identity_changed("Default v5.txt")

        self.assertEqual(content_emitted, ["Default v5.txt"])
        self.assertEqual(identity_emitted, ["Default v5.txt"])
        self.assertEqual(preset_file_store.source_path_calls, 0)

    def test_notify_presets_changed_only_emits(self) -> None:
        # Хранилище сигналов больше не держит копию списка и выбора: смена
        # списка — только сигнал, без чтения пресетов в GUI-пути.
        class _PresetFileStore:
            def list_manifests(self, _engine):
                raise AssertionError("notify must not list preset manifests")

        store = PresetUiStore(ENGINE_WINWS2, _PresetFileStore(), selection_service=SimpleNamespace())
        emitted: list[bool] = []
        store.presets_changed.connect(lambda: emitted.append(True))

        store.notify_presets_changed()

        self.assertEqual(emitted, [True])

    def test_selection_fallback_is_reported_without_switch_signal(self) -> None:
        from presets.selection_service import SELECTION_REASON_FALLBACK, SELECTION_REASON_RESTORED

        store = PresetUiStore(ENGINE_WINWS2, SimpleNamespace(), selection_service=SimpleNamespace())
        switched: list[str] = []
        fallbacks: list[tuple[str, str]] = []
        store.preset_switched.connect(switched.append)
        store.preset_selection_fallback.connect(lambda missing, used: fallbacks.append((missing, used)))

        store.notify_selection_changed("Default.txt", SELECTION_REASON_FALLBACK, "gone.txt")
        store.notify_selection_changed("gone.txt", SELECTION_REASON_RESTORED)

        # Подмена — только сообщение (работающий DPI сам не переключаем),
        # возврат файла — обычное переключение обратно.
        self.assertEqual(fallbacks, [("gone.txt", "Default.txt")])
        self.assertEqual(switched, ["gone.txt"])

    def test_restored_file_is_reported_even_if_it_was_last_sent_name(self) -> None:
        from presets.selection_service import SELECTION_REASON_FALLBACK, SELECTION_REASON_RESTORED

        store = PresetUiStore(ENGINE_WINWS2, SimpleNamespace(), selection_service=SimpleNamespace())
        switched: list[str] = []
        store.preset_switched.connect(switched.append)
        store.notify_preset_switched("A.txt")
        store.notify_selection_changed("B.txt", SELECTION_REASON_FALLBACK, "A.txt")

        # A — последнее отправленное имя, но страницы показывают запасной B:
        # возврат A нельзя отбрасывать как дубль.
        store.notify_selection_changed("A.txt", SELECTION_REASON_RESTORED)

        self.assertEqual(switched, ["A.txt", "A.txt"])


if __name__ == "__main__":
    unittest.main()
