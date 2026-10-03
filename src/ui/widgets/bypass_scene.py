"""Сцена обхода для карточки «Статус работы».

Посередине стоит талисман-медоед (это Zapret на вашем компьютере). Слева
компьютер, справа сайты, а между медоедом и сайтами — стена блокировки с
круглой кнопкой питания: это и есть выключатель Zapret. Медоед смотрит на
стену; у него два «приёмника» пакетов: пасть (верхняя дорожка) и лапа с
молнией «Z» (нижняя дорожка).

- Zapret остановлен: медоед лапой с «Z» кидает пакеты в стену, они
  отскакивают обратно, и он их встречает: один ловит пастью, другой
  отбивает лапой. Ответов от сайтов нет.
- Запуск не удался: то же самое, но медоед грустит и уже ничего не кидает.
- Идёт запуск или остановка: пакеты из-под лапы долетают до стены и ждут,
  вокруг кнопки бегает дуга, медоед суетится.
- Zapret работает: стена бледнеет, пакеты проходят сквозь кнопку и
  окрашиваются в её цвет. Лапа с «Z» выпускает их к сайтам, а ответы от сайтов
  медоед ловит пастью. Радостно подпрыгивает в момент включения, а потом
  спокойно дышит и оглядывается.

Кадры идут, только пока сцена видна, окно не свёрнуто и включены «живые
анимации». В остановленном состоянии это короткий залп и пауза (таймер
между залпами одиночный), а на ходу перерисовываются только дорожки и кнопка.
Пасть и лапу медоеда сцена двигает сама по положению пакетов, без своих таймеров.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QEasingCurve, QPoint, QPointF, QRect, QRectF, QSize, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QRegion
from PyQt6.QtWidgets import QSizePolicy

from ui.animation_policy import are_live_animations_enabled
from ui.pulsing_dot import PulsingDot
from ui.widgets.fun.badger import DrawnBadger
from ui.widgets.fun.mascot import GESTURE_TOSS, MOOD_ALARM, MOOD_BUSY, MOOD_HAPPY, MOOD_IDLE, MOOD_SAD


SCENE_HEIGHT = 76
# Наибольшая ширина виджета в Qt: «без ограничения».
QWIDGETSIZE_MAX = (1 << 24) - 1
SCENE_WIDTH = 330
SCENE_MIN_WIDTH = 240
MASCOT_SIZE = 44
# Высота пасти и лапы с «Z» на значке медоеда (доля его размера от верха):
# на этих высотах идут дорожки пакетов.
MOUTH_LANE = 0.34
PAW_LANE = 0.62
# Место под значки по краям: слева компьютер, справа сайты.
ENDPOINT_ROOM = 34
# Пакет входит в пасть и «рождается» из лапы чуть глубже края медоеда, px.
EAT_DEPTH = 12.0
BIRTH_DEPTH = 8.0
# Полный вдох и выдох спокойного талисмана, секунды.
BREATH_PERIOD_S = 3.6
# Период, с которым дышит ореол работающей кнопки, секунды.
HALO_PERIOD_S = 2.8
RAW_COLOR = QColor(150, 156, 168)

BUTTON_RADIUS = 15.0
HOVER_FADE_MS = 160
PRESS_SCALE = 0.9
WALL_HALF_HEIGHT = 33.0
WALL_BLOCKS = 3
# Ворота в стене: кирпичи разъезжаются вверх и вниз, ближний к кнопке — первым.
GATE_OPEN_MS = 760
GATE_CLOSE_MS = 640
GATE_SLIDE = 4.5
GATE_STAGGER = 0.16
# «Вздох» кнопки: при включении от неё расходится кольцо, при остановке — сходится.
POP_MS = 680
# Комета вокруг кнопки, пока идёт запуск или остановка.
COMET_SPAN_DEG = 210.0
COMET_SEGMENTS = 14
COMET_SPEED_DEG = 300.0

# (направление, скорость в px/с, сдвиги пакетов вдоль дорожки в долях длины)
# Верхняя дорожка — ответы к пасти, нижняя — пакеты из-под лапы с «Z».
FLOW_LANES = (
    (-1, 36.0, (0.12, 0.58, 0.66)),
    (1, 46.0, (0.0, 0.31, 0.47, 0.78)),
)
# Пакеты от компьютера идут к медоеду сзади (слева), по обеим дорожкам.
INBOUND_LANES = (
    (40.0, (0.0, 0.5)),
    (40.0, (0.25, 0.75)),
)
# Залп о стену: медоед кидает три пакета один за другим.
BLOCKED_BURST_MS = 2300
BLOCKED_REST_MS = 3700
# Когда (в долях залпа) вылетает каждый пакет и сколько длится его путь.
BLOCKED_PACKET_STARTS = (0.13, 0.33, 0.53)
BLOCKED_PACKET_SPAN = 0.36
# Доли пути пакета: долетел до стены / вернулся к медоеду (дальше — поймали или отбили).
BLOCKED_OUT = 0.42
BLOCKED_BACK_END = 0.88
# Кто что делает с вернувшимся пакетом: пасть ловит, лапа отбивает.
BLOCKED_FATES = ("mouth", "paw", "mouth")
# Как долго отбитый пакет улетает прочь: на этом отрезке он ещё виден.
SWAT_REACH = 26.0
SHAKE_MS = 460
# Сцена крупнее точки, поэтому кадры реже: 20 в секунду хватает для плавности.
SCENE_FRAME_MS = 50

BUSY_PHASES = frozenset({"autostart_pending", "starting", "stopping"})
KNOWN_PHASES = frozenset({"running", "failed", "stopped"}) | BUSY_PHASES
# Доля залпа, на которой первый пакет возвращается к медоеду.
FIRST_CATCH_PHASE = BLOCKED_PACKET_STARTS[0] + BLOCKED_BACK_END * BLOCKED_PACKET_SPAN


def _smooth(k: float) -> float:
    k = max(0.0, min(1.0, k))
    return k * k * (3.0 - 2.0 * k)


def _chomp(c: float) -> float:
    """Пасть после того, как пакет пойман: быстро захлопывается и расслабляется (c: 0..1)."""
    if c <= 0.0:
        return 1.0
    if c < 0.5:
        return 1.0 - 1.4 * _smooth(c / 0.5)
    return -0.4 * (1.0 - _smooth((c - 0.5) / 0.5))


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
        # Нарисованный медоед: моргает и двигает лапами, а не только поворачивается целиком.
        self._mascot = DrawnBadger(self, size=MASCOT_SIZE)
        # Стоит строго посередине сцены; сами дорожки привязаны к его пасти и лапе.
        self._place_mascot()

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

        # Ход ворот линейный: пружинистость каждому кирпичу добавляет _brick_shift.
        self._wall_fade = QVariantAnimation(self)
        self._wall_fade.setDuration(GATE_OPEN_MS)
        self._wall_fade.valueChanged.connect(self._on_wall_value)
        self._gate_opening = True

        self._pop = QVariantAnimation(self)
        self._pop.setStartValue(0.0)
        self._pop.setEndValue(1.0)
        self._pop.setDuration(POP_MS)
        self._pop.valueChanged.connect(self._on_pop_value)
        self._pop.finished.connect(self._on_pop_finished)
        self._pop_t = 0.0
        self._pop_up = True

        self._shake = QVariantAnimation(self)
        self._shake.setStartValue(0.0)
        self._shake.setEndValue(1.0)
        self._shake.setDuration(SHAKE_MS)
        self._shake.valueChanged.connect(self._on_shake_value)
        self._shake.finished.connect(self._on_shake_finished)

    def set_stretched(self, stretched: bool) -> None:
        """Растянуть сцену на всю доступную ширину (узкое окно) или вернуть обычную."""
        self.setMaximumWidth(QWIDGETSIZE_MAX if stretched else self._preferred_width)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding if stretched else QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )

    def is_stretched(self) -> bool:
        return self.maximumWidth() > self._preferred_width

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._preferred_width, SCENE_HEIGHT)

    def _place_mascot(self) -> None:
        """Ставит медоеда по центру сцены: и по ширине, и по высоте самого значка."""
        mascot = self._mascot
        box_top = mascot.height() - 2 - MASCOT_SIZE
        mascot.move(
            (self.width() - mascot.width()) // 2,
            max(0, (self.height() - MASCOT_SIZE) // 2 - box_top),
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place_mascot()

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
        if previous and self.isVisible() and are_live_animations_enabled():
            if phase == "running" and previous != "running":
                self._play_pop(up=True)
            elif phase == "stopping" and previous == "running":
                self._play_pop(up=False)
        # Встряска — только когда запуск сорвался на глазах, а не при первом показе.
        if phase == "failed" and previous and self.isVisible() and are_live_animations_enabled():
            self._shake.stop()
            self._shake.start()
        self._halt()
        self._resume()
        self.update()
        self.phaseChanged.emit(phase)

    def mascot(self) -> DrawnBadger:
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
            self._gate_opening = target > self._open_t
            span = abs(target - self._open_t)
            full = GATE_OPEN_MS if self._gate_opening else GATE_CLOSE_MS
            self._wall_fade.setDuration(max(1, round(full * span)))
            self._wall_fade.setStartValue(self._open_t)
            self._wall_fade.setEndValue(target)
            self._wall_fade.start()
        else:
            self._open_t = target

    def is_gate_moving(self) -> bool:
        return self._wall_fade.state() == QVariantAnimation.State.Running

    def _brick_progress(self, row: int) -> float:
        """Насколько открыт кирпич ``row`` (0 — у кнопки): 0 — на месте, 1 — отъехал."""
        span = 1.0 - (WALL_BLOCKS - 1) * GATE_STAGGER
        return max(0.0, min(1.0, (self._open_t - row * GATE_STAGGER) / span))

    def _brick_shift(self, row: int) -> float:
        """Сдвиг кирпича наружу с пружинкой: при открытии — перелёт, при закрытии — отскок."""
        k = self._brick_progress(row)
        if k <= 0.0 or k >= 1.0:
            return k
        if self._gate_opening:
            c1 = 1.70158
            return 1.0 + (c1 + 1.0) * (k - 1.0) ** 3 + c1 * (k - 1.0) ** 2
        # Закрытие: кирпич едет на место, касается и чуть подскакивает обратно.
        q = 1.0 - k
        if q < 0.78:
            return 1.0 - (q / 0.78) ** 2
        return 0.14 * math.sin(math.pi * (q - 0.78) / 0.22)

    def _play_pop(self, *, up: bool) -> None:
        self._pop_up = bool(up)
        self._pop.stop()
        self._pop_t = 0.0
        self._pop.start()

    def is_popping(self) -> bool:
        return self._pop.state() == QVariantAnimation.State.Running

    def _on_pop_value(self, value) -> None:
        try:
            self._pop_t = float(value)
        except (TypeError, ValueError):
            return
        self.update()

    def _on_pop_finished(self) -> None:
        self._pop_t = 0.0
        self.update()

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

    def hideEvent(self, event) -> None:  # noqa: N802
        # Скрытую сцену доводим до конечного вида: переходы не доигрываются впустую.
        self._wall_fade.stop()
        self._open_t = self._open_target()
        self._pop.stop()
        self._pop_t = 0.0
        super().hideEvent(event)

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

    def _halt(self) -> None:
        super()._halt()
        # Кадры встали: пасть и лапа возвращаются в покой.
        mascot = self.__dict__.get("_mascot")
        if mascot is not None:
            mascot.set_scene_pose(0.0, 0.0, 0.0)

    def _start_beat(self) -> None:
        if self._can_animate():
            if self._is_flowing():
                # Поток продолжается с того места, где остановился, без рывка.
                self._flow_origin = self._flow_time
            else:
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
            left, right, _top, _bottom = self._lanes()
            self._mascot.set_scene_pose(*self._flow_pose(left, right, self._open_t))
            if self._phase == "running":
                self._mascot.set_breath(math.sin(2 * math.pi * self._flow_time / BREATH_PERIOD_S))
                self.flowFrame.emit(self._flow_time)
            self.update(self._motion_region())
            return
        phase = self._beat_clock.elapsed() / BLOCKED_BURST_MS
        if phase < 1.0:
            self._pulse_phase = phase
            # Грустный медоед после сорванного запуска ничего не кидает и не ловит.
            if self._phase == "stopped":
                self._mascot.set_scene_pose(*self._blocked_pose(phase))
            self.update(self._motion_region())
            return
        self._beat.stop()
        self._pulse_phase = 0.0
        self._mascot.set_scene_pose(0.0, 0.0, 0.0)
        self.update(self._motion_region())
        if self._can_animate():
            self._rest_timer.start(BLOCKED_REST_MS)

    def _lanes(self) -> tuple[float, float, float, float]:
        """(правый край медоеда — отсюда дорожки к сайтам, правый край дорожек,
        y пасти — верхняя дорожка, y лапы с «Z» — нижняя)."""
        mascot = self._mascot
        box_top = mascot.y() + mascot.height() - 2 - MASCOT_SIZE
        return (
            float(mascot.geometry().right() + 1),
            float(self.width() - ENDPOINT_ROOM),
            box_top + MOUTH_LANE * MASCOT_SIZE,
            box_top + PAW_LANE * MASCOT_SIZE,
        )

    def _inbound_span(self) -> tuple[float, float]:
        """Отрезок слева: от компьютера до медоеда."""
        return float(ENDPOINT_ROOM - 4), float(self._mascot.geometry().left())

    def _motion_region(self) -> QRegion:
        """Что меняется от кадра к кадру: дорожки по обе стороны от медоеда, стена и кнопка.

        Сам медоед в область не входит: он перерисовывается только когда
        меняется его поза (иначе каждый кадр перерисовывался бы весь значок).
        """
        left, right, top, bottom = self._lanes()
        from_x, to_x = self._inbound_span()
        y, height = int(top) - 12, int(bottom - top) + 24
        region = QRegion(QRect(int(left), y, int(right - left) + 2, height))
        region |= QRegion(QRect(int(from_x), y, int(to_x - from_x), height))
        center = self.gate_center()
        wall = QRect(center.x() - 8, 0, 16, self.height())
        return region | QRegion(self.button_rect()) | QRegion(wall)

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

        self._paint_endpoints(painter, center, color, open_t)

        track = QColor(RAW_COLOR)
        track.setAlphaF(0.16)
        painter.setBrush(track)
        # Дорожки слева (от компьютера) и справа (от лапы к стене): без обхода
        # пути за стеной нет, поэтому за ней дорожки бледнеют.
        from_x, to_x = self._inbound_span()
        for y in (top, bottom):
            painter.drawRect(QRectF(from_x, y - 0.5, to_x - from_x, 1.0))
        painter.drawRect(QRectF(left, top - 0.5, right - left, 1.0))
        painter.drawRect(QRectF(left, bottom - 0.5, right - left, 1.0))
        beyond = QColor(RAW_COLOR)
        beyond.setAlphaF(0.16 * (1.0 - open_t))
        painter.setBrush(beyond)
        for y in (top, bottom):
            painter.drawRect(QRectF(center.x() + gate, y - 0.5, right - center.x() - gate, 1.0))

        impact = 0.0
        if self._is_flowing():
            self._paint_inbound(painter, from_x, to_x, (top, bottom))
            self._paint_stream(painter, center, left, right, (top, bottom), gate, color, open_t)
        elif self._phase:
            impact = self._paint_blocked(painter, center, left, top, bottom, gate, color)

        self._paint_wall(painter, center, color, open_t, impact)
        if self._phase == "running" and self.is_beating():
            # Ореол работающей кнопки мягко дышит.
            impact = 0.5 + 0.5 * math.sin(2 * math.pi * self._flow_time / HALO_PERIOD_S)
        self._paint_pop_ring(painter, center, color)
        self._paint_button(painter, center, impact)
        if self._phase in BUSY_PHASES:
            self._paint_spinner(painter, center, color)
        painter.end()

    def _paint_endpoints(self, painter: QPainter, center: QPointF, color: QColor, open_t: float) -> None:
        pen = QPen(RAW_COLOR)
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Сайты — глобус, ваш компьютер — монитор. Когда обход работает, оба
        # загораются цветом кнопки.
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
        screen = QPointF(15.0, center.y() - 1.5)
        painter.drawRoundedRect(QRectF(screen.x() - 9.0, screen.y() - 6.5, 18.0, 13.0), 2.2, 2.2)
        painter.drawLine(QPointF(screen.x(), screen.y() + 6.5), QPointF(screen.x(), screen.y() + 9.5))
        painter.drawLine(QPointF(screen.x() - 4.5, screen.y() + 9.5), QPointF(screen.x() + 4.5, screen.y() + 9.5))
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

    def _flow_x(self, direction: int, speed: float, offset: float, left: float, right: float) -> float:
        """Где сейчас пакет на дорожке справа от медоеда.

        Ответы (``direction < 0``) едут до пасти и чуть глубже — «внутрь»;
        пакеты из-под лапы (``direction > 0``) рождаются чуть глубже края лапы.
        Без анимаций поток стоит на месте.
        """
        if direction < 0:
            span = right - left + EAT_DEPTH
            u = (self._flow_time * speed / span + offset) % 1.0
            return right - u * span
        start = left - BIRTH_DEPTH
        span = right - start
        u = (self._flow_time * speed / span + offset) % 1.0
        return start + u * span

    def _paint_inbound(self, painter, from_x: float, to_x: float, lanes_y) -> None:
        """Пакеты от компьютера идут к медоеду и тают у его спины."""
        length = to_x - from_x
        if length <= 0.0:
            return
        for (speed, offsets), y in zip(INBOUND_LANES, lanes_y):
            for offset in offsets:
                u = (self._flow_time * speed / length + offset) % 1.0
                x = from_x + u * length
                alpha = min(1.0, min(x - from_x, to_x - x) / 12.0)
                self._paint_packet(painter, x, y, RAW_COLOR, alpha, 1)

    def _paint_stream(self, painter, center, left, right, lanes_y, gate, color, through: float) -> None:
        """Поток пакетов справа от медоеда. ``through`` — насколько открыты ворота (0..1).

        Пакеты всегда летят по тем же местам, что и при работающем обходе,
        поэтому при открытии ворот ничего не перескакивает: пакеты у стены
        просто начинают проходить сквозь проём, а за стеной и на дорожке
        ответов поток проявляется вместе с воротами. При закрытии —
        наоборот, тает.
        """
        stop = center.x() - gate
        for (direction, speed, offsets), y in zip(FLOW_LANES, lanes_y):
            for offset in offsets:
                x = self._flow_x(direction, speed, offset, left, right)
                alpha = max(0.0, min(1.0, min(x - left, right - x) / 12.0))
                if direction < 0:
                    # Ответы от сайтов идут, только пока ворота открыты.
                    alpha *= through
                    tint = color
                elif x > center.x():
                    alpha *= through
                    tint = color
                else:
                    # До стены пакет серый; у закрытой стены он тает и ждёт.
                    alpha *= max(through, min(1.0, max(0.0, stop - x) / 22.0))
                    tint = RAW_COLOR
                self._paint_packet(painter, x, y, tint, alpha, direction)

    def _flow_pose(self, left: float, right: float, through: float) -> tuple[float, float, float]:
        """Пасть, лапа и блик медоеда по положению пакетов в потоке."""
        jaw = paw = glow = 0.0
        for direction, speed, offsets in FLOW_LANES:
            for offset in offsets:
                x = self._flow_x(direction, speed, offset, left, right)
                if direction < 0:
                    d = x - left
                    if d >= 26.0 or d <= -EAT_DEPTH:
                        continue
                    # Пакет подлетает — пасть раскрывается; съеден — хлопок и расслабление.
                    value = _chomp(-d / EAT_DEPTH) if d <= 0.0 else _smooth((26.0 - d) / 20.0)
                    value *= through
                    if abs(value) > abs(jaw):
                        jaw = value
                else:
                    s = (x - (left - BIRTH_DEPTH)) / 22.0
                    if 0.0 <= s < 1.0:
                        # Лапа выпускает пакет: взмах и блик по молнии.
                        paw = max(paw, 16.0 * math.sin(math.pi * s))
                        glow = max(glow, s)
        return jaw, paw, glow

    def _blocked_leg(self, index: int, phase: float):
        """Где на своём пути пакет ``index``: (u, k), либо ``None``, если его сейчас нет.

        ``u`` — доля всего пути (0..1), ``k`` — доля обратного пути от стены к медоеду
        (меньше 0 — летит к стене, больше 1 — уже пойман или отбит).
        """
        u = (phase - BLOCKED_PACKET_STARTS[index]) / BLOCKED_PACKET_SPAN
        if u <= 0.0 or u >= 1.0:
            return None
        return u, (u - BLOCKED_OUT) / (BLOCKED_BACK_END - BLOCKED_OUT)

    def _blocked_pose(self, phase: float) -> tuple[float, float, float]:
        """Пасть и лапа медоеда в залпе: бросок, встреча пакета и поимка или отбивание."""
        jaw = paw = glow = 0.0

        def take(current: float, value: float) -> float:
            return value if abs(value) > abs(current) else current

        for index, fate in enumerate(BLOCKED_FATES):
            leg = self._blocked_leg(index, phase)
            if leg is None:
                continue
            u, k = leg
            if index > 0 and u < 0.2:
                # Первый бросок делает жест «бросок» самого медоеда, остальные — взмах лапой.
                paw = take(paw, 18.0 * math.sin(math.pi * u / 0.2))
            if k <= 0.0:
                continue
            after = (u - BLOCKED_BACK_END) / (1.0 - BLOCKED_BACK_END)
            if fate == "mouth":
                jaw = take(jaw, _smooth((k - 0.45) / 0.55) if after <= 0.0 else _chomp(after))
            elif after <= 0.0:
                # Лапа отводится назад, готовясь хлопнуть.
                paw = take(paw, -8.0 * _smooth((k - 0.3) / 0.7))
            else:
                swing = -8.0 + 38.0 * _smooth(after / 0.35) if after < 0.35 else 30.0 * (1.0 - _smooth((after - 0.35) / 0.65))
                paw = take(paw, swing)
                glow = max(glow, math.sin(math.pi * min(1.0, after)))
        return jaw, paw, glow

    def _paint_blocked(self, painter, center, left, top, bottom, gate, color) -> float:
        """Залп о стену. Возвращает силу удара (0..1) — от неё вздрагивает стена.

        Пакет вылетает из-под лапы (нижняя дорожка), бьётся о стену и летит
        назад красным. Потом либо взлетает к пасти и пропадает в ней, либо
        лапа хлопает по нему и отбрасывает прочь.
        """
        phase = self._pulse_phase if self.is_beating() else 0.0
        if phase <= 0.0:
            return 0.0
        hit = center.x() - gate
        impact = 0.0
        for index, fate in enumerate(BLOCKED_FATES):
            leg = self._blocked_leg(index, phase)
            if leg is None:
                continue
            u, k = leg
            if u < BLOCKED_OUT:
                f = u / BLOCKED_OUT
                x = left + (hit - left) * f * f
                self._paint_packet(painter, x, bottom, RAW_COLOR, max(0.0, min(1.0, (x - left) / 12.0)), 1)
                continue
            impact = max(impact, 1.0 - min(1.0, (u - BLOCKED_OUT) / 0.25))
            if k <= 1.0:
                # Назад к медоеду: к пасти по дуге вверх или прямо к лапе.
                x = hit + (left - hit) * k
                y = bottom + (top - bottom) * _smooth(k) if fate == "mouth" else bottom
                alpha = max(0.0, min(1.0, (x - left) / 10.0))
                self._paint_packet(painter, x, y, color, alpha, -1)
            elif fate == "paw":
                # Отбитый пакет улетает вверх и вправо и гаснет.
                after = (u - BLOCKED_BACK_END) / (1.0 - BLOCKED_BACK_END)
                x = left + SWAT_REACH * after
                y = bottom - 14.0 * math.sin(0.5 * math.pi * after)
                self._paint_packet(painter, x, y, color, 1.0 - after, 1)
        return impact

    def _paint_wall(self, painter, center, color, open_t: float, impact: float) -> None:
        # Стена из блоков сверху и снизу от кнопки. Когда обход
        # работает, она бледнеет: блокировка есть, но пакеты идут сквозь неё.
        wall = QColor(
            round(color.red() + (RAW_COLOR.red() - color.red()) * open_t),
            round(color.green() + (RAW_COLOR.green() - color.green()) * open_t),
            round(color.blue() + (RAW_COLOR.blue() - color.blue()) * open_t),
        )
        base_alpha = min(1.0, (0.7 - 0.48 * open_t) + 0.3 * impact)
        half_width = 3.5 + 1.0 * impact
        busy = self._phase in BUSY_PHASES
        # Блоки идут от кнопки к краям сцены; ближний к кнопке прячется под ней.
        step = (WALL_HALF_HEIGHT - BUTTON_RADIUS) / WALL_BLOCKS
        for sign in (-1.0, 1.0):
            for row in range(WALL_BLOCKS):
                moved = self._brick_shift(row)
                shift = moved * GATE_SLIDE * (1.0 + 0.6 * row)
                near = BUTTON_RADIUS + 3.0 + row * step + shift
                # Отъехавший кирпич чуть сжимается — ворота не вылезают за край сцены.
                height = (step - 2.0) * (1.0 - 0.3 * max(0.0, min(1.0, moved)))
                y = center.y() + near if sign > 0 else center.y() - near - height
                brick = QColor(wall)
                alpha = base_alpha
                if busy:
                    # Пока идёт запуск или остановка, кирпичи по очереди мерцают:
                    # от кнопки к краям бежит волна, будто стена «заряжается».
                    wave = 0.5 + 0.5 * math.sin(2 * math.pi * 1.3 * self._flow_time - row * 1.1)
                    alpha = min(1.0, alpha * (0.55 + 0.6 * wave))
                brick.setAlphaF(alpha)
                painter.setBrush(brick)
                painter.drawRoundedRect(
                    QRectF(center.x() - half_width, y, half_width * 2, height), 1.5, 1.5
                )

    def _pop_scale(self) -> float:
        t = self._pop_t
        if t <= 0.0:
            return 1.0
        # Включение: кнопка набирает воздух и мягко оседает. Остановка: выдох внутрь.
        swell = math.sin(math.pi * min(1.0, t / 0.55)) * (1.0 - 0.4 * t)
        return 1.0 + (0.17 if self._pop_up else -0.12) * swell

    def _paint_pop_ring(self, painter: QPainter, center: QPointF, color: QColor) -> None:
        t = self._pop_t
        if t <= 0.0:
            return
        ease = 1.0 - (1.0 - t) ** 3
        # Включение — кольцо расходится от кнопки; остановка — сходится к ней.
        reach = 26.0
        radius = BUTTON_RADIUS + (reach * ease if self._pop_up else reach * (1.0 - ease))
        ring = QColor(color)
        ring.setAlphaF(0.65 * (1.0 - t) if self._pop_up else 0.55 * math.sin(math.pi * t))
        pen = QPen(ring)
        pen.setWidthF(2.4 * (1.0 - t) + 0.6)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, radius, radius)
        painter.setPen(Qt.PenStyle.NoPen)

    def _paint_button(self, painter: QPainter, center: QPointF, impact: float) -> None:
        hover = self._hover_t if self._clickable else 0.0
        core_r = BUTTON_RADIUS * (PRESS_SCALE if self._pressed else 1.0) * self._pop_scale()

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
        """Комета вокруг кнопки: яркая голова и тающий хвост, под ней — бледная дорожка."""
        radius = BUTTON_RADIUS + 6.5
        arc = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
        pen = QPen(color)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        track = QColor(color)
        track.setAlphaF(0.14)
        pen.setColor(track)
        pen.setWidthF(1.6)
        painter.setPen(pen)
        painter.drawEllipse(center, radius, radius)

        # Запуск — по часовой стрелке, остановка — против: видно, что идёт обратный процесс.
        clockwise = self._phase != "stopping"
        turn = self._flow_time * COMET_SPEED_DEG
        # Qt считает углы против часовой стрелки; голова кометы — в начале отрезка.
        head = (-turn if clockwise else turn) % 360.0
        piece = COMET_SPAN_DEG / COMET_SEGMENTS
        for index in range(COMET_SEGMENTS):
            fade = 1.0 - index / COMET_SEGMENTS
            segment = QColor(color)
            segment.setAlphaF(0.95 * fade * fade)
            pen.setColor(segment)
            pen.setWidthF(1.2 + 1.6 * fade)
            painter.setPen(pen)
            start = head + index * piece if clockwise else head - (index + 1) * piece
            painter.drawArc(arc, round(start * 16), round(piece * 16) + 8)
        painter.setPen(Qt.PenStyle.NoPen)


__all__ = ["BypassScene", "mascot_mood_for_phase"]
