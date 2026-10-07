from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRegion
from PyQt6.QtWidgets import QApplication, QWidget

from ui.paint_layer_cache import LayerCache


BACKGROUND = "#20262e"
# Экраны Windows: 100 %, 125 %, 150 %, 175 %, 200 %, 250 %.
SCREEN_SCALES = (1.0, 1.25, 1.5, 1.75, 2.0, 2.5)
# Насколько слой может отличаться от прямого рисования: только округление
# при смешивании цветов, единицы из 255.
ROUNDING = 2


def paint_directly(_cache, painter, _key, _rect, paint) -> bool:
    """Замена ``LayerCache.draw`` для сравнения: рисует как раньше, без слоя."""
    return LayerCache._paint_directly(painter, paint)


def render(widget: QWidget, scale: float, *, region: QRegion | None = None) -> QImage:
    """Рисует виджет так, как он выглядит на экране с данным масштабом."""
    image = QImage(
        int(widget.width() * scale), int(widget.height() * scale), QImage.Format.Format_ARGB32_Premultiplied
    )
    image.setDevicePixelRatio(scale)
    image.fill(QColor(BACKGROUND))
    if region is None:
        widget.render(image)
    else:
        widget.render(image, QPoint(), region)
    return image.convertToFormat(QImage.Format.Format_RGBA8888)


def difference(first: QImage, second: QImage) -> int:
    """Самое большое расхождение цвета между двумя картинками (0..255)."""
    if first.size() != second.size():
        return 255
    a = first.constBits().asstring(first.sizeInBytes())
    b = second.constBits().asstring(second.sizeInBytes())
    if a == b:
        return 0
    return max(abs(x - y) for x, y in zip(a, b))


def share_above(first: QImage, second: QImage, threshold: int) -> float:
    """Доля значений цвета, которые расходятся больше чем на ``threshold``."""
    a = first.constBits().asstring(first.sizeInBytes())
    b = second.constBits().asstring(second.sizeInBytes())
    return sum(1 for x, y in zip(a, b) if abs(x - y) > threshold) / max(1, len(a))


def _sample(painter: QPainter) -> None:
    """Рисунок со всем, что чувствительно к доле точки экрана: градиент, обводки, полупрозрачность."""
    painter.setPen(Qt.PenStyle.NoPen)
    gradient = QLinearGradient(QPointF(5, 5), QPointF(60, 70))
    gradient.setColorAt(0.0, QColor("#2f9bf6"))
    gradient.setColorAt(1.0, QColor(44, 79, 166, 180))
    painter.setBrush(gradient)
    painter.drawRoundedRect(QRectF(3, 4, 70, 60), 9, 9)
    pen = QPen(QColor(255, 255, 255, 120), 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QPointF(40, 30), 18, 12)
    curve = QPainterPath(QPointF(10, 60))
    curve.cubicTo(QPointF(30, 20), QPointF(50, 80), QPointF(72, 10))
    painter.strokePath(curve, QPen(QColor(0, 0, 0, 40)))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(61, 252, 156, 90))
    painter.drawEllipse(QPointF(55, 50), 9.3, 9.3)


class _Picture(QWidget):
    """Виджет, который рисует ``_sample`` слоем или напрямую."""

    def __init__(self, parent: QWidget, *, layers: LayerCache | None, prepare=None) -> None:
        super().__init__(parent)
        self.layers = layers
        self.prepare = prepare
        self.painted = 0

    def _paint(self, painter: QPainter) -> None:
        self.painted += 1
        _sample(painter)

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.prepare is not None:
            self.prepare(painter)
        if self.layers is None:
            self._paint(painter)
        else:
            self.layers.draw(painter, "sample", QRectF(0, 0, 80, 80), self._paint)
        painter.end()


def _breathe(painter: QPainter) -> None:
    painter.translate(40, 78)
    painter.scale(0.988, 1.03)
    painter.translate(-40, -78)


def _tilt(painter: QPainter) -> None:
    painter.translate(40, 40)
    painter.rotate(6)
    painter.scale(0.64, 0.64)
    painter.translate(-40, -40)


class LayerCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _picture(self, *, layers: LayerCache | None, offset: QPoint = QPoint(3, 7), prepare=None):
        root = QWidget()
        root.resize(120, 120)
        picture = _Picture(root, layers=layers, prepare=prepare)
        picture.setGeometry(offset.x(), offset.y(), 90, 90)
        self.addCleanup(root.deleteLater)
        return root, picture

    def test_layer_looks_like_direct_painting_on_every_screen_scale(self) -> None:
        # Сдвиги 3,7 и 11,5 дают при 125–175 % начало виджета в доле точки экрана.
        for scale in SCREEN_SCALES:
            for offset in (QPoint(0, 0), QPoint(3, 7), QPoint(11, 5)):
                for name, prepare in (("ровно", None), ("растянут", _breathe), ("наклонён", _tilt)):
                    with self.subTest(scale=scale, offset=(offset.x(), offset.y()), transform=name):
                        direct_root, _ = self._picture(layers=None, offset=offset, prepare=prepare)
                        layered_root, _ = self._picture(layers=LayerCache(), offset=offset, prepare=prepare)
                        expected = render(direct_root, scale)

                        # Первый кадр рисует слой, второй берёт его готовым.
                        self.assertLessEqual(difference(expected, render(layered_root, scale)), ROUNDING)
                        self.assertLessEqual(difference(expected, render(layered_root, scale)), ROUNDING)

    def test_layer_ignoring_the_screen_point_fraction_would_be_noticed(self) -> None:
        """Проверка самого теста: без доли точки экрана края сглаживаются иначе."""
        root, picture = self._picture(layers=None, offset=QPoint(3, 7))
        expected = render(root, 1.5)
        picture.move(4, 8)  # полточки экрана при 150 %
        shifted = render(root, 1.5)
        self.assertGreater(difference(expected, shifted), 40)

    def test_layer_under_a_region_clip_looks_like_direct_painting(self) -> None:
        region = QRegion(QRect(0, 0, 40, 100)) | QRegion(QRect(60, 30, 50, 50))
        for scale in (1.0, 1.5, 2.0):
            with self.subTest(scale=scale):
                direct_root, _ = self._picture(layers=None)
                layered_root, _ = self._picture(layers=LayerCache())
                expected = render(direct_root, scale, region=region)
                render(layered_root, scale)
                self.assertLessEqual(difference(expected, render(layered_root, scale, region=region)), ROUNDING)

    def test_layer_is_painted_once_and_reused(self) -> None:
        layers = LayerCache()
        root, picture = self._picture(layers=layers)
        for _ in range(5):
            render(root, 1.0)
        self.assertEqual(picture.painted, 1)
        self.assertEqual(len(layers), 1)

    def test_move_by_whole_screen_points_keeps_the_layer(self) -> None:
        # Прокрутка страницы сдвигает виджет на целые точки: картинка слоя та же.
        layers = LayerCache()
        root, picture = self._picture(layers=layers)
        render(root, 1.0)
        picture.move(picture.x(), picture.y() + 13)
        render(root, 1.0)
        self.assertEqual(picture.painted, 1)

        # При 150 % сдвиг на одну точку виджета — полторы точки экрана: слой другой.
        render(root, 1.5)
        picture.move(picture.x() + 1, picture.y())
        render(root, 1.5)
        self.assertEqual(picture.painted, 3)

    def test_new_key_paints_a_new_layer(self) -> None:
        layers = LayerCache()
        image = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(BACKGROUND))
        painted: list[str] = []
        painter = QPainter(image)
        for key in ("green", "green", "red", "green", "red"):
            layers.draw(painter, key, QRectF(0, 0, 80, 80), lambda layer, key=key: painted.append(key))
        painter.end()
        self.assertEqual(painted, ["green", "red"])

    def test_rarely_used_layers_are_dropped(self) -> None:
        layers = LayerCache(capacity=2)
        image = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
        painted: list[str] = []
        painter = QPainter(image)
        for key in ("a", "b", "a", "c", "a", "b"):
            layers.draw(painter, key, QRectF(0, 0, 20, 20), lambda layer, key=key: painted.append(key))
        painter.end()
        # «b» вытеснен, когда появился «c», а нужный «a» остался.
        self.assertEqual(painted, ["a", "b", "c", "b"])
        self.assertEqual(len(layers), 2)
        self.assertLessEqual(len(layers._placed), 2)

    def test_translucent_or_unusual_painting_goes_directly(self) -> None:
        layers = LayerCache()
        image = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
        painted: list[int] = []
        painter = QPainter(image)
        painter.setOpacity(0.5)
        for _ in range(2):
            self.assertFalse(layers.draw(painter, "k", QRectF(0, 0, 20, 20), lambda layer: painted.append(1)))
        painter.setOpacity(1.0)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        self.assertFalse(layers.draw(painter, "k", QRectF(0, 0, 20, 20), lambda layer: painted.append(1)))
        painter.end()
        self.assertEqual(len(painted), 3)
        self.assertEqual(len(layers), 0)

    def test_too_large_layer_is_not_kept(self) -> None:
        layers = LayerCache()
        image = QImage(10, 10, QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(image)
        with mock.patch("ui.paint_layer_cache.MAX_LAYER_PIXELS", 100):
            self.assertFalse(layers.draw(painter, "k", QRectF(0, 0, 20, 20), lambda layer: None))
        painter.end()
        self.assertEqual(len(layers), 0)

    def test_pen_and_brush_stay_as_they_were(self) -> None:
        def messy(layer: QPainter) -> None:
            layer.setPen(QPen(QColor("#ff0000"), 5))
            layer.setBrush(QColor("#00ff00"))

        for layers in (LayerCache(), None):
            image = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
            painter = QPainter(image)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#123456"))
            if layers is None:
                LayerCache._paint_directly(painter, messy)
            else:
                layers.draw(painter, "k", QRectF(0, 0, 20, 20), messy)
                layers.draw(painter, "k", QRectF(0, 0, 20, 20), messy)
            self.assertEqual(painter.pen().style(), Qt.PenStyle.NoPen)
            self.assertEqual(painter.brush().color(), QColor("#123456"))
            painter.end()

    def test_layer_painter_starts_with_the_same_hints_pen_and_brush(self) -> None:
        seen: list[tuple] = []
        image = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#123456"))
        LayerCache().draw(
            painter,
            "k",
            QRectF(0, 0, 20, 20),
            lambda layer: seen.append(
                (
                    bool(layer.renderHints() & QPainter.RenderHint.Antialiasing),
                    layer.pen().style(),
                    layer.brush().color().name(),
                )
            ),
        )
        painter.end()
        self.assertEqual(seen, [(True, Qt.PenStyle.NoPen, "#123456")])


if __name__ == "__main__":
    unittest.main()
