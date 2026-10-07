"""Прощальный экран: анимация и лёгкий текст между «закрыть» и исчезновением окна."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from ui import exit_screen  # noqa: E402
from ui.exit_screen import ExitScreenSession, begin_exit_screen  # noqa: E402
from ui import exit_screen_texts  # noqa: E402
from ui.exit_screen_texts import (  # noqa: E402
    EXIT_KINDS,
    KIND_CLOSING_IDLE,
    KIND_CLOSING_KEEP,
    KIND_STOPPED,
    KIND_STOPPING,
    exit_screen_text,
    exit_screen_title,
    plain_exit_phrases,
)


def _phrases_for(kind: str, language: str = "ru") -> tuple[str, ...]:
    """Все строки, которые экран может показать: общий набор или запасные."""
    from ui.fun_phrases import phrases

    return phrases(kind, language) or plain_exit_phrases(kind, language)


_APP = QApplication.instance() or QApplication([])


def _spin(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class _Clock:
    def __init__(self) -> None:
        self.now = 50.0

    def __call__(self) -> float:
        return self.now


class ExitScreenTextTests(unittest.TestCase):
    def test_every_kind_has_title_and_plain_phrases_in_both_languages(self) -> None:
        for language in ("ru", "en"):
            for kind in EXIT_KINDS:
                with self.subTest(language=language, kind=kind):
                    self.assertTrue(exit_screen_title(kind, language).strip())
                    plain = plain_exit_phrases(kind, language)
                    self.assertGreaterEqual(len(plain), 4)
                    self.assertEqual(len(set(plain)), len(plain))
                    for phrase in plain:
                        # Экран виден около секунды: строка читается с одного взгляда.
                        self.assertLessEqual(len(phrase), 48, phrase)

    def test_title_says_what_happens(self) -> None:
        self.assertEqual(exit_screen_title(KIND_STOPPING, "ru"), "Останавливаем обход")
        self.assertEqual(exit_screen_title(KIND_STOPPED, "ru"), "Обход остановлен")
        self.assertEqual(exit_screen_title(KIND_CLOSING_KEEP, "en"), "Closing the window")

    def test_stopped_bypass_is_not_promised_to_keep_working(self) -> None:
        for phrase in plain_exit_phrases(KIND_CLOSING_IDLE, "ru"):
            self.assertNotIn("работает", phrase.lower())
            self.assertNotIn("остаётся", phrase.lower())

    def test_light_line_comes_from_the_shared_phrase_pool_when_it_has_the_block(self) -> None:
        # Владелец шуток один — ui.fun_phrases. Экран не держит своих наборов:
        # добавили блок exit_* в общий набор — на экране пошли фразы оттуда.
        with patch.object(exit_screen_texts, "random_phrase", return_value="шутка из общего набора") as pick:
            title, phrase = exit_screen_text(KIND_STOPPING, "ru")

        self.assertEqual(title, "Останавливаем обход")
        self.assertEqual(phrase, "шутка из общего набора")
        self.assertEqual(pick.call_args.args, (KIND_STOPPING, "ru"))
        self.assertIn(pick.call_args.kwargs["default"], plain_exit_phrases(KIND_STOPPING, "ru"))

    def test_plain_line_is_shown_while_the_shared_pool_has_no_such_block(self) -> None:
        with patch("ui.fun_phrases.phrases", return_value=()):
            _title, phrase = exit_screen_text(KIND_CLOSING_KEEP, "en")

        self.assertIn(phrase, plain_exit_phrases(KIND_CLOSING_KEEP, "en"))


class ExitScreenSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = QWidget()
        self.window.resize(900, 600)
        self.window.show()
        self.addCleanup(self.window.deleteLater)
        self.clock = _Clock()
        self.done: list[str] = []

    def _session(self, *, stop_dpi: bool, bypass_running: bool = True, animated: bool = False) -> ExitScreenSession:
        return ExitScreenSession(
            self.window,
            stop_dpi=stop_dpi,
            bypass_running=bypass_running,
            language="ru",
            animated=animated,
            clock=self.clock,
        )

    def test_screen_covers_the_window_and_follows_its_size(self) -> None:
        session = self._session(stop_dpi=False)
        screen = session.screen()

        self.assertTrue(screen.isVisible())
        self.assertEqual(screen.geometry(), self.window.rect())

        self.window.resize(700, 500)
        _APP.processEvents()
        self.assertEqual(screen.geometry(), self.window.rect())

    def test_closing_window_keeps_the_text_on_screen_long_enough_to_read(self) -> None:
        session = self._session(stop_dpi=False)
        self.assertEqual(session.screen().title(), "Окно закрывается")
        self.assertIn(session.screen().phrase(), _phrases_for(KIND_CLOSING_KEEP))

        session.finish(lambda: self.done.append("quit"))

        # Команда пришла сразу, но экран ещё не успели прочитать.
        self.assertEqual(self.done, [])
        self.assertEqual(session._leave_timer.interval(), exit_screen.MIN_VISIBLE_MS)

    def test_bypass_that_is_not_running_is_not_promised_to_keep_working(self) -> None:
        session = self._session(stop_dpi=False, bypass_running=False)

        self.assertIn(session.screen().phrase(), _phrases_for(KIND_CLOSING_IDLE))

    def test_stopping_bypass_shows_progress_then_result(self) -> None:
        session = self._session(stop_dpi=True)
        screen = session.screen()
        self.assertEqual(screen.title(), "Останавливаем обход")

        # Обход останавливался дольше, чем нужно на чтение: держим только итог.
        self.clock.now += 3.0
        session.finish(lambda: self.done.append("quit"))

        self.assertEqual(screen.title(), "Обход остановлен")
        self.assertIn(screen.phrase(), _phrases_for(KIND_STOPPED))
        self.assertEqual(self.done, [])
        self.assertEqual(session._leave_timer.interval(), exit_screen.STOPPED_HOLD_MS)

    def test_nothing_to_stop_is_shown_as_plain_closing(self) -> None:
        # «Закрыть программу» при выключенном обходе: об остановке говорить незачем.
        session = self._session(stop_dpi=True, bypass_running=False)
        screen = session.screen()
        self.assertEqual(screen.title(), "Окно закрывается")

        session.finish(lambda: self.done.append("quit"))

        self.assertEqual(screen.title(), "Окно закрывается")
        self.assertFalse(session._stop_wait.isActive())

    def test_finish_calls_on_done_exactly_once(self) -> None:
        with (
            patch.object(exit_screen, "MIN_VISIBLE_MS", 0),
            patch.object(exit_screen, "STOPPED_HOLD_MS", 0),
        ):
            session = self._session(stop_dpi=True)
            session.finish(lambda: self.done.append("first"))
            session.finish(lambda: self.done.append("second"))
            _spin(30)

        self.assertEqual(self.done, ["first"])

    def test_window_fades_out_before_on_done_when_animations_are_on(self) -> None:
        with (
            patch.object(exit_screen, "MIN_VISIBLE_MS", 0),
            patch.object(exit_screen, "WINDOW_FADE_MS", 20),
            patch.object(self.window, "setWindowOpacity") as set_opacity,
        ):
            session = self._session(stop_dpi=False, animated=True)
            session.finish(lambda: self.done.append("quit"))
            _spin(150)

        self.assertEqual(self.done, ["quit"])
        self.assertEqual(set_opacity.call_args.args[0], 0.0)

    def test_stop_that_never_reports_gives_the_window_back(self) -> None:
        with patch.object(exit_screen, "STOP_WAIT_MAX_MS", 0):
            session = self._session(stop_dpi=True)
            self.assertIsNotNone(session.screen())
            _spin(30)

        self.assertIsNone(session.screen())
        self.assertEqual(self.done, [])

    def test_late_stop_report_after_giving_up_still_exits(self) -> None:
        with (
            patch.object(exit_screen, "STOP_WAIT_MAX_MS", 0),
            patch.object(exit_screen, "MIN_VISIBLE_MS", 0),
        ):
            session = self._session(stop_dpi=True)
            _spin(30)
            session.finish(lambda: self.done.append("quit"))
            _spin(30)

        self.assertEqual(self.done, ["quit"])

    def test_keys_and_clicks_do_not_reach_the_window_under_the_screen(self) -> None:
        from PyQt6.QtCore import QEvent, QPointF, Qt
        from PyQt6.QtGui import QKeyEvent, QMouseEvent

        session = self._session(stop_dpi=False)
        screen = session.screen()

        key = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
        key.ignore()
        _APP.sendEvent(screen, key)
        click = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(10, 10),
            QPointF(10, 10),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        click.ignore()
        _APP.sendEvent(screen, click)

        self.assertTrue(key.isAccepted())
        self.assertTrue(click.isAccepted())


class BeginExitScreenTests(unittest.TestCase):
    def test_hidden_window_exits_at_once(self) -> None:
        # Выход из меню трея при скрытом окне: показывать экран некому.
        window = QWidget()
        self.addCleanup(window.deleteLater)
        done: list[str] = []

        begin_exit_screen(window, stop_dpi=False, bypass_running=True).finish(lambda: done.append("quit"))

        self.assertEqual(done, ["quit"])

    def test_broken_screen_never_blocks_the_exit(self) -> None:
        window = QWidget()
        window.show()
        self.addCleanup(window.deleteLater)
        done: list[str] = []

        with patch.object(exit_screen, "ExitScreenSession", side_effect=RuntimeError("boom")):
            begin_exit_screen(window, stop_dpi=True, bypass_running=True).finish(lambda: done.append("quit"))

        self.assertEqual(done, ["quit"])


class ExitScreenLifecycleTests(unittest.TestCase):
    """Прощальный экран показывает единственный владелец выхода."""

    def _lifecycle(self, *, running: bool = True):
        from main.application_lifecycle import ApplicationLifecycle

        self.sessions: list[Mock] = []

        def _begin(**kwargs):
            session = Mock()
            session.kwargs = kwargs
            self.sessions.append(session)
            return session

        window_port = Mock()
        window_port.begin_exit_screen.side_effect = _begin
        runtime_feature = Mock()
        runtime_feature.snapshot.return_value = Mock(running=running)
        lifecycle = ApplicationLifecycle(
            window_port=window_port,
            close_state=Mock(),
            runtime_feature=runtime_feature,
            premium_feature=Mock(),
            telegram_proxy_feature=Mock(),
            tray_feature=Mock(),
        )
        return lifecycle, window_port, runtime_feature

    def test_closing_only_the_window_plays_the_screen_and_then_quits(self) -> None:
        lifecycle, window_port, _runtime = self._lifecycle(running=True)

        with patch("main.application_lifecycle.QApplication") as q_application:
            lifecycle.exit_keep_dpi()
            window_port.begin_exit_screen.assert_called_once_with(stop_dpi=False, bypass_running=True)
            # Программа закрывается, когда экран доиграл, а не раньше.
            q_application.quit.assert_not_called()
            (session,) = self.sessions
            session.finish.call_args.args[0]()

        q_application.quit.assert_called_once()

    def test_stopping_bypass_plays_the_screen_until_bypass_has_stopped(self) -> None:
        lifecycle, window_port, runtime_feature = self._lifecycle(running=True)
        handed_over: list = []
        runtime_feature.stop_and_exit.side_effect = lambda *, on_stopped: handed_over.append(on_stopped) or True

        with patch("main.application_lifecycle.QApplication") as q_application:
            lifecycle.exit_stop_dpi()
            window_port.begin_exit_screen.assert_called_once_with(stop_dpi=True, bypass_running=True)
            (session,) = self.sessions
            session.finish.assert_not_called()

            handed_over[0]()
            session.finish.assert_called_once()
            q_application.quit.assert_not_called()
            session.finish.call_args.args[0]()

        q_application.quit.assert_called_once()

    def test_exit_for_update_shows_no_farewell(self) -> None:
        lifecycle, window_port, _runtime = self._lifecycle()

        with patch("main.application_lifecycle.QApplication") as q_application:
            lifecycle.request_exit(stop_dpi=False, farewell=False)

        window_port.begin_exit_screen.assert_not_called()
        q_application.quit.assert_called_once()

    def test_failed_screen_does_not_block_the_exit(self) -> None:
        lifecycle, window_port, _runtime = self._lifecycle()
        window_port.begin_exit_screen.side_effect = RuntimeError("boom")

        with patch("main.application_lifecycle.QApplication") as q_application:
            lifecycle.exit_keep_dpi()

        q_application.quit.assert_called_once()

    def test_windows_session_end_exits_without_the_screen(self) -> None:
        lifecycle, window_port, _runtime = self._lifecycle()
        lifecycle._close_state.windows_session_ending = False

        with patch("main.application_lifecycle.QApplication") as q_application:
            lifecycle.exit_for_windows_session_end()

        window_port.begin_exit_screen.assert_not_called()
        q_application.quit.assert_called_once()

    def test_user_exit_paths_ask_for_the_farewell(self) -> None:
        import inspect

        from main.window_lifecycle import WindowLifecycleMixin
        from updater.download import service as install_service

        # Кнопки окна «Закрыть приложение», кнопка «Закрыть программу» и меню
        # трея идут с прощальным экраном (значение по умолчанию); установка
        # обновления — единственный выход без него.
        self.assertIn("farewell: bool = True", inspect.getsource(WindowLifecycleMixin.request_exit))
        self.assertIn("farewell=False", inspect.getsource(install_service.UpdateInstallService._on_task_done))


if __name__ == "__main__":
    unittest.main()
