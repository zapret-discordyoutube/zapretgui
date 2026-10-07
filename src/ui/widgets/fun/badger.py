"""Медоед с логотипа программы, у которого двигаются части.

У обычного талисмана (``Mascot``) вместо тела картинка значка программы:
её можно только поворачивать и подбрасывать целиком. Здесь медоед с
логотипа собран из отдельных частей (``ui.widgets.fun.logo_badger``), и
каждая часть двигается сама:

- глаз моргает раз в несколько секунд (иногда дважды подряд) и смотрит
  туда, что сейчас происходит: на стену, когда медоед кидает пакеты, или
  по сторонам, когда он суетится;
- лапа с молнией «Z» покачивается в такт дыханию, замахивается при броске
  и радостно поднимается при включении обхода, а по молнии пробегает блик;
- челюсть жуёт, пока идёт запуск, широко раскрывается от радости и
  стискивается, когда медоед вздрагивает;
- ухо дёргается, когда медоед оглядывается или пугается.

Сцена обхода (``BypassScene``) сама знает, где сейчас летят пакеты, и через
``set_scene_pose`` подсказывает медоеду, что делать: раскрыть пасть навстречу
пакету и сомкнуть её, когда тот пойман, или хлопнуть лапой с молнией. Эта
подсказка складывается с обычными жестами.

Настроения, жесты и правила экономии процессора те же, что у ``Mascot``:
кадры идут только во время жеста или моргания, а между ними стоит
одиночный таймер.
"""

from __future__ import annotations

import math
import random

from PyQt6.QtCore import QTimer, QVariantAnimation
from PyQt6.QtGui import QPainter

from ui.frame_clock import frame_clock
from ui.widgets.fun.logo_badger import BadgerPose, paint_logo_badger
from ui.widgets.fun.mascot import GESTURE_TOSS, MOOD_ALARM, MOOD_BUSY, MOOD_HAPPY, MOOD_SAD, Mascot


# Моргание: закрыть и открыть глаза; пауза между морганиями — случайная.
BLINK_MS = 190
BLINK_PAUSE_MIN_MS = 2400
BLINK_PAUSE_MAX_MS = 5600
DOUBLE_BLINK_CHANCE = 0.25

# Ширина виджета в долях размера: узкая, чтобы пакеты пропадали у самой морды,
# а не за невидимым полем рядом с ней.
BODY_WIDTH_RATIO = 1.12
# Запас над головой — ровно на прыжок от радости, чтобы не занимать сцену пустотой.
BODY_HEIGHT_RATIO = 1.3

# Лапа с молнией в покое чуть покачивается в такт дыханию (градусы).
PAW_BREATH_SWING = 7.0
# Дыхание идёт ступенями: одна ступень растягивает медоеда меньше чем на 0,1
# точки экрана. Ступени повторяются на каждом вдохе, поэтому готовый слой тела
# для каждой из них рисуется один раз (см. ui.widgets.fun.logo_badger).
BREATH_STEP = 0.04


def _ease_in_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


