"""Присутствие окна: что происходит при уходе в фон и при возврате (ui.window_presence).

Жалоба пользователя 2026-10-17: окно долго в фоне, потом открываешь из панели
задач — «ничего не работает». В журналах win10 — блокировки интерфейса на
20–134 с: Windows отобрала память программы, и окно тянуло её с диска по одной
странице. Здесь проверяется порядок действий, а не скорость диска.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from ui import window_presence  # noqa: E402
from ui.window_presence import WindowPresence  # noqa: E402
from platforms.base import PrefetchResult, WorkingSetHold, WorkingSetSnapshot  # noqa: E402


_APP = QApplication.instance() or QApplication([])

SNAPSHOT = WorkingSetSnapshot(ranges=((0x1000, 0x2000), (0x8000, 0x1000)), total_bytes=0x3000, taken_ms=1.0)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _make(window, **overrides):
    calls: list[str] = []
    clock = overrides.pop("clock", _Clock())
    priority = overrides.pop("apply_priority", lambda shown: calls.append(f"priority:{shown}"))
    snapshot = overrides.pop("take_snapshot", lambda: calls.append("snapshot") or SNAPSHOT)
    prefetch = overrides.pop(
        "prefetch", lambda snap: calls.append("prefetch") or PrefetchResult(len(snap.ranges), snap.total_bytes, 1.0, True)
    )
    presence = WindowPresence(
        window,
        settle_ms=60_000,
        report_ms=60_000,
        min_away_for_prefetch_s=20.0,
        apply_priority=priority,
        take_snapshot=snapshot,
        prefetch=prefetch,
        start_worker=lambda fn: fn(),
        clock=clock,
        **overrides,
    )
    return presence, calls, clock


class WindowPresenceTests(unittest.TestCase):
    def _window(self, *, shown: bool = True) -> QWidget:
        window = QWidget()
        self.addCleanup(window.deleteLater)
        if shown:
            window.show()
            _APP.processEvents()
        return window

    def test_hidden_window_starts_away_with_background_priority(self) -> None:
        # Запуск в трей: окна нет на экране.
        presence, calls, _clock = _make(self._window(shown=False))

        self.assertTrue(presence.away)
        self.assertEqual(calls, ["priority:False"])

    def test_priority_follows_show_minimize_and_hide(self) -> None:
        window = self._window(shown=False)
        presence, calls, _clock = _make(window)
        calls.clear()

        window.show()
        _APP.processEvents()
        self.assertEqual(calls[-1], "priority:True")

        window.setWindowState(Qt.WindowState.WindowMinimized)
        _APP.processEvents()
        self.assertEqual(calls[-1], "priority:False")

        window.setWindowState(Qt.WindowState.WindowNoState)
        _APP.processEvents()
        self.assertEqual(calls[-1], "priority:True")

        window.hide()
        _APP.processEvents()
        self.assertEqual(calls[-1], "priority:False")
        self.assertTrue(presence.away)

    def test_pages_are_remembered_once_after_the_window_stayed_away(self) -> None:
        window = self._window()
        presence, calls, _clock = _make(window)
        window.hide()
        calls.clear()

        self.assertTrue(presence._settle_timer.isSingleShot())
        presence._settle_timer.stop()  # так таймер выглядит после срабатывания
        presence._on_settle_timeout()

        # Один снимок нужных страниц, и повторять его, пока окно скрыто, незачем.
        self.assertEqual(calls, ["snapshot"])
        self.assertFalse(presence._settle_timer.isActive())

    def test_showing_the_window_cancels_a_pending_snapshot(self) -> None:
        window = self._window()
        presence, calls, _clock = _make(window)
        window.hide()
        self.assertTrue(presence._settle_timer.isActive())

        window.show()
        _APP.processEvents()

        self.assertFalse(presence._settle_timer.isActive())
        calls.clear()
        presence._on_settle_timeout()
        self.assertEqual(calls, [])

    def test_return_after_a_long_absence_prefetches_pages(self) -> None:
        window = self._window()
        presence, calls, clock = _make(window)
        window.hide()
        presence._on_settle_timeout()
        calls.clear()
        clock.now += 600

        presence.return_intent()

        self.assertEqual(calls, ["priority:True", "prefetch"])
        self.assertFalse(presence.away)

    def test_return_intent_comes_before_qt_shows_the_window(self) -> None:
        # Окно из панели задач: команда Windows приходит раньше события показа Qt.
        window = self._window()
        presence, calls, clock = _make(window)
        window.setWindowState(Qt.WindowState.WindowMinimized)
        _APP.processEvents()
        presence._on_settle_timeout()
        clock.now += 600
        calls.clear()

        presence.return_intent()
        self.assertEqual(calls, ["priority:True", "prefetch"])

        # Позднее событие Qt уже ничего не повторяет.
        window.setWindowState(Qt.WindowState.WindowNoState)
        _APP.processEvents()
        self.assertEqual(calls, ["priority:True", "prefetch"])

    def test_short_absence_does_not_prefetch(self) -> None:
        window = self._window()
        presence, calls, clock = _make(window)
        window.hide()
        presence._on_settle_timeout()
        calls.clear()
        clock.now += 3

        presence.return_intent()

        self.assertEqual(calls, ["priority:True"])

    def test_return_before_the_snapshot_is_ready_does_not_fail(self) -> None:
        window = self._window()
        presence, calls, clock = _make(window)
        window.hide()
        clock.now += 600
        calls.clear()

        presence.return_intent()

        self.assertEqual(calls, ["priority:True"])

    def test_return_intent_while_shown_does_nothing(self) -> None:
        presence, calls, _clock = _make(self._window())
        calls.clear()

        presence.return_intent()

        self.assertEqual(calls, [])

    def test_return_report_goes_to_the_log(self) -> None:
        window = self._window()
        presence, _calls, clock = _make(window)
        window.hide()
        presence._on_settle_timeout()
        clock.now += 600
        presence.return_intent()

        with patch.object(window_presence, "log") as log:
            presence._report_return()

        message = log.call_args.args[0]
        self.assertIn("Окно вернулось из фона (600 с)", message)
        self.assertIn("подтянуто", message)


class WorkingSetHoldTests(unittest.TestCase):
    """Пока окно скрыто, рабочий набор удерживается, чтобы Windows не отобрала страницы."""

    def _window(self, *, shown: bool = True) -> QWidget:
        window = QWidget()
        self.addCleanup(window.deleteLater)
        if shown:
            window.show()
            _APP.processEvents()
        return window

    def _make(self, window):
        events: list[str] = []
        presence, _calls, clock = _make(
            window,
            hold=lambda: events.append("hold") or WorkingSetHold(bytes=80 << 20, ok=True, error=0),
            release_hold=lambda: events.append("release"),
        )
        events.clear()
        return presence, events, clock

    def test_going_away_holds_at_once_and_again_after_the_settle_time(self) -> None:
        window = self._window()
        presence, events, _clock = self._make(window)

        window.hide()
        self.assertEqual(events, ["hold"])

        presence._on_settle_timeout()
        self.assertEqual(events, ["hold", "hold"])

    def test_return_releases_the_hold(self) -> None:
        window = self._window()
        presence, events, clock = self._make(window)
        window.hide()
        clock.now += 600
        events.clear()

        presence.return_intent()

        self.assertEqual(events, ["release"])

    def test_hold_never_outlives_the_return(self) -> None:
        # Поток «удержать» опоздал и дошёл до замка уже после возврата окна.
        window = self._window()
        started: list = []
        presence, _calls, _clock = _make(
            window,
            hold=lambda: WorkingSetHold(bytes=1, ok=True, error=0),
            release_hold=lambda: started.append("release"),
        )
        presence._start_worker = started.append  # рабочие потоки не запускаем, копим
        window.hide()
        late_hold = [job for job in started if callable(job)][-1]
        started.clear()

        presence.return_intent()
        late_hold()

        self.assertIsNone(presence._held)

    def test_failed_hold_does_not_break_return_and_is_reported(self) -> None:
        window = self._window()
        presence, _calls, clock = _make(
            window,
            hold=lambda: WorkingSetHold(bytes=80 << 20, ok=False, error=1314),
            release_hold=lambda: None,
        )
        window.hide()
        clock.now += 600
        presence.return_intent()

        with patch.object(window_presence, "log") as log:
            presence._report_return()

        self.assertIn("удержать не вышло (код 1314)", log.call_args.args[0])

    def test_error_in_hold_is_swallowed(self) -> None:
        def broken() -> None:
            raise OSError("нет права")

        window = self._window()
        presence, _calls, clock = _make(window, hold=broken, release_hold=lambda: None)
        window.hide()
        clock.now += 600
        presence.return_intent()  # не падает

        self.assertFalse(presence.away)

    def test_report_names_the_held_size(self) -> None:
        window = self._window()
        presence, events, clock = self._make(window)
        window.hide()
        clock.now += 600
        presence.return_intent()

        with patch.object(window_presence, "log") as log:
            presence._report_return()

        self.assertIn("удержано 80 МБ", log.call_args.args[0])


class EarlyAwayIntentTests(unittest.TestCase):
    """Windows сжимает набор свёрнутого окна сразу: защита ставится до этого, а не после."""

    def _window(self) -> QWidget:
        window = QWidget()
        self.addCleanup(window.deleteLater)
        window.show()
        _APP.processEvents()
        return window

    def _make(self, window, *, snapshots=None):
        events: list[str] = []
        queue = list(snapshots or [SNAPSHOT])
        presence, calls, clock = _make(
            window,
            hold=lambda: events.append("hold") or WorkingSetHold(bytes=80 << 20, ok=True, error=0),
            release_hold=lambda: events.append("release"),
            take_snapshot=lambda: events.append("snapshot") or (queue.pop(0) if len(queue) > 1 else queue[0]),
        )
        events.clear()
        calls.clear()
        return presence, events, calls, clock

    def test_intent_holds_and_remembers_pages_before_the_window_leaves(self) -> None:
        window = self._window()
        presence, events, calls, _clock = self._make(window)

        presence.away_intent()

        self.assertEqual(events, ["hold", "snapshot"])
        # Состояние «в фоне» сам сигнал не ставит: сворачивание могло не состояться.
        self.assertFalse(presence.away)
        self.assertEqual(calls, [])

    def test_the_late_qt_event_does_not_repeat_the_snapshot_but_keeps_the_hold(self) -> None:
        window = self._window()
        presence, events, _calls, _clock = self._make(window)
        presence.away_intent()
        events.clear()

        window.hide()
        _APP.processEvents()

        self.assertTrue(presence.away)
        self.assertEqual(events, ["hold"])

    def test_window_that_stayed_on_screen_gets_its_protection_removed(self) -> None:
        window = self._window()
        presence, events, _calls, _clock = self._make(window)
        presence.away_intent()
        events.clear()

        presence._on_intent_timeout()

        self.assertEqual(events, ["release"])
        self.assertFalse(presence.away)

    def test_window_that_left_is_not_released_by_the_intent_timer(self) -> None:
        window = self._window()
        presence, events, _calls, _clock = self._make(window)
        presence.away_intent()
        window.hide()
        _APP.processEvents()
        events.clear()

        presence._on_intent_timeout()

        self.assertEqual(events, [])

    def test_intent_while_already_away_does_nothing(self) -> None:
        window = self._window()
        presence, events, _calls, _clock = self._make(window)
        window.hide()
        _APP.processEvents()
        events.clear()

        presence.away_intent()

        self.assertEqual(events, [])

    def test_poorer_snapshot_taken_after_the_trim_does_not_replace_the_full_one(self) -> None:
        full = WorkingSetSnapshot(ranges=((0x1000, 0x9000),), total_bytes=0x9000, taken_ms=1.0)
        trimmed = WorkingSetSnapshot(ranges=((0x1000, 0x1000),), total_bytes=0x1000, taken_ms=1.0)
        window = self._window()
        presence, _events, _calls, _clock = self._make(window, snapshots=[full, trimmed])

        presence.away_intent()  # снимок на виду: полный
        window.hide()
        _APP.processEvents()
        presence._on_settle_timeout()  # через 5 с в фоне: набор уже сжат

        self.assertIs(presence._snapshot, full)

    def test_view_snapshot_replaces_an_older_bigger_one(self) -> None:
        old = WorkingSetSnapshot(ranges=((0x1000, 0x9000),), total_bytes=0x9000, taken_ms=1.0)
        new = WorkingSetSnapshot(ranges=((0x1000, 0x2000),), total_bytes=0x2000, taken_ms=1.0)
        window = self._window()
        presence, _events, _calls, _clock = self._make(window, snapshots=[old, new])

        presence._store_snapshot(from_view=True)
        presence._store_snapshot(from_view=True)

        self.assertIs(presence._snapshot, new)

    def test_hold_size_follows_the_hot_size_not_the_trimmed_one(self) -> None:
        window = self._window()
        presence = WindowPresence(window, start_worker=lambda fn: fn())
        presence._hot_bytes = 150 << 20

        with (
            patch.object(window_presence, "current_working_set_bytes", return_value=10 << 20),
            patch.object(window_presence, "available_physical_bytes", return_value=4096 << 20),
            patch.object(window_presence, "hold_working_set") as hold,
        ):
            presence._hold_hot_set()

        # 10 МБ после сжатия не достойны защиты, а 150 МБ на виду — достойны.
        self.assertGreaterEqual(hold.call_args.args[0], 150 << 20)

    def test_hot_size_is_remembered_after_the_window_settles_on_screen(self) -> None:
        window = self._window()
        presence = WindowPresence(window, start_worker=lambda fn: fn(), take_snapshot=lambda: SNAPSHOT)

        with patch.object(window_presence, "current_working_set_bytes", return_value=90 << 20):
            presence._on_sample_timeout()
        with patch.object(window_presence, "current_working_set_bytes", return_value=30 << 20):
            presence._on_sample_timeout()

        self.assertEqual(presence._hot_bytes, 90 << 20)  # самое большое за время показа
        self.assertIs(presence._snapshot, SNAPSHOT)

    def test_page_faults_come_from_winapi_not_from_a_late_psutil_import(self) -> None:
        source = Path(window_presence.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import psutil", source)


class AwayHoldRefreshTests(unittest.TestCase):
    """Запуск в трей: хватка берётся рано, потом память растёт — хватка растёт вместе с ней."""

    def _window(self) -> QWidget:
        window = QWidget()
        self.addCleanup(window.deleteLater)
        return window

    def _make(self, window):
        sizes: list[int] = []

        def hold():
            size = (len(sizes) + 1) * 100 << 20
            sizes.append(size)
            return WorkingSetHold(bytes=size, ok=True, error=0)

        window.show()
        _APP.processEvents()
        presence, calls, _clock = _make(window, hold=hold, release_hold=lambda: None)
        sizes.clear()
        window.hide()
        _APP.processEvents()
        return presence, sizes

    def test_hold_is_raised_when_the_program_grew_while_hidden(self) -> None:
        window = self._window()
        presence, sizes = self._make(window)
        self.assertEqual(sizes, [100 << 20])  # хватка, взятая при уходе

        with patch.object(window_presence, "current_working_set_bytes", return_value=150 << 20):
            presence._refresh_hold_if_grown()

        self.assertEqual(sizes, [100 << 20, 200 << 20])

    def test_hold_is_not_touched_when_nothing_grew(self) -> None:
        window = self._window()
        presence, sizes = self._make(window)

        with patch.object(window_presence, "current_working_set_bytes", return_value=105 << 20):
            presence._refresh_hold_if_grown()

        self.assertEqual(sizes, [100 << 20])

    def test_refresh_runs_only_while_the_window_is_away(self) -> None:
        window = self._window()
        presence, sizes = self._make(window)
        self.assertTrue(presence._away_refresh_timer.isActive())

        window.show()
        _APP.processEvents()
        self.assertFalse(presence._away_refresh_timer.isActive())

        sizes.clear()
        with patch.object(window_presence, "current_working_set_bytes", return_value=900 << 20):
            presence._on_away_refresh()  # окно уже на экране: ничего не делает
        self.assertEqual(sizes, [])


class NoMemoryChurnTests(unittest.TestCase):
    def test_going_away_never_walks_the_python_heap_or_trims_the_working_set(self) -> None:
        # Сборка мусора обходит всю кучу и сама тянет с диска страницы, которые
        # Windows уже отобрала (на win10: 66 → 119 МБ ради ~100 объектов);
        # принудительная выгрузка и сброс кэша картинок замедляли открытие окна.
        source = Path(window_presence.__file__).read_text(encoding="utf-8")
        for forbidden in ("gc.collect", "_heapmin", "SetProcessWorkingSetSize", "EmptyWorkingSet", "QPixmapCache"):
            self.assertNotIn(forbidden, source)


class NativeRestoreCommandTests(unittest.TestCase):
    def test_restore_command_is_recognised_and_other_commands_are_not(self) -> None:
        from platforms.windows import native_commands as commands

        def message(command: int):
            fake = Mock()
            fake.message = commands.WM_SYSCOMMAND
            fake.wParam = command
            return fake

        with patch.object(commands.sys, "platform", "win32"), patch.object(commands, "_read_msg", side_effect=message):
            self.assertTrue(commands.is_native_restore_command(commands.SC_RESTORE))
            # Младшие 4 бита команды Windows используют сама.
            self.assertTrue(commands.is_native_restore_command(commands.SC_RESTORE | 0x2))
            self.assertFalse(commands.is_native_restore_command(commands.SC_MINIMIZE))

    def test_lifecycle_tells_presence_before_qt_handles_the_command(self) -> None:
        import inspect

        from platforms.windows.desktop import WindowsDesktop

        source = inspect.getsource(WindowsDesktop.handle_native_event)
        self.assertLess(source.index("is_native_restore_command(message)"), source.index("handle_native_minimize_command(\n"))
        self.assertIn("_note_return_intent", source)

    def test_minimize_command_is_recognised_before_qt_handles_it(self) -> None:
        import inspect

        from platforms.windows import native_commands as commands
        from platforms.windows.desktop import WindowsDesktop

        def message(command: int):
            fake = Mock()
            fake.message = commands.WM_SYSCOMMAND
            fake.wParam = command
            return fake

        with patch.object(commands.sys, "platform", "win32"), patch.object(commands, "_read_msg", side_effect=message):
            self.assertTrue(commands.is_native_minimize_command(commands.SC_MINIMIZE))
            self.assertFalse(commands.is_native_minimize_command(commands.SC_RESTORE))

        source = inspect.getsource(WindowsDesktop.handle_native_event)
        self.assertLess(source.index("is_native_minimize_command(message)"), source.index("handle_native_minimize_command(\n"))
        self.assertIn("_note_away_intent", source)

    def test_tray_and_minimize_calls_tell_presence_first(self) -> None:
        import inspect

        from main import window_lifecycle

        for name in ("close_to_tray", "showMinimized"):
            source = inspect.getsource(getattr(window_lifecycle.WindowLifecycleMixin, name))
            self.assertIn("_note_away_intent", source, name)

    def test_tray_show_tells_presence_first(self) -> None:
        import inspect

        from ui import window_adapter

        source = inspect.getsource(window_adapter.show_window)
        self.assertLess(source.index("return_intent"), source.index("show_from_hidden"))


if __name__ == "__main__":
    unittest.main()
