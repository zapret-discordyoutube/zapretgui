"""Анимированные схемы техник обхода для обучающего тура.

Схема одна на все техники: слева «Вы», в середине проверка у провайдера,
справа сайт. По дорожке между ними бегут пакеты-плашки. Сцена описывает,
какие плашки отправить, когда, и что с ними станет:

- pass — плашка доходит до сайта;
- blocked — проверка узнаёт имя и обрывает соединение;
- die — поддельная плашка проходит проверку и гаснет, не дойдя до сайта.

Так видно главное: проверка провайдера видит одно, а сайт получает другое.
Сцены повторяются по кругу. Когда анимации в системе выключены, рисуется
один неподвижный кадр с итогом.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget
from qfluentwidgets import isDarkTheme, themeColor

from ui.animation_policy import are_live_animations_enabled


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
    fate: str = "pass"  # pass | blocked | die
    # Текст после проверки: у сайта лишнее выпадает (oob, syndata).
    text_after_gate: str = ""
    # Плашка едет вплотную впереди следующей (мусор seqovl в tcpseg).
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
            Packet("junk", kind="junk", fate="die", glued_to_next=True),
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
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._on_tick)
        self.setFixedHeight(ILLUSTRATION_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

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

    def _restart(self) -> None:
        animate = bool(self._scene_key) and self.isVisible() and are_live_animations_enabled()
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
        self._phase = (elapsed % PERIOD_MS) / PERIOD_MS
        self.update()

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

    def _paint_scene(self, painter: QPainter, scene: Scene, phase: float) -> None:
        colors = self._palette()
        width = float(self.width())
        node_w, gate_w = 78.0, 100.0
        track_y = 70.0
        you_rect = QRectF(4, track_y - 26, node_w, 52)
        site_rect = QRectF(width - 4 - node_w, track_y - 26, node_w, 52)
        gate_rect = QRectF(width / 2 - gate_w / 2, track_y - 36, gate_w, 72)
        start_x = you_rect.right() + 8
        end_x = site_rect.left() - 8
        gate_x = gate_rect.center().x()

        fade = 1.0 if phase < FADE_FROM else max(0.0, 1.0 - (phase - FADE_FROM) / (1.0 - FADE_FROM))

        # Дорожка.
        pen = QPen(colors["track"], 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(you_rect.right(), track_y), QPointF(site_rect.left(), track_y))

        chip_font = QFont(self.font())
        chip_font.setPointSizeF(max(7.5, chip_font.pointSizeF() - 1.0))
        metrics = QFontMetrics(chip_font)
        chips = [self._chip_width(metrics, self._packet_label(packet, False)) for packet in scene.packets]
        starts = self._start_times(scene, chips, end_x - start_x)

        trigger_start = starts[scene.bubble_trigger]
        blocked = scene.site_result == "" and phase >= trigger_start + TRAVEL * 0.5
        site_done = bool(scene.site_result) and all(
            phase >= starts[index] + TRAVEL
            for index, packet in enumerate(scene.packets)
            if packet.fate == "pass"
        )

        self._paint_node(painter, you_rect, self._tr("onboarding.scene.you", "Вы"), colors, None)
        gate_tint = BLOCK_RED if blocked else None
        # Подпись провайдера — в верхней части блока: плашки едут ниже и её не закрывают.
        self._paint_node(
            painter,
            gate_rect,
            self._tr("onboarding.scene.provider", "Провайдер"),
            colors,
            gate_tint,
            text_rect=QRectF(gate_rect.left(), gate_rect.top() + 2, gate_rect.width(), 20),
        )
        painter.setPen(colors["muted"])
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, small.pointSizeF() - 1.5))
        painter.setFont(small)
        painter.drawText(
            QRectF(gate_rect.left(), gate_rect.bottom() + 2, gate_rect.width(), 16),
            Qt.AlignmentFlag.AlignCenter,
            self._tr("onboarding.scene.check", "проверка"),
        )
        self._paint_node(
            painter,
            site_rect,
            self._tr("onboarding.scene.site", "Сайт"),
            colors,
            PASS_GREEN if site_done else None,
        )

        # Плашки.
        painter.setFont(chip_font)
        for index, packet in enumerate(scene.packets):
            progress = (phase - starts[index]) / TRAVEL
            if progress <= 0.0:
                continue
            progress = min(progress, 1.0)
            x = start_x + (end_x - start_x) * progress
            if packet.fate == "blocked":
                x = min(x, gate_rect.left() - chips[index] / 2 - 6)
            if packet.glued_to_next and index + 1 < len(scene.packets):
                x -= chips[index + 1] / 2 + chips[index] / 2 + 1
            past_gate = x > gate_x
            alpha = min(1.0, progress / 0.06) * fade
            if packet.fate == "die" and past_gate:
                # Поддельная плашка гаснет между проверкой и сайтом.
                span = (end_x - gate_x) * 0.55
                alpha *= max(0.0, 1.0 - (x - gate_x) / max(1.0, span))
            if packet.fate == "pass" and progress >= 1.0:
                continue  # дошла — дальше её показывает итог у сайта
            if alpha <= 0.01:
                continue
            label = self._packet_label(packet, past_gate)
            self._paint_chip(painter, metrics, label, packet.kind, QPointF(x, track_y), alpha, colors)

        # Реплика проверки.
        if phase >= trigger_start + TRAVEL * 0.5:
            self._paint_bubble(painter, scene, gate_rect, colors, fade)

        # Итог у сайта.
        if site_done:
            painter.setFont(chip_font)
            self._paint_result(painter, metrics, scene.site_result, site_rect, colors, fade)
        if blocked:
            self._paint_cross(painter, QPointF(gate_rect.left() - 4, track_y), fade)

    @staticmethod
    def _start_times(scene: Scene, chips: list[float], track: float) -> list[float]:
        """Когда отправить каждую плашку, чтобы соседние не налезали друг на друга."""
        starts: list[float] = []
        moment = 0.0
        for index, packet in enumerate(scene.packets):
            previous = scene.packets[index - 1] if index else None
            if previous is not None and not previous.glued_to_next:
                gap = chips[index - 1] / 2 + chips[index] / 2 + CHIP_GAP
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
        text_rect: QRectF | None = None,
    ) -> None:
        fill = QColor(colors["node"])
        border = QColor(colors["node_border"])
        if tint is not None:
            fill = QColor(tint)
            fill.setAlpha(46)
            border = QColor(tint)
        painter.setPen(QPen(border, 1.5))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 10, 10)
        painter.setPen(colors["text"])
        font = QFont(self.font())
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(text_rect or rect, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_chip(self, painter, metrics, text, kind, center: QPointF, alpha: float, colors) -> None:
        width = self._chip_width(metrics, text)
        rect = QRectF(center.x() - width / 2, center.y() - 12, width, 24)
        painter.save()
        painter.setOpacity(alpha)
        if kind == "real" or kind == "syn":
            fill = QColor(colors["accent"])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, 7, 7)
            text_color = _readable_text_on(fill)
        else:
            base = FAKE_AMBER if kind == "fake" else JUNK_GREY
            fill = QColor(base)
            fill.setAlpha(60)
            painter.setBrush(fill)
            painter.setPen(QPen(base, 1.5, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(rect, 7, 7)
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

    def _paint_bubble(self, painter, scene: Scene, gate_rect: QRectF, colors, fade: float) -> None:
        text = self._tr(f"onboarding.scene.bubble.{scene.bubble_key}", scene.bubble_key)
        tone = {"block": BLOCK_RED, "confused": FAKE_AMBER}.get(scene.bubble_tone, PASS_GREEN)
        font = QFont(self.font())
        font.setPointSizeF(max(7.5, font.pointSizeF() - 1.0))
        painter.save()
        painter.setOpacity(fade)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        width = metrics.horizontalAdvance(text) + 18
        rect = QRectF(gate_rect.center().x() - width / 2, 2, width, 24)
        rect.moveLeft(max(0.0, min(rect.left(), self.width() - width)))
        fill = QColor(tone)
        fill.setAlpha(40)
        painter.setBrush(fill)
        painter.setPen(QPen(tone, 1.2))
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        tip_x = gate_rect.center().x()
        path.moveTo(tip_x - 5, rect.bottom())
        path.lineTo(tip_x, rect.bottom() + 6)
        path.lineTo(tip_x + 5, rect.bottom())
        painter.drawPath(path.simplified())
        painter.setPen(colors["text"])
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def _paint_result(self, painter, metrics, text, site_rect: QRectF, colors, fade: float) -> None:
        label = f"✓ {text}"
        width = self._chip_width(metrics, label)
        rect = QRectF(site_rect.right() - width, site_rect.bottom() + 20, width, 24)
        painter.save()
        painter.setOpacity(fade)
        fill = QColor(PASS_GREEN)
        fill.setAlpha(50)
        painter.setBrush(fill)
        painter.setPen(QPen(PASS_GREEN, 1.2))
        painter.drawRoundedRect(rect, 7, 7)
        painter.setPen(colors["text"])
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()

    def _paint_cross(self, painter, center: QPointF, fade: float) -> None:
        painter.save()
        painter.setOpacity(fade)
        painter.setPen(QPen(BLOCK_RED, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        size = 9.0
        x, y = center.x(), center.y() + 30
        painter.drawLine(QPointF(x - size, y - size), QPointF(x + size, y + size))
        painter.drawLine(QPointF(x - size, y + size), QPointF(x + size, y - size))
        painter.restore()


__all__ = ["ILLUSTRATION_HEIGHT", "SCENES", "TechniqueIllustration"]
