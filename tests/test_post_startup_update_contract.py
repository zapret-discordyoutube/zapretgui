from __future__ import annotations

import contextlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))


class _Timers:
    """Таймеры запуска срабатывают в тесте сразу, фоновая проверка — по команде.

    Фоновая проверка сама назначает следующую: сработай она сразу, тест ушёл
    бы в бесконечный круг.
    """

    def __init__(self) -> None:
        self.pending: list[tuple[int, object]] = []

    def schedule(self, delay_ms, callback) -> None:
        from main import post_startup_update

        if int(delay_ms) >= post_startup_update.BACKGROUND_CHECK_RETRY_MS:
            self.pending.append((int(delay_ms), callback))
            return
        callback()

    def fire(self, index: int = 0) -> None:
        _delay, callback = self.pending.pop(index)
        callback()


class PostStartupUpdateContractTests(unittest.TestCase):
    def test_skipped_startup_update_check_clears_checking_status(self) -> None:
        from main import post_startup_update

        class UpdaterFeature:
            finished_result = None

            def is_auto_update_enabled(self) -> bool:
                return True

            def begin_update_check(self, *, source: str) -> int:
                self.begin_source = source
                return 7

            def finish_update_check(self, result: dict, *, source: str, token: int) -> bool:
                self.finished_result = dict(result)
                self.finish_source = source
                self.finish_token = token
                return True

            def run_startup_update_check(self, *, signalled: bool = False) -> dict:
                return {
                    "has_update": False,
                    "version": "1.2.3",
                    "release_notes": "",
                    "error": None,
                    "skipped": True,
                    "skip_reason": "Следующая автоматическая проверка возможна через 5 мин",
                }

        startup_host = SimpleNamespace(
            startup_post_init_ready=object(),
            startup_interactive_ready=object(),
            startup_state=SimpleNamespace(post_init_ready=True, interactive_logged=True),
            is_alive=Mock(return_value=True),
            ensure_page=Mock(),
            show_whats_new=Mock(return_value=True),
        )
        statuses: list[str] = []

        updater_feature = UpdaterFeature()

        with (
            patch.object(post_startup_update, "bind_startup_gate", side_effect=lambda _signal, callback, **_kwargs: callback()),
            patch.object(post_startup_update, "schedule_after", side_effect=_Timers().schedule),
            patch.object(post_startup_update, "enqueue_subsystem_task", side_effect=lambda _queue, _name, target: target()),
            patch.object(post_startup_update, "log"),
        ):
            post_startup_update.install_update_check(
                startup_host,
                updater_feature=updater_feature,
                notify=Mock(),
                set_status=statuses.append,
                idle_tasks=_ImmediateIdleTasks(),
            )

        self.assertEqual(statuses[0], "Проверка обновлений...")
        self.assertNotEqual(statuses[-1], "Проверка обновлений...")
        self.assertIn("Следующая автоматическая проверка", statuses[-1])
        self.assertEqual(updater_feature.begin_source, "startup")
        self.assertEqual(updater_feature.finish_source, "startup")
        self.assertEqual(updater_feature.finish_token, 7)
        self.assertTrue(updater_feature.finished_result["skipped"])


class _ImmediateIdleTasks:
    """Очередь пауз пользователя, которая в тесте выполняет задачу сразу."""

    def add(self, _name, callback, *, delay_ms=0, needs_shown_window=True) -> None:
        callback()


class _RecordingIdleTasks:
    """Очередь пауз пользователя, которая только записывает задачи."""

    def __init__(self) -> None:
        self.tasks: list[tuple[str, object, bool]] = []

    def add(self, name, callback, *, delay_ms=0, needs_shown_window=True) -> None:
        self.tasks.append((name, callback, bool(needs_shown_window)))


