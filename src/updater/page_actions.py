from __future__ import annotations

"""Мелкие фоновые действия страницы «Серверы».

* ``BoolSetting`` — общий фоновый выключатель; ``AutoCheckSetting`` —
  выключатель автообновления, ``ShareLanSetting`` — раздача обновлений
  соседям по сети: чтение и
  запись в ``settings.sqlite3`` вне главного потока. Если переключатель
  щёлкнули несколько раз подряд, записывается последнее значение.
* ``ChannelOpener`` — открыть Telegram-канал обновлений.
* ``run_update_setting_write`` — записать отметку окна обновления
  («пропустить версию», текст «Что нового» перед установкой, попытка
  автообновления).

Потоки фоновые (daemon): закрытие программы их не ждёт.
"""

import threading

from PyQt6.QtCore import QObject, pyqtSignal

from log.log import log


class BoolSetting(QObject):
    """Выключатель, который читается и пишется в фоне (последний щелчок побеждает)."""

    loaded = pyqtSignal(bool)

    def __init__(self, *, updater_feature, what: str, thread_name: str, parent=None) -> None:
        super().__init__(parent)
        self._updater_feature = updater_feature
        self._what, self._thread_name = what, thread_name
        self._lock = threading.Lock()
        self._desired: bool | None = None
        self._writer_running = False
        self._user_changed = False

    def load(self) -> None:
        def run() -> None:
            try:
                enabled = bool(self._read_value())
            except Exception as exc:
                log(f"Не удалось загрузить настройку {self._what}: {exc}", "WARNING")
                return
            try:
                self.loaded.emit(enabled)
            except RuntimeError:
                pass

        threading.Thread(target=run, name=f"{self._thread_name}-load", daemon=True).start()

    def _read_value(self) -> bool:
        raise NotImplementedError

    def _write_value(self, value: bool) -> None:
        raise NotImplementedError

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
        threading.Thread(target=self._write_latest, name=f"{self._thread_name}-save", daemon=True).start()

    def _write_latest(self) -> None:
        while True:
            with self._lock:
                value = self._desired
                self._desired = None
                if value is None:
                    self._writer_running = False
                    return
            try:
                self._write_value(value)
            except Exception as exc:
                log(f"Не удалось сохранить настройку {self._what}: {exc}", "WARNING")


class AutoCheckSetting(BoolSetting):
    def __init__(self, *, updater_feature, parent=None) -> None:
        super().__init__(
            updater_feature=updater_feature,
            what="автообновления",
            thread_name="updater-auto-check",
            parent=parent,
        )

    def _read_value(self) -> bool:
        return bool(self._updater_feature.is_auto_update_enabled())

    def _write_value(self, value: bool) -> None:
        self._updater_feature.set_auto_update_enabled(value)


class ShareLanSetting(BoolSetting):
    """«Делиться обновлениями с компьютерами в моей сети»."""

    def __init__(self, *, updater_feature, parent=None) -> None:
        super().__init__(
            updater_feature=updater_feature,
            what="раздачи обновлений по сети",
            thread_name="updater-share-lan",
            parent=parent,
        )

    def _read_value(self) -> bool:
        return bool(self._updater_feature.is_share_updates_lan())

    def _write_value(self, value: bool) -> None:
        self._updater_feature.set_share_updates_lan(value)


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


__all__ = ["AutoCheckSetting", "BoolSetting", "ChannelOpener", "ShareLanSetting", "run_update_setting_write"]