class DrawnBadger(Mascot):
    """Талисман-медоед из частей. API тот же, что у ``Mascot``."""

    def __init__(self, parent=None, *, size: int = 44) -> None:
        super().__init__(parent, size=size)
        self.setFixedSize(int(self._side * BODY_WIDTH_RATIO), int(self._side * BODY_HEIGHT_RATIO))
        # Подсказка от сцены: насколько раскрыта пасть, поворот лапы и блик на молнии.
        self._scene_jaw = 0.0
        self._scene_paw = 0.0
        self._scene_glow = 0.0
        self._blink = 0.0
        self._blink_left = 0
        self._blink_anim = QVariantAnimation(self)
        self._blink_anim.setStartValue(0.0)
        self._blink_anim.setEndValue(1.0)
        self._blink_anim.setDuration(BLINK_MS)
        self._blink_anim.valueChanged.connect(self._on_blink_value)
        self._blink_anim.finished.connect(self._on_blink_finished)
        self._blink_timer = QTimer(self)
        self._blink_timer.setSingleShot(True)
        self._blink_timer.timeout.connect(self.blink)

    # ---- подсказка от сцены ---------------------------------------------

    def set_scene_pose(self, jaw: float, paw: float, glow: float) -> None:
        """Пасть, лапа и блик, которые нужны сцене в этот кадр (нули — без подсказки)."""
        jaw = max(-1.0, min(1.2, float(jaw)))
        paw = max(-30.0, min(40.0, float(paw)))
        glow = max(0.0, min(1.0, float(glow)))
        if (
            abs(jaw - self._scene_jaw) < 0.02
            and abs(paw - self._scene_paw) < 0.4
            and abs(glow - self._scene_glow) < 0.02
        ):
            return
        self._scene_jaw, self._scene_paw, self._scene_glow = jaw, paw, glow
        self.update()

    def scene_pose(self) -> tuple[float, float, float]:
        return self._scene_jaw, self._scene_paw, self._scene_glow

    # ---- моргание ------------------------------------------------------

    def blink(self) -> None:
        """Моргнуть сейчас (кадры идут только эти доли секунды)."""
        if not self._can_animate():
            return
        if self._blink_anim.state() == QVariantAnimation.State.Running:
            return
        if frame_clock().is_paused():
            # Экран никто не видит (сеанс заблокирован, дисплей выключен):
            # не моргаем, но следующее моргание назначаем.
            self._schedule_blink()
            return
        self._blink_left = 1 if random.random() < DOUBLE_BLINK_CHANCE else 0
        self._blink_anim.start()

    def blink_progress(self) -> float:
        return self._blink

    def _on_blink_value(self, value) -> None:
        try:
            t = float(value)
        except (TypeError, ValueError):
            return
        # Веки быстро закрываются и чуть медленнее открываются.
        self._blink = min(1.0, t / 0.4) if t < 0.4 else max(0.0, 1.0 - (t - 0.4) / 0.6)
        self.update()

    def _on_blink_finished(self) -> None:
        self._blink = 0.0
        self.update()
        if self._blink_left > 0:
            self._blink_left -= 1
            QTimer.singleShot(90, self._blink_anim.start)
            return
        self._schedule_blink()

    def _schedule_blink(self) -> None:
        self._blink_timer.stop()
        if self._can_animate():
            self._blink_timer.start(random.randint(BLINK_PAUSE_MIN_MS, BLINK_PAUSE_MAX_MS))

    def is_blink_scheduled(self) -> bool:
        return self._blink_timer.isActive()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._schedule_blink()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._blink_timer.stop()
        self._blink_anim.stop()
        self._blink = 0.0
        super().hideEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        window = self.window()
        if window is not None and window.isMinimized():
            self._blink_timer.stop()
            self._blink_anim.stop()
            self._blink = 0.0
        elif not self._blink_timer.isActive():
            self._schedule_blink()

    # ---- позы частей ---------------------------------------------------

    def logo_pose(self) -> BadgerPose:
        """Положение частей медоеда с логотипа для текущего настроения и жеста."""
        t = self._t
        g = self._gesture
        bump = math.sin(math.pi * t) if g else 0.0
        paw = PAW_BREATH_SWING * self._breath
        jaw = 0.25 * max(0.0, self._breath)
        ear = 0.0
        glow = 0.0
        blink = self._blink
        if g == MOOD_BUSY:
            # Жуёт и перебирает лапой, пока идёт запуск или остановка.
            jaw = 0.55 * math.sin(4.0 * math.pi * t)
            paw = 10.0 * math.sin(4.0 * math.pi * t)
            ear = 6.0 * math.sin(2.0 * math.pi * t)
        elif g == MOOD_HAPPY:
            # Широко раскрывает пасть, поднимает молнию, по ней бежит блик, жмурится.
            jaw = 1.0 * bump
            paw = 26.0 * bump
            glow = t
            blink = max(blink, 0.85 * bump)
        elif g == GESTURE_TOSS:
            # Бросок: замах вниз, рывок вверх и отпускание — пасть приоткрывается.
            if t < 0.3:
                paw = -9.0 * _ease_in_out(t / 0.3)
            elif t < 0.55:
                paw = -9.0 + 39.0 * _ease_in_out((t - 0.3) / 0.25)
            else:
                paw = 30.0 * (1.0 - _ease_in_out((t - 0.55) / 0.45))
            jaw = 0.7 * math.sin(math.pi * min(1.0, max(0.0, (t - 0.35) / 0.5)))
        elif g == MOOD_ALARM:
            # Вздрогнул: ухо прижато, челюсть стиснута.
            ear = -14.0 * bump
            jaw = -0.8 * bump
            paw = 8.0 * bump
        elif g == "look":
            ear = 8.0 * math.sin(2.0 * math.pi * t)
        elif self._mood == MOOD_SAD:
            jaw, paw, ear = -0.6, -8.0, 10.0
        elif self._mood == MOOD_ALARM:
            jaw, ear = -0.3, -6.0
        return BadgerPose(
            blink=blink,
            eye_open=self._eye_size(),
            look=self.look_offset(),
            jaw=jaw + self._scene_jaw,
            paw=paw + self._scene_paw,
            ear=ear,
            bolt_glow=max(glow, self._scene_glow),
        )

    def look_offset(self) -> float:
        """Куда смотрят зрачки: -1 влево, 1 вправо (к стене и сайтам)."""
        g = self._gesture
        if g == GESTURE_TOSS or self._mood == MOOD_ALARM:
            return 0.9
        if g == MOOD_BUSY:
            return math.sin(2 * math.pi * self._t)
        if g == "look":
            return math.sin(2 * math.pi * self._t)
        return 0.25

    def eye_openness(self) -> float:
        """Насколько открыт глаз: 0 — закрыт, 1 — обычно, больше 1 — широко."""
        if self._gesture == MOOD_HAPPY:
            return 0.0
        return self._eye_size() * (1.0 - self._blink)

    def _eye_size(self) -> float:
        """Размер глаза без моргания: грустный прикрывает, испуганный округляет."""
        if self._mood == MOOD_SAD:
            return 0.5
        if self._mood == MOOD_ALARM or self._gesture == MOOD_ALARM:
            return 1.18
        return 1.0

    def set_breath(self, value: float) -> None:
        # Дыхание двигает и лапы, поэтому перерисовываем его и при чуть меньшем шаге.
        value = max(-1.0, min(1.0, float(value)))
        value = round(value / BREATH_STEP) * BREATH_STEP
        if value == self._breath:
            return
        self._breath = value
        if not self._gesture:
            self.update()

    def is_steady(self) -> bool:
        """Медоед не моргает и не делает жест: ухо, глаз и взгляд стоят на месте."""
        return not self._gesture and self._blink <= 0.0

    # ---- отрисовка -----------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        dy, angle, sx, sy = self.pose()
        side = float(self._side)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Опорная точка — низ лап: медоед приседает и прыгает от «пола».
        painter.translate(self.width() / 2, self.height() - 2 + dy * side)
        painter.scale(sx, sy)
        painter.translate(0, -side / 2)
        painter.rotate(angle)
        painter.scale(side / 100.0, side / 100.0)
        painter.translate(-50.0, -50.0)
        self.paint_badger(painter)
        painter.end()

    def paint_badger(self, painter: QPainter) -> None:
        """Рисует медоеда с логотипа в квадрате 100×100 в текущей позе."""
        paint_logo_badger(painter, self.logo_pose(), steady=self.is_steady())


__all__ = ["DrawnBadger"]
