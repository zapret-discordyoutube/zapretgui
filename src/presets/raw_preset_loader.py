from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from log.log import log


class RawPresetLoadWorker(QThread):
    loaded = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(self, request_id: int, load_preset, file_name: str, parent=None):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._load_preset = load_preset
        self._file_name = str(file_name or "").strip()

    def run(self) -> None:
        try:
            result = self._load_preset(self._file_name)
        except Exception as exc:
            log(f"RawPresetLoadWorker: не удалось прочитать preset: {exc}", "ERROR")
            self.failed.emit(self._request_id, str(exc))
            return
        self.loaded.emit(self._request_id, result)


class RawPresetSaveWorker(QThread):
    saved = pyqtSignal(int, str, object, bool)
    failed = pyqtSignal(int, str)

    def __init__(
        self,
        request_id: int,
        save_text,
        *,
        file_name: str,
        source_text: str,
        publish_content_changed: bool,
        parent=None,
    ):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._save_text = save_text
        self._file_name = str(file_name or "").strip()
        self._source_text = str(source_text or "")
        self._publish_content_changed = bool(publish_content_changed)

    def run(self) -> None:
        try:
            result = self._save_text(
                file_name=self._file_name,
                source_text=self._source_text,
                publish_content_changed=self._publish_content_changed,
            )
        except Exception as exc:
            log(f"RawPresetSaveWorker: не удалось сохранить preset: {exc}", "ERROR")
            self.failed.emit(self._request_id, str(exc))
            return
        self.saved.emit(
            self._request_id,
            self._file_name,
            result,
            self._publish_content_changed,
        )


class RawPresetActionWorker(QThread):
    completed = pyqtSignal(int, str, object, object)
    failed = pyqtSignal(int, str, str, object)

    def __init__(
        self,
        request_id: int,
        open_source_file,
        rename_preset,
        duplicate_preset,
        export_preset,
        reset_to_builtin,
        delete_preset,
        source_path,
        *,
        action: str,
        payload: dict | None = None,
        load_preset=None,
        parent=None,
    ):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._open_source_file = open_source_file
        self._rename_preset = rename_preset
        self._duplicate_preset = duplicate_preset
        self._export_preset = export_preset
        self._reset_to_builtin = reset_to_builtin
        self._delete_preset = delete_preset
        self._source_path = source_path
        self._load_preset = load_preset
        self._action = str(action or "").strip()
        self._payload = dict(payload or {})

    def _load_result_after_action(self, file_name: str):
        """Итоговое содержимое читается здесь же, в фоновом потоке: страница
        применяет его напрямую, без второго цикла load-worker-а."""
        if not callable(self._load_preset):
            return None
        return self._load_preset(file_name)

    def run(self) -> None:
        action = self._action
        payload = self._payload
        try:
            if action == "open":
                result = self._open_source_file(payload.get("path"))
            elif action == "rename":
                updated = self._rename_preset(
                    file_name=str(payload.get("file_name") or ""),
                    new_name=str(payload.get("new_name") or ""),
                )
                result = (updated, self._source_path(updated.file_name), self._load_result_after_action(updated.file_name))
            elif action == "duplicate":
                updated = self._duplicate_preset(
                    file_name=str(payload.get("file_name") or ""),
                    new_name=str(payload.get("new_name") or ""),
                )
                result = (updated, self._source_path(updated.file_name), self._load_result_after_action(updated.file_name))
            elif action == "export":
                target_path = str(payload.get("target_path") or "")
                exported = self._export_preset(
                    file_name=str(payload.get("file_name") or ""),
                    target_path=target_path,
                )
                result = exported
            elif action == "reset":
                updated = self._reset_to_builtin(
                    file_name=str(payload.get("file_name") or ""),
                )
                result = (updated, self._source_path(updated.file_name), self._load_result_after_action(updated.file_name))
            elif action == "delete":
                result = self._delete_preset(
                    file_name=str(payload.get("file_name") or ""),
                )
            else:
                raise ValueError(f"Неизвестное действие preset: {action}")
        except Exception as exc:
            log(f"RawPresetActionWorker: действие {action} не выполнено: {exc}", "ERROR")
            self.failed.emit(self._request_id, action, str(exc), payload)
            return
        self.completed.emit(self._request_id, action, result, payload)


class RawPresetActivateWorker(QThread):
    activated = pyqtSignal(int, bool)
    failed = pyqtSignal(int, str)

    def __init__(self, request_id: int, activate, file_name: str, parent=None):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._activate = activate
        self._file_name = str(file_name or "").strip()

    def run(self) -> None:
        try:
            activated = bool(self._activate(file_name=self._file_name))
        except Exception as exc:
            log(f"RawPresetActivateWorker: не удалось активировать preset: {exc}", "ERROR")
            self.failed.emit(self._request_id, str(exc))
            return
        self.activated.emit(self._request_id, activated)
