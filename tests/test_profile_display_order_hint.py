"""Перетаскивание в списке профилей меняет только вид списка.

Порядок профилей в файле пресета меняет отдельная страница «Порядок в
пресете». Список похож на сам пресет, поэтому человеку это объясняют: подсказка
едет за курсором, пока профиль тащат, а после первого перетаскивания страница
показывает уведомление.
"""

from __future__ import annotations

import contextlib
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication

from profile import service as service_module
from profile.service import ProfilePresetService
from profile.ui import preset_setup_page as page_module
from profile.ui import profile_list_view, profiles_list
from profile.ui.preset_setup_page import PresetSetupPageBase
from profile.ui.profile_order_list import ProfileOrderList


class DisplayOrderOnlyTests(unittest.TestCase):
    def test_drag_in_the_list_saves_the_list_layout_and_not_the_preset(self) -> None:
        source = SimpleNamespace(key="profile:1", profile=SimpleNamespace(persistent_key="uid:a"))
        destination = SimpleNamespace(key="profile:2", profile=SimpleNamespace(persistent_key="uid:b"))
        service = Mock()
        service._profile_sources_for_folder_order.return_value = (source, destination)
        planned = {"folders": {"games": ["uid:b", "uid:a"]}}

        with (
            patch.object(
                service_module,
                "find_profile_list_source",
                side_effect=lambda _sources, key: {"profile:1": source, "profile:2": destination}.get(key),
            ),
            patch.object(service_module, "live_items_from_sources", return_value=()),
            patch.object(service_module, "load_profile_folder_state", return_value={}),
            patch.object(service_module, "profile_folder_state_lock", return_value=contextlib.nullcontext()),
            patch.object(service_module, "plan_profile_move", return_value=planned) as plan,
            patch.object(service_module, "save_profile_folder_state") as save_layout,
        ):
            moved_key = ProfilePresetService._move_profile_in_folder(
                service,
                "after",
                "profile:1",
                destination_profile_key="profile:2",
                destination_folder_key="games",
            )

        self.assertEqual(moved_key, "profile:1")
        save_layout.assert_called_once_with(planned)
        self.assertEqual(plan.call_args.kwargs["source_key"], "uid:a")
        # Файл пресета не читается и не переписывается: порядок профилей в нём прежний.
        service.load_selected_preset.assert_not_called()
        service.save_selected_preset.assert_not_called()


class DragHintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        self.app.closeAllWindows()
        self.app.processEvents()

    def _view(self) -> profile_list_view.ProfileListView:
        view = profile_list_view.ProfileListView()
        self.addCleanup(view.deleteLater)
        view.resize(500, 300)
        view.show()
        self.app.processEvents()
        view._drop_target_at = Mock(
            return_value=({"marker": {"row": -1, "mode": ""}, "destination_kind": "end"}, "", "")
        )
        return view

    def test_hint_follows_the_cursor_while_a_profile_is_dragged(self) -> None:
        view = self._view()
        view.set_drag_hint_text("Меняется только вид списка.")
        view._drag_source = ("profile", "profile:a")

        view._update_internal_drag(QPoint(40, 40))

        self.assertTrue(view._drag_hint.is_visible())
        self.assertEqual(view._drag_hint._tooltip.text(), "Меняется только вид списка.")
        first_pos = view._drag_hint._tooltip.pos()

        view._update_internal_drag(QPoint(140, 120))

        self.assertNotEqual(view._drag_hint._tooltip.pos(), first_pos)

    def test_hint_disappears_when_the_profile_is_dropped(self) -> None:
        view = self._view()
        view.set_drag_hint_text("Меняется только вид списка.")
        view._drag_source = ("profile", "profile:a")
        view._update_internal_drag(QPoint(40, 40))

        view._finish_internal_drag(QPoint(40, 40))

        self.assertFalse(view._drag_hint.is_visible())

    def test_hint_disappears_when_the_cursor_leaves_the_list(self) -> None:
        view = self._view()
        view.set_drag_hint_text("Меняется только вид списка.")
        view._drag_source = ("profile", "profile:a")
        view._update_internal_drag(QPoint(40, 40))

        view._update_internal_drag(QPoint(-50, -50))

        self.assertFalse(view._drag_hint.is_visible())

    def test_list_without_hint_text_shows_nothing(self) -> None:
        view = self._view()
        view._drag_source = ("profile", "profile:a")

        view._update_internal_drag(QPoint(40, 40))

        self.assertIsNone(view._drag_hint)

    def test_profiles_list_explains_the_drag_and_the_order_page_does_not(self) -> None:
        display_list = profiles_list.ProfilesList()
        self.addCleanup(display_list.deleteLater)
        order_list = ProfileOrderList()
        self.addCleanup(order_list.deleteLater)

        self.assertEqual(display_list._view._drag_hint_text, profiles_list.DISPLAY_ORDER_DRAG_HINT)
        self.assertIn("Порядок в пресете остаётся прежним", profiles_list.DISPLAY_ORDER_DRAG_HINT)
        # На странице «Порядок в пресете» перетаскивание меняет сам пресет.
        self.assertEqual(order_list._view._drag_hint_text, "")


class _Runtime:
    """Запоминает запущенного работника и отдаёт его ответ по команде теста."""

    def __init__(self) -> None:
        self.started = []

    def start_qthread_worker(self, *, worker_factory, on_loaded=None, **_kwargs):
        worker = worker_factory(len(self.started) + 1)
        self.started.append((worker, on_loaded))
        return len(self.started), worker

    def answer(self, first_time: bool) -> None:
        _worker, on_loaded = self.started[-1]
        on_loaded(len(self.started), first_time)


