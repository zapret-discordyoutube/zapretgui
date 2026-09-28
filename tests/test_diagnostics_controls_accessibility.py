from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    IndeterminateProgressBar,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
)

from diagnostics.ui.build import build_connection_controls, build_connection_log_viewer
from diagnostics.ui.components import ConnectionResultsPanel
from diagnostics.ui.page import ConnectionTestPage
from diagnostics.ui.runtime_helpers import (
    apply_connection_language,
    apply_interaction_state,
    refresh_test_combo_items,
    set_connection_status,
    start_connection_test,
)


def _controls(layout, progress_bar_cls=ProgressBar):
    return build_connection_controls(
        container_layout=layout,
        tr_fn=lambda _key, default: default,
        combo_cls=ComboBox,
        body_label_cls=BodyLabel,
        caption_label_cls=CaptionLabel,
        progress_bar_cls=progress_bar_cls,
        primary_button_cls=PrimaryPushButton,
        push_button_cls=PushButton,
        on_start=lambda: None,
        on_stop=lambda: None,
    )


def _log_viewer(layout):
    return build_connection_log_viewer(
        container_layout=layout,
        tr_fn=lambda _key, default: default,
        caption_label_cls=CaptionLabel,
        push_button_cls=PushButton,
        on_toggle=lambda: None,
        on_support=lambda: None,
    )


class DiagnosticsControlsAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_connection_action_buttons_have_screen_reader_names(self) -> None:
        parent = QWidget()
        layout = QVBoxLayout(parent)

        widgets = _controls(layout)
        log = _log_viewer(layout)

        self.assertEqual(widgets.start_btn.text(), "Проверить")
        self.assertEqual(widgets.start_btn.accessibleName(), "Запустить диагностический тест")
        self.assertEqual(
            widgets.start_btn.property("screenReaderStateText"),
            "Запустить диагностический тест",
        )
        self.assertIn("Discord и YouTube", widgets.start_btn.accessibleDescription())
        self.assertEqual(widgets.stop_btn.accessibleName(), "Остановить диагностический тест")
        self.assertIn("Останавливает текущий тест", widgets.stop_btn.accessibleDescription())
        self.assertTrue(widgets.stop_btn.isHidden())
        self.assertEqual(log.send_log_btn.accessibleName(), "Подготовить обращение с логами")
        self.assertIn("архив логов", log.send_log_btn.accessibleDescription())
        self.assertEqual(log.toggle_btn.accessibleName(), "Показать подробный отчёт")

    def test_selector_label_and_ready_status_are_compact(self) -> None:
        parent = QWidget()
        layout = QVBoxLayout(parent)

        widgets = _controls(layout)

        self.assertEqual(
            widgets.test_select_label.property("screenReaderStateText"),
            "Поле диагностики: Что проверить:",
        )
        self.assertIn("как браузер", widgets.status_label.text())
        # Карточка без шапки: во вкладке BlockCheck лишние заголовки съедали место.
        self.assertIsNone(widgets.controls_card._title_label)

    def test_test_combo_name_includes_selected_scenario(self) -> None:
        combo = ComboBox()

        refresh_test_combo_items(combo=combo, language="ru")

        self.assertEqual(combo.accessibleName(), "Сценарий диагностики, выбрано: Discord и YouTube")
        self.assertIn("Discord и YouTube", combo.accessibleDescription())
        self.assertIn("стрелками вверх и вниз", combo.accessibleDescription())

        combo.setCurrentIndex(1)

        self.assertEqual(combo.accessibleName(), "Сценарий диагностики, выбрано: Только Discord")

    def test_test_combo_menu_items_are_named_for_screen_reader(self) -> None:
        combo = ComboBox()

        refresh_test_combo_items(combo=combo, language="ru")
        create_menu = getattr(combo, "_create_accessible_combo_menu", None)
        self.assertIsNotNone(create_menu)
        menu = create_menu()

        self.assertEqual(
            menu.view.item(0).data(Qt.ItemDataRole.AccessibleTextRole),
            "Сценарий диагностики: Discord и YouTube, выбран",
        )
        self.assertEqual(
            menu.view.item(1).data(Qt.ItemDataRole.AccessibleTextRole),
            "Сценарий диагностики: Только Discord, не выбран",
        )

    def test_status_text_is_exposed_as_screen_reader_state(self) -> None:
        status_label = CaptionLabel()

        set_connection_status(status_label=status_label, text="🔄 Проверяем…", status="info")

        self.assertEqual(status_label.text(), "🔄 Проверяем…")
        self.assertEqual(status_label.accessibleName(), "Статус диагностики: Проверяем…")

    def test_result_log_is_hidden_until_requested_and_named(self) -> None:
        parent = QWidget()
        layout = QVBoxLayout(parent)

        widgets = _log_viewer(layout)

        self.assertTrue(widgets.result_text.isHidden())
        self.assertEqual(
            widgets.result_text.accessibleName(),
            "Результат диагностики соединений: диагностика ещё не запускалась",
        )
        self.assertIn("ход и итог проверки Discord и YouTube", widgets.result_text.accessibleDescription())

    def test_start_connection_test_updates_result_screen_reader_state(self) -> None:
        from ui.accessibility import set_state_text

        parent = QWidget()
        layout = QVBoxLayout(parent)
        widgets = _log_viewer(layout)
        combo = ComboBox()
        refresh_test_combo_items(combo=combo, language="ru")
        set_state_text(widgets.result_text, "Старый результат диагностики")

        state = start_connection_test(
            is_testing=False,
            ui_language="ru",
            test_combo=combo,
            result_text=widgets.result_text,
            apply_interaction_state_callback=lambda **_kwargs: None,
            set_status_callback=lambda _text, _status: None,
        )

        expected = "Результат диагностики соединений: Запуск тестирования: Discord и YouTube"
        self.assertEqual(widgets.result_text.accessibleName(), expected)
        self.assertEqual(state["test_type"], "all")

    def test_appended_result_line_updates_result_screen_reader_state(self) -> None:
        from ui.accessibility import set_state_text

        parent = QWidget()
        layout = QVBoxLayout(parent)
        widgets = _log_viewer(layout)
        page = ConnectionTestPage.__new__(ConnectionTestPage)
        page.result_text = widgets.result_text
        set_state_text(widgets.result_text, "Старый результат диагностики")

        ConnectionTestPage._append(page, "✅ Discord открывается")

        expected = "Результат диагностики соединений: Discord открывается"
        self.assertEqual(widgets.result_text.accessibleName(), expected)

    def test_interaction_state_shows_stop_only_while_running(self) -> None:
        parent = QWidget()
        layout = QVBoxLayout(parent)
        widgets = _controls(layout, progress_bar_cls=IndeterminateProgressBar)
        log = _log_viewer(layout)

        self.assertEqual(widgets.progress_bar.accessibleName(), "Ход диагностики соединений")

        def _apply(running: bool) -> None:
            apply_interaction_state(
                start_btn=widgets.start_btn,
                stop_btn=widgets.stop_btn,
                test_combo=widgets.test_combo,
                send_log_btn=log.send_log_btn,
                progress_bar=widgets.progress_bar,
                start_enabled=not running,
                stop_enabled=running,
                combo_enabled=not running,
                send_log_enabled=not running,
                progress_visible=running,
            )

        _apply(True)
        self.assertEqual(widgets.progress_bar.accessibleName(), "Ход диагностики соединений: выполняется")
        self.assertEqual(widgets.start_btn.accessibleName(), "Запустить диагностический тест, недоступно")
        self.assertEqual(widgets.stop_btn.accessibleName(), "Остановить диагностический тест, доступно")
        self.assertFalse(widgets.stop_btn.isHidden())
        self.assertEqual(log.send_log_btn.accessibleName(), "Подготовить обращение с логами, недоступно")

        _apply(False)
        self.assertEqual(widgets.start_btn.accessibleName(), "Запустить диагностический тест, доступно")
        self.assertTrue(widgets.stop_btn.isHidden())
        self.assertEqual(log.send_log_btn.accessibleName(), "Подготовить обращение с логами, доступно")

    def test_connection_language_refresh_keeps_screen_reader_descriptions(self) -> None:
        parent = QWidget()
        layout = QVBoxLayout(parent)
        widgets = _controls(layout)
        log = _log_viewer(layout)

        apply_connection_language(
            language="ru",
            test_select_label=widgets.test_select_label,
            log_hint_label=log.hint_label,
            refresh_test_combo_items_callback=lambda: None,
            start_btn=widgets.start_btn,
            stop_btn=widgets.stop_btn,
            toggle_log_btn=log.toggle_btn,
            log_visible=True,
            send_log_btn=log.send_log_btn,
        )

        self.assertEqual(widgets.start_btn.accessibleName(), "Запустить диагностический тест")
        self.assertIn("Останавливает текущий тест", widgets.stop_btn.accessibleDescription())
        self.assertEqual(log.send_log_btn.accessibleName(), "Подготовить обращение с логами")
        self.assertEqual(log.toggle_btn.text(), "Скрыть отчёт")


class ResultsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_pending_hides_unselected_service_and_report_fills_cards(self) -> None:
        panel = ConnectionResultsPanel()

        panel.set_pending("youtube")

        self.assertTrue(panel.cards["discord"].isHidden())
        self.assertEqual(panel.cards["youtube"].level, "pending")

        panel.show_report(
            {
                "services": [
                    {
                        "key": "youtube",
                        "level": "warn",
                        "headline": "YouTube открывается",
                        "advice": ["Включите DNS с шифрованием (DoH)"],
                        "dns_note": "DNS подменяет адрес www.youtube.com",
                        "targets": [
                            {"host": "www.youtube.com", "purpose": "сайт", "ok": True, "text": "открывается (300 мс)"},
                        ],
                    }
                ]
            }
        )

        card = panel.cards["youtube"]
        self.assertEqual(card.level, "warn")
        self.assertEqual(card.headline(), "YouTube открывается")
        self.assertEqual(
            card.property("screenReaderStateText"),
            "YouTube: есть проблемы. YouTube открывается",
        )
        self.assertFalse(card._dns_notice.isHidden())

    def test_card_reserves_height_for_wrapped_text(self) -> None:
        """Во вкладках BlockCheck текст с переносом не передавал свою высоту, и строки наезжали."""
        panel = ConnectionResultsPanel()
        panel.resize(420, 800)
        panel.show()
        long_note = "DNS подменяет адрес www.youtube.com — провайдер перехватывает обычные DNS-запросы. " * 3
        panel.show_report(
            {
                "services": [
                    {
                        "key": "youtube",
                        "level": "warn",
                        "headline": "YouTube открывается",
                        "advice": ["Включите DNS с шифрованием (DoH) в разделе «Настройка DNS»"],
                        "dns_note": long_note,
                        "targets": [{"purpose": "сайт", "ok": True, "short": "открывается"}] * 3,
                    }
                ]
            }
        )
        QApplication.processEvents()
        card = panel.cards["youtube"]
        needed = card.layout().totalHeightForWidth(card.width())

        self.assertGreater(needed, 0)
        self.assertEqual(card.minimumHeight(), needed)

    def test_unfinished_cards_do_not_stay_pending(self) -> None:
        panel = ConnectionResultsPanel()
        panel.set_pending("all")

        panel.mark_unfinished("Проверка остановлена.")

        self.assertEqual(panel.cards["discord"].level, "unknown")
        self.assertEqual(panel.cards["discord"].headline(), "Проверка остановлена.")


if __name__ == "__main__":
    unittest.main()
