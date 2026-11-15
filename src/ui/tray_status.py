"""Shared pieces of the tray icon of every system: the state dot on the logo and the tooltip text."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter

from ui.launch_control import mode_label_for_launch_method, normalize_launch_phase, phase_color
from ui.tray_menu.model import tray_status_text

try:
    from app.ui_texts import tr as tr_catalog
except Exception:
    def tr_catalog(_key, *, language=None, default=""):  # type: ignore[no-redef]
        return default


# ---- иконка трея с точкой состояния ----------------------------------------

# Точка в правом нижнем углу логотипа: примерно 40% стороны. Вокруг неё логотип
# «вырезается» прозрачным кольцом — так точка читается и на светлой, и на тёмной
# панели задач, как значки-наклейки самой Windows.
STATUS_DOT_RADIUS_RATIO = 0.2
STATUS_DOT_CUTOUT_RATIO = 0.075


def tray_icon_dot_color(phase: str) -> str | None:
    """Цвет точки на иконке трея. Когда Zapret остановлен, точки нет вовсе."""
    color = phase_color(phase)
    if normalize_launch_phase(phase) == "failed":
        # Ошибка видна в меню и уведомлениях; на иконке красная точка
        # «мозолила бы глаза» до следующего запуска.
        return None
    return color


def render_status_icon_image(base: QImage, color: str | None, size: int) -> QImage:
    """Рисует логотип размера size×size и, если задан цвет, точку состояния в углу."""
    size = max(8, int(size))
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    if not base.isNull():
        scaled = base.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter.drawImage(
            QPointF((size - scaled.width()) / 2, (size - scaled.height()) / 2),
            scaled,
        )
    if color:
        radius = size * STATUS_DOT_RADIUS_RATIO
        cutout = radius + max(1.0, size * STATUS_DOT_CUTOUT_RATIO)
        center = QPointF(size - radius - 0.5, size - radius - 0.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.setBrush(Qt.GlobalColor.black)
        painter.drawEllipse(center, cutout, cutout)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setBrush(QColor(color))
        painter.drawEllipse(center, radius, radius)
    painter.end()
    return image


# ---- тексты ---------------------------------------------------------------

def build_tray_tooltip(*, phase: str, launch_method: str, preset_name: str, language: str | None = None) -> str:
    mode = mode_label_for_launch_method(launch_method)
    lines = [f"{mode} — {tray_status_text(phase, language=language)}"]
    if preset_name:
        lines.append(
            tr_catalog("tray.tooltip.preset", language=language, default="Пресет: {preset}").format(preset=preset_name)
        )
    return "\n".join(lines)
