from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


class _TextTarget:
    def __init__(self) -> None:
        self.text = ""
        self.accessible_name = ""
        self.accessible_description = ""
        self.properties: dict[str, str] = {}

    def setText(self, text: str) -> None:  # noqa: N802
        self.text = text

    def accessibleName(self) -> str:  # noqa: N802
        return self.accessible_name

    def setAccessibleName(self, text: str) -> None:  # noqa: N802
        self.accessible_name = str(text)

    def accessibleDescription(self) -> str:  # noqa: N802
        return self.accessible_description

    def setAccessibleDescription(self, text: str) -> None:  # noqa: N802
        self.accessible_description = str(text)

    def property(self, name: str) -> object:
        return self.properties.get(name)

    def setProperty(self, name: str, value: object) -> None:  # noqa: N802
        self.properties[str(name)] = str(value)


class _VisibleTarget:
    def __init__(self) -> None:
        self.visible = None

    def setVisible(self, visible: bool) -> None:  # noqa: N802
        self.visible = bool(visible)


class _StatusDot:
    def __init__(self) -> None:
        self.color = ""
        self.started = 0
        self.stopped = 0
        self.accessible_name = ""
        self.properties: dict[str, str] = {}

    def set_color(self, color: str) -> None:
        self.color = color

    def start_pulse(self) -> None:
        self.started += 1

    def stop_pulse(self) -> None:
        self.stopped += 1

    def accessibleName(self) -> str:  # noqa: N802
        return self.accessible_name

    def setAccessibleName(self, text: str) -> None:  # noqa: N802
        self.accessible_name = str(text)

    def setProperty(self, name: str, value: str) -> None:  # noqa: N802
        self.properties[str(name)] = str(value)


class _ToggleTarget:
    def __init__(self, checked: bool) -> None:
        self.checked = bool(checked)
        self.calls: list[tuple[bool, bool]] = []

    def isChecked(self) -> bool:  # noqa: N802
        return self.checked

    def setChecked(self, checked: bool, block_signals: bool = False) -> None:  # noqa: N802
        self.calls.append((bool(checked), bool(block_signals)))
        self.checked = bool(checked)


class _WidgetStateTarget:
    def __init__(self, *, text: str = "", visible: bool = True, enabled: bool = True) -> None:
        self._text = text
        self._hidden = not bool(visible)
        self._enabled = bool(enabled)
        self.text_calls: list[str] = []
        self.visible_calls: list[bool] = []
        self.enabled_calls: list[bool] = []

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:  # noqa: N802
        self.text_calls.append(str(text))
        self._text = str(text)

    def isHidden(self) -> bool:  # noqa: N802
        return self._hidden

    def setVisible(self, visible: bool) -> None:  # noqa: N802
        self.visible_calls.append(bool(visible))
        self._hidden = not bool(visible)

    def isEnabled(self) -> bool:  # noqa: N802
        return self._enabled

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        self.enabled_calls.append(bool(enabled))
        self._enabled = bool(enabled)


class _ProgressTarget:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0
        self.accessible_name = ""
        self.properties: dict[str, str] = {}

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def accessibleName(self) -> str:  # noqa: N802
        return self.accessible_name

    def setAccessibleName(self, text: str) -> None:  # noqa: N802
        self.accessible_name = str(text)

    def property(self, name: str) -> object:
        return self.properties.get(name)

    def setProperty(self, name: str, value: object) -> None:  # noqa: N802
        self.properties[str(name)] = str(value)


