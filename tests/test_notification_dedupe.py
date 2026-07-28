"""Дедупликация уведомлений между специализированным каналом и логгером.

Одна ошибка запуска попадает в центр уведомлений дважды: сначала от
`launch.dpi_error` (с заголовком и кнопкой автолечения), затем эхом от
глобального логгера, который показывает любую ERROR-строку. Ключи
`dedupe_key` содержат имя источника, поэтому такие пары раньше не
склеивались и пользователь видел два тоста с одним текстом.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt6.QtWidgets import QApplication

from app_notifications import advisory_notification
from ui.window_notification_center import (
    GLOBAL_ERROR_DEDUPE_WINDOW_MS,
    GLOBAL_LOGGER_SOURCE,
    WindowNotificationCenter,
)


_LAUNCH_ERROR_TEXT = (
    "winws2 не запустился. Найдена причина: Объекты WinDivert от предыдущего "
    "запуска ещё используются системой (код ошибки Windows 2150760464 / 0x80320010; "
    "код завершения процесса 10)"
)


def _launch_error_payload(text: str = _LAUNCH_ERROR_TEXT) -> dict:
    return advisory_notification(
        level="error",
        title="Ошибка запуска Zapret",
        content=text,
        source="launch.dpi_error",
        presentation="infobar",
        queue="immediate",
        duration=-1,
        dedupe_key=f"launch.dpi_error:{' '.join(text.split()).lower()}",
    )


def _global_logger_payload(text: str) -> dict:
    return advisory_notification(
        level="error",
        title="Ошибка",
        content=text,
        source=GLOBAL_LOGGER_SOURCE,
        presentation="infobar",
        queue="immediate",
        duration=10000,
        dedupe_key=f"global_logger:{' '.join(text.split()).lower()}",
        dedupe_window_ms=GLOBAL_ERROR_DEDUPE_WINDOW_MS,
    )


class NotificationDedupeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _make_center(self) -> WindowNotificationCenter:
        return WindowNotificationCenter(
            None,
            startup_state=SimpleNamespace(post_init_ready=True, background_init_started=True),
            runtime_actions=Mock(),
            create_open_url_worker=Mock(),
            create_notification_action_worker=Mock(),
            show_tray_notification=Mock(),
            show_page=Mock(),
            is_window_visible=lambda: True,
            is_window_minimized=lambda: False,
        )

    def test_logger_echo_of_shown_error_is_suppressed(self) -> None:
        center = self._make_center()

        with patch.object(center, "_present_notification") as present:
            center.notify(_launch_error_payload())
            center.notify(_global_logger_payload(f"[ERROR] {_LAUNCH_ERROR_TEXT}"))

        self.assertEqual(present.call_count, 1)
        self.assertEqual(present.call_args.args[0].get("source"), "launch.dpi_error")

    def test_autofix_marker_does_not_break_text_matching(self) -> None:
        center = self._make_center()

        with patch.object(center, "_present_notification") as present:
            center.notify(_launch_error_payload())
            center.notify(
                _global_logger_payload(f"[ERROR] [AUTOFIX:cleanup_driver]{_LAUNCH_ERROR_TEXT}")
            )

        self.assertEqual(present.call_count, 1)

    def test_specialized_channel_is_not_suppressed_by_logger_echo(self) -> None:
        """Обратное подавление стоило бы уведомления с кнопкой «Исправить»."""
        center = self._make_center()

        with patch.object(center, "_present_notification") as present:
            center.notify(_global_logger_payload(f"[ERROR] {_LAUNCH_ERROR_TEXT}"))
            center.notify(_launch_error_payload())

        self.assertEqual(present.call_count, 2)

    def test_different_errors_from_logger_are_both_shown(self) -> None:
        center = self._make_center()

        with patch.object(center, "_present_notification") as present:
            center.notify(_launch_error_payload())
            center.notify(_global_logger_payload("[ERROR] Не удалось сохранить preset"))

        self.assertEqual(present.call_count, 2)

    def test_expired_window_lets_the_error_appear_again(self) -> None:
        center = self._make_center()

        with patch.object(center, "_present_notification") as present:
            center.notify(_launch_error_payload())
            # Сдвигаем историю за пределы окна дедупликации.
            shift = (GLOBAL_ERROR_DEDUPE_WINDOW_MS / 1000.0) + 1.0
            for registry in (center._recent_signatures, center._recent_content_signatures):
                for key in list(registry):
                    registry[key] -= shift
            center.notify(_global_logger_payload(f"[ERROR] {_LAUNCH_ERROR_TEXT}"))

        self.assertEqual(present.call_count, 2)


class ServicePrefixStrippingTests(unittest.TestCase):
    def test_strips_level_and_autofix_prefixes(self) -> None:
        strip = WindowNotificationCenter._strip_log_level_prefix

        self.assertEqual(strip("[ERROR] текст"), "текст")
        self.assertEqual(strip("[ERROR] [AUTOFIX:cleanup_driver]текст"), "текст")
        self.assertEqual(strip("текст"), "текст")

    def test_keeps_meaningful_bracketed_text(self) -> None:
        strip = WindowNotificationCenter._strip_log_level_prefix
        long_bracket = "[очень длинный текст в скобках, который не является служебным префиксом] хвост"

        self.assertEqual(strip(long_bracket), long_bracket)


if __name__ == "__main__":
    unittest.main()