class _Feature:
    """Проверка при запуске возвращает ``result``; «Что нового» — ``whats_new``."""

    def __init__(self, result: dict, whats_new: tuple = ()) -> None:
        self.result = result
        self.whats_new = whats_new
        self.seen: list[str] = []
        self.ready: list[str] = []
        self.auto_update_enabled = True
        self.sources: list[str] = []
        self.signalled: list[bool] = []
        self.finished: list[dict] = []
        self.busy = False
        # Сервер с очередью обновлений: по умолчанию в тестах его нет.
        self.queue_reachable: bool | None = False
        self.watcher = None
        self.on_release = None
        self.on_queued = None
        # Чем занят человек при очередном вопросе; список кончился — свободен.
        self.busy_reasons: list[str] = []
        self.failed_versions: list[str] = []

    def update_busy_reason(self) -> str:
        return self.busy_reasons.pop(0) if self.busy_reasons else ""

    def note_auto_install_failed(self, version: str) -> None:
        self.failed_versions.append(version)

    def is_auto_update_enabled(self) -> bool:
        return self.auto_update_enabled

    def begin_update_check(self, *, source: str) -> int | None:
        if self.busy:
            return None
        self.sources.append(source)
        return 1

    def finish_update_check(self, result: dict, *, source: str, token: int) -> bool:
        self.finished.append(dict(result))
        return True

    def run_startup_update_check(self, *, signalled: bool = False) -> dict:
        self.signalled.append(bool(signalled))
        return dict(self.result)

    def create_release_watcher(self, *, on_release, on_queued, is_bypass_running=None):
        self.is_bypass_running = is_bypass_running
        self.on_release = on_release
        self.on_queued = on_queued
        self.watcher = Mock()
        type(self.watcher).reachable = property(lambda _watcher: self.queue_reachable)
        self.watcher.wait_until_probed.side_effect = lambda _timeout: self.queue_reachable
        return self.watcher

    def startup_whats_new(self, _version: str) -> tuple:
        return self.whats_new

    def mark_whats_new_seen(self, version: str) -> None:
        self.seen.append(version)

    def mark_update_app_ready(self, version: str) -> bool:
        self.ready.append(version)
        return True


