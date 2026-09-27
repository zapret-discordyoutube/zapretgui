from __future__ import annotations

"""Таблица источников на странице «Серверы» и то, что получает страница.

Таблица только показывает, кто отвечает: Telegram никого не задерживает и
не создаёт предложение обновиться, строка Forgejo отражает текущее
состояние, а страница и её сервисы получают действия с DPI, а не весь
runtime.
"""

import inspect
import time
import unittest
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

from updater.check import sources
from updater.release.mirrors import MirrorReleaseError

SERVER = {
    "id": "primary",
    "name": "Primary",
    "host": "updates.example",
    "https_port": 888,
    "http_port": 887,
}
VERSIONS = {"stable": {"version": "21.1.1.5"}, "dev": {"version": "21.1.5.45"}}


class SourceRowsTests(unittest.TestCase):
    def _probe(self, **patches) -> tuple[bool, dict[str, dict]]:
        rows: dict[str, dict] = {}
        with (
            patch("updater.release.forgejo.probe_forgejo", **patches.get("forgejo", {"return_value": 0.01})),
            patch("updater.release.mirrors.fetch_versions", **patches.get("mirror", {"return_value": (VERSIONS, "HTTPS", "https://x", False, 0.02)})),
        ):
            online = sources.probe_update_sources(
                language="ru",
                emit_row=lambda name, status: rows.__setitem__(name, status),
                servers=[SERVER],
            )
        return online, rows

    def test_mirror_network_error_becomes_error_row(self) -> None:
        online, rows = self._probe(mirror={"side_effect": MirrorReleaseError("HTTPS: нет ответа (тайм-аут)")})

        self.assertTrue(online, "Forgejo ответил")
        self.assertEqual(rows["Primary"]["status"], "error")
        self.assertIn("тайм-аут", rows["Primary"]["error"])

    def test_forgejo_row_reflects_current_failure(self) -> None:
        """Раньше старый ответ из памяти показывал «online» при недоступном Forgejo."""
        online, rows = self._probe(
            forgejo={"side_effect": ConnectionError("обрыв")},
            mirror={"side_effect": MirrorReleaseError("нет ответа")},
        )

        self.assertFalse(online)
        self.assertEqual(rows["Forgejo API"]["status"], "error")

    def test_first_online_mirror_is_marked_current(self) -> None:
        _online, rows = self._probe()

        self.assertTrue(rows["Primary"]["is_current"])
        self.assertEqual(rows["Primary"]["dev_version"], "21.1.5.45")

    def test_telegram_row_is_diagnostic_and_cannot_announce_an_update(self) -> None:
        rows: list[tuple[str, dict]] = []
        with patch("updater.release.telegram.get_telegram_version_info", return_value={"version": "99.1.2.3"}):
            sources.start_telegram_probe(language="ru", emit_row=lambda *row: rows.append(row)).join(5)

        name, status = rows[0]
        self.assertEqual(name, "Telegram")
        self.assertEqual(status["status"], "online")
        self.assertFalse(status["is_current"])
        self.assertFalse(status["update_source"])

    def test_update_check_does_not_wait_for_telegram(self) -> None:
        from updater.check.flow import run_update_check
        from updater.release.resolver import ReleaseLookup

        telegram_released = Event()
        self.addCleanup(telegram_released.set)

        def slow_telegram(_channel):
            telegram_released.wait(5)
            return None

        started = time.monotonic()
        with patch("updater.release.telegram.get_telegram_version_info", side_effect=slow_telegram):
            outcome = run_update_check(
                "dev",
                language="ru",
                emit_row=lambda *_row: None,
                dpi=None,
                lookup=lambda _channel: ReleaseLookup({"version": "1.0.0.1"}),
                probe=lambda **_kwargs: True,
            )

        self.assertEqual(outcome.release["version"], "1.0.0.1")
        self.assertLess(time.monotonic() - started, 2.0)

    def test_client_telegram_diagnostic_has_no_bot_api_secret(self) -> None:
        from updater.release import telegram

        source = inspect.getsource(telegram)
        self.assertNotIn("TG_UPDATE_BOT_TOKEN", source)
        self.assertNotIn("api.telegram.org", source)
        self.assertNotIn("_call_bot_api", source)

    def test_telegram_version_comes_only_from_installer_name(self) -> None:
        """Числа из текста поста (например, IP-адрес) версией не считаются."""
        from updater.release import telegram

        html = "<p>Сервер 10.20.30.40</p><a>Zapret2Setup_DEV_21_1_5_78.exe</a><p>1.2.3.4</p>"
        session = SimpleNamespace(get=Mock(return_value=SimpleNamespace(status_code=200, text=html)), close=Mock())
        with patch.object(telegram, "new_session", return_value=session):
            self.assertEqual(telegram.get_telegram_version_info("dev")["version"], "21.1.5.78")

        session.get.return_value = SimpleNamespace(status_code=200, text="<p>10.20.30.40</p>")
        with patch.object(telegram, "new_session", return_value=session):
            self.assertIsNone(telegram.get_telegram_version_info("dev"))


class ServersPageDependenciesTests(unittest.TestCase):
    def test_services_receive_runtime_actions_instead_of_full_runtime_feature(self) -> None:
        from PyQt6.QtWidgets import QApplication

        from app.page_names import PageName
        from ui.page_deps.system import build_servers_page_kwargs
        from updater.ui.page import ServersPage

        QApplication.instance() or QApplication([])
        runtime_feature = SimpleNamespace(
            is_any_running=Mock(),
            shutdown_sync=Mock(),
            shutdown_sync_from_worker=Mock(),
            is_available=Mock(),
            restart=Mock(),
            objects=SimpleNamespace(runtime_service=SimpleNamespace(mark_stopped=Mock())),
        )
        request_exit = Mock()
        kwargs = build_servers_page_kwargs(
            page_name=PageName.SERVERS,
            runtime_feature=runtime_feature,
            updater_feature=Mock(),
            external_actions_feature=Mock(),
            show_page=Mock(),
            request_exit=request_exit,
        )

        self.assertNotIn("runtime_feature", inspect.signature(ServersPage.__init__).parameters)
        self.assertNotIn("runtime_feature", kwargs)
        for service in (kwargs["check_service"], kwargs["install_service"]):
            actions = service._runtime_actions
            self.assertIs(actions.is_any_running, runtime_feature.is_any_running)
            # Остановки обновлятора идут из фоновых потоков: runtime-state и
            # UI-подписчиков обновляет GUI-поток.
            self.assertIs(actions.shutdown_sync, runtime_feature.shutdown_sync_from_worker)
            self.assertIs(actions.restart, runtime_feature.restart)
            self.assertIs(actions.request_exit, request_exit)
        kwargs["install_service"]._runtime_actions.mark_stopped()
        runtime_feature.objects.runtime_service.mark_stopped.assert_called_once_with(clear_error=True)


if __name__ == "__main__":
    unittest.main()
