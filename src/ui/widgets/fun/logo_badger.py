"""Медоед с логотипа программы, разобранный на части.

Логотип у программы есть только картинкой (``.ico`` и PNG внутри ``logo.svg``
сайта), поэтому его части обведены здесь заново по картинке 256×256:
тело буквой «C» со складкой внутри, голова со светлой полоской, ухо, глаз,
ноздря, раскрытая пасть с зубами, нижняя челюсть, лапа и молния «Z» в зубах.

Всё нарисовано в квадрате 100×100 тем же порядком слоёв, что и на логотипе.
Части, которые двигаются, принимают параметры ``BadgerPose``: моргание,
взгляд, челюсть («кусь»), лапа, ухо и блеск молнии.

Пока медоед просто стоит и дышит, от кадра к кадру у него двигаются только
челюсть и лапа. Тело, голова, ухо, глаз и молния не меняются, а это самая
дорогая часть рисунка: большие контуры с градиентами и обводками. Поэтому
``paint_logo_badger`` умеет брать их готовыми слоями (``ui.paint_layer_cache``)
и дорисовывать поверх только подвижное. Медоед на сцене перерисовывается около
28 раз в секунду; замер на Windows: на его рисование уходило около 290 мс за
30 секунд, стало около 200 (остаток — жесты и моргание, они рисуются напрямую).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen

from ui.paint_layer_cache import LayerCache


# Цвета сняты с логотипа.
BODY_TOP = QColor("#2f9bf6")
BODY_BOTTOM = QColor("#2c4fa6")
FOLD_LEFT = QColor("#0a4ab8")
FOLD_RIGHT = QColor("#052a6c")
NECK = QColor("#1d7fe4")
HEAD = QColor("#2aa6ff")
HEAD_SHADE = QColor("#1f8ef0")
EAR = QColor("#0f8cf5")
STRIPE = QColor("#e8f5ff")
MOUTH = QColor("#062b6e")
JAW = QColor("#2f8ae0")
PUPIL = QColor("#023b9e")
PAW = QColor(36, 120, 214, 235)
BOLT_GREEN = QColor("#3dfc9c")
BOLT_YELLOW = QColor("#ffdf00")
BOLT_MARK = QColor("#0a3a9a")


@dataclass(frozen=True, slots=True)
class BadgerPose:
    """Положение подвижных частей. Все нули — медоед как на логотипе."""

    blink: float = 0.0  # 0 — глаз открыт, 1 — закрыт
    eye_open: float = 1.0  # множитель высоты глаза: грусть меньше 1, испуг больше 1
    look: float = 0.0  # -1 зрачок влево, 1 вправо
    jaw: float = 0.0  # 0 — как на логотипе, 1 — челюсть приоткрыта шире, -1 — сомкнута
    paw: float = 0.0  # поворот лапы в градусах, плюс — вверх
    ear: float = 0.0  # поворот уха в градусах
    bolt_glow: float = 0.0  # 0..1 — по молнии бежит блик


def _path(points, *, close: bool = True) -> QPainterPath:
    path = QPainterPath(QPointF(*points[0]))
    for item in points[1:]:
        if len(item) == 2:
            path.lineTo(QPointF(*item))
        elif len(item) == 4:
            path.quadTo(QPointF(item[0], item[1]), QPointF(item[2], item[3]))
        else:
            path.cubicTo(QPointF(item[0], item[1]), QPointF(item[2], item[3]), QPointF(item[4], item[5]))
    if close:
        path.closeSubpath()
    return path


def _smooth(points, *, close: bool = True) -> QPainterPath:
    """Плавный контур через точки (кривая Катмулла — Рома, переведённая в кубические)."""
    pts = [QPointF(x, y) for x, y in points]
    count = len(pts)
    path = QPainterPath(pts[0])
    last = count if close else count - 1
    for i in range(last):
        p0 = pts[(i - 1) % count] if close or i > 0 else pts[i]
        p1 = pts[i]
        p2 = pts[(i + 1) % count]
        p3 = pts[(i + 2) % count] if close or i + 2 < count else p2
        path.cubicTo(p1 + (p2 - p0) / 6.0, p2 - (p3 - p1) / 6.0, p2)
    if close:
        path.closeSubpath()
    return path


@lru_cache(maxsize=None)
def _outer_body() -> QPainterPath:
    # Спина и хвост: большая дуга от шеи вниз и вправо до кончика у лапы.
    return _path([
        (25.0, 10.0),
        (13.0, 17.0, 4.0, 31.0, 1.0, 46.0),
        (-2.0, 66.0, 7.0, 84.0, 24.0, 94.0),
        (40.0, 102.0, 63.0, 101.0, 78.0, 94.0),
        (88.0, 89.0, 93.5, 82.0, 93.0, 76.0),
        (92.0, 70.0, 88.0, 64.0, 86.0, 58.0),
        (70.0, 50.0),
        (40.0, 40.0),
        (30.0, 14.0),
    ])


@lru_cache(maxsize=None)
def _fold() -> QPainterPath:
    # Тёмная складка тела — внутренняя «C» вокруг просвета под челюстью.
    return _smooth([
        (30.0, 40.0), (24.0, 50.0), (24.0, 62.0), (30.0, 70.0), (42.0, 76.0), (56.0, 77.0),
        (70.0, 75.0), (82.0, 70.0), (88.0, 63.0), (87.0, 57.0), (80.0, 52.0), (72.0, 50.0),
        (60.0, 52.0), (45.0, 48.0), (36.0, 42.0),
    ])


@lru_cache(maxsize=None)
def _gap() -> QPainterPath:
    # Просвет между челюстью и складкой: на логотипе он прозрачный (замерено по картинке).
    return _smooth([
        (34.5, 46.0), (40.0, 46.5), (44.0, 50.0), (48.0, 52.0), (52.0, 53.0), (56.0, 52.0),
        (60.0, 50.0), (64.0, 47.0), (68.0, 43.5), (70.5, 43.0), (70.5, 48.5), (68.0, 52.5),
        (64.0, 55.0), (60.0, 56.5), (52.0, 58.5), (44.0, 57.5), (40.0, 55.5), (36.5, 51.5),
    ])


@lru_cache(maxsize=None)
def _neck() -> QPainterPath:
    # Светлая шея под ухом, от неё начинается челюсть.
    return _path([
        (26.0, 22.0),
        (34.0, 25.0, 40.0, 30.0, 44.0, 36.0),
        (46.0, 40.0, 47.0, 43.0, 43.0, 44.0),
        (37.0, 44.0, 31.0, 41.0, 28.0, 38.0),
        (25.0, 33.0, 24.0, 27.0, 26.0, 22.0),
    ])


@lru_cache(maxsize=None)
def _head() -> QPainterPath:
    # Голова с верхней челюстью: макушка, вытянутая морда и три зуба вниз.
    return _path([
        (24.0, 12.0),
        (30.0, 5.0, 40.0, 2.0, 52.0, 2.0),
        (63.0, 2.0, 70.0, 6.0, 74.0, 12.0),
        (78.0, 17.0, 84.0, 19.0, 88.0, 20.0),
        (93.0, 21.0, 94.0, 25.0, 91.0, 28.0),
        (87.0, 32.0, 82.0, 35.0, 78.0, 37.5),
        (76.5, 38.5, 74.0, 38.5, 74.5, 36.0),
        (75.0, 34.5),
        (72.5, 36.5, 69.5, 38.0, 68.5, 35.5),
        (69.0, 33.5),
        (66.5, 35.5, 63.5, 36.0, 63.5, 33.5),
        (64.5, 30.5),
        (58.0, 27.0, 50.0, 24.0, 43.0, 23.0),
        (38.0, 25.0, 33.0, 27.0, 28.0, 25.0),
        (24.0, 22.0, 22.5, 17.0, 24.0, 12.0),
    ])


@lru_cache(maxsize=None)
def _mouth() -> QPainterPath:
    # Тёмная часть раскрытой пасти — полумесяц слева от глотки.
    return _smooth([
        (44.0, 24.0), (48.0, 20.5), (55.0, 22.5), (61.0, 26.0), (55.0, 27.5), (51.5, 31.0),
        (51.5, 38.5), (48.0, 38.0), (45.0, 33.0),
    ])


@lru_cache(maxsize=None)
def _throat() -> QPainterPath:
    # Глотка внутри пасти: на логотипе она тоже прозрачная.
    return _smooth([
        (51.5, 31.0), (54.0, 27.5), (60.0, 26.5), (64.5, 31.0), (68.5, 34.0), (70.0, 37.5),
        (67.0, 40.5), (63.0, 42.0), (59.0, 40.5), (55.0, 39.5), (52.0, 38.5),
    ])


@lru_cache(maxsize=None)
def _jaw() -> QPainterPath:
    # Нижняя челюсть: сверху мелкие зубы, снизу край над просветом.
    return _path([
        (40.0, 45.5),
        (40.5, 41.0, 43.0, 38.5, 46.0, 38.5),
        (50.0, 40.0, 53.0, 40.5, 54.5, 40.0),
        (56.0, 37.5),
        (57.0, 40.0),
        (59.0, 38.5),
        (60.5, 41.5),
        (63.0, 40.0),
        (64.0, 43.0),
        (66.5, 41.5),
        (68.5, 43.5),
        (66.0, 46.0, 63.0, 48.5, 60.0, 50.0),
        (57.0, 51.5, 54.0, 53.0, 52.0, 53.0),
        (47.0, 52.5, 43.0, 49.5, 40.0, 45.5),
    ])


@lru_cache(maxsize=None)
def _bolt() -> QPainterPath:
    # Молния «Z»: верхняя перекладина, косая черта и нижняя перекладина.
    return _path([
        (69.0, 44.5),
        (69.0, 40.5, 92.0, 40.0, 96.5, 41.5),
        (98.5, 42.5, 97.5, 45.0, 95.5, 47.0),
        (79.0, 68.0),
        (90.0, 71.0, 100.0, 74.5, 100.5, 77.5),
        (99.5, 80.5, 86.0, 80.0, 79.0, 79.0),
        (60.0, 75.0),
        (56.5, 74.5, 57.0, 72.0, 59.0, 70.0),
        (80.5, 48.5),
        (76.0, 48.0, 69.0, 47.5, 69.0, 44.5),
    ])


@lru_cache(maxsize=None)
def _paw() -> QPainterPath:
    # Лапа, которая держит молнию снизу.
    return _path([
        (59.0, 77.0),
        (57.0, 73.0, 66.0, 69.0, 74.0, 68.0),
        (80.0, 67.5, 83.0, 70.0, 79.5, 73.5),
        (84.0, 71.5, 91.0, 72.0, 92.5, 74.5),
        (94.0, 78.0, 88.0, 80.5, 79.0, 80.5),
        (68.0, 80.5, 61.0, 80.0, 59.0, 77.0),
    ])


# Контуры медоеда не зависят от позы. Раньше каждый кадр заново собирал их из
# списков точек (Катмулл — Ром для складки, просвета, пасти и глотки) и дважды
# вырезал просвет из тела и складки — это самое дорогое место кадра. Теперь всё
# собирается один раз; рисуют их те же вызовы, картинка не меняется. Кэшированные
# контуры общие: их можно только рисовать, не изменять.

@lru_cache(maxsize=None)
def _holes() -> QPainterPath:
    return _gap().united(_throat())


@lru_cache(maxsize=None)
def _body_shape() -> QPainterPath:
    return _outer_body().subtracted(_holes())


@lru_cache(maxsize=None)
def _fold_shape() -> QPainterPath:
    return _fold().subtracted(_holes())


@lru_cache(maxsize=None)
def _stripe_path() -> QPainterPath:
    return _path([(39.0, 7.0), (50.0, 3.0, 60.0, 5.0, 65.0, 9.5)], close=False)


@lru_cache(maxsize=None)
def _ear_inner_path() -> QPainterPath:
    return _path([(28.5, 18.0), (26.0, 10.5, 30.5, 8.5, 32.0, 10.5), (33.5, 12.5, 34.0, 14.0, 35.0, 15.0)], close=False)


@lru_cache(maxsize=None)
def _closed_eye_path() -> QPainterPath:
    return _path([(51.5, 15.5), (56.0, 18.0, 60.5, 15.5)], close=False)


# Области слоёв в квадрате 100×100, с запасом на обводки и поворот уха.
_BACK_RECT = QRectF(-4.0, -3.0, 108.0, 108.0)
_BOLT_RECT = QRectF(52.0, 36.0, 52.0, 48.0)
# Готовые слои общие для всех медоедов программы. Спокойный медоед дышит —
# его масштаб проходит около полусотни ступеней (DrawnBadger.set_breath), и
# у каждой ступени свой слой тела и свой слой молнии.
_LAYERS = LayerCache(capacity=160)


def paint_logo_badger(painter: QPainter, pose: BadgerPose = BadgerPose(), *, steady: bool = False) -> None:
    """Рисует медоеда с логотипа в квадрате 100×100.

    ``steady`` — медоед сейчас не моргает и не делает жест: ухо, глаз и взгляд
    стоят на месте. Тогда неподвижные части берутся готовыми слоями, а заново
    рисуются только челюсть и лапа. Без ``steady`` всё рисуется напрямую.
    """
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)

    if steady and pose.blink <= 0.0:
        key = ("back", round(pose.ear, 3), round(pose.eye_open, 3), round(pose.look, 3))
        _LAYERS.draw(painter, key, _BACK_RECT, lambda layer: _paint_back(layer, pose))
    else:
        _paint_back(painter, pose)

    _paint_jaw(painter, pose.jaw)
    if steady and pose.bolt_glow <= 0.0:
        _LAYERS.draw(painter, "bolt", _BOLT_RECT, lambda layer: _paint_bolt(layer, 0.0))
    else:
        _paint_bolt(painter, pose.bolt_glow)
    _paint_paw(painter, pose.paw)
    painter.restore()


def _paint_back(painter: QPainter, pose: BadgerPose) -> None:
    """Всё, что лежит под челюстью: тело, складка, шея, пасть, голова, ухо, глаз и ноздря."""
    body = QLinearGradient(QPointF(30.0, 5.0), QPointF(55.0, 100.0))
    body.setColorAt(0.0, BODY_TOP)
    body.setColorAt(1.0, BODY_BOTTOM)
    painter.setBrush(body)
    painter.drawPath(_body_shape())

    fold = QLinearGradient(QPointF(28.0, 50.0), QPointF(88.0, 70.0))
    fold.setColorAt(0.0, FOLD_LEFT)
    fold.setColorAt(1.0, FOLD_RIGHT)
    painter.setBrush(fold)
    painter.drawPath(_fold_shape())

    painter.setBrush(NECK)
    painter.drawPath(_neck())

    # Пасть: тёмная, с прозрачной глоткой. Когда челюсть открывается шире,
    # пасть тянется за ней — без дыр за челюстью.
    painter.setBrush(MOUTH)
    painter.drawPath(_mouth())

    head = QLinearGradient(QPointF(40.0, 2.0), QPointF(70.0, 38.0))
    head.setColorAt(0.0, HEAD)
    head.setColorAt(1.0, HEAD_SHADE)
    painter.setBrush(head)
    painter.drawPath(_head())

    # Светлая полоса на макушке.
    stripe = QPen(STRIPE, 3.6)
    stripe.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(stripe)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(_stripe_path())
    painter.setPen(Qt.PenStyle.NoPen)

    _paint_ear(painter, pose.ear)
    _paint_eye(painter, pose)

    # Ноздря на кончике морды.
    painter.setBrush(STRIPE)
    painter.drawEllipse(QPointF(83.5, 23.0), 1.6, 1.9)


def _paint_ear(painter: QPainter, angle: float) -> None:
    painter.save()
    painter.translate(30.0, 20.0)
    painter.rotate(angle)
    painter.translate(-30.0, -20.0)
    painter.setBrush(EAR)
    painter.drawEllipse(QPointF(31.0, 14.0), 10.0, 10.5)
    inner = QPen(STRIPE, 3.0)
    inner.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(inner)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(_ear_inner_path())
    painter.setPen(Qt.PenStyle.NoPen)
    painter.restore()


def _paint_eye(painter: QPainter, pose: BadgerPose) -> None:
    center = QPointF(56.0, 15.0)
    rx, ry = 4.8, 4.8 * max(0.2, min(1.25, pose.eye_open))
    openness = 1.0 - max(0.0, min(1.0, pose.blink))
    if openness < 0.12:
        # Закрытый глаз — дужка цвета головы потемнее.
        lid = QPen(PUPIL, 1.6)
        lid.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(lid)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(_closed_eye_path())
        painter.setPen(Qt.PenStyle.NoPen)
        return
    ry *= openness
    painter.setBrush(STRIPE)
    painter.drawEllipse(center, rx, ry)
    painter.save()
    clip = QPainterPath()
    clip.addEllipse(center, rx, ry)
    painter.setClipPath(clip)
    painter.setBrush(PUPIL)
    painter.drawEllipse(QPointF(57.5 + 1.2 * pose.look, 16.3), 2.7, 2.7)
    painter.restore()


def _paint_jaw(painter: QPainter, amount: float) -> None:
    painter.save()
    # Челюсть ходит вокруг шарнира у шеи: плюс — открывается шире, минус — смыкается.
    painter.translate(43.0, 42.0)
    painter.rotate(9.0 * amount)
    painter.translate(-43.0, -42.0)
    painter.setBrush(JAW)
    painter.drawPath(_jaw())
    painter.restore()


def _paint_bolt(painter: QPainter, glow: float) -> None:
    bolt = _bolt()
    gradient = QLinearGradient(QPointF(66.0, 52.0), QPointF(86.0, 62.0))
    gradient.setColorAt(0.0, BOLT_GREEN)
    gradient.setColorAt(0.55, BOLT_YELLOW)
    gradient.setColorAt(1.0, BOLT_YELLOW)
    painter.setBrush(gradient)
    painter.drawPath(bolt)
    if glow > 0.0:
        # Блик пробегает по молнии сверху вниз.
        painter.save()
        painter.setClipPath(bolt)
        center = 30.0 + 60.0 * glow
        shine = QLinearGradient(QPointF(60.0, center - 10.0), QPointF(100.0, center + 10.0))
        clear = QColor(255, 255, 255, 0)
        shine.setColorAt(0.0, clear)
        shine.setColorAt(0.5, QColor(255, 255, 255, round(200 * math.sin(math.pi * glow))))
        shine.setColorAt(1.0, clear)
        painter.setBrush(shine)
        painter.drawRect(QRectF(55.0, 35.0, 50.0, 50.0))
        painter.restore()
    mark = QPen(BOLT_MARK, 1.5)
    mark.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(mark)
    painter.drawLine(QPointF(81.5, 43.8), QPointF(88.5, 42.8))
    painter.drawLine(QPointF(84.0, 41.0), QPointF(85.8, 46.0))
    painter.setPen(Qt.PenStyle.NoPen)


def _paint_paw(painter: QPainter, angle: float) -> None:
    painter.save()
    painter.translate(60.0, 77.0)
    painter.rotate(-angle)
    painter.translate(-60.0, -77.0)
    painter.setBrush(PAW)
    painter.drawPath(_paw())
    painter.restore()


__all__ = ["BadgerPose", "paint_logo_badger"]
