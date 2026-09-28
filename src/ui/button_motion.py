"""Живые кнопки во всей программе: значок покачивается, нажатие пружинит.

При наведении значок кнопки слегка покачивается и затухает. При нажатии
значок вжимается, при отпускании пружинит обратно, а от точки клика по
кнопке расходится мягкая волна. Всё это подчиняется переключателю
«Живые анимации» и ничего не тратит, пока с кнопкой ничего не происходит.

Как подключается (один раз при старте, до создания виджетов):

* кнопки qfluentwidgets (PushButton, ToolButton и все наследники) —
  обёртки на enterEvent/mousePressEvent/mouseReleaseEvent базовых классов
  и на ``_drawIcon`` каждого класса, где он определён. Обёртка поворачивает
  и масштабирует painter вокруг центра значка; флаг повторного входа не
  даёт применить поворот дважды, когда один ``_drawIcon`` зовёт другой
  (ToggleButton → PrimaryPushButton → PushButton);
* простые QPushButton, которые qfluentwidgets создаёт сам («Отмена» в
  диалогах, кнопка карточки PushSettingCard) — обёртка на конструктор
  владельца вешает на них общий фильтр событий;
* волна рисуется общим фильтром событий: на Paint он вызывает родную
  отрисовку кнопки и рисует волну последним слоем своим QPainter.
  Отдельных виджетов-слоёв нет, дерево доступности не меняется.

Анимации — QVariantAnimation: при выключенных анимациях WinUI общий
fallback подменяет QPropertyAnimation.start (см. ui/animation_policy.py).
Подписок на смену темы нет, цвет волны берётся в момент отрисовки.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPointF, QRectF, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QPainter, QPainterPath
from PyQt6.QtWidgets import QPushButton

from log.log import log
from ui.animation_policy import are_live_animations_enabled


WOBBLE_DURATION_MS = 560
WOBBLE_AMPLITUDE_DEG = 9.0
WOBBLE_SWINGS = 2.0
PRESS_DURATION_MS = 90
RELEASE_DURATION_MS = 300
PRESSED_ICON_SHRINK = 0.16
RIPPLE_DURATION_MS = 450
RIPPLE_MAX_ALPHA = 0.22
BUTTON_CORNER_RADIUS = 5.0

_MOTION_ATTR = "_zapret_button_motion"
_DRAWING_ATTR = "_zapret_button_motion_drawing"
_FILTERED_ATTR = "_zapret_button_motion_filtered"
_PATCHED_ATTR = "_zapret_button_motion_patched"
_PLAIN_BUTTON_ATTRS = ("yesButton", "cancelButton", "button", "completeButton")

_INSTALLED = False
_FLUENT_BUTTON_TYPES: tuple[type, ...] = ()
_FILTER: "_ButtonMotionFilter | None" = None


def _can_animate(button) -> bool:
    try:
        if not button.isEnabled() or not button.isVisible():
            return False
        window = button.window()
        if window is not None and window.isMinimized():
            return False
    except RuntimeError:
        return False
    return are_live_animations_enabled()


def _make_animation(parent: QObject, on_value, *, duration_ms: int = 0, curve=None) -> QVariantAnimation:
    animation = QVariantAnimation(parent)
    # Значения задаются до подписки: setStartValue сразу шлёт valueChanged,
    # и жест считался бы начатым, хотя его никто не запускал.
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    if duration_ms:
        animation.setDuration(duration_ms)
    if curve is not None:
        animation.setEasingCurve(curve)
    animation.valueChanged.connect(on_value)
    return animation


class _ButtonMotion(QObject):
    """Состояние жестов одной кнопки. Создаётся при первом наведении или нажатии."""

    def __init__(self, button) -> None:
        super().__init__(button)
        self._button = button
        self._wobble_t = 0.0
        self._press = 0.0
        self._ripple_t = 1.0
        self._ripple_origin = QPointF()

        self._wobble = _make_animation(self, self._on_wobble, duration_ms=WOBBLE_DURATION_MS)
        self._wobble.finished.connect(self._on_wobble_finished)

        self._press_anim = QVariantAnimation(self)
        self._press_anim.valueChanged.connect(self._on_press)

        self._ripple = _make_animation(
            self,
            self._on_ripple,
            duration_ms=RIPPLE_DURATION_MS,
            curve=QEasingCurve.Type.OutCubic,
        )
        self._ripple.finished.connect(self._on_ripple_finished)

    # ---- жесты ---------------------------------------------------------

    def hover(self) -> None:
        if self._wobble.state() == QVariantAnimation.State.Running:
            return
        if not _button_has_icon(self._button) or not _can_animate(self._button):
            return
        self._wobble.start()

    def press(self, pos: QPointF) -> None:
        if not _can_animate(self._button):
            return
        self._animate_press(1.0, PRESS_DURATION_MS, QEasingCurve.Type.OutCubic)
        self._ripple.stop()
        self._ripple_origin = QPointF(pos)
        self._ripple_t = 0.0
        _ensure_paint_filter(self._button)
        self._ripple.start()

    def release(self) -> None:
        if self._press <= 0.0 and self._press_anim.state() != QVariantAnimation.State.Running:
            return
        self._animate_press(0.0, RELEASE_DURATION_MS, QEasingCurve.Type.OutBack)

    def _animate_press(self, target: float, duration_ms: int, curve: QEasingCurve.Type) -> None:
        self._press_anim.stop()
        self._press_anim.setStartValue(float(self._press))
        self._press_anim.setEndValue(float(target))
        self._press_anim.setDuration(duration_ms)
        self._press_anim.setEasingCurve(curve)
        self._press_anim.start()

    # ---- текущее состояние ---------------------------------------------

    def icon_angle(self) -> float:
        t = self._wobble_t
        if t <= 0.0 or t >= 1.0:
            return 0.0
        return WOBBLE_AMPLITUDE_DEG * math.sin(2.0 * math.pi * WOBBLE_SWINGS * t) * (1.0 - t) ** 1.3

    def icon_scale(self) -> float:
        return 1.0 - PRESSED_ICON_SHRINK * self._press

    def icon_transformed(self) -> bool:
        return abs(self.icon_angle()) > 0.01 or abs(self.icon_scale() - 1.0) > 0.001

    def ripple_active(self) -> bool:
        return self._ripple_t < 1.0

    def paint_ripple(self, button) -> None:
        t = self._ripple_t
        if t >= 1.0:
            return
        rect = QRectF(button.rect())
        if rect.isEmpty():
            return
        origin = self._ripple_origin
        reach = max(
            math.hypot(origin.x() - corner.x(), origin.y() - corner.y())
            for corner in (rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight())
        )
        color = _ripple_color(button)
        color.setAlphaF(RIPPLE_MAX_ALPHA * (1.0 - t))

        radius = _corner_radius(button, rect)
        clip = QPainterPath()
        clip.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

        painter = QPainter(button)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setClipPath(clip)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(origin, reach * t, reach * t)
        finally:
            painter.end()

    # ---- анимации ------------------------------------------------------

    def _repaint(self) -> None:
        try:
            self._button.update()
        except RuntimeError:
            pass

    def _on_wobble(self, value) -> None:
        self._wobble_t = float(value)
        self._repaint()

    def _on_wobble_finished(self) -> None:
        self._wobble_t = 0.0
        self._repaint()

    def _on_press(self, value) -> None:
        self._press = float(value)
        self._repaint()

    def _on_ripple(self, value) -> None:
        self._ripple_t = float(value)
        self._repaint()

    def _on_ripple_finished(self) -> None:
        self._ripple_t = 1.0
        self._repaint()


def _button_has_icon(button) -> bool:
    try:
        return not button.icon().isNull()
    except Exception:
        return False


def _is_primary(button) -> bool:
    try:
        from qfluentwidgets.components.widgets.button import (
            PrimaryDropDownButtonBase,
            PrimaryPushButton,
            PrimaryToolButton,
            PrimarySplitDropButton,
        )

        if isinstance(button, (PrimaryPushButton, PrimaryToolButton, PrimaryDropDownButtonBase, PrimarySplitDropButton)):
            return True
    except Exception:
        pass
    try:
        if button.objectName() == "primaryButton":
            return True
        return bool(button.isCheckable() and button.isChecked())
    except Exception:
        return False


def _ripple_color(button) -> QColor:
    try:
        from qfluentwidgets import isDarkTheme

        dark = bool(isDarkTheme())
    except Exception:
        dark = False
    # На акцентной кнопке фон контрастный теме, поэтому и волна наоборот.
    light_wave = dark != _is_primary(button)
    return QColor(255, 255, 255) if light_wave else QColor(0, 0, 0)


def _corner_radius(button, rect: QRectF) -> float:
    try:
        from qfluentwidgets.components.widgets.button import PillButtonBase

        if isinstance(button, PillButtonBase):
            return rect.height() / 2.0
    except Exception:
        pass
    return min(BUTTON_CORNER_RADIUS, rect.height() / 2.0)


def button_motion(button) -> _ButtonMotion:
    """Возвращает состояние жестов кнопки, создавая его при первом обращении."""
    motion = getattr(button, _MOTION_ATTR, None)
    if motion is None:
        motion = _ButtonMotion(button)
        setattr(button, _MOTION_ATTR, motion)
    return motion


class _ButtonMotionFilter(QObject):
    """Общий фильтр: волна поверх отрисовки и жесты простых QPushButton."""

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        try:
            kind = event.type()
            if kind == QEvent.Type.Paint:
                motion = getattr(obj, _MOTION_ATTR, None)
                if motion is None or not motion.ripple_active():
                    return False
                obj.paintEvent(event)
                motion.paint_ripple(obj)
                return True
            if isinstance(obj, _FLUENT_BUTTON_TYPES):
                return False
            if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                button_motion(obj).press(event.position())
            elif kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                motion = getattr(obj, _MOTION_ATTR, None)
                if motion is not None:
                    motion.release()
        except Exception:
            return False
        return False


def _motion_filter() -> _ButtonMotionFilter:
    global _FILTER
    if _FILTER is None:
        _FILTER = _ButtonMotionFilter()
    return _FILTER


def _ensure_paint_filter(button) -> None:
    if getattr(button, _FILTERED_ATTR, False):
        return
    button.installEventFilter(_motion_filter())
    setattr(button, _FILTERED_ATTR, True)


def attach_button_motion(button) -> None:
    """Подключает жесты к кнопке, которую нельзя обернуть через её класс."""
    try:
        _ensure_paint_filter(button)
    except Exception:
        pass


# ---- установка -------------------------------------------------------------


def _wrap_draw_icon(cls: type) -> None:
    original = cls.__dict__["_drawIcon"]

    def _drawIcon(self, icon, painter, rect, *args, **kwargs):  # noqa: N802
        motion = getattr(self, _MOTION_ATTR, None)
        if motion is None or getattr(self, _DRAWING_ATTR, False) or not motion.icon_transformed():
            return original(self, icon, painter, rect, *args, **kwargs)

        setattr(self, _DRAWING_ATTR, True)
        painter.save()
        try:
            center = QRectF(rect).center()
            scale = motion.icon_scale()
            painter.translate(center)
            painter.rotate(motion.icon_angle())
            painter.scale(scale, scale)
            painter.translate(-center)
            return original(self, icon, painter, rect, *args, **kwargs)
        finally:
            painter.restore()
            setattr(self, _DRAWING_ATTR, False)

    _drawIcon.__wrapped__ = original
    cls._drawIcon = _drawIcon


def _patch_fluent_events(base: type) -> None:
    original_enter = base.enterEvent
    original_press = base.mousePressEvent
    original_release = base.mouseReleaseEvent

    def enterEvent(self, e):  # noqa: N802
        original_enter(self, e)
        try:
            button_motion(self).hover()
        except Exception:
            pass

    def mousePressEvent(self, e):  # noqa: N802
        try:
            if e.button() == Qt.MouseButton.LeftButton:
                button_motion(self).press(e.position())
        except Exception:
            pass
        original_press(self, e)

    def mouseReleaseEvent(self, e):  # noqa: N802
        # Жест до оригинала: обработчик clicked может удалить кнопку.
        try:
            motion = getattr(self, _MOTION_ATTR, None)
            if motion is not None and e.button() == Qt.MouseButton.LeftButton:
                motion.release()
        except Exception:
            pass
        original_release(self, e)

    base.enterEvent = enterEvent
    base.mousePressEvent = mousePressEvent
    base.mouseReleaseEvent = mouseReleaseEvent


def _patch_plain_button_owner(owner: type, method_name: str) -> None:
    original = owner.__dict__.get(method_name)
    if original is None or getattr(original, _PATCHED_ATTR, False):
        return

    def wrapped(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        for attr in _PLAIN_BUTTON_ATTRS:
            button = getattr(self, attr, None)
            if isinstance(button, QPushButton):
                attach_button_motion(button)
        return result

    setattr(wrapped, _PATCHED_ATTR, True)
    wrapped.__wrapped__ = original
    setattr(owner, method_name, wrapped)


def _iter_subclasses(*bases: type):
    seen: set[type] = set()
    queue = list(bases)
    while queue:
        cls = queue.pop(0)
        if cls in seen:
            continue
        seen.add(cls)
        yield cls
        queue.extend(cls.__subclasses__())


def install_button_motion() -> None:
    """Ставит жесты кнопок один раз, строго до создания первых кнопок."""
    global _INSTALLED, _FLUENT_BUTTON_TYPES
    if _INSTALLED:
        return
    _INSTALLED = True

    try:
        import qfluentwidgets  # noqa: F401 — загружает все компоненты и их подклассы кнопок
        from qfluentwidgets.components.widgets import button as fluent_button

        bases = (fluent_button.PushButton, fluent_button.ToolButton)
        _FLUENT_BUTTON_TYPES = bases
        for base in bases:
            _patch_fluent_events(base)
        for cls in _iter_subclasses(*bases):
            if "_drawIcon" in cls.__dict__:
                _wrap_draw_icon(cls)
    except Exception as exc:  # noqa: BLE001 — анимация не должна валить старт
        log(f"Не удалось подключить жесты кнопок qfluentwidgets: {exc}", "WARNING")

    owners = (
        ("qfluentwidgets.components.dialog_box.message_box_base", "MessageBoxBase", "__init__"),
        ("qfluentwidgets.components.dialog_box.dialog", "Ui_MessageBox", "_setUpUi"),
        ("qfluentwidgets.components.dialog_box.message_dialog", "MessageDialog", "__init__"),
        ("qfluentwidgets.components.dialog_box.color_dialog", "ColorDialog", "__init__"),
        ("qfluentwidgets.components.settings.setting_card", "PushSettingCard", "__init__"),
    )
    for module_name, class_name, method_name in owners:
        try:
            module = __import__(module_name, fromlist=[class_name])
            _patch_plain_button_owner(getattr(module, class_name), method_name)
        except Exception as exc:  # noqa: BLE001
            log(f"Не удалось подключить жесты к {class_name}: {exc}", "WARNING")


__all__ = ["attach_button_motion", "button_motion", "install_button_motion"]
