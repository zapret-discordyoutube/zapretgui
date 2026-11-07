"""Весёлые эффекты шуточного окна плитки «НСДИ» (dns.joke_confirm).

Только для этого окна, больше нигде в программе таких эффектов нет:
- заголовок получает смайлик по номеру фразы (title_emoji) и мигает им с «❓»;
- окно трясётся влево-вправо, как головой «нет-нет-нет» (shake_offset);
- поверх окна коротко сыплются 🚫 🧱 📵 🙈 🤡 (EmojiRain).

Всё это — украшение: смысл окна («Да» / «Нет») не зависит от него. Если человек
выключил живые анимации (ui.animation_policy), эффектов нет совсем, остаётся
только смайлик в заголовке. Эффекты не ловят мышь и клавиатуру, поэтому кнопки
остаются на месте и доступны с клавиатуры и диктору.
"""

from __future__ import annotations

import math
import random

from PyQt6.QtCore import QElapsedTimer, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QFont, QPainter
from PyQt6.QtWidgets import QWidget

from ui.animation_policy import are_live_animations_enabled

# Смайлик заголовка: по одному на фразу (порядок как в app.ui_texts, номер фразы минус один).
TITLE_EMOJI = ("🚨", "👵", "🎀", "☠️", "🧐", "📜", "💣", "💡", "✉️", "🪲", "🔙", "🤔")
BLINK_EMOJI = "❓"
RAIN_EMOJI = ("🚫", "🧱", "📵", "🙈", "🤡")

SHAKE_MS = 560
SHAKE_AMPLITUDE_PX = 18.0
SHAKE_SWINGS = 5
BLINK_MS = 320
BLINK_STEPS = 8
RAIN_MS = 2600
RAIN_COUNT = 22


def title_emoji(index: int) -> str:
    """Смайлик для фразы номер index (от 1)."""
    return TITLE_EMOJI[(int(index) - 1) % len(TITLE_EMOJI)]


def shake_offset(progress: float, amplitude: float = SHAKE_AMPLITUDE_PX, swings: int = SHAKE_SWINGS) -> float:
    """Сдвиг окна по горизонтали в долях пути: затухающие качания, в начале и в конце — ноль."""
    progress = min(max(float(progress), 0.0), 1.0)
    return amplitude * (1.0 - progress) * math.sin(2.0 * math.pi * swings * progress)


class EmojiRain(QWidget):
    """Прозрачный слой поверх окна: смайлики падают сверху вниз и сами исчезают."""

    FRAME_MS = 33

    def __init__(self, parent: QWidget, *, count: int | None = None, duration_ms: int | None = None, rng: random.Random | None = None):
        super().__init__(parent)
        count = RAIN_COUNT if count is None else count
        duration_ms = RAIN_MS if duration_ms is None else duration_ms
        chooser = rng or random.Random()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._duration_ms = int(duration_ms)
        # Каждый смайлик: доля ширины, задержка старта (мс), скорость (пикселей в секунду), знак, размер шрифта.
        self._particles = [
            (
                chooser.random(),
                chooser.uniform(0, duration_ms * 0.45),
                chooser.uniform(220, 460),
                chooser.choice(RAIN_EMOJI),
                chooser.randint(18, 30),
            )
            for _ in range(count)
        ]
        self._clock = QElapsedTimer()
        self._clock.start()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(self.FRAME_MS)
        self._fit()
        self.show()
        self.raise_()

    def finished(self) -> bool:
        return self._clock.elapsed() >= self._duration_ms

    def _fit(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())

    def _tick(self) -> None:
        if self.finished():
            self._timer.stop()
            self.hide()
            self.deleteLater()
            return
        self._fit()
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - имя из Qt
        painter = QPainter(self)
        elapsed = self._clock.elapsed()
        for fraction, delay, speed, emoji, size in self._particles:
            if elapsed < delay:
                continue
            y = (elapsed - delay) / 1000.0 * speed
            if y > self.height() + size:
                continue
            painter.setFont(QFont("Segoe UI Emoji", size // 2 + 4))
            painter.drawText(int(fraction * max(self.width() - size, 1)), int(y), emoji)
        painter.end()


def play(box, *, index: int, title: str) -> None:
    """Запускает эффекты на окне box (fluent MessageBox); вызывать до box.exec()."""
    if not are_live_animations_enabled():
        return
    emoji = title_emoji(index)
    _start_blink(box, emoji, title)
    _start_shake(box)
    EmojiRain(box)


def _start_blink(box, emoji: str, title: str) -> None:
    label = getattr(box, "titleLabel", None)
    if label is None:
        return
    timer = QTimer(box)
    state = {"step": 0}

    def tick() -> None:
        state["step"] += 1
        last = state["step"] >= BLINK_STEPS
        label.setText(f"{emoji if last or state['step'] % 2 == 0 else BLINK_EMOJI} {title}")
        if last:
            timer.stop()

    timer.timeout.connect(tick)
    timer.start(BLINK_MS)


def _start_shake(box) -> None:
    layout = box.layout()
    if layout is None:
        return
    base = layout.contentsMargins()
    animation = QVariantAnimation(box)
    animation.setDuration(SHAKE_MS)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)

    def apply(progress) -> None:
        # Окно стоит по центру: больше левое поле — оно уезжает вправо, и наоборот.
        shift = int(round(shake_offset(float(progress))))
        layout.setContentsMargins(
            base.left() + max(shift, 0) * 2,
            base.top(),
            base.right() + max(-shift, 0) * 2,
            base.bottom(),
        )

    animation.valueChanged.connect(apply)
    animation.finished.connect(lambda: layout.setContentsMargins(base))
    animation.start()


__all__ = ["EmojiRain", "play", "shake_offset", "title_emoji"]
