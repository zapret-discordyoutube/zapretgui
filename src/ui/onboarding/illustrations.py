"""Анимированные схемы техник обхода для обучающего тура.

Схема одна на все техники: слева «Вы», в середине проверка у провайдера,
справа сайт. По дорожке между ними бегут пакеты-плашки. Сцена описывает,
какие плашки отправить, когда, и что с ними станет:

- pass — плашка доходит до сайта;
- blocked — проверка узнаёт имя и обрывает соединение;
- die — поддельная плашка проходит проверку и гаснет, не дойдя до сайта;
- discard — мусор доезжает до сайта вместе с данными, и сайт его отбрасывает.

Плашки едут слева направо, поэтому правее — значит раньше: правая плашка
первой попадает в проверку и первой приходит на сайт. Просвет между плашками
означает отдельные пакеты. Части одного пакета нарисованы слитно, одной
плашкой из двух половин с подписью «один пакет» (мусор seqovl в tcpseg).

Так видно главное: проверка провайдера видит одно, а сайт получает другое.
Сцены повторяются по кругу. Все переходы (появление реплики, вспышка у
сайта, падение подделок) заканчиваются до STATIC_PHASE: когда анимации в
системе выключены, рисуется этот неподвижный кадр с итогом.

В углу схемы есть кнопка паузы: анимацию можно остановить на любом кадре и
спокойно рассмотреть. На следующем шаге схема снова идёт сама.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QElapsedTimer, QPointF, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget
from qfluentwidgets import FluentIcon, TransparentToolButton, isDarkTheme, themeColor

from ui.animation_policy import are_live_animations_enabled
from ui.fluent_widgets import set_tooltip


PERIOD_MS = 5600
FRAME_MS = 33
ILLUSTRATION_HEIGHT = 156
# Сколько доли круга плашка идёт от «Вы» до сайта.
TRAVEL = 0.45
# С этого места круга всё плавно гаснет перед повтором.
FADE_FROM = 0.9
# Неподвижный кадр, когда анимации выключены: всё уже дошло.
STATIC_PHASE = 0.86
# Просвет между соседними плашками на дорожке, px.
CHIP_GAP = 10.0
# Длительности переходов, доля круга.
POP = 0.05  # появление реплики, итога и красного креста
APPEAR = 0.08  # плашка выходит от «Вы»
ABSORB = 0.1  # хвост пути, на котором настоящая плашка входит в сайт
PULSE = 0.08  # кольцо вокруг сайта, когда всё дошло
SHAKE = 0.07  # плашка трясётся, упёршись в блок
DISCARD = 0.1  # сайт отбрасывает мусор
PAUSE_BUTTON_SIZE = 28

BLOCK_RED = QColor(232, 17, 35)
PASS_GREEN = QColor(60, 179, 113)
FAKE_AMBER = QColor(240, 160, 48)
JUNK_GREY = QColor(140, 140, 140)


@dataclass(frozen=True, slots=True)
class Packet:
    text: str
    # Номер части в исходном сообщении: видно, в каком порядке их режут.
    part: str = ""
    # Пауза перед отправкой сверх обычного просвета, доля круга.
    delay: float = 0.0
    kind: str = "real"  # real | fake | junk | syn
    fate: str = "pass"  # pass | blocked | die | discard
    # Текст после проверки: у сайта лишнее выпадает (oob, syndata).
    text_after_gate: str = ""
    # Плашка слита со следующей в один пакет и едет в нём первой (мусор seqovl в tcpseg).
    glued_to_next: bool = False


@dataclass(frozen=True, slots=True)
class Scene:
    packets: tuple[Packet, ...]
    # Реплика проверки и какая плашка её вызывает (номер в packets).
    bubble_key: str
    bubble_trigger: int
    bubble_tone: str = "ok"  # ok | block | confused
    # Что в итоге собрал сайт; пусто — сайт ничего не получил.
    site_result: str = "youtube.com"


SCENES: dict[str, Scene] = {
    "blocked": Scene(
        packets=(Packet("youtube.com", fate="blocked"),),
        bubble_key="blocked",
        bubble_trigger=0,
        bubble_tone="block",
        site_result="",
    ),
    "fake": Scene(
        packets=(
            Packet("google.com", kind="fake", fate="die"),
            Packet("youtube.com", delay=0.06),
        ),
        bubble_key="fake",
        bubble_trigger=0,
    ),
    "multisplit": Scene(
        packets=(Packet("you", part="1"), Packet("tube.com", part="2")),
        bubble_key="unknown",
        bubble_trigger=0,
        bubble_tone="confused",
    ),
    "multidisorder": Scene(
        packets=(Packet("tube.com", part="2"), Packet("you", part="1")),
        bubble_key="unknown",
        bubble_trigger=0,
        bubble_tone="confused",
    ),
    "fakedsplit": Scene(
        packets=(
            Packet("q7z", kind="fake", fate="die"),
            Packet("you", part="1"),
            Packet("xr1k.zzz", kind="fake", fate="die"),
            Packet("tube.com", part="2"),
        ),
        bubble_key="which_real",
        bubble_trigger=1,
        bubble_tone="confused",
    ),
    "hostfakesplit": Scene(
        packets=(
            Packet("abc.ru", kind="fake", fate="die"),
            Packet("youtube.com"),
            Packet("abc.ru", kind="fake", fate="die"),
        ),
        bubble_key="fake_host",
        bubble_trigger=0,
    ),
    "tcpseg": Scene(
        packets=(
            Packet("junk", kind="junk", fate="discard", glued_to_next=True),
            Packet("youtube.com"),
        ),
        bubble_key="junk",
        bubble_trigger=0,
    ),
    "oob": Scene(
        packets=(Packet("you#tube.com", text_after_gate="youtube.com"),),
        bubble_key="unknown",
        bubble_trigger=0,
        bubble_tone="confused",
    ),
    "syndata": Scene(
        packets=(
            Packet("SYN", kind="syn", text_after_gate="SYN"),
            Packet("youtube.com", delay=0.08),
        ),
        bubble_key="odd",
        bubble_trigger=0,
    ),
}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _ease_out(t: float) -> float:
    t = _clamp01(t)
    return 1.0 - (1.0 - t) ** 3


def _ease_in(t: float) -> float:
    t = _clamp01(t)
    return t * t


def _ease_out_back(t: float) -> float:
    """Быстро вырастает с лёгким перелётом — «выскакивает»."""
    t = _clamp01(t)
    c1 = 1.70158
    return 1.0 + (c1 + 1.0) * (t - 1.0) ** 3 + c1 * (t - 1.0) ** 2


@dataclass(frozen=True, slots=True)
class _Layout:
    you_rect: QRectF
    gate_rect: QRectF
    site_rect: QRectF
    start_x: float
    end_x: float
    track_y: float

    @property
    def gate_x(self) -> float:
        return self.gate_rect.center().x()


@dataclass(frozen=True, slots=True)
class ChipFrame:
    """Где и как нарисовать одну плашку в данный момент."""

    index: int
    label: str
    kind: str
    x: float
    dy: float = 0.0
    alpha: float = 1.0
    scale: float = 1.0
    angle: float = 0.0
    # Плашка-начало слитого пакета: следующая плашка едет вплотную за ней.
    glued: bool = False
    # С какой стороны плашка слита с соседней: там край прямой, без скругления.
    flat: str = ""  # "" | left | right
    # Ширина плашки на дорожке, px: по ней считаются просветы.
    width: float = 0.0


@dataclass(frozen=True, slots=True)
class SceneTimes:
    starts: tuple[float, ...]
    # Когда проверка подаёт реплику (и, для блока, обрывает соединение).
    verdict: float
    # Когда сайт собрал итог; None — не соберёт.
    site_done: float | None
    # Когда всё на схеме замирает до следующего круга.
    settled: float


def _readable_text_on(fill: QColor) -> QColor:
    luminance = 0.2126 * fill.redF() + 0.7152 * fill.greenF() + 0.0722 * fill.blueF()
    return QColor(20, 20, 20) if luminance > 0.55 else QColor(250, 250, 250)


class TechniqueIllustration(QWidget):
    """Схема «Вы → проверка провайдера → сайт» с бегущими пакетами."""

    def __init__(self, parent: QWidget | None = None, *, tr_fn: Callable[[str, str], str]) -> None:
        super().__init__(parent)
        self._tr = tr_fn
        self._scene_key = ""
        self._phase = STATIC_PHASE
        self._paused = False
        # С какого места круга идёт отсчёт после снятия с паузы, мс.
        self._clock_offset_ms = 0.0
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._on_tick)
        self.setFixedHeight(ILLUSTRATION_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Пауза — в правом верхнем углу: там на схеме пусто.
        self.pause_button = TransparentToolButton(FluentIcon.PAUSE, self)
        self.pause_button.setFixedSize(PAUSE_BUTTON_SIZE, PAUSE_BUTTON_SIZE)
        self.pause_button.setIconSize(QSize(12, 12))
        self.pause_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pause_button.clicked.connect(self.toggle_paused)
        self.pause_button.hide()
        self._sync_pause_button()

    # ── публичное ─────────────────────────────────────────────────────

    def scene_key(self) -> str:
        return self._scene_key

    def set_scene(self, key: str) -> None:
        key = key if key in SCENES else ""
        if key == self._scene_key:
            return
        self._scene_key = key
        self._restart()

    def is_animating(self) -> bool:
        return self._timer.isActive()

    def is_paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool) -> None:
        """Остановить схему на текущем кадре или пустить дальше с него же."""
        paused = bool(paused)
        if paused == self._paused or not self._can_animate():
            return
        self._paused = paused
        if paused:
            self._timer.stop()
        else:
            self._clock_offset_ms = self._phase * PERIOD_MS
            self._clock.start()
            self._timer.start()
        self._sync_pause_button()

    def toggle_paused(self) -> None:
        self.set_paused(not self._paused)

    def phase(self) -> float:
        return self._phase

    def set_phase(self, phase: float) -> None:
        """Кадр в заданный момент круга (для снимков и тестов)."""
        self._phase = max(0.0, min(0.999, float(phase)))
        self.update()

    # ── жизненный цикл ────────────────────────────────────────────────

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        self._restart()

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().hideEvent(event)
        self._timer.stop()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self.pause_button.move(self.width() - self.pause_button.width(), 0)

    def _can_animate(self) -> bool:
        return bool(self._scene_key) and self.isVisible() and are_live_animations_enabled()

    def _restart(self) -> None:
        """Новая схема или новый показ: круг идёт с начала, пауза снята."""
        animate = self._can_animate()
        self._paused = False
        self._clock_offset_ms = 0.0
        # Без анимаций кадр один и ставить на паузу нечего — кнопка не нужна.
        self.pause_button.setVisible(animate)
        self._sync_pause_button()
        if not animate:
            self._timer.stop()
            self._phase = STATIC_PHASE
            self.update()
            return
        self._phase = 0.0
        self._clock.start()
        self._timer.start()
        self.update()

    def _on_tick(self) -> None:
        elapsed = self._clock.elapsed() if self._clock.isValid() else 0
        self._phase = ((self._clock_offset_ms + elapsed) % PERIOD_MS) / PERIOD_MS
        self.update()

    def _sync_pause_button(self) -> None:
        if self._paused:
            icon, text = FluentIcon.PLAY, self._tr("onboarding.scene.resume", "Продолжить анимацию")
        else:
            icon, text = FluentIcon.PAUSE, self._tr("onboarding.scene.pause", "Остановить анимацию")
        self.pause_button.setIcon(icon)
        self.pause_button.setAccessibleName(text)
        set_tooltip(self.pause_button, text)

    # ── отрисовка ─────────────────────────────────────────────────────

    def paintEvent(self, event):  # noqa: N802 (Qt override)
        _ = event
        scene = SCENES.get(self._scene_key)
        if scene is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            self._paint_scene(painter, scene, self._phase)
        finally:
            painter.end()

    def _palette(self) -> dict[str, QColor]:
        dark = isDarkTheme()
        return {
            "text": QColor(235, 235, 235) if dark else QColor(30, 30, 30),
            "muted": QColor(160, 160, 160) if dark else QColor(110, 110, 110),
            "node": QColor(255, 255, 255, 16) if dark else QColor(0, 0, 0, 10),
            "node_border": QColor(255, 255, 255, 40) if dark else QColor(0, 0, 0, 40),
            "track": QColor(255, 255, 255, 36) if dark else QColor(0, 0, 0, 36),
            "accent": QColor(themeColor()),
        }

    def _layout(self) -> _Layout:
        width = float(self.width())
        node_w, gate_w = 78.0, 100.0
        track_y = 70.0
        you_rect = QRectF(4, track_y - 26, node_w, 52)
        site_rect = QRectF(width - 4 - node_w, track_y - 26, node_w, 52)
        gate_rect = QRectF(width / 2 - gate_w / 2, track_y - 36, gate_w, 72)
        return _Layout(you_rect, gate_rect, site_rect, you_rect.right() + 8, site_rect.left() - 8, track_y)

    def _chip_font(self) -> QFont:
        font = QFont(self.font())
        font.setPointSizeF(max(7.5, font.pointSizeF() - 1.0))
        return font

    def _chip_widths(self, scene: Scene, metrics: QFontMetrics) -> list[float]:
        return [self._chip_width(metrics, self._packet_label(packet, False)) for packet in scene.packets]

    @staticmethod
    def _blocked_x(layout: _Layout, chip_width: float) -> float:
        return layout.gate_rect.left() - chip_width / 2 - 6

    def scene_times(self, scene: Scene | None = None) -> SceneTimes:
        """Ключевые моменты круга для сцены (по умолчанию — текущей)."""
        scene = scene or SCENES[self._scene_key]
        layout = self._layout()
        chips = self._chip_widths(scene, QFontMetrics(self._chip_font()))
        return self._scene_times(scene, layout, chips)

    def _scene_times(self, scene: Scene, layout: _Layout, chips: list[float]) -> SceneTimes:
        span = max(1.0, layout.end_x - layout.start_x)
        starts = tuple(self._start_times(scene, chips, span))
        trigger = scene.packets[scene.bubble_trigger]
        if trigger.fate == "blocked":
            # Реплика и обрыв — ровно когда плашка упирается в проверку.
            target = self._blocked_x(layout, chips[scene.bubble_trigger])
        else:
            # Реплика — когда середина плашки в середине проверки.
            target = layout.gate_x
        verdict = starts[scene.bubble_trigger] + TRAVEL * _clamp01((target - layout.start_x) / span)
        arrivals = [starts[i] + TRAVEL for i, packet in enumerate(scene.packets) if packet.fate == "pass"]
        site_done = max(arrivals) if (scene.site_result and arrivals) else None
        ends = [verdict + max(POP, SHAKE)]
        for index, packet in enumerate(scene.packets):
            if packet.fate == "die":
                # Подделка гаснет на полпути от проверки до сайта.
                ends.append(starts[index] + TRAVEL)
            elif packet.fate == "discard":
                ends.append(starts[index] + TRAVEL + DISCARD)
        if site_done is not None:
            ends.append(site_done + max(POP, PULSE))
        return SceneTimes(starts=starts, verdict=verdict, site_done=site_done, settled=max(ends))

    def chip_frames(self, phase: float) -> list[ChipFrame]:
        """Плашки текущей сцены в момент phase (для тестов и снимков)."""
        scene = SCENES.get(self._scene_key)
        if scene is None:
            return []
        layout = self._layout()
        chips = self._chip_widths(scene, QFontMetrics(self._chip_font()))
        times = self._scene_times(scene, layout, chips)
        return self._chip_frames(scene, phase, layout, chips, times)

    def _chip_frames(
        self, scene: Scene, phase: float, layout: _Layout, chips: list[float], times: SceneTimes
    ) -> list[ChipFrame]:
        span = layout.end_x - layout.start_x
        frames: list[ChipFrame] = []
        for index, packet in enumerate(scene.packets):
            raw = (phase - times.starts[index]) / TRAVEL
            if raw <= 0.0:
                continue
            x = layout.start_x + span * min(raw, 1.0)
            appear = _ease_out(raw * TRAVEL / APPEAR)
            alpha, scale, dy, angle = appear, 0.85 + 0.15 * appear, 0.0, 0.0
            # Пакет слит, пока его начало не доехало до сайта.
            glued = packet.glued_to_next and index + 1 < len(scene.packets) and raw < 1.0
            behind_glued = index > 0 and scene.packets[index - 1].glued_to_next
            flat = ""
            if glued:
                flat = "left"
            elif behind_glued and phase < times.starts[index - 1] + TRAVEL:
                flat = "right"
            if glued or behind_glued:
                scale = 1.0  # половины одного пакета не расходятся при появлении
            if behind_glued:
                alpha = 1.0  # вторая половина выезжает из-за «Вы» сразу плотной, как первая
            if packet.fate == "blocked":
                wall = self._blocked_x(layout, chips[index])
                if x >= wall:
                    x = wall
                    shake = _clamp01((phase - times.verdict) / SHAKE)
                    if shake < 1.0:
                        x += math.sin(shake * math.pi * 5) * 5.0 * (1.0 - shake)
            elif packet.fate == "die" and x > layout.gate_x:
                # Подделка проседает, кренится и гаснет между проверкой и сайтом.
                gone = _ease_in((x - layout.gate_x) / max(1.0, (layout.end_x - layout.gate_x) * 0.55))
                alpha *= 1.0 - gone
                dy, scale, angle = 12.0 * gone, scale * (1.0 - 0.2 * gone), 14.0 * gone
            elif packet.fate == "discard" and raw >= 1.0:
                # Мусор приехал первым, и сайт его отбрасывает: он падает с дорожки
                # у входа, а данные следом входят внутрь.
                t = _clamp01((phase - times.starts[index] - TRAVEL) / DISCARD)
                drop = _ease_out(t)
                alpha *= 1.0 - _ease_in(t)
                dy, scale, angle = 30.0 * drop, scale * (1.0 - 0.15 * t), 18.0 * drop
            elif packet.fate == "pass":
                if raw >= 1.0:
                    continue  # дошла — дальше её показывает итог у сайта
                absorb = _clamp01((raw - (1.0 - ABSORB)) / ABSORB)
                alpha *= 1.0 - _ease_in(absorb)
                scale *= 1.0 - 0.3 * absorb
            if alpha <= 0.01:
                continue
            label = self._packet_label(packet, x > layout.gate_x)
            frames.append(ChipFrame(index, label, packet.kind, x, dy, alpha, scale, angle, glued, flat, chips[index]))
        return frames

    def _paint_scene(self, painter: QPainter, scene: Scene, phase: float) -> None:
        colors = self._palette()
        layout = self._layout()
        chip_font = self._chip_font()
        metrics = QFontMetrics(chip_font)
        chips = self._chip_widths(scene, metrics)
        times = self._scene_times(scene, layout, chips)
        frames = self._chip_frames(scene, phase, layout, chips, times)
        fade = 1.0 if phase < FADE_FROM else max(0.0, 1.0 - (phase - FADE_FROM) / (1.0 - FADE_FROM))
        blocked = 0.0 if scene.site_result else _ease_out((phase - times.verdict) / POP)
        done = 0.0 if times.site_done is None else _ease_out((phase - times.site_done) / POP)

        # Дорожка.
        painter.setPen(QPen(colors["track"], 2, Qt.PenStyle.DashLine))
        painter.drawLine(
            QPointF(layout.you_rect.right(), layout.track_y), QPointF(layout.site_rect.left(), layout.track_y)
        )

        self._paint_node(painter, layout.you_rect, self._tr("onboarding.scene.you", "Вы"), colors, None)
        # Подпись провайдера — в верхней части блока: плашки едут ниже и её не закрывают.
        self._paint_node(
            painter,
            layout.gate_rect,
            self._tr("onboarding.scene.provider", "Провайдер"),
            colors,
            BLOCK_RED if blocked > 0.0 else None,
            tint_strength=blocked * fade,
            text_rect=QRectF(layout.gate_rect.left(), layout.gate_rect.top() + 2, layout.gate_rect.width(), 20),
        )
        self._paint_scan(painter, layout, frames, chips, phase, colors)
        painter.setPen(colors["muted"])
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, small.pointSizeF() - 1.5))
        painter.setFont(small)
        painter.drawText(
            QRectF(layout.gate_rect.left(), layout.gate_rect.bottom() + 2, layout.gate_rect.width(), 16),
            Qt.AlignmentFlag.AlignCenter,
            self._tr("onboarding.scene.check", "проверка"),
        )
        self._paint_node(
            painter,
            layout.site_rect,
            self._tr("onboarding.scene.site", "Сайт"),
            colors,
            PASS_GREEN if done > 0.0 else None,
            tint_strength=done * fade,
        )
        if times.site_done is not None:
            self._paint_pulse(painter, layout.site_rect, (phase - times.site_done) / PULSE)

        # Плашки выезжают из-за края «Вы», а не рисуются поверх него.
        painter.save()
        painter.setClipRect(QRectF(layout.you_rect.right() + 1, 0, self.width(), self.height()))
        painter.setFont(chip_font)
        for frame in frames:
            self._paint_chip(painter, metrics, frame, layout.track_y, frame.alpha * fade, colors)
        for frame in frames:
            if frame.glued:
                self._paint_one_packet_caption(painter, frame, chips, layout, colors, fade)
        painter.restore()

        # Реплика проверки.
        if phase >= times.verdict:
            self._paint_bubble(painter, scene, layout.gate_rect, colors, fade, (phase - times.verdict) / POP)

        # Итог у сайта.
        if done > 0.0:
            painter.setFont(chip_font)
            self._paint_result(painter, metrics, scene.site_result, layout.site_rect, colors, fade * done, done)
        if blocked > 0.0:
            self._paint_cross(
                painter, QPointF(layout.gate_rect.left() - 4, layout.track_y), fade, (phase - times.verdict) / POP
            )

    @staticmethod
    def _start_times(scene: Scene, chips: list[float], track: float) -> list[float]:
        """Когда отправить каждую плашку, чтобы соседние не налезали друг на друга."""
        starts: list[float] = []
        moment = 0.0
        for index, packet in enumerate(scene.packets):
            previous = scene.packets[index - 1] if index else None
            if previous is not None:
                # Отдельные пакеты идут с просветом, части одного пакета — вплотную.
                gap = chips[index - 1] / 2 + chips[index] / 2
                if not previous.glued_to_next:
                    gap += CHIP_GAP
                moment += gap / max(1.0, track) * TRAVEL
            moment += packet.delay
            starts.append(moment)
        return starts

    def _packet_label(self, packet: Packet, past_gate: bool) -> str:
        text = packet.text_after_gate if (past_gate and packet.text_after_gate) else packet.text
        if packet.kind == "junk":
            return self._tr("onboarding.scene.junk", "мусор")
        if packet.kind == "syn" and not past_gate:
            return self._tr("onboarding.scene.syn_data", "SYN + данные")
        return f"{packet.part}. {text}" if packet.part else text

    @staticmethod
    def _chip_width(metrics: QFontMetrics, text: str) -> float:
        return float(metrics.horizontalAdvance(text) + 16)

    def _paint_node(
        self,
        painter: QPainter,
        rect: QRectF,
        text: str,
        colors,
        tint: QColor | None,
        *,
        tint_strength: float = 1.0,
        text_rect: QRectF | None = None,
    ) -> None:
        fill = QColor(colors["node"])
        border = QColor(colors["node_border"])
        if tint is not None:
            fill = self._mix(fill, QColor(tint.red(), tint.green(), tint.blue(), 46), tint_strength)
            border = self._mix(border, QColor(tint), tint_strength)
        painter.setPen(QPen(border, 1.5))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 10, 10)
        painter.setPen(colors["text"])
        font = QFont(self.font())
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(text_rect or rect, Qt.AlignmentFlag.AlignCenter, text)

    @staticmethod
    def _mix(a: QColor, b: QColor, k: float) -> QColor:
        k = _clamp01(k)
        return QColor(
            round(a.red() + (b.red() - a.red()) * k),
            round(a.green() + (b.green() - a.green()) * k),
            round(a.blue() + (b.blue() - a.blue()) * k),
            round(a.alpha() + (b.alpha() - a.alpha()) * k),
        )

    def _paint_scan(self, painter, layout: _Layout, frames, chips, phase: float, colors) -> None:
        """Пока плашка внутри проверки, блок подсвечен и по нему бегает луч."""
        gate = layout.gate_rect
        reach = gate.width() / 2
        glow = 0.0
        for frame in frames:
            near = 1.0 - abs(frame.x - layout.gate_x) / (reach + chips[frame.index] / 2)
            glow = max(glow, _clamp01(near) * frame.alpha)
        if glow <= 0.01:
            return
        accent = QColor(colors["accent"])
        painter.save()
        fill = QColor(accent)
        fill.setAlpha(round(34 * glow))
        border = QColor(accent)
        border.setAlpha(round(200 * glow))
        painter.setPen(QPen(border, 1.5))
        painter.setBrush(fill)
        painter.drawRoundedRect(gate, 10, 10)
        sweep = 0.5 - 0.5 * math.cos(phase * math.tau * 9)
        x = gate.left() + 10 + (gate.width() - 20) * sweep
        beam = QLinearGradient(x, gate.top() + 22, x, gate.bottom() - 6)
        edge = QColor(accent)
        edge.setAlpha(0)
        mid = QColor(accent)
        mid.setAlpha(round(220 * glow))
        beam.setColorAt(0.0, edge)
        beam.setColorAt(0.5, mid)
        beam.setColorAt(1.0, edge)
        painter.setPen(QPen(QBrush(beam), 2))
        painter.drawLine(QPointF(x, gate.top() + 22), QPointF(x, gate.bottom() - 6))
        painter.restore()

    def _paint_pulse(self, painter, rect: QRectF, t: float) -> None:
        """Кольцо расходится от сайта, когда он собрал итог."""
        if not 0.0 < t < 1.0:
            return
        grow = 12.0 * _ease_out(t)
        ring = QColor(PASS_GREEN)
        ring.setAlpha(round(170 * (1.0 - t)))
        painter.save()
        painter.setPen(QPen(ring, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(-grow, -grow, grow, grow), 10 + grow, 10 + grow)
        painter.restore()

    def _paint_one_packet_caption(self, painter, frame: ChipFrame, chips, layout: _Layout, colors, fade) -> None:
        """Подпись под слитой плашкой: мусор и данные — один пакет."""
        right = frame.x + chips[frame.index] / 2
        left = frame.x - chips[frame.index] / 2 - chips[frame.index + 1]
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
        painter.save()
        # Гаснет на подъезде к сайту: там пакет распадается.
        painter.setOpacity(frame.alpha * fade * _clamp01((layout.end_x - frame.x) / 24.0))
        painter.setFont(font)
        painter.setPen(colors["muted"])
        painter.drawText(
            QRectF(left, layout.track_y + 14, right - left, 16),
            Qt.AlignmentFlag.AlignCenter,
            self._tr("onboarding.scene.one_packet", "один пакет"),
        )
        painter.restore()

    @staticmethod
    def _chip_shape(rect: QRectF, radius: float, flat: str) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        if flat:
            half = QRectF(rect)
            if flat == "left":
                half.setRight(rect.center().x())
            else:
                half.setLeft(rect.center().x())
            square = QPainterPath()
            square.addRect(half)
            path = path.united(square)
        return path

    def _paint_chip(self, painter, metrics, frame: ChipFrame, track_y: float, alpha: float, colors) -> None:
        text, kind = frame.label, frame.kind
        width = self._chip_width(metrics, text)
        rect = QRectF(-width / 2, -12, width, 24)
        painter.save()
        painter.translate(frame.x, track_y + frame.dy)
        painter.rotate(frame.angle)
        painter.scale(frame.scale, frame.scale)
        painter.setOpacity(alpha)
        if kind == "real" or kind == "syn":
            fill = QColor(colors["accent"])
            # Мягкое свечение под настоящей плашкой.
            glow = QColor(fill)
            glow.setAlpha(50)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            halo = rect.adjusted(-3, -3, 3, 3)
            if frame.flat == "right":
                halo.setRight(rect.right())
            elif frame.flat == "left":
                halo.setLeft(rect.left())
            painter.drawPath(self._chip_shape(halo, 10, frame.flat))
            painter.setBrush(fill)
            painter.drawPath(self._chip_shape(rect, 7, frame.flat))
            text_color = _readable_text_on(fill)
        else:
            base = FAKE_AMBER if kind == "fake" else JUNK_GREY
            fill = QColor(base)
            fill.setAlpha(60)
            painter.setBrush(fill)
            painter.setPen(QPen(base, 1.5, Qt.PenStyle.DashLine))
            painter.drawPath(self._chip_shape(rect, 7, frame.flat))
            text_color = colors["text"]
        if "#" in text:
            self._paint_oob_text(painter, metrics, text, rect, text_color)
        else:
            painter.setPen(text_color)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def _paint_oob_text(self, painter, metrics, text, rect: QRectF, text_color: QColor) -> None:
        """Лишний «срочный» байт подсвечен красным внутри имени."""
        x = rect.center().x() - metrics.horizontalAdvance(text) / 2
        baseline = rect.center().y() + (metrics.ascent() - metrics.descent()) / 2
        for char in text:
            painter.setPen(BLOCK_RED if char == "#" else text_color)
            painter.drawText(QPointF(x, baseline), char)
            x += metrics.horizontalAdvance(char)

    def _paint_bubble(self, painter, scene: Scene, gate_rect: QRectF, colors, fade: float, t: float) -> None:
        text = self._tr(f"onboarding.scene.bubble.{scene.bubble_key}", scene.bubble_key)
        tone = {"block": BLOCK_RED, "confused": FAKE_AMBER}.get(scene.bubble_tone, PASS_GREEN)
        font = QFont(self.font())
        font.setPointSizeF(max(7.5, font.pointSizeF() - 1.0))
        painter.save()
        painter.setOpacity(fade * _ease_out(t))
        painter.setFont(font)
        metrics = QFontMetrics(font)
        width = metrics.horizontalAdvance(text) + 18
        rect = QRectF(gate_rect.center().x() - width / 2, 2, width, 24)
        rect.moveLeft(max(0.0, min(rect.left(), self.width() - width)))
        tip_x = gate_rect.center().x()
        # Реплика выскакивает из блока проверки: растёт от кончика хвостика.
        grow = 0.6 + 0.4 * _ease_out_back(t)
        anchor = QPointF(tip_x, rect.bottom() + 6)
        painter.translate(anchor)
        painter.scale(grow, grow)
        painter.translate(-anchor)
        fill = QColor(tone)
        fill.setAlpha(40)
        painter.setBrush(fill)
        painter.setPen(QPen(tone, 1.2))
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        path.moveTo(tip_x - 5, rect.bottom())
        path.lineTo(tip_x, rect.bottom() + 6)
        path.lineTo(tip_x + 5, rect.bottom())
        painter.drawPath(path.simplified())
        painter.setPen(colors["text"])
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def _paint_result(self, painter, metrics, text, site_rect: QRectF, colors, alpha: float, t: float) -> None:
        label = f"✓ {text}"
        width = self._chip_width(metrics, label)
        # Итог выезжает снизу вверх под сайтом.
        rect = QRectF(site_rect.right() - width, site_rect.bottom() + 20 + 8 * (1.0 - t), width, 24)
        painter.save()
        painter.setOpacity(alpha)
        fill = QColor(PASS_GREEN)
        fill.setAlpha(50)
        painter.setBrush(fill)
        painter.setPen(QPen(PASS_GREEN, 1.2))
        painter.drawRoundedRect(rect, 7, 7)
        painter.setPen(colors["text"])
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()

    def _paint_cross(self, painter, center: QPointF, fade: float, t: float) -> None:
        size = 9.0 * _ease_out_back(t)
        if size <= 0.1:
            return
        painter.save()
        painter.setOpacity(fade * _ease_out(t))
        painter.setPen(QPen(BLOCK_RED, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        x, y = center.x(), center.y() + 30
        painter.drawLine(QPointF(x - size, y - size), QPointF(x + size, y + size))
        painter.drawLine(QPointF(x - size, y + size), QPointF(x + size, y - size))
        painter.restore()


__all__ = ["ILLUSTRATION_HEIGHT", "SCENES", "ChipFrame", "SceneTimes", "TechniqueIllustration"]