class ControlStatusDotPulseTests(unittest.TestCase):
    def _apply_plan(self, *, pulsing: bool) -> _StatusDot:
        from presets.ui.control.control_page_runtime_shared import apply_status_plan

        dot = _StatusDot()
        apply_status_plan(
            SimpleNamespace(
                phase="running",
                title="Zapret работает",
                description="Обход блокировок активен",
                dot_color="#6ccb5f",
                pulsing=pulsing,
                clickable=True,
                action_name="Остановить Zapret",
                show_close=True,
            ),
            status_title=_TextTarget(),
            status_desc=_TextTarget(),
            status_dot=dot,
            close_btn=_VisibleTarget(),
        )
        return dot

    def test_apply_status_plan_starts_pulse_when_plan_requests_it(self) -> None:
        dot = self._apply_plan(pulsing=True)

        self.assertEqual(dot.started, 1)
        self.assertEqual(dot.stopped, 0)

    def test_apply_status_plan_stops_pulse_when_plan_is_static(self) -> None:
        dot = self._apply_plan(pulsing=False)

        self.assertEqual(dot.started, 0)
        self.assertEqual(dot.stopped, 1)

    def test_apply_status_plan_sets_screen_reader_status_text(self) -> None:
        from presets.ui.control.control_page_runtime_shared import apply_status_plan

        status_title = _TextTarget()
        status_desc = _TextTarget()

        apply_status_plan(
            SimpleNamespace(
                phase="failed",
                title="Ошибка запуска",
                description="Не удалось запустить процесс обхода блокировок",
                dot_color="#f5c04d",
                pulsing=False,
                clickable=True,
                action_name="Запустить Zapret",
                show_close=False,
            ),
            status_title=status_title,
            status_desc=status_desc,
            status_dot=_StatusDot(),
            close_btn=_VisibleTarget(),
        )

        self.assertEqual(status_title.accessible_name, "Статус Zapret: Ошибка запуска")
        self.assertEqual(
            status_title.properties.get("screenReaderStateText"),
            "Статус Zapret: Ошибка запуска",
        )
        self.assertEqual(status_title.accessible_description, "Не удалось запустить процесс обхода блокировок")
        self.assertEqual(
            status_desc.accessible_name,
            "Описание состояния Zapret: Не удалось запустить процесс обхода блокировок",
        )
        self.assertEqual(
            status_desc.properties.get("screenReaderStateText"),
            "Описание состояния Zapret: Не удалось запустить процесс обхода блокировок",
        )

    def test_apply_status_plan_sets_screen_reader_dot_state_text(self) -> None:
        from presets.ui.control.control_page_runtime_shared import apply_status_plan

        status_dot = _StatusDot()

        apply_status_plan(
            SimpleNamespace(
                phase="failed",
                title="Ошибка запуска",
                description="Не удалось запустить процесс обхода блокировок",
                dot_color="#f5c04d",
                pulsing=False,
                clickable=True,
                action_name="Запустить Zapret",
                show_close=False,
            ),
            status_title=_TextTarget(),
            status_desc=_TextTarget(),
            status_dot=status_dot,
            close_btn=_VisibleTarget(),
        )

        self.assertEqual(status_dot.accessible_name, "Индикатор состояния Zapret: Ошибка запуска")
        self.assertEqual(
            status_dot.properties.get("screenReaderStateText"),
            "Индикатор состояния Zapret: Ошибка запуска",
        )

    def test_apply_status_plan_skips_duplicate_render(self) -> None:
        from presets.ui.control.control_page_runtime_shared import apply_status_plan

        plan = SimpleNamespace(
            phase="running",
            title="Zapret работает",
            description="Обход блокировок активен",
            dot_color="#6ccb5f",
            pulsing=True,
            clickable=True,
            action_name="Остановить Zapret",
            show_close=True,
        )
        status_title = _TextTarget()
        status_desc = _TextTarget()
        status_dot = _StatusDot()
        close_btn = _VisibleTarget()

        apply_status_plan(
            plan,
            status_title=status_title,
            status_desc=status_desc,
            status_dot=status_dot,
            close_btn=close_btn,
        )
        status_title.setText = Mock(side_effect=AssertionError("same status must not rewrite title"))
        status_desc.setText = Mock(side_effect=AssertionError("same status must not rewrite description"))
        status_dot.set_color = Mock(side_effect=AssertionError("same status must not rewrite dot color"))
        status_dot.start_pulse = Mock(side_effect=AssertionError("same status must not restart pulse"))
        status_dot.stop_pulse = Mock(side_effect=AssertionError("same status must not stop pulse"))
        close_btn.setVisible = Mock(side_effect=AssertionError("same status must not rewrite close visibility"))

        self.assertTrue(apply_status_plan(
            plan,
            status_title=status_title,
            status_desc=status_desc,
            status_dot=status_dot,
            close_btn=close_btn,
        ))

        status_title.setText.assert_not_called()
        status_desc.setText.assert_not_called()
        status_dot.set_color.assert_not_called()
        status_dot.start_pulse.assert_not_called()
        status_dot.stop_pulse.assert_not_called()
        close_btn.setVisible.assert_not_called()

    def test_apply_status_plan_skips_unchanged_text_and_visibility_when_dot_changes(self) -> None:
        from presets.ui.control.control_page_runtime_shared import apply_status_plan

        first_plan = SimpleNamespace(
            phase="running",
            title="Zapret работает",
            description="Обход блокировок активен",
            dot_color="#6ccb5f",
            pulsing=True,
            clickable=True,
            action_name="Остановить Zapret",
            show_close=True,
        )
        second_plan = SimpleNamespace(
            phase="running",
            title="Zapret работает",
            description="Обход блокировок активен",
            dot_color="#7aa7ff",
            pulsing=True,
            clickable=True,
            action_name="Остановить Zapret",
            show_close=True,
        )
        status_title = _WidgetStateTarget(text="Zapret работает")
        status_desc = _WidgetStateTarget(text="Обход блокировок активен")
        status_dot = _StatusDot()
        close_btn = _WidgetStateTarget(visible=True)

        apply_status_plan(
            first_plan,
            status_title=status_title,
            status_desc=status_desc,
            status_dot=status_dot,
            close_btn=close_btn,
        )
        status_title.text_calls.clear()
        status_desc.text_calls.clear()
        close_btn.visible_calls.clear()

        apply_status_plan(
            second_plan,
            status_title=status_title,
            status_desc=status_desc,
            status_dot=status_dot,
            close_btn=close_btn,
        )

        self.assertEqual(status_dot.color, "#7aa7ff")
        self.assertEqual(status_title.text_calls, [])
        self.assertEqual(status_desc.text_calls, [])
        self.assertEqual(close_btn.visible_calls, [])

    def test_last_status_message_skips_duplicate_render(self) -> None:
        from presets.ui.control.control_page_runtime_shared import apply_last_status_message

        message_label = _TextTarget()
        message_dot = _StatusDot()

        apply_last_status_message(
            "Пресет успешно применён",
            message_label=message_label,
            message_dot=message_dot,
            empty_text="Нет сообщений",
        )
        message_label.setText = Mock(side_effect=AssertionError("same message must not rewrite label"))
        message_dot.set_color = Mock(side_effect=AssertionError("same message must not rewrite dot color"))
        message_dot.stop_pulse = Mock(side_effect=AssertionError("same message must not stop pulse again"))

        apply_last_status_message(
            "Пресет успешно применён",
            message_label=message_label,
            message_dot=message_dot,
            empty_text="Нет сообщений",
        )

        message_label.setText.assert_not_called()
        message_dot.set_color.assert_not_called()
        message_dot.stop_pulse.assert_not_called()

    def test_last_status_message_sets_screen_reader_dot_state_text(self) -> None:
        from presets.ui.control.control_page_runtime_shared import apply_last_status_message

        message_dot = _StatusDot()

        apply_last_status_message(
            "Пресет успешно применён",
            message_label=_TextTarget(),
            message_dot=message_dot,
            empty_text="Нет сообщений",
        )

        self.assertEqual(
            message_dot.accessible_name,
            "Индикатор последнего сообщения: Пресет успешно применён",
        )
        self.assertEqual(
            message_dot.properties.get("screenReaderStateText"),
            "Индикатор последнего сообщения: Пресет успешно применён",
        )

    def test_last_status_message_sets_screen_reader_label_context(self) -> None:
        from presets.ui.control.control_page_runtime_shared import apply_last_status_message

        message_label = _TextTarget()

        apply_last_status_message(
            "Пресет успешно применён",
            message_label=message_label,
            message_dot=_StatusDot(),
            empty_text="Нет сообщений",
        )

        self.assertEqual(
            message_label.accessible_name,
            "Последнее сообщение Zapret: Пресет успешно применён",
        )
        self.assertEqual(
            message_label.properties.get("screenReaderStateText"),
            "Последнее сообщение Zapret: Пресет успешно применён",
        )

    def test_set_toggle_checked_skips_duplicate_state(self) -> None:
        from presets.ui.control.control_page_runtime_shared import set_toggle_checked

        toggle = _ToggleTarget(True)

        set_toggle_checked(toggle, True)

        self.assertEqual(toggle.calls, [])

    def test_set_toggle_checked_applies_changed_state_with_blocked_signals(self) -> None:
        from presets.ui.control.control_page_runtime_shared import set_toggle_checked

        toggle = _ToggleTarget(False)

        set_toggle_checked(toggle, True)

        self.assertEqual(toggle.calls, [(True, True)])
        self.assertTrue(toggle.checked)

    def test_zapret2_additional_settings_state_skips_duplicate_toggles(self) -> None:
        from presets.ui.control.zapret2.runtime_helpers import apply_additional_settings_state

        state = SimpleNamespace(
            discord_restart=True,
            wssize_enabled=False,
            debug_log_enabled=True,
        )
        discord_toggle = _ToggleTarget(True)
        wssize_toggle = _ToggleTarget(False)
        debug_log_toggle = _ToggleTarget(True)

        apply_additional_settings_state(
            state,
            discord_restart_toggle=discord_toggle,
            wssize_toggle=wssize_toggle,
            debug_log_toggle=debug_log_toggle,
        )

        self.assertEqual(discord_toggle.calls, [])
        self.assertEqual(wssize_toggle.calls, [])
        self.assertEqual(debug_log_toggle.calls, [])

    def test_widget_text_visibility_and_enabled_updates_skip_duplicate_state(self) -> None:
        from presets.ui.control.control_page_runtime_shared import (
            set_enabled_if_changed,
            set_text_if_changed,
            set_visible_if_changed,
        )

        widget = _WidgetStateTarget(text="Запуск", visible=True, enabled=False)

        self.assertFalse(set_text_if_changed(widget, "Запуск"))
        self.assertFalse(set_visible_if_changed(widget, True))
        self.assertFalse(set_enabled_if_changed(widget, False))

        self.assertEqual(widget.text_calls, [])
        self.assertEqual(widget.visible_calls, [])
        self.assertEqual(widget.enabled_calls, [])

    def test_widget_text_visibility_and_enabled_updates_apply_changed_state(self) -> None:
        from presets.ui.control.control_page_runtime_shared import (
            set_enabled_if_changed,
            set_text_if_changed,
            set_visible_if_changed,
        )

        widget = _WidgetStateTarget(text="Запуск", visible=True, enabled=False)

        self.assertTrue(set_text_if_changed(widget, "Остановка"))
        self.assertTrue(set_visible_if_changed(widget, False))
        self.assertTrue(set_enabled_if_changed(widget, True))

        self.assertEqual(widget.text_calls, ["Остановка"])
        self.assertEqual(widget.visible_calls, [False])
        self.assertEqual(widget.enabled_calls, [True])

    def test_progress_active_update_skips_duplicate_start_and_stop(self) -> None:
        from presets.ui.control.control_page_runtime_shared import set_progress_active_if_changed

        progress = _ProgressTarget()

        self.assertTrue(set_progress_active_if_changed(progress, True))
        self.assertFalse(set_progress_active_if_changed(progress, True))
        self.assertTrue(set_progress_active_if_changed(progress, False))
        self.assertFalse(set_progress_active_if_changed(progress, False))

        self.assertEqual(progress.started, 1)
        self.assertEqual(progress.stopped, 1)
        self.assertEqual(progress.accessibleName(), "Ход запуска Zapret: не выполняется")
        self.assertEqual(progress.property("screenReaderStateText"), "Ход запуска Zapret: не выполняется")

    def test_loading_status_text_updates_screen_reader_state(self) -> None:
        from presets.ui.control.control_page_runtime_shared import set_loading_status_accessibility

        label = _TextTarget()

        set_loading_status_accessibility(label, active=True, text="Запуск winws...")

        self.assertEqual(label.accessibleName(), "Статус запуска Zapret: Запуск winws...")
        self.assertEqual(label.property("screenReaderStateText"), "Статус запуска Zapret: Запуск winws...")

        set_loading_status_accessibility(label, active=False, text="")

        self.assertEqual(label.accessibleName(), "Статус запуска Zapret: нет активного запуска")
        self.assertEqual(label.property("screenReaderStateText"), "Статус запуска Zapret: нет активного запуска")

    def test_running_status_pulses_for_both_control_modes(self) -> None:
        from presets.ui.control import control_runtime
        from presets.ui.control.zapret2 import page_runtime as zapret2_page_runtime

        winws1_plan = control_runtime.build_status_plan(state="running", last_error="", language="ru")
        winws2_plan = zapret2_page_runtime.build_status_plan(state="running", last_error="", language="ru")

        self.assertTrue(winws1_plan.pulsing)
        self.assertTrue(winws2_plan.pulsing)

    def test_status_dot_is_a_switch_in_every_phase_except_stopping(self) -> None:
        from presets.ui.control import control_runtime
        from presets.ui.control.zapret2 import page_runtime as zapret2_page_runtime

        expectations = {
            "running": (True, "Остановить Zapret", True),
            "starting": (True, "Остановить Zapret", True),
            "autostart_pending": (True, "Остановить Zapret", True),
            "stopping": (False, "Zapret останавливается", False),
            "failed": (True, "Запустить Zapret", False),
            "stopped": (True, "Запустить Zapret", False),
        }
        for build in (control_runtime.build_status_plan, zapret2_page_runtime.build_status_plan):
            for phase, (clickable, action_name, show_close) in expectations.items():
                with self.subTest(build=build.__module__, phase=phase):
                    plan = build(state=phase, last_error="", language="ru")
                    self.assertEqual(plan.clickable, clickable)
                    self.assertEqual(plan.action_name, action_name)
                    self.assertEqual(plan.show_close, show_close)

    def test_stopped_status_tells_to_click_the_button(self) -> None:
        from presets.ui.control.zapret2 import page_runtime as zapret2_page_runtime

        plan = zapret2_page_runtime.build_status_plan(state="stopped", last_error="", language="ru")

        self.assertIn("на кнопку", plan.description)
        self.assertNotIn("«Запустить»", plan.description)

    def test_apply_status_plan_passes_switch_state_to_dot(self) -> None:
        from presets.ui.control.control_page_runtime_shared import apply_status_plan

        class _SwitchDot(_StatusDot):
            def __init__(self) -> None:
                super().__init__()
                self.click_enabled: list[bool] = []
                self.description = ""

            def set_click_enabled(self, enabled: bool) -> None:
                self.click_enabled.append(bool(enabled))

            def accessibleDescription(self) -> str:  # noqa: N802
                return self.description

            def setAccessibleDescription(self, text: str) -> None:  # noqa: N802
                self.description = str(text)

        dot = _SwitchDot()
        close_btn = _VisibleTarget()
        apply_status_plan(
            SimpleNamespace(
                phase="stopping",
                title="Zapret останавливается",
                description="Завершаем процесс",
                dot_color="#f5a623",
                pulsing=True,
                clickable=False,
                action_name="Zapret останавливается",
                show_close=False,
            ),
            status_title=_TextTarget(),
            status_desc=_TextTarget(),
            status_dot=dot,
            close_btn=close_btn,
        )

        self.assertEqual(dot.click_enabled, [False])
        self.assertEqual(dot.description, "Zapret останавливается")
        self.assertFalse(close_btn.visible)

    def test_apply_status_plan_passes_phase_to_the_real_scene(self) -> None:
        from PyQt6.QtGui import QColor
        from PyQt6.QtWidgets import QApplication

        from presets.ui.control.control_page_runtime_shared import apply_status_plan
        from presets.ui.control.zapret2 import page_runtime as zapret2_page_runtime
        from ui.widgets.bypass_scene import BypassScene

        _app = QApplication.instance() or QApplication([])
        scene = BypassScene()
        self.addCleanup(scene.deleteLater)
        scene.set_clickable(True)

        for phase, clickable in (("starting", True), ("running", True), ("stopping", False), ("failed", True)):
            with self.subTest(phase=phase):
                plan = zapret2_page_runtime.build_status_plan(state=phase, last_error="", language="ru")
                apply_status_plan(
                    plan,
                    status_title=_TextTarget(),
                    status_desc=_TextTarget(),
                    status_dot=scene,
                    close_btn=_VisibleTarget(),
                )
                self.assertEqual(scene.phase(), phase)
                self.assertEqual(scene.target_color(), QColor(plan.dot_color))
                self.assertEqual(scene.is_click_enabled(), clickable)


if __name__ == "__main__":
    unittest.main()
