"""Сцена обхода для карточки «Статус работы».

Слева талисман-медоед (это Zapret на вашем компьютере), справа сайты,
посередине стена блокировки, а в стене — круглая кнопка питания: это и есть
выключатель Zapret.

- Zapret остановлен: медоед замахивается и кидает пакеты, они разбиваются о
  стену и отскакивают, медоед вздрагивает. Ответы не приходят.
- Запуск не удался: то же самое, но медоед грустит и уже ничего не кидает.
- Идёт запуск или остановка: пакеты доходят до стены и ждут, вокруг кнопки
  бегает дуга, медоед суетится.
- Zapret работает: стена бледнеет, пакеты проходят сквозь кнопку и
  окрашиваются в её цвет, по нижней дорожке летят ответы. Медоед радостно
  подпрыгивает в момент включения, а потом спокойно дышит и оглядывается.

Кадры идут, только пока сцена видна, окно не свёрнуто и включены «живые
анимации». В остановленном состоянии это короткий залп и пауза (таймер
между залпами одиночный), а на ходу перерисовываются только дорожки и кнопка.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QEasingCurve, QPoint, QPointF, QRect, QRectF, QSize, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QRegion
from PyQt6.QtWidgets import QSizePolicy

from ui.animation_policy import are_live_animations_enabled
from ui.pulsing_dot import PulsingDot
from ui.widgets.fun.mascot import GESTURE_TOSS, MOOD_ALARM, MOOD_BUSY, MOOD_HAPPY, MOOD_IDLE, MOOD_SAD, Mascot


SCENE_HEIGHT = 76
SCENE_WIDTH = 330
SCENE_MIN_WIDTH = 240
MASCOT_SIZE = 40
# Место под значок сайтов справа; слева стоит талисман.
ENDPOINT_ROOM = 34
# Полный вдох и выдох спокойного талисмана, секунды.
BREATH_PERIOD_S = 3.6
# Период, с которым дышит ореол работающей кнопки, секунды.
HALO_PERIOD_S = 2.8
LANE_GAP = 6
RAW_COLOR = QColor(150, 156, 168)

BUTTON_RADIUS = 15.0
HOVER_FADE_MS = 160
PRESS_SCALE = 0.9
WALL_HALF_HEIGHT = 33.0
WALL_FADE_MS = 420
WALL_BLOCKS = 3

# (направление, скорость в px/с, сдвиги пакетов вдоль дорожки в долях длины)
FLOW_LANES = (
    (1, 46.0, (0.0, 0.31, 0.47, 0.78)),
    (-1, 36.0, (0.12, 0.58, 0.66)),
)
# Залп о стену: три пакета вылетают один за другим.
BLOCKED_BURST_MS = 1500
BLOCKED_REST_MS = 3200
BLOCKED_PACKET_DELAYS = (0.0, 0.14, 0.28)
BLOCKED_FLIGHT = 0.62
SHAKE_MS = 460
# Сцена крупнее точки, поэтому кадры реже: 20 в секунду хватает для плавности.
SCENE_FRAME_MS = 50

BUSY_PHASES = frozenset({"autostart_pending", "starting", "stopping"})
KNOWN_PHASES = frozenset({"running", "failed", "stopped"}) | BUSY_PHASES
# Доля залпа, на которой первый пакет долетает до стены.
FIRST_IMPACT_PHASE = BLOCKED_FLIGHT * (1.0 - BLOCKED_PACKET_DELAYS[-1])


def mascot_mood_for_phase(phase: str, previous: str = "") -> str:
    """Настроение талисмана по состоянию Zapret."""
    if phase == "running":
        # Радуется, когда обход включился на глазах; при первом показе просто сидит.
        return MOOD_HAPPY if previous and previous != "running" else MOOD_IDLE
    if phase in BUSY_PHASES:
        return MOOD_BUSY
    if phase == "failed":
        return MOOD_SAD
    return MOOD_ALARM


class BypassScene(PulsingDot):
    """Выключатель Zapret с живой картинкой: пакеты, стена и кнопка в ней."""

    clicked = pyqtSignal()
    # Фаза сменилась: талисман меняет настроение, по карточке идёт волна.
    phaseChanged = pyqtSignal(str)
    # Итоговый цвет кнопки сменился: карточка красит им свой фон.
    colorChanged = pyqtSignal(str)
    # Кадр потока при работающем обходе (время потока в секундах): по нему
    # карточка двигает своё свечение, не заводя собственный таймер.
    flowFrame = pyqtSignal(float)

    def __init__(self, parent=None, *, width: int = SCENE_WIDTH):
        super().__init__(parent, size=SCENE_HEIGHT)
        self._preferred_width = max(SCENE_MIN_WIDTH, int(width))
        self.setMinimumSize(SCENE_MIN_WIDTH, SCENE_HEIGHT)
        self.setMaximumSize(self._preferred_width, SCENE_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        self._beat.setInterval(SCENE_FRAME_MS)

        # Талисман — участник сцены: это он отправляет пакеты к сайтам.
        self._mascot = Mascot(self, size=MASCOT_SIZE)
        self._mascot.move(0, SCENE_HEIGHT // 2 - (self._mascot.height() - 2 - MASCOT_SIZE // 2))
        self._flinched = False

        # Политика фокуса обычного виджета: к ней сцена вернётся, если перестанет быть кнопкой.
        self._plain_focus_policy = self.focusPolicy()
        self._phase = ""
        self._flow_time = 0.0
        self._flow_origin = 0.0
        self._clickable = False
        self._click_enabled = True
        self._click_locked = False
        self._hovered = False
        self._pressed = False
        self._hover_t = 0.0
        self._open_t = 0.0
        self._shake_t = 0.0

        # QVariantAnimation, а не QPropertyAnimation: при выключенных
        # анимациях общий fallback подменяет QPropertyAnimation.start.
        self._hover_fade = QVariantAnimation(self)
        self._hover_fade.setDuration(HOVER_FADE_MS)
        self._hover_fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_fade.valueChanged.connect(self._on_hover_value)

        self._wall_fade = QVariantAnimation(self)
        self._wall_fade.setDuration(WALL_FADE_MS)
        self._wall_fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._wall_fade.valueChanged.connect(self._on_wall_value)

        self._shake = QVariantAnimation(self)
        self._shake.setStartValue(0.0)
        self._shake.setEndValue(1.0)
        self._shake.setDuration(SHAKE_MS)
        self._shake.valueChanged.connect(self._on_shake_value)
        self._shake.finished.connect(self._on_shake_finished)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._preferred_width, SCENE_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(SCENE_MIN_WIDTH, SCENE_HEIGHT)

    # ---- фаза ----------------------------------------------------------

    def set_color(self, color: str) -> None:
        before = QColor(self._color)
        super().set_color(color)
        if self._color != before:
            self.colorChanged.emit(self._color.name())

    def target_color(self) -> QColor:
        """Цвет, к которому кнопка придёт после перетекания."""
        return QColor(self._color)

    def phase(self) -> str:
        return self._phase

    def set_phase(self, phase: str) -> None:
        phase = str(phase or "").strip().lower()
        if phase not in KNOWN_PHASES:
            phase = "stopped"
        if phase == self._phase:
            return
        previous = self._phase
        self._phase = phase
        self._mascot.set_mood(mascot_mood_for_phase(phase, previous))
        self._animate_wall()
        # Встряска — только когда запуск сорвался на глазах, а не при первом показе.
        if phase == "failed" and previous and self.isVisible() and are_live_animations_enabled():
            self._shake.stop()
            self._shake.start()
        self._halt()
        self._resume()
        self.update()
        self.phaseChanged.emit(phase)

    def mascot(self) -> Mascot:
        return self._mascot

    def gate_center(self) -> QPoint:
        """Центр кнопки в координатах сцены: отсюда по карточке расходится волна."""
        left, right, _top, _bottom = self._lanes()
        return QPoint(round((left + right) / 2), self.height() // 2)

    def _is_flowing(self) -> bool:
        return self._phase == "running" or self._phase in BUSY_PHASES

    def _open_target(self) -> float:
        return 1.0 if self._phase == "running" else 0.0

    def _animate_wall(self) -> None:
        target = self._open_target()
        self._wall_fade.stop()
        if self.isVisible() and are_live_animations_enabled() and abs(target - self._open_t) > 0.001:
            self._wall_fade.setStartValue(self._open_t)
            self._wall_fade.setEndValue(target)
            self._wall_fade.start()
        else:
            self._open_t = target

    def _on_wall_value(self, value) -> None:
        try:
            self._open_t = float(value)
        except (TypeError, ValueError):
            return
        self.update()

    def _on_shake_value(self, value) -> None:
        try:
            self._shake_t = float(value)
        except (TypeError, ValueError):
            return
        self.update()

    def _on_shake_finished(self) -> None:
        self._shake_t = 0.0
        self.update()

    # ---- режим кнопки --------------------------------------------------

    def set_clickable(self, clickable: bool) -> None:
        self._clickable = bool(clickable)
        self.setMouseTracking(self._clickable)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus if self._clickable else self._plain_focus_policy)
        self._sync_cursor()
        self.update()

    def is_clickable(self) -> bool:
        return self._clickable

    def set_click_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._click_enabled:
            return
        self._click_enabled = enabled
        if not enabled:
            self._pressed = False
        self._sync_cursor()
        self._animate_hover()
        self.update()

    def set_click_locked(self, locked: bool) -> None:
        """Временная блокировка, пока идёт загрузка. Не спорит с set_click_enabled."""
        locked = bool(locked)
        if locked == self._click_locked:
            return
        self._click_locked = locked
        if locked:
            self._pressed = False
        self._sync_cursor()
        self._animate_hover()
        self.update()

    def is_click_enabled(self) -> bool:
        return self._clickable and self._click_enabled and not self._click_locked

    def click(self) -> None:
        """Нажатие кнопки (в том числе с клавиатуры через enable_keyboard_click)."""
        if self.is_click_enabled():
            self.clicked.emit()

    def button_rect(self) -> QRect:
        """Кнопка вместе со свечением: по этой области ловится мышь."""
        center = self.gate_center()
        reach = int(BUTTON_RADIUS) + 8
        return QRect(center.x() - reach, center.y() - reach, reach * 2, reach * 2)

    def _is_over_button(self, point) -> bool:
        center = self.gate_center()
        return math.hypot(point.x() - center.x(), point.y() - center.y()) <= BUTTON_RADIUS + 8

    def _sync_cursor(self) -> None:
        if self._hovered and self.is_click_enabled():
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()

    def _hover_target(self) -> float:
        return 1.0 if (self._hovered or self.hasFocus()) and self.is_click_enabled() else 0.0

    def _animate_hover(self) -> None:
        target = self._hover_target()
        if abs(target - self._hover_t) < 0.001:
            return
        self._hover_fade.stop()
        if self.isVisible() and are_live_animations_enabled():
            self._hover_fade.setStartValue(self._hover_t)
            self._hover_fade.setEndValue(target)
            self._hover_fade.start()
        else:
            self._hover_t = target
            self.update(self.button_rect())

    def _on_hover_value(self, value) -> None:
        try:
            self._hover_t = float(value)
        except (TypeError, ValueError):
            return
        self.update(self.button_rect())

    def _set_hovered(self, hovered: bool) -> None:
        if hovered == self._hovered:
            return
        self._hovered = hovered
        if not hovered:
            self._pressed = False
        self._sync_cursor()
        self._animate_hover()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._clickable:
            self._set_hovered(self._is_over_button(event.position()))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self._set_hovered(False)

    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        self._animate_hover()

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self._animate_hover()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if (
            self.is_click_enabled()
            and event.button() == Qt.MouseButton.LeftButton
            and self._is_over_button(event.position())
        ):
            self._pressed = True
            self.update(self.button_rect())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._pressed and event.button() == Qt.MouseButton.LeftButton:
            self._pressed = False
            self.update(self.button_rect())
            event.accept()
            if self._is_over_button(event.position()):
                self.click()
            return
        super().mouseReleaseEvent(event)

    # ---- кадры ---------------------------------------------------------

    def stop_pulse(self) -> None:
        # Сцена живёт и без «пульса»: остановленный Zapret показывает залпы о стену.
        self._is_pulsing = False
        self._resume()
        self.update()

    def _can_animate(self) -> bool:
        if not self._phase or not self.isVisible():
            return False
        window = self.window()
        if window is not None and window.isMinimized():
            return False
        return are_live_animations_enabled()

    def _start_beat(self) -> None:
        if self._can_animate():
            if self._is_flowing():
                # Поток продолжается с того места, где остановился, без рывка.
                self._flow_origin = self._flow_time
            else:
                self._flinched = False
                if self._phase == "stopped":
                    # Новый залп: медоед замахивается и кидает пакеты в стену.
                    self._mascot.react(GESTURE_TOSS)
        super()._start_beat()

    def _on_beat_frame(self) -> None:
        # В потоке пауз нет, поэтому на каждом кадре заново проверяем,
        # можно ли ещё анимировать (окно свернули, анимации выключили).
        if not self._can_animate():
            self._halt()
            self.update()
            return
        if self._is_flowing():
            self._flow_time = self._flow_origin + self._beat_clock.elapsed() / 1000.0
            if self._phase == "running":
                self._mascot.set_breath(math.sin(2 * math.pi * self._flow_time / BREATH_PERIOD_S))
                self.flowFrame.emit(self._flow_time)
            self.update(self._motion_region())
            return
        phase = self._beat_clock.elapsed() / BLOCKED_BURST_MS
        if phase < 1.0:
            self._pulse_phase = phase
            if phase >= FIRST_IMPACT_PHASE and not self._flinched:
                # Первый пакет разбился о стену: медоед вздрагивает.
                self._flinched = True
                if self._phase == "stopped":
                    self._mascot.react(MOOD_ALARM)
            self.update(self._motion_region())
            return
        self._beat.stop()
        self._pulse_phase = 0.0
        self.update(self._motion_region())
        if self._can_animate():
            self._rest_timer.start(BLOCKED_REST_MS)

    def _lanes(self) -> tuple[float, float, float, float]:
        """(левый край дорожек, правый край, y верхней дорожки, y нижней)."""
        center_y = self.height() / 2
        return (
            float(self._mascot.width() + 2),
            float(self.width() - ENDPOINT_ROOM),
            center_y - LANE_GAP,
            center_y + LANE_GAP,
        )

    def _motion_region(self) -> QRegion:
        """Что меняется от кадра к кадру: полоса дорожек, стена и кнопка."""
        left, right, top, bottom = self._lanes()
        lanes = QRect(int(left) - 2, int(top) - 12, int(right - left) + 4, int(bottom - top) + 24)
        center = self.gate_center()
        wall = QRect(center.x() - 8, 0, 16, self.height())
        return QRegion(lanes) | QRegion(self.button_rect()) | QRegion(wall)

    # ---- отрисовка -----------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        if self._shake_t > 0.0:
            t = self._shake_t
            painter.translate(4.0 * math.sin(6 * math.pi * t) * (1.0 - t), 0.0)

        center = QPointF(self.gate_center())
        left, right, top, bottom = self._lanes()
        gate = BUTTON_RADIUS + 3.0
        open_t = self._open_t
        color = self._shown_color

        self._paint_globe(painter, center, color, open_t)

        track = QColor(RAW_COLOR)
        track.setAlphaF(0.16)
        painter.setBrush(track)
        painter.drawRect(QRectF(left, top - 0.5, right - left, 1.0))
        # Нижняя дорожка — путь ответов: без обхода она обрывается у стены.
        back = QColor(RAW_COLOR)
        back.setAlphaF(0.16 * (0.35 + 0.65 * open_t))
        painter.setBrush(back)
        painter.drawRect(QRectF(left, bottom - 0.5, right - left, 1.0))

        impact = 0.0
        if self._phase == "running":
            self._paint_flow(painter, center, left, right, (top, bottom), color)
        elif self._phase in BUSY_PHASES:
            self._paint_waiting(painter, center, left, top, gate)
        elif self._phase:
            impact = self._paint_blocked(painter, center, left, top, gate, color)

        self._paint_wall(painter, center, color, open_t, impact)
        if self._phase == "running" and self.is_beating():
            # Ореол работающей кнопки мягко дышит.
            impact = 0.5 + 0.5 * math.sin(2 * math.pi * self._flow_time / HALO_PERIOD_S)
        self._paint_button(painter, center, impact)
        if self._phase in BUSY_PHASES:
            self._paint_spinner(painter, center, color)
        painter.end()

    def _paint_globe(self, painter: QPainter, center: QPointF, color: QColor, open_t: float) -> None:
        pen = QPen(RAW_COLOR)
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Сайты: глобус. Когда обход работает, он загорается цветом кнопки.
        globe = QColor(
            round(RAW_COLOR.red() + (color.red() - RAW_COLOR.red()) * open_t),
            round(RAW_COLOR.green() + (color.green() - RAW_COLOR.green()) * open_t),
            round(RAW_COLOR.blue() + (color.blue() - RAW_COLOR.blue()) * open_t),
        )
        pen.setColor(globe)
        painter.setPen(pen)
        globe_center = QPointF(self.width() - 15.0, center.y())
        painter.drawEllipse(globe_center, 10.0, 10.0)
        painter.drawEllipse(globe_center, 4.2, 10.0)
        painter.drawLine(
            QPointF(globe_center.x() - 10.0, globe_center.y()),
            QPointF(globe_center.x() + 10.0, globe_center.y()),
        )
        painter.setPen(Qt.PenStyle.NoPen)

    @staticmethod
    def _paint_packet(painter: QPainter, x: float, y: float, color: QColor, alpha: float, direction: int) -> None:
        if alpha <= 0.0:
            return
        tail = QColor(color)
        tail.setAlphaF(0.25 * alpha)
        painter.setBrush(tail)
        painter.drawRect(QRectF(x - 3.5 - direction * 7.0, y - 1.0, 7.0, 2.0))
        body = QColor(color)
        body.setAlphaF(alpha)
        painter.setBrush(body)
        painter.drawRoundedRect(QRectF(x - 3.5, y - 1.75, 7.0, 3.5), 1.75, 1.75)

    def _paint_flow(self, painter, center, left, right, lanes_y, color) -> None:
        length = right - left
        for (direction, speed, offsets), y in zip(FLOW_LANES, lanes_y):
            for offset in offsets:
                # Без анимаций пакеты стоят на месте: «работает» всё равно
                # отличается от «остановлен» не только цветом.
                u = (self._flow_time * speed / length + offset) % 1.0
                x = left + u * length if direction > 0 else right - u * length
                edge = min(1.0, min(x - left, right - x) / 12.0)
                passed = x > center.x() if direction > 0 else True
                self._paint_packet(painter, x, y, color if passed else RAW_COLOR, edge, direction)

    def _paint_waiting(self, painter, center, left, top, gate) -> None:
        # Пакеты доходят до стены и тают: дальше их пока не пускают.
        stop = center.x() - gate
        length = stop - left
        direction, speed, offsets = FLOW_LANES[0]
        for offset in offsets:
            u = (self._flow_time * speed * 0.7 / length + offset) % 1.0
            x = left + u * length
            alpha = min(1.0, (x - left) / 12.0) * min(1.0, (stop - x) / 22.0)
            self._paint_packet(painter, x, top, RAW_COLOR, alpha, direction)

    def _paint_blocked(self, painter, center, left, top, gate, color) -> float:
        """Залп о стену. Возвращает силу удара (0..1) — от неё вздрагивает стена."""
        phase = self._pulse_phase if self.is_beating() else 0.0
        if phase <= 0.0:
            return 0.0
        hit = center.x() - gate
        impact = 0.0
        for index, delay in enumerate(BLOCKED_PACKET_DELAYS):
            t = (phase - delay) / (1.0 - BLOCKED_PACKET_DELAYS[-1])
            if t <= 0.0 or t >= 1.0:
                continue
            if t < BLOCKED_FLIGHT:
                k = t / BLOCKED_FLIGHT
                x = left + (hit - left) * k * k
                self._paint_packet(painter, x, top, RAW_COLOR, min(1.0, (x - left) / 12.0), 1)
                continue
            # Отскок: пакет краснеет, отлетает назад дугой и гаснет.
            k = (t - BLOCKED_FLIGHT) / (1.0 - BLOCKED_FLIGHT)
            impact = max(impact, 1.0 - k)
            back = 1.0 - (1.0 - k) ** 2
            side = -1.0 if index % 2 == 0 else 1.0
            x = hit - 20.0 * back
            y = top + side * 9.0 * math.sin(math.pi * 0.5 * back)
            self._paint_packet(painter, x, y, color, 1.0 - k, -1)
        return impact

    def _paint_wall(self, painter, center, color, open_t: float, impact: float) -> None:
        # Стена из блоков сверху и снизу от кнопки. Когда обход
        # работает, она бледнеет: блокировка есть, но пакеты идут сквозь неё.
        wall = QColor(
            round(color.red() + (RAW_COLOR.red() - color.red()) * open_t),
            round(color.green() + (RAW_COLOR.green() - color.green()) * open_t),
            round(color.blue() + (RAW_COLOR.blue() - color.blue()) * open_t),
        )
        wall.setAlphaF(min(1.0, (0.7 - 0.48 * open_t) + 0.3 * impact))
        painter.setBrush(wall)
        half_width = 3.5 + 1.0 * impact
        # Блоки идут от кнопки к краям сцены; ближний к кнопке прячется под ней.
        step = (WALL_HALF_HEIGHT - BUTTON_RADIUS) / WALL_BLOCKS
        for sign in (-1.0, 1.0):
            for row in range(WALL_BLOCKS):
                near = BUTTON_RADIUS + 3.0 + row * step
                height = step - 2.0
                y = center.y() + near if sign > 0 else center.y() - near - height
                painter.drawRoundedRect(
                    QRectF(center.x() - half_width, y, half_width * 2, height), 1.5, 1.5
                )

    def _paint_button(self, painter: QPainter, center: QPointF, impact: float) -> None:
        hover = self._hover_t if self._clickable else 0.0
        core_r = BUTTON_RADIUS * (PRESS_SCALE if self._pressed else 1.0)

        # Свечение: при наведении шире и ярче — так видно, что кнопку можно нажать.
        glow = QColor(self._shown_color)
        glow.setAlphaF(min(1.0, 0.26 + 0.2 * impact + 0.22 * hover))
        painter.setBrush(glow)
        glow_r = core_r + 4.0 * (1.0 + 0.4 * impact + 0.8 * hover)
        painter.drawEllipse(center, glow_r, glow_r)

        painter.setBrush(self._shown_color)
        painter.drawEllipse(center, core_r, core_r)

        if not self._clickable:
            return
        # Значок питания: разомкнутое кольцо и вертикальная черта сверху.
        icon_alpha = 0.95 if self.is_click_enabled() else 0.45
        pen = QPen(QColor(255, 255, 255, round(255 * icon_alpha)))
        pen.setWidthF(1.9)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        ring_r = core_r * 0.48
        ring = QRectF(center.x() - ring_r, center.y() - ring_r, ring_r * 2, ring_r * 2)
        # Qt считает углы в 1/16 градуса от «трёх часов» против часовой стрелки;
        # разрыв кольца сверху — 70°.
        painter.drawArc(ring, (90 + 35) * 16, (360 - 70) * 16)
        painter.drawLine(
            QPointF(center.x(), center.y() - ring_r * 1.25),
            QPointF(center.x(), center.y() - ring_r * 0.2),
        )
        painter.setPen(Qt.PenStyle.NoPen)

    def _paint_spinner(self, painter: QPainter, center: QPointF, color: QColor) -> None:
        pen = QPen(color)
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        radius = BUTTON_RADIUS + 6.5
        arc = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
        start = -(self._flow_time * 260.0) % 360.0
        painter.drawArc(arc, round(start * 16), 100 * 16)
        painter.setPen(Qt.PenStyle.NoPen)


__all__ = ["BypassScene", "mascot_mood_for_phase"]
