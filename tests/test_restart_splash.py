from __future__ import annotations

"""Окно-продолжение: обновление видно от загрузки до открытия новой версии.

Раньше между закрытием старой версии и открытием новой экран был пустым.
Теперь окно обновления сменяет отдельное окно PowerShell, которое переживает
замену ``Zapret.exe`` и гаснет, когда новая версия откроется.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from updater.install import paths, splash
from updater.install.splash import RestartSplashSpec
from updater.install.splash_script import SPLASH_SCRIPT_TEMPLATE, render_splash_script


def _spec(**overrides) -> RestartSplashSpec:
    values = dict(
        x=10,
        y=20,
        width=900,
        height=600,
        title="Обновляем Zapret до v2.0",
        subtitle="v1.9 → v2.0",
        stages=("Закрываем", "Устанавливаем v2.0", "Запускаем"),
        footer="Окно закроется само",
        window_title="Zapret — обновление",
        jokes=("шутка",),
        colors={"background": "#2b2b2b", "accent": "#00c8ff"},
        logo_png=b"\x89PNG-logo",
    )
    values.update(overrides)
    return RestartSplashSpec(**values)


class _StateDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        patcher = patch.object(paths, "update_state_dir", return_value=self.state_dir)
        patcher.start()
        self.addCleanup(patcher.stop)


class PrepareSplashTests(_StateDirCase):
    def test_files_are_laid_out_next_to_update_state(self) -> None:
        (self.state_dir / paths.RESTART_SPLASH_SHOWN_NAME).write_text("старая метка")
        (self.state_dir / paths.UPDATE_APP_READY_NAME).write_text("старая метка")

        command = splash.prepare_restart_splash(_spec())

        payload = json.loads((self.state_dir / paths.RESTART_SPLASH_SPEC_NAME).read_text(encoding="utf-8"))
        self.assertEqual((payload["x"], payload["y"], payload["width"], payload["height"]), (10, 20, 900, 600))
        self.assertEqual(payload["texts"]["stages"][1], "Устанавливаем v2.0")
        self.assertEqual(payload["app_process_name"], "Zapret")
        self.assertEqual(payload["old_pid"], os.getpid())
        self.assertEqual((self.state_dir / paths.RESTART_SPLASH_LOGO_NAME).read_bytes(), b"\x89PNG-logo")
        script = (self.state_dir / paths.RESTART_SPLASH_SCRIPT_NAME).read_bytes()
        # BOM: PowerShell 5.1 иначе портит русский текст.
        self.assertTrue(script.startswith(b"\xef\xbb\xbf"))
        # Старые метки не гасят новое окно и не обманывают ожидание «показано».
        self.assertFalse((self.state_dir / paths.RESTART_SPLASH_SHOWN_NAME).exists())
        self.assertFalse((self.state_dir / paths.UPDATE_APP_READY_NAME).exists())
        self.assertIn("-STA", command)
        self.assertEqual(command[command.index("-StatePath") + 1], str(self.state_dir / paths.HANDOFF_STATE_NAME))

    def test_window_is_allowed_to_take_focus_before_start(self) -> None:
        order: list[str] = []

        with patch.object(splash, "_allow_splash_foreground", side_effect=lambda: order.append("allow")):
            splash.show_restart_splash(_spec(), spawn=lambda _cmd: order.append("spawn") or True, wait=lambda _p: True)

        self.assertEqual(order, ["allow", "spawn"])

    def test_show_waits_for_window_and_never_raises(self) -> None:
        spawned: list[tuple] = []

        self.assertTrue(splash.show_restart_splash(_spec(), spawn=lambda cmd: spawned.append(cmd) or True, wait=lambda _p: True))
        self.assertEqual(len(spawned), 1)
        self.assertFalse(splash.show_restart_splash(_spec(), spawn=lambda _cmd: False, wait=lambda _p: True))
        self.assertFalse(splash.show_restart_splash(_spec(), spawn=lambda _cmd: True, wait=lambda _p: False))
        self.assertFalse(splash.show_restart_splash(None, spawn=Mock(), wait=Mock()))

    def test_ready_mark_is_written_only_while_window_waits(self) -> None:
        self.assertFalse(splash.mark_update_app_ready("2.0"))
        self.assertFalse((self.state_dir / paths.UPDATE_APP_READY_NAME).exists())

        splash.prepare_restart_splash(_spec())

        self.assertTrue(splash.mark_update_app_ready("2.0"))
        marker = json.loads((self.state_dir / paths.UPDATE_APP_READY_NAME).read_text(encoding="utf-8"))
        self.assertEqual(marker["version"], "2.0")

    def test_wait_for_shown_polls_until_marker(self) -> None:
        marker = self.state_dir / "shown"
        clock = iter(range(100))

        def sleep(_seconds: float) -> None:
            marker.write_text("ok")

        self.assertTrue(
            splash.wait_for_splash_shown(marker, timeout_seconds=5, sleep=sleep, monotonic=lambda: next(clock))
        )


class InstallFlowSplashTests(unittest.TestCase):
    def _run(self, *, started: bool, show_splash) -> None:
        from updater.download import flow

        dpi = Mock()
        dpi.is_running.return_value = False
        with patch.object(flow, "_resolve_and_download", return_value=Mock()):
            flow.run_update_install(
                "2.0",
                token=Mock(),
                dpi=dpi,
                on_stage=lambda _text: None,
                start_installation=lambda _handoff: started,
                splash=_spec(),
                show_splash=show_splash,
            )

    def test_window_appears_only_after_installer_was_handed_over(self) -> None:
        shown = Mock(return_value=True)

        self._run(started=True, show_splash=shown)
        shown.assert_called_once()

        not_shown = Mock()
        from updater.download.downloader import UpdatePipelineError

        with self.assertRaises(UpdatePipelineError):
            self._run(started=False, show_splash=not_shown)
        not_shown.assert_not_called()

    def test_window_failure_does_not_break_update(self) -> None:
        with patch("updater.download.flow.log"):
            self._run(started=True, show_splash=Mock(side_effect=OSError("диск занят")))


class SplashSpecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PyQt6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_physical_rect_scales_offset_inside_screen(self) -> None:
        from PyQt6.QtCore import QPoint, QRect

        from updater.ui.restart_splash_spec import to_physical_rect

        screen = Mock()
        screen.devicePixelRatio.return_value = 1.5
        screen.geometry.return_value.topLeft.return_value = QPoint(1920, 0)

        self.assertEqual(to_physical_rect(QRect(2020, 100, 800, 600), screen=screen), (2070, 150, 1200, 900))

    def test_theme_colors_become_solid_hex(self) -> None:
        from types import SimpleNamespace

        from updater.ui.restart_splash_spec import splash_colors

        colors = splash_colors(
            SimpleNamespace(
                is_light=False,
                fg="rgba(255, 255, 255, 0.5)",
                fg_muted="rgba(255, 255, 255, 0.65)",
                accent_hex="#00c8ff",
                accent_fg="#000000",
            )
        )

        for value in colors.values():
            self.assertRegex(value, r"^#[0-9a-f]{6}$")
        # Полупрозрачный белый над тёмной карточкой — серый, а не белый.
        self.assertEqual(colors["foreground"], "#959595")

    def test_spec_takes_place_of_update_window(self) -> None:
        from PyQt6.QtWidgets import QWidget

        from updater.ui.restart_splash_spec import build_restart_splash_spec

        host = QWidget()
        host.resize(1000, 800)
        host.show()
        self.addCleanup(host.deleteLater)
        card = QWidget(host)
        card.setGeometry(100, 50, 700, 500)
        card.show()

        spec = build_restart_splash_spec(
            host, dialog_widget=card, current_version="1.9", target_version="2.0", language="ru"
        )

        self.assertEqual((spec.width, spec.height), (700, 500))
        self.assertIn("v2.0", spec.title)
        self.assertIn("v1.9", spec.subtitle)
        self.assertEqual(len(spec.stages), 3)
        self.assertTrue(spec.jokes)

        hidden = build_restart_splash_spec(
            host, dialog_widget=None, current_version="1.9", target_version="2.0", language="en"
        )
        self.assertGreaterEqual(hidden.width, 720)
        self.assertIn("Updating", hidden.title)


class SplashScriptContractTests(unittest.TestCase):
    """Правила скрипта, которые на Linux можно проверить только по тексту."""

    def test_rules_that_keep_update_safe(self) -> None:
        script = render_splash_script()

        self.assertNotRegex(script, r"@[A-Z_]+@")
        # Поверх всех — только пока старая программа на экране: иначе защита
        # от кражи фокуса ставила окно позади неё. Потом — обычное окно,
        # чтобы сообщение установщика было видно.
        self.assertIn("$form.TopMost = (-not $S.Snapshot)", script)
        self.assertIn("function Watch-OldApp", script)
        watch = script[script.index("function Watch-OldApp"):script.index("function Poll-State")]
        self.assertIn("$form.TopMost = $false", watch)
        self.assertIn("Watch-OldApp", script[script.index("function Poll-State"):])
        # Неудача установки или отменённое обновление — окно уходит сразу.
        self.assertIn("'failed'    { Start-Closing $true", script)
        self.assertIn("'обновление отменено'", script)
        # Гаснет по метке новой версии, по её окну или по срокам.
        self.assertIn("function Test-AppReady", script)
        self.assertIn("function Test-NewAppWindow", script)
        self.assertIn("$readyTimeoutSeconds = 90", script)
        # Страховка вне общего try: окно не может висеть вечно.
        self.assertIn("[System.Windows.Forms.Application]::Exit()", script)
        # Прозрачность — только украшение: её сбой не мешает закрытию.
        self.assertIn("function Set-FormOpacity", script)
        # Ничего не берёт из каталога установки: он как раз заменяется.
        self.assertNotIn("_internal", SPLASH_SCRIPT_TEMPLATE)


class PageSplashTests(unittest.TestCase):
    def test_install_passes_splash_spec(self) -> None:
        from app.feature_facades.updater import UpdaterFeature
        from test_update_check_coordinator import UpdateCheckCoordinatorTests

        case = UpdateCheckCoordinatorTests()
        page = case._page(UpdaterFeature())
        token = page._updater_feature.begin_update_check(source="manual")
        page._updater_feature.finish_update_check(dict(case._FOUND), source="manual", token=token)
        page._apply_check_snapshot(page._updater_feature.current_update_check_snapshot())
        page._install_service.start.return_value = True
        spec = _spec()
        page._build_restart_splash_spec = Mock(return_value=spec)

        with patch("updater.ui.page.run_update_setting_write"):
            page._request_install_update()

        page._install_service.start.assert_called_once_with("21.1.5.80", splash=spec)


if __name__ == "__main__":
    unittest.main()
