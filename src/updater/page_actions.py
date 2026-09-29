from __future__ import annotations

"""Мелкие фоновые действия страницы «Серверы».

* ``AutoCheckSetting`` — переключатель «проверять при запуске»: чтение и
  запись в ``settings.sqlite3`` вне главного потока. Если переключатель
  щёлкнули несколько раз подряд, записывается последнее значение.
* ``ChannelOpener`` — открыть Telegram-канал обновлений.
* ``run_update_setting_write`` — записать отметку окна обновления
  («пропустить версию», текст «Что нового» перед установкой).

Потоки фоновые (daemon): закрытие программы их не ждёт.
"""

import threading

from PyQt6.QtCore import QObject, pyqtSignal

from log.log import log


class AutoCheckSetting(QObject):
    loaded = pyqtSignal(bool)

    def __init__(self, *, updater_feature, parent=None) -> None:
        super().__init__(parent)
        self._updater_feature = updater_feature
        self._lock = threading.Lock()
        self._desired: bool | None = None
        self._writer_running = False
        self._user_changed = False

    def load(self) -> None:
        def run() -> None:
            try:
                enabled = bool(self._updater_feature.is_auto_update_enabled())
            except Exception as exc:
                log(f"Не удалось загрузить автопроверку обновлений: {exc}", "WARNING")
                return
            try:
                self.loaded.emit(enabled)
            except RuntimeError:
                pass

        threading.Thread(target=run, name="updater-auto-check-load", daemon=True).start()

    @property
    def user_changed(self) -> bool:
        """Пользователь уже менял переключатель: загруженное значение устарело."""
        return self._user_changed

    def save(self, enabled: bool) -> None:
        self._user_changed = True
        with self._lock:
            self._desired = bool(enabled)
            if self._writer_running:
                return
            self._writer_running = True
        threading.Thread(target=self._write_latest, name="updater-auto-check-save", daemon=True).start()

    def _write_latest(self) -> None:
        while True:
            with self._lock:
                value = self._desired
                self._desired = None
                if value is None:
                    self._writer_running = False
                    return
            try:
                self._updater_feature.set_auto_update_enabled(value)
            except Exception as exc:
                log(f"Не удалось сохранить автопроверку обновлений: {exc}", "WARNING")


class ChannelOpener(QObject):
    failed = pyqtSignal(str)

    def __init__(self, *, updater_feature, parent=None) -> None:
        super().__init__(parent)
        self._updater_feature = updater_feature

    def open(self, channel: str) -> None:
        def run() -> None:
            try:
                result = self._updater_feature.open_update_channel(str(channel or ""))
                error = "" if bool(getattr(result, "ok", False)) else str(getattr(result, "message", "") or "")
            except Exception as exc:
                error = str(exc)
            if error:
                try:
                    self.failed.emit(error)
                except RuntimeError:
                    pass

        threading.Thread(target=run, name="updater-channel-open", daemon=True).start()


def run_update_setting_write(action, *, name: str, description: str) -> None:
    """Короткая запись в settings.sqlite3 вне главного потока."""

    def run() -> None:
        try:
            action()
        except Exception as exc:
            log(f"Не удалось сохранить {description}: {exc}", "WARNING")

    threading.Thread(target=run, name=name, daemon=True).start()


__all__ = ["AutoCheckSetting", "ChannelOpener", "run_update_setting_write"]
