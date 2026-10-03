from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QIcon, QKeyEvent
from PyQt6.QtWidgets import QApplication, QWidget
from qfluentwidgets import (
    CaptionLabel,
    IndeterminateProgressBar,
    PushSettingCard,
    StrongBodyLabel,
    TransparentPushButton,
)


class _ButtonTarget:
    def __init__(self) -> None:
        self._text = ""
        self._accessible_name = ""
        self._accessible_description = ""
        self._properties = {}
        self.fixed_width = None

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:  # noqa: N802
        self._text = str(text)

    def accessibleName(self) -> str:  # noqa: N802
        return self._accessible_name

    def setAccessibleName(self, text: str) -> None:  # noqa: N802
        self._accessible_name = str(text)

    def accessibleDescription(self) -> str:  # noqa: N802
        return self._accessible_description

    def setAccessibleDescription(self, text: str) -> None:  # noqa: N802
        self._accessible_description = str(text)

    def setIcon(self, _icon) -> None:  # noqa: N802
        pass

    def setMinimumWidth(self, _width: int) -> None:  # noqa: N802
        pass

    def setFixedWidth(self, width: int) -> None:  # noqa: N802
        self.fixed_width = int(width)

    def property(self, name: str) -> object:  # noqa: A003
        return self._properties.get(name)

    def setProperty(self, name: str, value: object) -> None:  # noqa: N802
        self._properties[name] = value


class _TitleLabel:
    def __init__(self) -> None:
        self.text = ""

    def setText(self, text: str) -> None:  # noqa: N802
        self.text = str(text)


class _CardTarget:
    def __init__(self) -> None:
        self.titleLabel = _TitleLabel()
        self.button = _ButtonTarget()

    def setTitle(self, text: str) -> None:  # noqa: N802
        self.title = str(text)

    def setContent(self, text: str) -> None:  # noqa: N802
        self.content = str(text)


class _SignalTarget:
    def __init__(self) -> None:
        self.callback = None

    def connect(self, callback) -> None:
        self.callback = callback


class _PushSettingCardTarget(_CardTarget):
    def __init__(self, button_text, _icon, title_text, content_text, _parent=None) -> None:
        super().__init__()
        self.button.setText(button_text)
        self.title = str(title_text)
        self.content = str(content_text or "")
        self.clicked = _SignalTarget()

    def setProperty(self, _name: str, _value: object) -> None:  # noqa: N802
        pass

    def setIconSize(self, _width: int, _height: int) -> None:  # noqa: N802
        pass


class _ToggleTarget:
    def set_texts(self, _title: str, _description: str) -> None:
        pass


class _TileTarget:
    """Плитка быстрого действия: запоминает, какие тексты ей поставили."""

    def __init__(self) -> None:
        self.title = ""
        self.content = ""
        self.accessible_name = ""

    def set_texts(self, title: str, content: str = "", *, accessible_name: str = "") -> None:
        self.title = str(title)
        self.content = str(content)
        self.accessible_name = str(accessible_name)


def _language_refresh_kwargs() -> dict[str, object]:
    kwargs = {
        "language": "ru",
        "program_settings_card": _CardTarget(),
        "auto_dpi_toggle": _ToggleTarget(),
        "gui_autostart_toggle": _ToggleTarget(),
        "tray_close_mode_combo": _ToggleTarget(),
        "defender_toggle": _ToggleTarget(),
        "max_block_toggle": _ToggleTarget(),
        "state_media_block_toggle": _ToggleTarget(),
        "tour_card": _TileTarget(),
        "test_card": _TileTarget(),
        "internet_cleanup_card": _TileTarget(),
        "folder_card": _TileTarget(),
        "docs_card": _TileTarget(),
        "additional_settings_card": _CardTarget(),
        "additional_settings_notice": _TitleLabel(),
        "discord_restart_toggle": _ToggleTarget(),
        "wssize_toggle": _ToggleTarget(),
        "debug_log_toggle": _ToggleTarget(),
    }
    return kwargs


class ControlAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _build_status_section(self, *, on_toggle=None, on_close=None):
        from presets.ui.control.shared_builders import build_mode_status_section_common

        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        widgets = build_mode_status_section_common(
            tr_fn=lambda _key, default: default,
            strong_body_label_cls=StrongBodyLabel,
            caption_label_cls=CaptionLabel,
            indeterminate_progress_bar_cls=IndeterminateProgressBar,
            close_button_cls=TransparentPushButton,
            checking_key="checking",
            checking_default="Проверка состояния",
            detecting_key="detecting",
            detecting_default="Определяем текущий статус",
            on_toggle=on_toggle or (lambda: None),
            on_close=on_close or (lambda: None),
            parent=parent,
        )
        widgets.card.setParent(parent)
        return widgets

    def test_status_card_controls_have_screen_reader_names_and_descriptions(self) -> None:
        widgets = self._build_status_section()

        self.assertEqual(widgets.close_btn.accessibleName(), "Закрыть программу")
        self.assertEqual(widgets.close_btn.property("screenReaderStateText"), "Закрыть программу")
        self.assertIn("закрыть программу", widgets.close_btn.accessibleDescription())
        self.assertFalse(widgets.close_btn.isVisibleTo(widgets.card))
        self.assertIn("запустить или остановить", widgets.status_dot.accessibleDescription())
        self.assertEqual(widgets.progress_bar.accessibleName(), "Ход запуска Zapret: не выполняется")
        self.assertEqual(
            widgets.progress_bar.property("screenReaderStateText"),
            "Ход запуска Zapret: не выполняется",
        )
        self.assertIn("Показывает", widgets.progress_bar.accessibleDescription())
        self.assertEqual(widgets.loading_label.accessibleName(), "Статус запуска Zapret: нет активного запуска")
        self.assertEqual(
            widgets.loading_label.property("screenReaderStateText"),
            "Статус запуска Zapret: нет активного запуска",
        )

    def test_status_dot_switch_works_from_keyboard_and_respects_lock(self) -> None:
        toggled: list[bool] = []
        widgets = self._build_status_section(on_toggle=lambda: toggled.append(True))
        dot = widgets.status_dot

        self.assertTrue(dot.is_click_enabled())
        self.assertEqual(dot.focusPolicy(), Qt.FocusPolicy.StrongFocus)
        QApplication.sendEvent(dot, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
        self.assertEqual(toggled, [True])

        dot.set_click_locked(True)
        dot.click()
        dot.set_click_locked(False)
        dot.set_click_enabled(False)
        dot.click()
        self.assertEqual(toggled, [True])

        dot.set_click_enabled(True)
        dot.click()
        self.assertEqual(toggled, [True, True])

    def test_status_close_button_calls_close_action(self) -> None:
        closed: list[bool] = []
        widgets = self._build_status_section(on_close=lambda: closed.append(True))

        widgets.close_btn.click()

        self.assertEqual(closed, [True])

    def test_status_dot_has_initial_screen_reader_state(self) -> None:
        status_dot = self._build_status_section().status_dot

        self.assertEqual(status_dot.accessibleName(), "Индикатор состояния Zapret: состояние пока не загружено")
        self.assertEqual(
            status_dot.property("screenReaderStateText"),
            "Индикатор состояния Zapret: состояние пока не загружено",
        )

    def test_status_section_has_initial_screen_reader_text(self) -> None:
        widgets = self._build_status_section()

        self.assertEqual(widgets.card.accessibleName(), "Проверка состояния: Определяем текущий статус")
        self.assertEqual(
            widgets.card.property("screenReaderStateText"),
            "Проверка состояния: Определяем текущий статус",
        )
        self.assertEqual(widgets.status_title.accessibleName(), "Статус Zapret: Проверка состояния")
        self.assertEqual(widgets.status_desc.accessibleName(), "Описание состояния Zapret: Определяем текущий статус")

    def test_last_status_message_dot_has_initial_screen_reader_state(self) -> None:
        from presets.ui.control.shared_builders import build_last_status_message_card_common

        widgets = build_last_status_message_card_common(
            tr_fn=lambda _key, default: default,
            strong_body_label_cls=StrongBodyLabel,
            caption_label_cls=CaptionLabel,
        )

        self.assertEqual(widgets.dot.accessibleName(), "Индикатор последнего сообщения: пока нет новых сообщений")
        self.assertEqual(
            widgets.dot.property("screenReaderStateText"),
            "Индикатор последнего сообщения: пока нет новых сообщений",
        )

    def test_last_status_message_card_has_initial_screen_reader_text(self) -> None:
        from presets.ui.control.shared_builders import build_last_status_message_card_common

        widgets = build_last_status_message_card_common(
            tr_fn=lambda _key, default: default,
            strong_body_label_cls=StrongBodyLabel,
            caption_label_cls=CaptionLabel,
        )

        self.assertEqual(widgets.card.accessibleName(), "Последнее сообщение: Пока нет новых сообщений")
        self.assertEqual(
            widgets.card.property("screenReaderStateText"),
            "Последнее сообщение: Пока нет новых сообщений",
        )
        self.assertEqual(widgets.title_label.accessibleName(), "Раздел статуса Zapret: Последнее сообщение")
        self.assertEqual(widgets.message_label.accessibleName(), "Последнее сообщение Zapret: Пока нет новых сообщений")

    def test_push_setting_card_button_has_specific_screen_reader_name(self) -> None:
        from presets.ui.control.shared_builders import ACTION_CARD_BUTTON_WIDTH, build_push_setting_card_common

        card = build_push_setting_card_common(
            push_setting_card_cls=_PushSettingCardTarget,
            button_text="Открыть",
            icon=QIcon(),
            title_text="Тест соединения",
            content_text="Проверить доступность сети и состояние обхода",
            on_click=lambda: None,
        )

        self.assertEqual(card.button.accessibleName(), "Открыть тест соединения")
        self.assertEqual(card.button.text(), "  Открыть")
        self.assertEqual(card.button.fixed_width, ACTION_CARD_BUTTON_WIDTH)
        self.assertIn("Проверить доступность сети", card.button.accessibleDescription())
        self.assertEqual(card.button.property("screenReaderStateText"), "Открыть тест соединения")

    def test_push_setting_card_itself_works_from_keyboard(self) -> None:
        from presets.ui.control.shared_builders import build_push_setting_card_common

        opened: list[bool] = []
        card = build_push_setting_card_common(
            push_setting_card_cls=PushSettingCard,
            button_text="Открыть",
            icon=QIcon(),
            title_text="Тест соединения",
            content_text="Проверить доступность сети и состояние обхода",
            on_click=lambda: opened.append(True),
        )

        self.assertEqual(card.accessibleName(), "Открыть тест соединения")
        self.assertEqual(card.property("screenReaderStateText"), "Открыть тест соединения")
        self.assertIn("Проверить доступность сети", card.accessibleDescription())
        self.assertEqual(card.focusPolicy(), Qt.FocusPolicy.StrongFocus)

        card.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Return,
                Qt.KeyboardModifier.NoModifier,
            )
        )

        self.assertEqual(opened, [True])

    def test_winws1_language_refresh_updates_control_button_screen_reader_names(self) -> None:
        from presets.ui.control.zapret1.runtime_helpers import apply_winws1_pages_language

        close_btn = _ButtonTarget()

        apply_winws1_pages_language(
            **_language_refresh_kwargs(),
            close_btn=close_btn,
            refresh_preset_name=lambda: None,
            get_current_dpi_runtime_state=lambda: ("stopped", ""),
            update_status=lambda _phase, _last_error: None,
        )

        self.assertEqual(close_btn.accessibleName(), "Закрыть программу")
        self.assertIn("закрыть программу", close_btn.accessibleDescription())

    def test_winws1_language_refresh_updates_quick_action_tiles(self) -> None:
        from presets.ui.control.zapret1.runtime_helpers import apply_winws1_pages_language

        kwargs = _language_refresh_kwargs()
        apply_winws1_pages_language(
            **kwargs,
            close_btn=_ButtonTarget(),
            refresh_preset_name=lambda: None,
            get_current_dpi_runtime_state=lambda: ("stopped", ""),
            update_status=lambda _phase, _last_error: None,
        )

        self.assertEqual(kwargs["tour_card"].accessible_name, "Показать обучающий тур")
        self.assertEqual(kwargs["test_card"].accessible_name, "Открыть тест соединения")
        self.assertEqual(kwargs["internet_cleanup_card"].accessible_name, "Сбросить сеть Windows")
        self.assertEqual(kwargs["folder_card"].accessible_name, "Открыть папку программы")
        self.assertEqual(kwargs["docs_card"].accessible_name, "Открыть документацию")
        self.assertEqual(kwargs["test_card"].title, "Тест соединения")
        self.assertEqual(kwargs["internet_cleanup_card"].title, "Сбросить сеть Windows")
        self.assertIn("перезагрузка", kwargs["internet_cleanup_card"].content)
        self.assertEqual(kwargs["folder_card"].title, "Открыть папку")
        self.assertEqual(kwargs["docs_card"].title, "Документация")

    def test_winws2_language_refresh_updates_control_button_screen_reader_names(self) -> None:
        from presets.ui.control.zapret2.runtime_helpers import apply_profile_language

        close_btn = _ButtonTarget()

        apply_profile_language(
            **_language_refresh_kwargs(),
            close_btn=close_btn,
            fakes_card=None,
        )

        self.assertEqual(close_btn.accessibleName(), "Закрыть программу")
        self.assertIn("закрыть программу", close_btn.accessibleDescription())

    def test_winws2_language_refresh_updates_quick_action_tiles(self) -> None:
        from presets.ui.control.zapret2.runtime_helpers import apply_profile_language

        kwargs = _language_refresh_kwargs()
        apply_profile_language(
            **kwargs,
            close_btn=_ButtonTarget(),
            fakes_card=None,
        )

        self.assertEqual(kwargs["tour_card"].accessible_name, "Показать обучающий тур")
        self.assertEqual(kwargs["test_card"].accessible_name, "Открыть тест соединения")
        self.assertEqual(kwargs["internet_cleanup_card"].accessible_name, "Сбросить сеть Windows")
        self.assertEqual(kwargs["folder_card"].accessible_name, "Открыть папку программы")
        self.assertEqual(kwargs["docs_card"].accessible_name, "Открыть документацию")
        self.assertEqual(kwargs["test_card"].title, "Тест соединения")
        self.assertEqual(kwargs["internet_cleanup_card"].title, "Сбросить сеть Windows")
        self.assertIn("перезагрузка", kwargs["internet_cleanup_card"].content)
        self.assertEqual(kwargs["folder_card"].title, "Открыть папку")
        self.assertEqual(kwargs["docs_card"].title, "Документация")


if __name__ == "__main__":
    unittest.main()
