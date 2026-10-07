import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from profile.ui.preset_setup_page import PresetSetupPageBase
from profile.ui.profile_setup_page import ProfileSetupPageBase


def _worker_stub(*_args, **_kwargs):
    return None


class _DialogButton:
    def __init__(self, text: str = "", parent=None) -> None:
        self._text = ""
        if text:
            self._text = str(text)
        self.parent = parent
        self._accessible_name = ""
        self._accessible_description = ""
        self._properties: dict[str, object] = {}
        self.hidden = False
        self.clicked = _Signal()

    def hide(self) -> None:
        self.hidden = True

    def setText(self, text: str) -> None:  # noqa: N802
        self._text = str(text)

    def text(self) -> str:
        return self._text

    def click(self) -> None:
        self.clicked.emit()

    def accessibleName(self) -> str:  # noqa: N802
        return self._accessible_name

    def setAccessibleName(self, text: str) -> None:  # noqa: N802
        self._accessible_name = str(text)

    def accessibleDescription(self) -> str:  # noqa: N802
        return self._accessible_description

    def setAccessibleDescription(self, text: str) -> None:  # noqa: N802
        self._accessible_description = str(text)

    def property(self, name: str):  # noqa: A003
        return self._properties.get(name)

    def setProperty(self, name: str, value) -> None:  # noqa: N802
        self._properties[name] = value


class _Signal:
    def __init__(self) -> None:
        self._callbacks = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)

    def emit(self) -> None:
        for callback in list(self._callbacks):
            callback()


class _ButtonLayout:
    def __init__(self) -> None:
        self.widgets = []

    def insertWidget(self, index: int, widget) -> None:  # noqa: N802
        self.widgets.insert(index, widget)


class _MessageBox:
    instances: list["_MessageBox"] = []

    def __init__(self, title: str, body: str, parent=None) -> None:
        self.title = title
        self.body = body
        self.parent = parent
        self.yesButton = _DialogButton()
        self.cancelButton = _DialogButton()
        self.buttonLayout = _ButtonLayout()
        self.exec_called = False
        _MessageBox.instances.append(self)

    def exec(self) -> bool:
        self.exec_called = True
        return False


class ProfileSetupAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        self.app.closeAllWindows()
        self.app.processEvents()

    def _make_page(self) -> ProfileSetupPageBase:
        return ProfileSetupPageBase(
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


    def test_settings_line_edit_buttons_do_not_take_tab_focus(self) -> None:
        page = self._make_page()
        self.addCleanup(page.deleteLater)

        for line_edit in (page._filter_value, page._in_range_value, page._out_range_value):
            line_edit.setText("8")
            buttons = [
                child
                for child in line_edit.findChildren(object)
                if str(getattr(child, "objectName", lambda: "")() or "") == "lineEditButton"
                and hasattr(child, "setFocusPolicy")
            ]

            self.assertTrue(buttons)
            self.assertTrue(all(button.focusPolicy() == Qt.FocusPolicy.NoFocus for button in buttons))



    def test_range_mode_combo_options_are_named_for_screen_reader(self) -> None:
        page = self._make_page()
        self.addCleanup(page.deleteLater)

        menu = page._in_range_mode._create_accessible_combo_menu()

        self.assertEqual(
            menu.view.item(0).data(Qt.ItemDataRole.AccessibleTextRole),
            "Режим in-range: a — всегда, выбран",
        )
        self.assertEqual(
            menu.view.item(1).data(Qt.ItemDataRole.AccessibleTextRole),
            "Режим in-range: x — никогда, не выбран",
        )

    def test_filter_kind_combo_options_are_named_for_screen_reader(self) -> None:
        page = self._make_page()
        self.addCleanup(page.deleteLater)
        create_menu = getattr(page._filter_combo, "_create_accessible_combo_menu", None)
        self.assertIsNotNone(create_menu)

        menu = create_menu()

        self.assertEqual(
            menu.view.item(0).data(Qt.ItemDataRole.AccessibleTextRole),
            "Тип списка profile: Hostlist, выбран",
        )
        self.assertEqual(
            menu.view.item(1).data(Qt.ItemDataRole.AccessibleTextRole),
            "Тип списка profile: IPset, не выбран",
        )

    def test_delete_user_profile_dialog_buttons_are_named_for_screen_reader(self) -> None:
        page = ProfileSetupPageBase.__new__(ProfileSetupPageBase)
        page._profile_key = "template:user:user-1"
        page._payload = SimpleNamespace(item=SimpleNamespace(user_profile_id=""))
        page._request_user_profile_delete = Mock()
        _MessageBox.instances = []

        with patch("profile.ui.profile_setup_page.MessageBox", _MessageBox):
            ProfileSetupPageBase._on_delete_user_profile_clicked(page)

        dialog = _MessageBox.instances[0]
        self.assertEqual(dialog.yesButton.accessibleName(), "Удалить пользовательский profile")
        self.assertIn("будет удалён из библиотеки", dialog.yesButton.accessibleDescription())
        self.assertEqual(dialog.cancelButton.accessibleName(), "Отменить удаление пользовательского profile")
        self.assertTrue(dialog.exec_called)
        page._request_user_profile_delete.assert_not_called()

    def test_delete_profile_from_preset_dialog_buttons_are_named_for_screen_reader(self) -> None:
        page = PresetSetupPageBase.__new__(PresetSetupPageBase)
        page._request_profile_context_action = Mock()
        _MessageBox.instances = []

        with patch("profile.ui.preset_setup_page.MessageBox", _MessageBox):
            PresetSetupPageBase._delete_profile_from_menu(page, "profile-1")

        dialog = _MessageBox.instances[0]
        self.assertEqual(dialog.yesButton.accessibleName(), "Удалить profile из текущего preset")
        self.assertIn("только из текущего preset", dialog.yesButton.accessibleDescription())
        self.assertEqual(dialog.cancelButton.accessibleName(), "Отменить удаление profile из preset")
        self.assertTrue(dialog.exec_called)
        page._request_profile_context_action.assert_not_called()

    def test_delete_user_profile_from_preset_dialog_buttons_are_named_for_screen_reader(self) -> None:
        page = PresetSetupPageBase.__new__(PresetSetupPageBase)
        page._profiles_list = SimpleNamespace(
            profile_item_for_key=lambda _profile_key: SimpleNamespace(user_profile_id="")
        )
        page._request_user_profile_delete = Mock()
        _MessageBox.instances = []

        with patch("profile.ui.preset_setup_page.MessageBox", _MessageBox):
            PresetSetupPageBase._delete_user_profile_from_menu(page, "template:user:user-1")

        dialog = _MessageBox.instances[0]
        self.assertEqual(dialog.yesButton.accessibleName(), "Удалить пользовательский profile")
        self.assertIn("будет удалён из библиотеки", dialog.yesButton.accessibleDescription())
        self.assertEqual(dialog.cancelButton.accessibleName(), "Отменить удаление пользовательского profile")
        self.assertTrue(dialog.exec_called)
        page._request_user_profile_delete.assert_not_called()

    def test_preset_profile_info_dialog_close_button_is_named_for_screen_reader(self) -> None:
        page = PresetSetupPageBase.__new__(PresetSetupPageBase)
        _MessageBox.instances = []

        with patch("profile.ui.preset_setup_page.MessageBox", _MessageBox):
            PresetSetupPageBase._show_profile_info(page)

        dialog = _MessageBox.instances[0]
        self.assertEqual(dialog.yesButton.accessibleName(), "Закрыть справку о настройке пресета")
        self.assertIn("Закрывает справку", dialog.yesButton.accessibleDescription())
        self.assertTrue(dialog.cancelButton.hidden)
        self.assertTrue(dialog.exec_called)

    def test_preset_profile_info_dialog_has_profile_site_button(self) -> None:
        page = PresetSetupPageBase.__new__(PresetSetupPageBase)
        _MessageBox.instances = []
        opened_urls = []

        with (
            patch("profile.ui.preset_setup_page.MessageBox", _MessageBox),
            patch("profile.ui.preset_setup_page.PushButton", _DialogButton, create=True),
            patch(
                "profile.ui.preset_setup_page.QDesktopServices.openUrl",
                side_effect=lambda url: opened_urls.append(url),
            ),
        ):
            PresetSetupPageBase._show_profile_info(page)
            _MessageBox.instances[0].buttonLayout.widgets[0].click()

        dialog = _MessageBox.instances[0]
        self.assertEqual(len(dialog.buttonLayout.widgets), 1)
        site_button = dialog.buttonLayout.widgets[0]
        self.assertEqual(site_button.text(), "Открыть сайт с профилями")
        self.assertEqual(site_button.accessibleName(), "Открыть сайт с профилями")
        self.assertEqual(opened_urls, [QUrl("https://wiki.zapret.moe/Zapret2/filter")])


if __name__ == "__main__":
    unittest.main()
