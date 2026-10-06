"""Тень диалога, нарисованная один раз вместо живого размытия.

qfluentwidgets вешает на центральную панель диалога QGraphicsDropShadowEffect
с размытием 60. У виджетов Qt такой эффект ничего не запоминает: стоит
перерисоваться любой мелочи внутри панели (кадр талисмана, мигание курсора в
поле ввода, покачивание значка кнопки под мышью), и Qt заново рисует всю
панель в картинку и заново её размывает.

Замер на win10 (панель 640×612, внутри значок 62×70 с анимацией):

    живая тень библиотеки     47 % одного ядра, пока диалог открыт
    без живой тени             0,3 %

Окно «Доступно обновление» с талисманом открывается прямо при запуске, так
что на слабом компьютере это давало рывки всей программы.

Здесь тень размывается один раз на процесс и хранится маленькой картинкой
(углы и по пикселю от каждой стороны). Вокруг панели её растягивает отдельный
виджет-рамка. Середина рамки вырезана маской, поэтому перерисовка содержимого
диалога его вообще не задевает.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QPoint, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap, QRegion
from PyQt6.QtWidgets import QGraphicsBlurEffect, QGraphicsScene, QWidget


# border-radius у #centerWidget в стилях диалогов qfluentwidgets.
DIALOG_CORNER_RADIUS = 10

# QGraphicsBlurEffect внутри Qt умножает радиус на 2,5, а тень — нет. Чтобы
# получить ту же мягкость, что у QGraphicsDropShadowEffect, радиус делится.
_BLUR_EFFECT_RADIUS_SCALE = 2.5

_PATCH_CACHE: dict[tuple[int, int, int, int], QPixmap] = {}

_NO_PEN = Qt.PenStyle.NoPen
_TRANSPARENT = Qt.GlobalColor.transparent


def _corner_reach(blur_radius: int, corner_radius: int) -> int:
    """На сколько от угла панели тень ещё зависит от этого угла."""
    return int(blur_radius) + int(corner_radius)


def shadow_patch(blur_radius: int, color: QColor, corner_radius: int, dpr: float) -> QPixmap:
    """Картинка тени для панели минимального размера: углы и середины сторон.

    Размытие считается в пикселях экрана и не растёт с масштабом — ровно так
    же ведёт себя тень библиотеки.
    """
    blur = max(0, int(blur_radius))
    corner = max(0, int(corner_radius))
    dpr_key = max(1, int(round(float(dpr) * 100)))
    key = (blur, int(color.rgba()), corner, dpr_key)
    cached = _PATCH_CACHE.get(key)
    if cached is not None:
        return cached

    scale = dpr_key / 100.0
    side = 2 * (blur + _corner_reach(blur, corner)) + 2
    device_side = max(1, int(math.ceil(side * scale)))

    source = QImage(device_side, device_side, QImage.Format.Format_ARGB32_Premultiplied)
    source.fill(_TRANSPARENT)
    painter = QPainter(source)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(scale, scale)
    path = QPainterPath()
    path.addRoundedRect(
        QRectF(blur, blur, side - 2 * blur, side - 2 * blur),
        corner,
        corner,
    )
    painter.setPen(_NO_PEN)
    painter.fillPath(path, QColor(color.red(), color.green(), color.blue(), 255))
    painter.end()

    blurred = QImage(device_side, device_side, QImage.Format.Format_ARGB32_Premultiplied)
    blurred.fill(_TRANSPARENT)
    scene = QGraphicsScene()
    item = scene.addPixmap(QPixmap.fromImage(source))
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(blur / _BLUR_EFFECT_RADIUS_SCALE)
    item.setGraphicsEffect(effect)
    area = QRectF(0, 0, device_side, device_side)
    painter = QPainter(blurred)
    scene.render(painter, area, area)
    painter.end()

    # Прозрачность цвета тени накладывается отдельным проходом: эффект
    # размытия рисует своим painter-ом и setOpacity снаружи не учитывает.
    tinted = QImage(device_side, device_side, QImage.Format.Format_ARGB32_Premultiplied)
    tinted.fill(_TRANSPARENT)
    painter = QPainter(tinted)
    painter.setOpacity(color.alphaF())
    painter.drawImage(0, 0, blurred)
    painter.end()

    patch = QPixmap.fromImage(tinted)
    patch.setDevicePixelRatio(scale)
    _PATCH_CACHE[key] = patch
    return patch


class DialogStaticShadow(QWidget):
    """Рамка с готовой тенью вокруг центральной панели диалога."""

    def __init__(self, dialog: QWidget, panel: QWidget) -> None:
        super().__init__(dialog)
        self._panel = panel
        self._blur = 0
        self._offset = QPoint(0, 0)
        self._color = QColor(0, 0, 0, 0)
        self._corner = DIALOG_CORNER_RADIUS
        # Щелчок по тени должен, как и раньше, попадать в затемнение за панелью.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_shadow(self, blur_radius: int, offset: tuple[int, int], color: QColor) -> None:
        self._blur = max(0, int(blur_radius))
        self._offset = QPoint(int(offset[0]), int(offset[1]))
        self._color = QColor(color)
        self.stackUnder(self._panel)
        self.sync_geometry()
        self.update()

    def sync_geometry(self) -> None:
        """Ставит рамку вокруг панели: вызывается при её сдвиге и смене размера."""
        panel_rect = self._panel.geometry()
        blur = self._blur
        rect = panel_rect.adjusted(-blur, -blur, blur, blur).translated(self._offset)
        if rect != self.geometry():
            self.setGeometry(rect)
        # Под непрозрачной серединой панели тень не видна. Вырезаем её из
        # рамки: тогда перерисовка содержимого диалога рамку не вызывает.
        covered = panel_rect.adjusted(self._corner, self._corner, -self._corner, -self._corner)
        ring = QRegion(QRect(0, 0, rect.width(), rect.height()))
        if covered.isValid():
            ring = ring.subtracted(QRegion(covered.translated(-rect.topLeft())))
        self.setMask(ring)

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt API)
        if self._color.alpha() <= 0:
            return
        patch = shadow_patch(self._blur, self._color, self._corner, self.devicePixelRatioF())
        patch_side = patch.width() / patch.devicePixelRatioF()
        width = self.width()
        height = self.height()
        # Угол картинки: поле размытия плюс участок панели, где тень ещё
        # зависит от угла. У маленькой панели углы сжимаются до половины.
        reach = self._blur + _corner_reach(self._blur, self._corner)
        corner_w = min(reach, width / 2.0)
        corner_h = min(reach, height / 2.0)
        scale = patch.devicePixelRatioF()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        xs = ((0.0, corner_w, 0.0, reach),
              (corner_w, width - 2 * corner_w, reach, patch_side - 2 * reach),
              (width - corner_w, corner_w, patch_side - reach, reach))
        ys = ((0.0, corner_h, 0.0, reach),
              (corner_h, height - 2 * corner_h, reach, patch_side - 2 * reach),
              (height - corner_h, corner_h, patch_side - reach, reach))
        for row, (y, h, sy, sh) in enumerate(ys):
            for column, (x, w, sx, sw) in enumerate(xs):
                if w <= 0 or h <= 0 or (row == 1 and column == 1):
                    continue
                painter.drawPixmap(
                    QRectF(x, y, w, h),
                    patch,
                    QRectF(sx * scale, sy * scale, sw * scale, sh * scale),
                )
        painter.end()


__all__ = ["DIALOG_CORNER_RADIUS", "DialogStaticShadow", "shadow_patch"]