class PostStartupUpdateWindowTests(unittest.TestCase):
    """Одно окно обновления: путь при запуске больше не спрашивает сам."""

    _FOUND = {"has_update": True, "version": "9.9.9", "release_notes": "новое", "error": None}

    def _run(
        self, feature: _Feature, *, idle_tasks=None, queued_tasks=None, timers=None, notify=None, window_shown=None
    ):
        from main import post_startup_update

        # Патчи живут до конца теста: фоновую проверку тест запускает позже.
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)

        def _enqueue(queue, name, target):
            if queued_tasks is not None:
                queued_tasks.append((queue, name))
            target()

        host = SimpleNamespace(
            startup_post_init_ready=object(),
            startup_interactive_ready=object(),
            startup_state=SimpleNamespace(post_init_ready=True, interactive_logged=True),
            is_alive=Mock(return_value=True),
            ensure_page=Mock(),
            show_whats_new=Mock(return_value=True),
        )
        if window_shown is not None:
            host.is_window_shown = Mock(return_value=window_shown)
        for item in (
            patch.object(post_startup_update, "bind_startup_gate", side_effect=lambda _signal, callback, **_kwargs: callback()),
            patch.object(post_startup_update, "schedule_after", side_effect=(timers or _Timers()).schedule),
            patch.object(post_startup_update, "enqueue_subsystem_task", side_effect=_enqueue),
            patch.object(post_startup_update, "log"),
        ):
            stack.enter_context(item)
        post_startup_update.install_update_check(
            host,
            updater_feature=feature,
            notify=notify or Mock(),
            set_status=Mock(),
            idle_tasks=idle_tasks or _ImmediateIdleTasks(),
        )
        return host

    def test_found_update_creates_servers_page_which_opens_the_window(self) -> None:
        from app.page_names import PageName

        host = self._run(_Feature(self._FOUND))

        host.ensure_page.assert_called_once_with(PageName.SERVERS)

    def test_update_page_is_built_in_user_pause_not_in_the_result_handler(self) -> None:
        from app.page_names import PageName

        idle_tasks = _RecordingIdleTasks()

        host = self._run(_Feature(self._FOUND), idle_tasks=idle_tasks)

        # Сборка страницы занимает GUI-поток: посреди клика пользователя она
        # давала рывок, поэтому ждёт паузы. При окне в трее ждать нечего.
        host.ensure_page.assert_not_called()
        self.assertEqual(
            [(name, needs_window) for name, _cb, needs_window in idle_tasks.tasks],
            [("UpdateWindowPage", False)],
        )

        idle_tasks.tasks[0][1]()
        host.ensure_page.assert_called_once_with(PageName.SERVERS)

    def test_update_page_is_not_built_after_app_started_closing(self) -> None:
        idle_tasks = _RecordingIdleTasks()
        host = self._run(_Feature(self._FOUND), idle_tasks=idle_tasks)

        host.is_alive.return_value = False
        idle_tasks.tasks[0][1]()

        host.ensure_page.assert_not_called()

    def test_interrupted_update_is_checked_in_background_queue(self) -> None:
        queued: list[tuple[str, str]] = []

        self._run(_Feature({"has_update": False, "version": "1.0", "error": None}), queued_tasks=queued)

        # Первый импорт updater.install и чтение его файлов в GUI-потоке
        # задерживали кадр сразу после появления окна.
        self.assertIn(("update", "InterruptedUpdateCheck"), queued)

    def test_whats_new_dialog_waits_for_user_pause_and_shown_window(self) -> None:
        from config.build_info import APP_VERSION

        history = ({"version": APP_VERSION, "notes": "новое"},)
        feature = _Feature({"has_update": False, "version": APP_VERSION, "error": None}, whats_new=history)
        idle_tasks = _RecordingIdleTasks()

        host = self._run(feature, idle_tasks=idle_tasks)

        # Окно строится в GUI-потоке и раньше появлялось по таймеру — посреди
        # действий пользователя и вместе со значком в трее.
        host.show_whats_new.assert_not_called()
        self.assertEqual(
            [(name, needs_window) for name, _cb, needs_window in idle_tasks.tasks],
            [("WhatsNewDialog", True)],
        )

        idle_tasks.tasks[0][1]()
        host.show_whats_new.assert_called_once_with(APP_VERSION, history)

    def test_skipped_version_does_not_create_the_page(self) -> None:
        host = self._run(_Feature(dict(self._FOUND, user_skipped=True)))

        host.ensure_page.assert_not_called()

    def test_whats_new_is_shown_once_and_marked_seen(self) -> None:
        from config.build_info import APP_VERSION

        history = ({"version": APP_VERSION, "notes": "новое"},)
        feature = _Feature({"has_update": False, "version": APP_VERSION, "error": None}, whats_new=history)

        host = self._run(feature)

        host.show_whats_new.assert_called_once_with(APP_VERSION, history)
        self.assertEqual(feature.seen, [APP_VERSION])

    def test_new_version_tells_restart_window_it_opened(self) -> None:
        from config.build_info import APP_VERSION

        feature = _Feature({"has_update": False, "version": APP_VERSION, "error": None})

        self._run(feature)

        self.assertEqual(feature.ready, [APP_VERSION])

    def test_nothing_new_shows_no_window(self) -> None:
        host = self._run(_Feature({"has_update": False, "version": "1.0", "error": None}))

        host.show_whats_new.assert_not_called()


