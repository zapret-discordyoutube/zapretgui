"""Прощальный экран: что видит человек между «закрыть» и исчезновением окна.

Раньше после выбора «Закрыть только GUI» окно просто пропадало, а после
«Закрыть и остановить DPI» стояло как было, пока останавливался обход, — без
единого знака, что программа приняла команду. Теперь поверх окна плавно
проступает экран с медоедом и двумя строками: заголовок прямо говорит, что
происходит («Окно закрывается», «Останавливаем обход», «Обход остановлен»),
строка под ним — лёгкая. Потом окно гаснет, и программа закрывается.

Экран — украшение, а не шаг выхода: любая ошибка в нём не должна задержать
закрытие. Поэтому сеанс (``ExitScreenSession``) в любом случае вызывает
переданное ему «дальше», а если остановка обхода так и не сообщила о себе —
сам убирает экран и возвращает управление окну.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PyQt6.QtCore import QEasingCurve, QEvent, QObject, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, SubtitleLabel, isDarkTheme

from log.log import log
from ui.accessibility import set_control_accessibility, set_state_text
from ui.animation_policy import are_live_animations_enabled
from ui.exit_screen_texts import (
    KIND_CLOSING_IDLE,
    KIND_CLOSING_KEEP,
    KIND_STOPPED,
    KIND_STOPPING,
    exit_screen_text,
)
from ui.widgets.fun.badger import DrawnBadger
from ui.widgets.fun.mascot import MOOD_BUSY, MOOD_HAPPY, MOOD_SAD


# Экран проступает и окно гаснет за это время.
FADE_IN_MS = 160
# Окно именно гаснет, а не обрывается: прозрачность уходит в ноль плавно, и
# только потом программа закрывается. Уборка при выходе (потоки, трей) идёт
# уже при невидимом окне — раньше оно на это время замирало на экране.
WINDOW_FADE_MS = 260
# Меньше этого экран на виду не бывает: иначе текст не успеть прочитать.
MIN_VISIBLE_MS = 800
# Сколько держится «Обход остановлен» перед тем, как окно погаснет.
STOPPED_HOLD_MS = 450
# Остановка обхода не сообщила о себе: экран убирается, окно снова доступно.
STOP_WAIT_MAX_MS = 20_000

MASCOT_SIZE = 92
# Насколько плотно экран закрывает окно (0–255): интерфейс под ним едва виден.
_SCRIM_ALPHA = 236


class ExitScreen(QWidget):
    """Затемнение на всё окно с медоедом и двумя строками текста."""

    def __init__(self, window: QWidget, *, title: str, phrase: str, mood: str) -> None:
        super().__init__(window)
        self._window = window
        self._progress = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        # Клавиши и мышь остаются здесь: под экраном ничего нажать нельзя.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._content = QWidget(self)
        self._mascot = DrawnBadger(self._content, size=MASCOT_SIZE)
        self._title = SubtitleLabel(title, self._content)
        self._phrase = BodyLabel(phrase, self._content)
        for label in (self._title, self._phrase):
            label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._phrase.setTextColor(QColor(96, 96, 96), QColor(170, 170, 170))

        column = QVBoxLayout(self._content)
        column.setContentsMargins(24, 24, 24, 24)
        column.setSpacing(6)
        column.addWidget(self._mascot, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addSpacing(10)
        column.addWidget(self._title, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(self._phrase, 0, Qt.AlignmentFlag.AlignHCenter)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addStretch(1)
        root.addWidget(self._content, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addStretch(1)

        self._content_opacity = QGraphicsOpacityEffect(self._content)
        self._content_opacity.setOpacity(0.0)
        self._content.setGraphicsEffect(self._content_opacity)

        self._fade = QVariantAnimation(self)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setDuration(FADE_IN_MS)
        self._fade.valueChanged.connect(self._set_progress)

        self._announce(title, phrase)
        self._mascot.set_mood(mood)
        window.installEventFilter(self)
        self.setGeometry(window.rect())

    def appear(self, *, animated: bool) -> None:
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        if animated:
            self._fade.start()
        else:
            self._set_progress(1.0)

    def set_text(self, title: str, phrase: str, *, mood: str) -> None:
        self._title.setText(title)
        self._phrase.setText(phrase)
        self._announce(title, phrase)
        self._mascot.set_mood(mood)

    def title(self) -> str:
        return self._title.text()

    def phrase(self) -> str:
        return self._phrase.text()

    def _announce(self, title: str, phrase: str) -> None:
        set_state_text(self, title)
        set_control_accessibility(self, name=title, description=phrase)

    def _set_progress(self, value) -> None:
        self._progress = max(0.0, min(1.0, float(value)))
        self._content_opacity.setOpacity(self._progress)
        self.update()

    def eventFilter(self, watched, event):  # noqa: N802 (Qt API)
        if watched is self._window and event.type() == QEvent.Type.Resize:
            self.setGeometry(self._window.rect())
        return False

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt API)
        base = QColor(32, 32, 32) if isDarkTheme() else QColor(243, 243, 243)
        base.setAlpha(int(_SCRIM_ALPHA * self._progress))
        painter = QPainter(self)
        painter.fillRect(self.rect(), base)
        painter.end()

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        event.accept()


class ExitScreenSession(QObject):
    """Один показ прощального экрана: от команды «выйти» до закрытия программы."""

    def __init__(
        self,
        window: QWidget,
        *,
        stop_dpi: bool,
        bypass_running: bool,
        language: str | None = None,
        animated: bool | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(window)
        self._window = window
        # Останавливать нечего — и говорить об остановке незачем: тогда это
        # обычное закрытие окна.
        self._stop_dpi = bool(stop_dpi) and bool(bypass_running)
        self._language = language
        self._animated = are_live_animations_enabled() if animated is None else bool(animated)
        self._clock = clock
        self._on_done: Callable[[], None] | None = None
        self._finishing = False
        self._done = False

        if self._stop_dpi:
            kind, mood = KIND_STOPPING, MOOD_BUSY
        elif bypass_running:
            kind, mood = KIND_CLOSING_KEEP, MOOD_HAPPY
        else:
            kind, mood = KIND_CLOSING_IDLE, MOOD_HAPPY
        title, phrase = exit_screen_text(kind, language)
        self._screen: ExitScreen | None = ExitScreen(window, title=title, phrase=phrase, mood=mood)
        self._shown_at = self._clock()
        self._screen.appear(animated=self._animated)

        self._leave_timer = QTimer(self)
        self._leave_timer.setSingleShot(True)
        self._leave_timer.timeout.connect(self._fade_window_out)

        self._window_fade = QVariantAnimation(self)
        self._window_fade.setDuration(WINDOW_FADE_MS)
        self._window_fade.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._window_fade.valueChanged.connect(self._set_window_opacity)
        self._window_fade.finished.connect(self._finish_now)

        # Только для остановки обхода: закрытие окна от нас самих не зависит
        # ни от чего и ждать ему нечего.
        self._stop_wait = QTimer(self)
        self._stop_wait.setSingleShot(True)
        self._stop_wait.timeout.connect(self._give_window_back)
        if self._stop_dpi:
            self._stop_wait.start(STOP_WAIT_MAX_MS)

    def screen(self) -> ExitScreen | None:
        return self._screen

    def finish(self, on_done: Callable[[], None]) -> None:
        """Работа перед выходом закончена: доиграть экран и вызвать on_done."""
        if self._finishing or self._done:
            return
        self._finishing = True
        self._on_done = on_done
        self._stop_wait.stop()
        try:
            hold_ms = 0
            if self._stop_dpi and self._screen is not None:
                title, phrase = exit_screen_text(KIND_STOPPED, self._language)
                self._screen.set_text(title, phrase, mood=MOOD_SAD)
                hold_ms = STOPPED_HOLD_MS
            shown_ms = int((self._clock() - self._shown_at) * 1000.0)
            self._leave_timer.start(max(hold_ms, MIN_VISIBLE_MS - shown_ms, 0))
        except Exception as exc:
            log(f"Прощальный экран не доиграл: {exc}", "DEBUG")
            self._finish_now()

    def _fade_window_out(self) -> None:
        try:
            if not self._animated:
                self._finish_now()
                return
            self._window_fade.setStartValue(float(self._window.windowOpacity()))
            self._window_fade.setEndValue(0.0)
            self._window_fade.start()
        except Exception as exc:
            log(f"Окно не удалось погасить плавно: {exc}", "DEBUG")
            self._finish_now()

    def _set_window_opacity(self, value) -> None:
        try:
            self._window.setWindowOpacity(max(0.0, min(1.0, float(value))))
        except Exception:
            pass

    def _finish_now(self) -> None:
        if self._done:
            return
        self._done = True
        self._leave_timer.stop()
        self._stop_wait.stop()
        on_done, self._on_done = self._on_done, None
        if on_done is not None:
            on_done()

    def _give_window_back(self) -> None:
        """Остановка обхода так и не сообщила о себе: не держим окно закрытым экраном."""
        if self._finishing or self._done:
            return
        log("Прощальный экран убран: остановка обхода не завершилась вовремя", "WARNING")
        screen, self._screen = self._screen, None
        if screen is not None:
            screen.hide()
            screen.deleteLater()


class _NoExitScreen:
    """Окна нет на экране или экран не удалось показать: выходим сразу."""

    def finish(self, on_done: Callable[[], None]) -> None:
        on_done()


def begin_exit_screen(window, *, stop_dpi: bool, bypass_running: bool, language: str | None = None):
    """Показывает прощальный экран и возвращает сеанс с методом finish(on_done).

    Если окна нет на экране (программа в трее, окно свёрнуто), показывать
    нечего: возвращается сеанс, который вызывает on_done сразу.
    """
    try:
        if window is None or not window.isVisible() or window.isMinimized():
            return _NoExitScreen()
        return ExitScreenSession(
            window,
            stop_dpi=bool(stop_dpi),
            bypass_running=bool(bypass_running),
            language=language,
        )
    except Exception as exc:
        log(f"Прощальный экран не показан: {exc}", "DEBUG")
        return _NoExitScreen()


__all__ = [
    "ExitScreen",
    "ExitScreenSession",
    "FADE_IN_MS",
    "MIN_VISIBLE_MS",
    "STOPPED_HOLD_MS",
    "STOP_WAIT_MAX_MS",
    "WINDOW_FADE_MS",
    "begin_exit_screen",
]
