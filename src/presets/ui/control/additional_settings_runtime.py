from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from log.log import log
from presets.ui.control.refresh_runtime_state import (
    ModeControlRefreshRuntime,
    create_refresh_runtime,
)

# Public class ModeControlRefreshRuntime is re-exported here; the small
# implementation lives in refresh_runtime_state to keep early page imports light.

class ControlAdditionalSettingsState:
    def __init__(self, *, discord_restart: bool, wssize_enabled: bool, debug_log_enabled: bool):
        self.discord_restart = bool(discord_restart)
        self.wssize_enabled = bool(wssize_enabled)
        self.debug_log_enabled = bool(debug_log_enabled)


class ControlTopSummaryState:
    def __init__(
        self,
        *,
        preset_text: str,
        preset_tooltip: str,
        profile_count: int | None,
        profile_tab_visible: bool = True,
        profile_icons: tuple[tuple[str, str], ...] = (),
    ):
        self.preset_text = str(preset_text or "")
        self.preset_tooltip = str(preset_tooltip or "")
        self.profile_count = profile_count
        self.profile_tab_visible = bool(profile_tab_visible)
        # Значки сервисов из включённых профилей: (имя значка, цвет).
        self.profile_icons = tuple(profile_icons or ())


def create_additional_settings_worker(request_id: int, create_load_worker, *, launch_method: str, parent=None):
    return create_load_worker(
        request_id,
        parent,
        launch_method=launch_method,
    )


class ControlTopSummaryWorker(QThread):
    loaded = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(
        self,
        request_id: int,
        summary_loader,
        parent=None,
    ):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._summary_loader = summary_loader

    def run(self) -> None:
        try:
            state = self._summary_loader()
        except Exception as exc:
            log(f"ControlTopSummaryWorker: не удалось загрузить сводку: {exc}", "WARNING")
            self.failed.emit(self._request_id, str(exc))
            return
        self.loaded.emit(self._request_id, state)


def create_top_summary_worker(
    request_id: int,
    get_selected_source_preset_display,
    get_enabled_profile_count_snapshot,
    read_selected_preset_source=None,
    *,
    launch_method: str,
    get_enabled_profile_count_fallback=None,
    get_enabled_profile_icons_snapshot=None,
    parent=None,
):
    clean_launch_method = str(launch_method or "").strip()

    def _load_top_summary_state() -> ControlTopSummaryState:
        preset_text = ""
        preset_tooltip = ""
        try:
            preset_display = get_selected_source_preset_display(
                clean_launch_method,
            )
            if preset_display:
                preset_text = str(preset_display[0] or "")
                preset_tooltip = str(preset_display[1] or "")
        except Exception as exc:
            log(f"ControlTopSummaryWorker: не удалось прочитать выбранный preset: {exc}", "DEBUG")

        profile_count = None
        try:
            count = get_enabled_profile_count_snapshot(
                clean_launch_method,
            )
            profile_count = int(count) if count is not None else None
        except Exception as exc:
            log(f"ControlTopSummaryWorker: не удалось прочитать количество profile: {exc}", "DEBUG")
        if profile_count is None and callable(get_enabled_profile_count_fallback):
            try:
                count = get_enabled_profile_count_fallback(clean_launch_method)
                profile_count = int(count) if count is not None else None
            except Exception as exc:
                log(f"ControlTopSummaryWorker: не удалось пересчитать количество profile: {exc}", "DEBUG")

        profile_icons: tuple[tuple[str, str], ...] = ()
        if callable(get_enabled_profile_icons_snapshot):
            try:
                profile_icons = tuple(get_enabled_profile_icons_snapshot(clean_launch_method) or ())
            except Exception as exc:
                log(f"ControlTopSummaryWorker: не удалось прочитать значки profile: {exc}", "DEBUG")

        profile_tab_visible = True
        if callable(read_selected_preset_source):
            try:
                from profile.winws2_preset_source import is_winws2_circular_preset_source
                from settings.mode import is_zapret2_launch_method

                if is_zapret2_launch_method(clean_launch_method):
                    source_text, _manifest = read_selected_preset_source(clean_launch_method)
                    profile_tab_visible = not is_winws2_circular_preset_source(str(source_text or ""))
            except Exception as exc:
                log(f"ControlTopSummaryWorker: не удалось проверить circular preset: {exc}", "DEBUG")

        return ControlTopSummaryState(
            preset_text=preset_text,
            preset_tooltip=preset_tooltip,
            profile_count=profile_count,
            profile_tab_visible=profile_tab_visible,
            profile_icons=profile_icons,
        )

    return ControlTopSummaryWorker(
        request_id,
        _load_top_summary_state,
        parent=parent,
    )


class AdditionalSettingsSaveWorker(QThread):
    saved = pyqtSignal(int, str, bool)
    failed = pyqtSignal(int, str, str)

    def __init__(
        self,
        request_id: int,
        save_setting,
        *,
        setting: str,
        enabled: bool,
        parent=None,
    ):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._save_setting = save_setting
        self._setting = str(setting or "").strip()
        self._enabled = bool(enabled)

    def run(self) -> None:
        try:
            self._save_setting(self._setting, self._enabled)
        except Exception as exc:
            log(f"AdditionalSettingsSaveWorker: не удалось сохранить настройку {self._setting}: {exc}", "ERROR")
            self.failed.emit(self._request_id, self._setting, str(exc))
            return
        self.saved.emit(self._request_id, self._setting, self._enabled)


def create_additional_settings_save_worker(
    request_id: int,
    set_discord_restart_setting,
    set_wssize_enabled,
    set_debug_log_enabled,
    *,
    launch_method: str,
    setting: str,
    enabled: bool,
    parent=None,
):
    clean_launch_method = str(launch_method or "").strip()

    def _save_setting(setting: str, enabled: bool) -> None:
        if setting == "discord_restart":
            set_discord_restart_setting(bool(enabled))
        elif setting == "wssize":
            set_wssize_enabled(
                bool(enabled),
                launch_method=clean_launch_method,
            )
        elif setting == "debug_log":
            set_debug_log_enabled(
                bool(enabled),
                launch_method=clean_launch_method,
            )
        else:
            raise ValueError(f"Неизвестная дополнительная настройка: {setting}")

    return AdditionalSettingsSaveWorker(
        request_id,
        _save_setting,
        setting=setting,
        enabled=enabled,
        parent=parent,
    )


def build_additional_settings_state(state: dict | None) -> ControlAdditionalSettingsState:
    state = state if isinstance(state, dict) else {}
    return ControlAdditionalSettingsState(
        discord_restart=bool(state.get("discord_restart", True)),
        wssize_enabled=bool(state.get("wssize_enabled", False)),
        debug_log_enabled=bool(state.get("debug_log_enabled", False)),
    )
