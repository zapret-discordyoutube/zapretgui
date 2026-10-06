"""Update status card for Servers page."""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QRectF, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from config.build_info import APP_VERSION

from app.ui_texts import tr as tr_catalog
from ui.accessibility import set_control_accessibility, set_state_text
from ui.animation_policy import are_live_animations_enabled
from ui.theme import get_cached_qta_pixmap, get_theme_tokens
from ui.theme_refresh import ThemeRefreshBinding
from updater.ui import plans
from updater.ui.sync_icon import ICON_MODE_CHECKING, ICON_MODE_ERROR, ICON_MODE_IDLE, UpdateSyncIcon
from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    FluentIcon,
    IndeterminateProgressRing,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
)


# Оборот стрелок на кнопке после завершения проверки.
FINISH_TURN_MS = 700
FINISH_TURN_DEGREES = 360.0


class IndeterminateProgressPushButton(PushButton):
    """Кнопка проверки обновлений: значок со стрелками и кольцо на время проверки.

    Покачивание значка при наведении, пружинку при нажатии и волну от щелчка
    даёт общий модуль ui/button_motion.py — он оживляет любую кнопку со
    значком. Своё здесь одно: когда проверка закончилась, стрелки делают
    плавный оборот и останавливаются.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stored_text = ""
        self._loading = False
        self._turn_angle = 0.0
        self._idle_minimum_width: int | None = None
        self.setIcon(FluentIcon.SYNC)
        self._ring = IndeterminateProgressRing(start=False, parent=self)
        self._ring.setFixedSize(20, 20)
        self._ring.setStrokeWidth(2)
        self._ring.hide()

        # QVariantAnimation, а не QPropertyAnimation: при выключенных
        # анимациях общий fallback подменяет QPropertyAnimation.start.
        # Значения задаются до подписки: setStartValue сразу шлёт valueChanged.
        self._turn = QVariantAnimation(self)
        self._turn.setStartValue(0.0)
        self._turn.setEndValue(1.0)
        self._turn.setDuration(FINISH_TURN_MS)
        self._turn.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._turn.valueChanged.connect(self._on_turn_value)
        self._turn.finished.connect(self._on_turn_finished)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._ring is not None:
            x = (self.width() - 20) // 2
            y = (self.height() - 20) // 2
            self._ring.move(x, y)

    def hideEvent(self, event):  # noqa: N802
        self._turn.stop()
        self._turn_angle = 0.0
        super().hideEvent(event)

    def start_loading(self):
        self._stored_text = self.text()
        self._loading = True
        self._turn.stop()
        self._turn_angle = 0.0
        if self._idle_minimum_width is None and self.isVisible():
            # Без текста кнопка сжалась бы до минимума и дёрнулась по ширине.
            self._idle_minimum_width = self.minimumWidth()
            self.setMinimumWidth(max(self._idle_minimum_width, self.width()))
        self.setText("")
        if self._ring is not None:
            set_state_text(self._ring, "Проверка обновлений выполняется")
            self._ring.show()
            self._ring.start()
        self.setEnabled(False)

    def stop_loading(self, text: str = ""):
        was_loading = self._loading
        self._loading = False
        if self._idle_minimum_width is not None:
            self.setMinimumWidth(self._idle_minimum_width)
            self._idle_minimum_width = None
        self.setText(text or self._stored_text)
        if self._ring is not None:
            self._ring.stop()
            self._ring.hide()
            set_state_text(self._ring, "Индикатор проверки обновлений: не выполняется")
        self.setEnabled(True)
        if was_loading:
            self._play_finish_turn()
        self.update()

    def is_finish_turn_running(self) -> bool:
        return self._turn.state() == QVariantAnimation.State.Running

    def _play_finish_turn(self) -> None:
        self._turn.stop()
        self._turn_angle = 0.0
        try:
            if not self.isVisible():
                return
            window = self.window()
            if window is not None and window.isMinimized():
                return
        except RuntimeError:
            return
        if not are_live_animations_enabled():
            return
        self._turn.start()

    def _on_turn_value(self, value) -> None:
        try:
            self._turn_angle = float(value) * FINISH_TURN_DEGREES
        except (TypeError, ValueError):
            return
        self.update()

    def _on_turn_finished(self) -> None:
        self._turn_angle = 0.0
        self.update()

    def _drawIcon(self, icon, painter, rect, state=QIcon.State.Off):  # noqa: N802
        if self._loading:
            # На время проверки место значка занимает кольцо в центре кнопки.
            return
        angle = self._turn_angle
        if not angle:
            super()._drawIcon(icon, painter, rect, state)
            return
        painter.save()
        try:
            center = QRectF(rect).center()
            painter.translate(center)
            painter.rotate(angle)
            painter.translate(-center)
            super()._drawIcon(icon, painter, rect, state)
        finally:
            painter.restore()


class UpdateStatusCard(CardWidget):
    """Карточка статуса обновлений."""

    check_clicked = pyqtSignal()
    # «Подробнее» / «Показать»: открыть окно обновления.
    details_clicked = pyqtSignal()

    def __init__(self, parent=None, *, language: str = "ru"):
        super().__init__(parent)
        self.setObjectName("updateStatusCard")
        self._ui_language = language
        self._is_checking = False
        self._state = "idle"
        self._state_version = ""
        self._state_source = ""
        self._state_message = ""
        self._state_elapsed = 0.0
        self._tokens = get_theme_tokens()
        self._build_ui()
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme)

    def _tr(self, key: str, default: str) -> str:
        return tr_catalog(key, language=self._ui_language, default=default)

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(20, 16, 20, 16)
        content_layout.setSpacing(16)

        self._icon_label = UpdateSyncIcon(size=40)
        content_layout.addWidget(self._icon_label)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)

        self.title_label = StrongBodyLabel(
            self._tr("page.servers.update.title.default", "Проверка обновлений")
        )
        text_layout.addWidget(self.title_label)

        self.subtitle_label = CaptionLabel(
            self._tr(
                "page.servers.update.subtitle.default",
                "Нажмите для проверки доступных обновлений",
            )
        )
        text_layout.addWidget(self.subtitle_label)

        content_layout.addLayout(text_layout, 1)

        self.details_btn = PrimaryPushButton()
        self.details_btn.setFixedHeight(32)
        self.details_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.details_btn.clicked.connect(self.details_clicked.emit)
        self.details_btn.hide()
        content_layout.addWidget(self.details_btn)

        self.check_btn = IndeterminateProgressPushButton()
        self.check_btn.setText(
            self._tr("page.servers.update.button.check", "Проверить обновления")
        )
        self.check_btn.setFixedHeight(32)
        self.check_btn.setMinimumWidth(180)
        self.check_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.check_btn.clicked.connect(self._on_check_clicked)
        content_layout.addWidget(self.check_btn)

        self.clicked.connect(self._on_check_clicked)
        main_layout.addWidget(content)

        self._apply_theme(force=True)
        self._update_accessibility()

    def _apply_theme(self, tokens=None, force: bool = False) -> None:
        _ = force
        self._tokens = tokens or get_theme_tokens()
        try:
            self._icon_label.set_colors(
                accent=self._tokens.accent_hex,
                error=self._error_hex(),
                is_light=self._tokens.is_light,
            )
            self._icon_label.set_error_glyph(
                get_cached_qta_pixmap('fa5s.exclamation-triangle', color=self._error_hex(), size=20)
            )
        except Exception:
            pass

    def _error_hex(self) -> str:
        return "#dc2626" if self._tokens.is_light else "#f87171"

    def _set_icon_idle(self):
        self._icon_label.set_mode(ICON_MODE_IDLE)

    def _on_check_clicked(self):
        self.check_clicked.emit()

    def _set_error_icon(self) -> None:
        self._icon_label.set_mode(ICON_MODE_ERROR)

    def _apply_state_text(self) -> None:
        plan = plans.build_update_status_card_plan(
            state=self._state,
            version=self._state_version,
            source=self._state_source,
            message=self._state_message,
            elapsed=self._state_elapsed,
            app_version=APP_VERSION,
            language=self._ui_language,
        )
        self.title_label.setText(plan.title)
        self.subtitle_label.setText(plan.subtitle)
        self.check_btn.setText(plan.button_text)
        self._update_accessibility()

    def _update_accessibility(self) -> None:
        title = str(self.title_label.text() or "").strip()
        subtitle = str(self.subtitle_label.text() or "").strip()
        if title and subtitle:
            separator = " " if title.endswith((".", "!", "?", "…")) else ". "
            card_name = f"{title}{separator}{subtitle}"
        else:
            card_name = title or subtitle

        set_control_accessibility(
            self,
            name=card_name,
            description="Карточка проверки обновлений. Сообщает текущий статус проверки и доступное действие.",
        )
        set_state_text(self, card_name)
        set_state_text(self._icon_label, f"Индикатор проверки обновлений: {card_name or '—'}")
        set_state_text(self.title_label, f"Заголовок проверки обновлений: {title or '—'}")
        set_state_text(self.subtitle_label, f"Описание проверки обновлений: {subtitle or '—'}")

        if self._is_checking:
            button_state = "Проверка обновлений выполняется"
            set_control_accessibility(
                self.check_btn,
                name=button_state,
                description="Дождитесь завершения проверки обновлений.",
            )
            set_state_text(self.check_btn, button_state)
        else:
            button_text = str(self.check_btn.text() or "").strip()
            base_state = button_text or "Проверить обновления"
            is_enabled = True
            try:
                is_enabled = bool(self.check_btn.isEnabled())
            except Exception:
                pass
            button_state = base_state if is_enabled else f"{base_state}, недоступно"
            description = (
                "Запускает проверку доступных обновлений."
                if is_enabled
                else "Проверка обновлений сейчас недоступна. Дождитесь завершения текущего действия."
            )
            set_control_accessibility(
                self.check_btn,
                name=button_state,
                description=description,
            )
            set_state_text(self.check_btn, button_state)

    def _apply_transition_plan(self, plan) -> None:
        self._is_checking = plan.is_checking
        self._state = plan.state
        self._state_version = plan.state_version
        self._state_source = plan.state_source
        self._state_message = plan.state_message
        self._state_elapsed = plan.state_elapsed

        if plan.icon_mode == "error":
            self._set_error_icon()
        elif plan.icon_mode == "checking":
            self._icon_label.set_mode(ICON_MODE_CHECKING)
        elif plan.icon_mode == "idle":
            self._set_icon_idle()

        self._apply_state_text()

        if plan.loading_mode == "start":
            self.check_btn.start_loading()
        elif plan.loading_mode == "stop":
            self.check_btn.stop_loading(plan.stop_loading_text)

        if plan.check_enabled is not None:
            self.check_btn.setEnabled(plan.check_enabled)
            self._update_accessibility()

    def set_details_action(self, text: str) -> None:
        """Кнопка открытия окна обновления; пустой текст прячет её."""
        label = str(text or "").strip()
        self.details_btn.setText(label)
        self.details_btn.setVisible(bool(label))
        set_control_accessibility(
            self.details_btn,
            name=label or "Окно обновления",
            description="Открывает окно обновления со списком изменений и кнопками установки.",
        )
        set_state_text(self.details_btn, label or "Окно обновления: недоступно")

    def show_downloading(self, version: str, message: str = "") -> None:
        plan = plans.build_update_status_transition_plan(
            target_state="downloading",
            language=self._ui_language,
            version=version,
            message=message,
        )
        self._apply_transition_plan(plan)

    def set_check_enabled(self, enabled: bool) -> None:
        self.check_btn.setEnabled(bool(enabled))
        self._update_accessibility()

    def start_checking(self):
        plan = plans.build_update_status_transition_plan(
            target_state="checking",
            language=self._ui_language,
        )
        self._apply_transition_plan(plan)

    def stop_checking(self, found_update: bool = False, version: str = ""):
        plan = plans.build_update_status_transition_plan(
            target_state="result",
            language=self._ui_language,
            version=version,
            found_update=found_update,
        )
        self._apply_transition_plan(plan)

    def set_error(self, message: str):
        plan = plans.build_update_status_transition_plan(
            target_state="error",
            language=self._ui_language,
            message=message,
        )
        self._apply_transition_plan(plan)

    def show_found_update(self, version: str, source: str) -> None:
        plan = plans.build_update_status_transition_plan(
            target_state="found",
            language=self._ui_language,
            version=version,
            source=source,
        )
        self._apply_transition_plan(plan)

    def show_download_error(self) -> None:
        plan = plans.build_update_status_transition_plan(
            target_state="download_error",
            language=self._ui_language,
        )
        self._apply_transition_plan(plan)

    def show_deferred(self, version: str) -> None:
        plan = plans.build_update_status_transition_plan(
            target_state="deferred",
            language=self._ui_language,
            version=version,
        )
        self._apply_transition_plan(plan)

    def show_checked_ago(self, elapsed: float) -> None:
        plan = plans.build_update_status_transition_plan(
            target_state="checked_ago",
            language=self._ui_language,
            elapsed=elapsed,
        )
        self._apply_transition_plan(plan)

    def show_manual_hint(self) -> None:
        plan = plans.build_update_status_transition_plan(
            target_state="manual",
            language=self._ui_language,
        )
        self._apply_transition_plan(plan)

    def show_auto_enabled_hint(self) -> None:
        plan = plans.build_update_status_transition_plan(
            target_state="auto_on",
            language=self._ui_language,
        )
        self._apply_transition_plan(plan)

    def set_ui_language(self, language: str) -> None:
        self._ui_language = language
        self._apply_state_text()