class FirstMoveNoticeTests(unittest.TestCase):
    def setUp(self) -> None:
        flag = patch.object(page_module, "_display_order_explained", False)
        flag.start()
        self.addCleanup(flag.stop)

    def _page(self, *, with_settings: bool = True):
        page = PresetSetupPageBase.__new__(PresetSetupPageBase)
        page.window = Mock(return_value="main window")
        page._profile_display_order_notice_runtime = _Runtime()
        page._create_profile_display_order_notice_worker_fn = (
            Mock(return_value="notice worker") if with_settings else None
        )
        return page

    def test_very_first_move_explains_that_the_preset_order_is_the_same(self) -> None:
        page = self._page()

        with patch.object(page_module.InfoBar, "info") as info:
            PresetSetupPageBase._explain_display_order_once(page, "profile:a", "profile:b", "games")
            # Пока настройки не ответили, уведомления нет.
            info.assert_not_called()
            page._profile_display_order_notice_runtime.answer(True)

        page._create_profile_display_order_notice_worker_fn.assert_called_once_with(1, parent=page)
        info.assert_called_once()
        kwargs = info.call_args.kwargs
        self.assertEqual(kwargs["title"], "Порядок в пресете не изменился")
        self.assertIn("только вид этого списка", kwargs["content"])
        self.assertIn("«Порядок в пресете»", kwargs["content"])
        self.assertEqual(kwargs["parent"], "main window")

    def test_notice_already_shown_in_an_earlier_run_is_not_repeated(self) -> None:
        page = self._page()

        with patch.object(page_module.InfoBar, "info") as info:
            PresetSetupPageBase._explain_display_order_once(page, "profile:a")
            page._profile_display_order_notice_runtime.answer(False)

        info.assert_not_called()

    def test_settings_are_asked_once_per_program_run(self) -> None:
        page = self._page()

        with patch.object(page_module.InfoBar, "info"):
            PresetSetupPageBase._explain_display_order_once(page, "profile:a")
            PresetSetupPageBase._explain_display_order_once(page, "profile:b", "games")
            other_page = self._page()
            PresetSetupPageBase._explain_display_order_once(other_page, "profile:c")

        self.assertEqual(len(page._profile_display_order_notice_runtime.started), 1)
        self.assertEqual(other_page._profile_display_order_notice_runtime.started, [])

    def test_page_without_settings_access_explains_on_the_first_move_of_the_run(self) -> None:
        page = self._page(with_settings=False)

        with patch.object(page_module.InfoBar, "info") as info:
            PresetSetupPageBase._explain_display_order_once(page, "profile:a")
            PresetSetupPageBase._explain_display_order_once(page, "profile:b")

        info.assert_called_once()

    def test_every_kind_of_move_in_the_list_leads_to_the_notice(self) -> None:
        import inspect

        wiring = inspect.getsource(PresetSetupPageBase)

        for signal in (
            "profile_move_requested",
            "profile_move_after_requested",
            "profile_move_to_folder_requested",
            "profile_move_to_end_requested",
        ):
            self.assertIn(f"profiles_list.{signal},", wiring)
        self.assertIn("moved.connect(self._explain_display_order_once)", wiring)

    def test_real_page_gets_the_settings_worker(self) -> None:
        from app.feature_facades.profile import ProfileFeature
        from ui.page_deps import presets as deps

        self.assertIn(
            '"create_profile_display_order_notice_worker": profile_feature.create_profile_display_order_notice_worker',
            Path(deps.__file__).read_text(encoding="utf-8"),
        )
        self.assertTrue(callable(ProfileFeature.create_profile_display_order_notice_worker))


class NoticeIsRememberedTests(unittest.TestCase):
    """«Пояснение показано» лежит в настройках программы и переживает перезапуск."""

    def test_notice_is_claimed_exactly_once(self) -> None:
        from profile import commands

        stored = {"shown": False}

        def remember(value: bool = True) -> bool:
            stored["shown"] = bool(value)
            return True

        with (
            patch("settings.store.get_profile_display_order_hint_shown", side_effect=lambda: stored["shown"]),
            patch("settings.store.set_profile_display_order_hint_shown", side_effect=remember) as save,
        ):
            self.assertTrue(commands.claim_display_order_notice())
            self.assertFalse(commands.claim_display_order_notice())
            self.assertFalse(commands.claim_display_order_notice())

        save.assert_called_once_with(True)

    def test_worker_reports_the_claim(self) -> None:
        from profile.profile_setup_loader import ProfileDisplayOrderNoticeWorker

        for first_time in (True, False):
            with self.subTest(first_time=first_time):
                answers = []
                worker = ProfileDisplayOrderNoticeWorker(7, lambda value=first_time: value)
                worker.loaded.connect(lambda request_id, value: answers.append((request_id, value)))

                worker.run()

                self.assertEqual(answers, [(7, first_time)])

    def test_settings_keep_the_flag_and_default_to_not_shown(self) -> None:
        from settings.normalize import normalize_warnings
        from settings.schema import default_warnings

        self.assertFalse(default_warnings()["profile_display_order_hint_shown"])
        self.assertFalse(normalize_warnings({})["profile_display_order_hint_shown"])
        self.assertTrue(
            normalize_warnings({"profile_display_order_hint_shown": True})["profile_display_order_hint_shown"]
        )


if __name__ == "__main__":
    unittest.main()
