"""Размытый снимок окна для подложки карточки тура.

Только средствами Qt: gaussianBlur из qfluentwidgets тянет numpy и PIL,
которых нет в собранной программе. Снимок сначала уменьшаем — так размытие
дешёвое и заодно мягче.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene


BLUR_DOWNSCALE = 0.25
BLUR_RADIUS = 9.0


def blur_pixmap(source: QPixmap, *, downscale: float = BLUR_DOWNSCALE, radius: float = BLUR_RADIUS) -> QPixmap | None:
    if source is None or source.isNull():
        return None
    image = source.toImage()
    width = max(1, round(image.width() * downscale))
    height = max(1, round(image.height() * downscale))
    small = QPixmap.fromImage(
        image.scaled(
            width,
            height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    )
    small.setDevicePixelRatio(1.0)

    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(small)
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius)
    effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(effect)
    scene.addItem(item)

    result = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(QColor(0, 0, 0, 0))
    painter = QPainter(result)
    try:
        scene.render(painter, QRectF(0, 0, width, height), QRectF(0, 0, width, height))
    finally:
        painter.end()
    scene.clear()

    blurred = QPixmap.fromImage(result)
    blurred.setDevicePixelRatio(1.0)
    return blurred


__all__ = ["blur_pixmap"]
