"""Три вкладки страницы профиля: стратегии, список сайтов и текст профиля.

Каждая вкладка про одно. Вкладку стратегий целиком занимает их список,
свои записи списка стоят раньше встроенных, а на вкладке текста нет ничего,
кроме редактора.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QPlainTextEdit

from profile.state import ProfileListFileEditorState
from profile.strategy_state import ProfileStrategyState
from profile.ui.profile_list_file_tab import SIDE_BY_SIDE_MIN_WIDTH, ProfileListFileTab
from profile.ui.profile_raw_text_tab import ProfileRawTextTab
from profile.ui.profile_setup_page import ProfileSetupPageBase


def _worker_stub(*_args, **_kwargs):
    return None


class _TabsCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        self.app.closeAllWindows()
        self.app.processEvents()

    def _page(self) -> ProfileSetupPageBase:
        page = ProfileSetupPageBase(
            create_profile_setup_load_worker=_worker_stub,
            create_profile_list_file_load_worker=_worker_stub,
            create_profile_list_file_save_worker=_worker_stub,
            create_profile_list_file_validation_worker=_worker_stub,
            create_profile_settings_save_worker=_worker_stub,
            create_profile_raw_text_save_worker=_worker_stub,
            create_profile_enabled_save_worker=_worker_stub,
            create_profile_user_update_worker=_worker_stub,
            create_profile_user_delete_worker=_worker_stub,
            create_profile_strategy_apply_worker=_worker_stub,
            create_profile_strategy_feedback_save_worker=_worker_stub,
            open_profiles=lambda: None,
            open_root=lambda: None,
            on_profile_changed=lambda: None,
        )
        self.addCleanup(page.deleteLater)
        return page

    def _payload(self, *, rating: str = "", favorite: bool = False, strategy_id: str = "tls_fake"):
        return SimpleNamespace(
            item=SimpleNamespace(in_preset=True, enabled=True, strategy_id=strategy_id, strategy_name="General ALT2 1.9.9"),
            current_strategy_state=ProfileStrategyState(rating=rating, favorite=favorite),
        )


class StrategiesTabTests(_TabsCase):
    def test_list_takes_the_whole_tab_and_other_tabs_stay_unbuilt(self) -> None:
        page = self._page()

        # Под списком нет кнопок: оценка и избранное живут в меню стратегии.
        self.assertIs(page._strategy_stack.widget(0), page._strategy_list)
        self.assertFalse(hasattr(page, "_strategy_feedback_bar"))
        self.assertFalse(page._raw_tab_built)
        self.assertFalse(page._editor_tab_built)

    def test_rating_from_the_menu_is_saved_for_the_clicked_strategy(self) -> None:
        page = self._page()
        page._payload = self._payload(strategy_id="tls_fake")
        page._profile_key = "profile:0"
        page._loading = False
        page._request_strategy_feedback_save = Mock()

        # Оценили не ту стратегию, что выбрана для профиля.
        page._strategy_list.strategy_rating_requested.emit("multisplit_1", "notwork")
        page._strategy_list.strategy_favorite_requested.emit("multisplit_1", True)

        self.assertEqual(
            page._request_strategy_feedback_save.call_args_list[0].args,
            ({"strategy_id": "multisplit_1", "rating": "notwork", "favorite": None},),
        )
        self.assertEqual(
            page._request_strategy_feedback_save.call_args_list[1].args,
            ({"strategy_id": "multisplit_1", "rating": None, "favorite": True},),
        )

    def test_nothing_is_saved_while_the_profile_is_loading(self) -> None:
        page = self._page()
        page._payload = self._payload()
        page._profile_key = "profile:0"
        page._loading = True
        page._request_strategy_feedback_save = Mock()

        page._strategy_list.strategy_rating_requested.emit("tls_fake", "work")

        page._request_strategy_feedback_save.assert_not_called()


class ListFileTabTests(_TabsCase):
    def _tab(self, width: int) -> ProfileListFileTab:
        tab = ProfileListFileTab()
        self.addCleanup(tab.deleteLater)
        tab.resize(width, 600)
        tab.show()
        self.app.processEvents()
        return tab

    def test_wide_tab_puts_user_entries_and_base_side_by_side(self) -> None:
        tab = self._tab(SIDE_BY_SIDE_MIN_WIDTH + 200)

        self.assertTrue(tab.side_by_side())
        self.assertEqual(tab.user_pane.geometry().top(), tab.base_pane.geometry().top())
        self.assertLess(tab.user_pane.geometry().left(), tab.base_pane.geometry().left())
        # Своим записям отдано больше места, чем базе.
        self.assertGreater(tab.user_pane.width(), tab.base_pane.width())

    def test_narrow_tab_stacks_them_with_user_entries_first(self) -> None:
        tab = self._tab(SIDE_BY_SIDE_MIN_WIDTH - 200)

        self.assertFalse(tab.side_by_side())
        self.assertLess(tab.user_pane.geometry().top(), tab.base_pane.geometry().top())

    def test_layout_follows_the_width(self) -> None:
        tab = self._tab(SIDE_BY_SIDE_MIN_WIDTH - 200)

        tab.resize(SIDE_BY_SIDE_MIN_WIDTH + 200, 600)
        self.app.processEvents()

        self.assertTrue(tab.side_by_side())

    def test_only_user_entries_can_be_edited(self) -> None:
        tab = self._tab(900)

        self.assertFalse(tab.user_text.isReadOnly())
        self.assertTrue(tab.base_text.isReadOnly())

    def test_page_names_both_fields_by_their_files(self) -> None:
        page = self._page()
        page._ensure_editor_tab_built()

        page._apply_list_file_editor_state(
            ProfileListFileEditorState(
                kind="hostlist",
                display_path="lists/googlevideo.txt",
                base_text="googlevideo.com",
                user_text="example.com",
                base_display_path="lists/base/googlevideo.txt",
                user_display_path="lists/user/googlevideo.txt",
                editable=True,
                base_entries_count=1,
                user_entries_count=1,
            )
        )

        self.assertEqual(page._list_file_user_title.text(), "Ваши записи: lists/user/googlevideo.txt")
        self.assertEqual(
            page._list_file_base_title.text(),
            "Встроенные записи (только просмотр): lists/base/googlevideo.txt",
        )
        self.assertFalse(page._list_file_base_pane.isHidden())
        self.assertEqual(page._list_file_text.toPlainText(), "example.com")
        # Отдельной строки с именем файла над полями нет: оно уже в подписях.
        self.assertFalse(hasattr(page, "_list_file_title"))

    def test_list_that_cannot_be_edited_is_shown_without_the_base_field(self) -> None:
        page = self._page()
        page._ensure_editor_tab_built()

        page._apply_list_file_editor_state(
            ProfileListFileEditorState(
                kind="hostlist",
                display_path="lists/service.txt",
                text="service.example",
                editable=False,
                error_text="Это служебный список.",
            )
        )

        self.assertTrue(page._list_file_base_pane.isHidden())
        self.assertTrue(page._list_file_text.isReadOnly())
        self.assertEqual(page._list_file_text.toPlainText(), "service.example")


class RawTextTabTests(_TabsCase):
    def test_tab_holds_one_editor_and_the_save_button(self) -> None:
        tab = ProfileRawTextTab()
        self.addCleanup(tab.deleteLater)

        self.assertEqual(tab.findChildren(QPlainTextEdit), [tab.text])
        self.assertEqual(tab.save_button.text(), "Сохранить текст профиля")
        # Редактор растягивается на всю высоту вкладки.
        self.assertGreater(tab.text.maximumHeight(), 10000)


    def test_page_fills_the_editor_from_the_profile(self) -> None:
        page = self._page()
        page._payload = SimpleNamespace(
            item=SimpleNamespace(in_preset=True, enabled=True, strategy_id="tls_fake", strategy_name="TLS"),
            raw_profile_text="--filter-tcp=443\n--lua-desync=fake",
            preset_preamble_text="",
        )

        page._switch_strategy_tab(2)

        self.assertTrue(page._raw_tab_built)
        self.assertEqual(page._raw_profile_text.toPlainText(), "--filter-tcp=443\n--lua-desync=fake")
        self.assertFalse(page._raw_profile_text.isReadOnly())
        self.assertTrue(page._raw_profile_save_button.isEnabled())
        self.assertEqual(page._strategy_stack.currentIndex(), 2)


if __name__ == "__main__":
    unittest.main()