class BackgroundUpdateCheckTests(unittest.TestCase):
    """Программа сама ищет обновление, пока работает, а не только при запуске."""

    _FOUND = PostStartupUpdateWindowTests._FOUND
    _CLEAN = {"has_update": False, "version": "1.0", "error": None}
    _run = PostStartupUpdateWindowTests._run

    def test_background_check_is_planned_about_every_half_hour(self) -> None:
        from main import post_startup_update

        timers = _Timers()
        feature = _Feature(self._CLEAN)

        self._run(feature, timers=timers)

        self.assertEqual(feature.sources, ["startup"])
        self.assertEqual(len(timers.pending), 1)
        delay = timers.pending[0][0]
        self.assertGreaterEqual(delay, post_startup_update.BACKGROUND_CHECK_INTERVAL_MS)
        self.assertLessEqual(
            delay,
            post_startup_update.BACKGROUND_CHECK_INTERVAL_MS + post_startup_update.BACKGROUND_CHECK_JITTER_MS,
        )

    def test_background_check_repeats_and_hands_the_find_to_servers_page(self) -> None:
        from app.page_names import PageName

        timers = _Timers()
        feature = _Feature(self._CLEAN)
        host = self._run(feature, timers=timers)
        host.ensure_page.assert_not_called()

        # Пока программа работала, вышла новая версия.
        feature.result = dict(self._FOUND, auto_install=True)
        timers.fire()

        self.assertEqual(feature.sources, ["startup", "background"])
        host.ensure_page.assert_called_once_with(PageName.SERVERS)
        # Следующая фоновая проверка уже назначена.
        self.assertEqual(len(timers.pending), 1)

    def test_background_check_without_news_stays_silent(self) -> None:
        timers = _Timers()
        notify = Mock()
        self._run(_Feature(self._CLEAN), timers=timers, notify=notify)
        notify.reset_mock()

        timers.fire()

        # «Обновлений нет» каждые полчаса было бы назойливо.
        notify.assert_not_called()

    def test_background_check_respects_the_switch(self) -> None:
        timers = _Timers()
        feature = _Feature(self._CLEAN)
        self._run(feature, timers=timers)

        feature.auto_update_enabled = False
        timers.fire()

        self.assertEqual(feature.sources, ["startup"])
        # Круг не рвётся: выключатель могут включить обратно.
        self.assertEqual(len(timers.pending), 1)

    def test_failed_check_is_retried_sooner(self) -> None:
        from main import post_startup_update

        timers = _Timers()
        feature = _Feature({"has_update": False, "version": "", "error": "нет сети"})

        self._run(feature, timers=timers)

        delays = sorted(delay for delay, _callback in timers.pending)
        self.assertEqual(delays[0], post_startup_update.BACKGROUND_CHECK_RETRY_MS)
        feature.result = dict(self._CLEAN)
        timers.fire(
            next(i for i, (delay, _cb) in enumerate(timers.pending) if delay == delays[0])
        )
        self.assertEqual(feature.sources, ["startup", "background"])

    def test_server_permission_starts_a_signalled_check_at_once(self) -> None:
        from app.page_names import PageName

        feature = _Feature(self._CLEAN)
        host = self._run(feature)
        feature.watcher.start.assert_called_once_with()

        feature.result = dict(self._FOUND, auto_install=True)
        feature.on_release("9.9.9")

        self.assertEqual(feature.sources, ["startup", "background"])
        # Только проверка по разрешению сервера вправе ставить без вопроса.
        self.assertEqual(feature.signalled, [False, True])
        host.ensure_page.assert_called_once_with(PageName.SERVERS)

    def test_program_started_in_tray_knows_its_window_is_hidden(self) -> None:
        from core.runtime import presence

        for shown in (False, True):
            # Окно ни разу не показывалось и само о себе ничего не отметило.
            with self.subTest(shown=shown), patch.object(presence, "_window_shown", None):
                self._run(_Feature(self._CLEAN), window_shown=shown)

                self.assertIs(presence.window_shown(), shown)

    def test_window_that_already_spoke_for_itself_is_not_overruled(self) -> None:
        from core.runtime import presence

        with patch.object(presence, "_window_shown", True):
            self._run(_Feature(self._CLEAN), window_shown=False)

            self.assertIs(presence.window_shown(), True)

    def test_permission_waits_while_the_person_is_busy(self) -> None:
        from app.page_names import PageName
        from main import post_startup_update

        feature = _Feature(self._CLEAN)
        notify = Mock()
        waits: list = []

        class _BusyTimers(_Timers):
            def schedule(self, delay_ms, callback) -> None:
                if int(delay_ms) == post_startup_update.BUSY_RECHECK_MS:
                    waits.append(callback)
                    return
                super().schedule(delay_ms, callback)

        host = self._run(feature, timers=_BusyTimers(), notify=notify)
        notify.reset_mock()
        feature.result = dict(self._FOUND, auto_install=True)
        feature.busy_reasons = ["fullscreen", "fullscreen"]

        feature.on_release("9.9.9")

        # Идёт игра на весь экран: установка закрыла бы программу посреди неё.
        self.assertEqual(feature.sources, ["startup"])
        host.ensure_page.assert_not_called()
        self.assertEqual(len(waits), 1)
        self.assertEqual(notify.call_args.args[0]["source"], "update.deferred")

        waits.pop()()
        # Всё ещё занят: ждём дальше, но второй раз об этом не говорим.
        self.assertEqual(feature.sources, ["startup"])
        self.assertEqual(notify.call_count, 1)

        waits.pop()()
        # Освободился: разрешение сервера не пропало.
        self.assertEqual(feature.sources, ["startup", "background"])
        self.assertEqual(feature.signalled, [False, True])
        host.ensure_page.assert_called_once_with(PageName.SERVERS)

    def test_deferred_update_is_dropped_when_auto_update_is_switched_off(self) -> None:
        from main import post_startup_update

        feature = _Feature(self._CLEAN)
        waits: list = []

        class _BusyTimers(_Timers):
            def schedule(self, delay_ms, callback) -> None:
                if int(delay_ms) == post_startup_update.BUSY_RECHECK_MS:
                    waits.append(callback)
                    return
                super().schedule(delay_ms, callback)

        host = self._run(feature, timers=_BusyTimers())
        feature.result = dict(self._FOUND, auto_install=True)
        feature.busy_reasons = ["blockcheck"]
        feature.on_release("9.9.9")

        feature.auto_update_enabled = False
        waits.pop()()

        self.assertEqual(waits, [])
        self.assertEqual(feature.sources, ["startup"])
        host.ensure_page.assert_not_called()

    def test_startup_check_is_skipped_when_permission_came_first(self) -> None:
        from main import post_startup_update

        feature = _Feature(dict(self._FOUND, auto_install=True))
        feature.queue_reachable = True
        delayed: list = []

        def schedule(delay_ms, callback) -> None:
            delayed.append(callback)

        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for item in (
            patch.object(post_startup_update, "bind_startup_gate", side_effect=lambda _signal, callback, **_kwargs: callback()),
            patch.object(post_startup_update, "schedule_after", side_effect=schedule),
            patch.object(post_startup_update, "enqueue_subsystem_task", side_effect=lambda _queue, _name, target: target()),
            patch.object(post_startup_update, "log"),
        ):
            stack.enter_context(item)
        host = SimpleNamespace(
            startup_post_init_ready=object(),
            startup_interactive_ready=object(),
            startup_state=SimpleNamespace(post_init_ready=True, interactive_logged=True),
            is_alive=Mock(return_value=True),
            ensure_page=Mock(),
            show_whats_new=Mock(return_value=True),
        )
        post_startup_update.install_update_check(
            host, updater_feature=feature, notify=Mock(), set_status=Mock(), idle_tasks=_ImmediateIdleTasks()
        )

        # Сервер ответил за доли секунды — раньше, чем сработал таймер
        # проверки при запуске.
        feature.on_release("9.9.9")
        for callback in list(delayed):
            callback()

        self.assertEqual(feature.sources, ["background"])

    def test_permission_stays_valid_for_a_retry_after_failed_download(self) -> None:
        timers = _Timers()
        feature = _Feature(self._CLEAN)
        self._run(feature, timers=timers)
        feature.result = {"has_update": False, "version": "", "error": "нет сети"}
        feature.on_release("9.9.9")

        # Проверка по разрешению не удалась: повтор через пять минут тоже
        # идёт «по сигналу», иначе разрешение сервера пропало бы зря.
        feature.result = dict(self._FOUND, auto_install=True)
        retry = next(
            i for i, (delay, _cb) in enumerate(timers.pending)
            if delay == __import__("main.post_startup_update", fromlist=["x"]).BACKGROUND_CHECK_RETRY_MS
        )
        timers.fire(retry)

        self.assertEqual(feature.signalled, [False, True, True])

    def test_signalled_check_is_repeated_until_release_list_shows_the_version(self) -> None:
        feature = _Feature(self._CLEAN)
        self._run(feature)
        calls = {"count": 0}
        original = feature.run_startup_update_check

        def run(*, signalled: bool = False) -> dict:
            calls["count"] += 1
            # Сервер версию уже объявил, а список выпусков покажет её чуть позже.
            if calls["count"] == 3:
                feature.result = dict(self._FOUND, auto_install=True)
            return original(signalled=signalled)

        feature.run_startup_update_check = run
        feature.on_release("9.9.9")

        self.assertEqual(feature.signalled[1:], [True, True, True])
        self.assertTrue(feature.finished[-1]["has_update"])

    def test_server_permission_during_another_check_is_not_lost(self) -> None:
        timers = _Timers()
        feature = _Feature(self._CLEAN)
        self._run(feature, timers=timers)
        waiting = []

        def schedule(delay_ms, callback) -> None:
            waiting.append((int(delay_ms), callback))

        from main import post_startup_update

        # Идёт ручная проверка: своя запустится следом.
        feature.busy = True
        with patch.object(post_startup_update, "schedule_after", side_effect=schedule):
            feature.on_release("9.9.9")
        self.assertEqual(feature.sources, ["startup"])
        self.assertEqual([delay for delay, _cb in waiting], [post_startup_update.SIGNAL_CHECK_RETRY_MS])

        feature.busy = False
        feature.result = dict(self._FOUND, auto_install=True)
        waiting[0][1]()

        self.assertEqual(feature.sources, ["startup", "background"])
        self.assertEqual(feature.signalled[-1], True)

    def test_server_permission_respects_the_switch(self) -> None:
        feature = _Feature(self._CLEAN)
        self._run(feature)

        feature.auto_update_enabled = False
        feature.on_release("9.9.9")

        self.assertEqual(feature.sources, ["startup"])

    def test_scheduled_check_keeps_quiet_while_server_queue_is_reachable(self) -> None:
        timers = _Timers()
        feature = _Feature(self._CLEAN)
        feature.queue_reachable = True
        self._run(feature, timers=timers)

        timers.fire()

        # О версии сообщит сервер: проверка по расписанию от миллиона
        # программ только нагружала бы Forgejo.
        self.assertEqual(feature.sources, ["startup"])
        self.assertEqual(len(timers.pending), 1)

    def test_find_at_startup_waits_for_the_server_queue_silently(self) -> None:
        feature = _Feature(self._FOUND)
        feature.queue_reachable = True

        self._run(feature)

        self.assertTrue(feature.finished[0]["awaiting_signal"])
        feature.watcher.wait_until_probed.assert_called_once()

    def test_find_at_startup_is_offered_by_window_when_there_is_no_server_queue(self) -> None:
        for reachable in (False, None):
            with self.subTest(reachable=reachable):
                feature = _Feature(self._FOUND)
                feature.queue_reachable = reachable

                self._run(feature)

                self.assertNotIn("awaiting_signal", feature.finished[0])

    def test_skipped_version_does_not_wait_for_the_queue(self) -> None:
        feature = _Feature(dict(self._FOUND, user_skipped=True))
        feature.queue_reachable = True

        self._run(feature)

        self.assertNotIn("awaiting_signal", feature.finished[0])

    def test_queued_version_is_explained_to_the_user(self) -> None:
        notify = Mock()
        feature = _Feature(self._CLEAN)
        self._run(feature, notify=notify)
        notify.reset_mock()

        feature.on_queued("9.9.9")

        payload = notify.call_args.args[0]
        self.assertEqual(payload.get("source"), "update.queued")
        self.assertIn("9.9.9", payload.get("title"))

    def test_self_installed_update_is_announced(self) -> None:
        notify = Mock()

        self._run(_Feature(dict(self._FOUND, auto_install=True)), notify=notify)

        sources = [call.args[0].get("source") for call in notify.call_args_list]
        self.assertIn("update.auto_install", sources)

    def test_update_offered_by_window_is_not_announced_as_self_installed(self) -> None:
        notify = Mock()

        self._run(_Feature(self._FOUND), notify=notify)

        sources = [call.args[0].get("source") for call in notify.call_args_list]
        self.assertNotIn("update.auto_install", sources)


if __name__ == "__main__":
    unittest.main()
