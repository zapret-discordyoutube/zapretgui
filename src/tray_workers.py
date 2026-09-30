from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QThread, pyqtSignal

from log.log import log


@dataclass(frozen=True, slots=True)
class TrayPresetSnapshot:
    """Что нужно трею про пресеты: режим, список и активный пресет."""

    launch_method: str = ""
    presets: tuple[tuple[str, str], ...] = ()
    selected_file_name: str = ""
    selected_display_name: str = ""

    def is_selected(self, file_name: str) -> bool:
        selected = self.selected_file_name.casefold()
        return bool(selected) and selected == str(file_name or "").casefold()


def load_tray_preset_snapshot(*, get_launch_method, presets_feature) -> TrayPresetSnapshot:
    from settings.mode import is_preset_launch_method

    method = str(get_launch_method() or "").strip().lower()
    if not is_preset_launch_method(method):
        return TrayPresetSnapshot(launch_method=method)

    presets = tuple(
        (str(manifest.file_name), str(manifest.name or manifest.file_name))
        for manifest in presets_feature.list_preset_manifests(method)
    )
    selected = str(presets_feature.get_selected_source_preset_file_name(method) or "").strip()
    selected_display = ""
    for file_name, display_name in presets:
        if selected and file_name.casefold() == selected.casefold():
            selected_display = display_name
            break
    if selected and not selected_display:
        selected_display = selected.rsplit(".", 1)[0]
    return TrayPresetSnapshot(
        launch_method=method,
        presets=presets,
        selected_file_name=selected,
        selected_display_name=selected_display,
    )


class TrayPresetSnapshotWorker(QThread):
    loaded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, *, load_snapshot, parent=None):
        super().__init__(parent)
        self._load_snapshot = load_snapshot

    def run(self) -> None:
        try:
            snapshot = self._load_snapshot()
        except Exception as exc:
            log(f"Трей: не удалось прочитать список пресетов: {exc}", "DEBUG")
            self.failed.emit(str(exc))
            return
        self.loaded.emit(snapshot)


class TrayDiscordRestartToggleWorker(QThread):
    completed = pyqtSignal(bool, str)
    failed = pyqtSignal(str)

    def __init__(self, *, set_discord_restart_enabled, enabled: bool, parent=None):
        super().__init__(parent)
        self._set_discord_restart_enabled = set_discord_restart_enabled
        self._enabled = bool(enabled)

    def run(self) -> None:
        try:
            ok = bool(self._set_discord_restart_enabled(self._enabled))
        except Exception as exc:
            message = f"Ошибка при переключении автоперезапуска Discord: {exc}"
            log(message, "WARNING")
            self.failed.emit(message)
            return

        if ok:
            state_text = "включён" if self._enabled else "отключён"
            message = f"Автоматический перезапуск Discord {state_text}"
        else:
            message = "Ошибка при сохранении настройки автоперезапуска Discord"
        self.completed.emit(ok, message)


__all__ = [
    "TrayDiscordRestartToggleWorker",
    "TrayPresetSnapshot",
    "TrayPresetSnapshotWorker",
    "load_tray_preset_snapshot",
]
