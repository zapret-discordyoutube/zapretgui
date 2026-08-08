"""Перетаскивание файлов preset-ов в главное окно."""

from __future__ import annotations

import os
from collections.abc import Callable

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    QRectF,
    Qt,
    pyqtProperty,
)
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget
from qfluentwidgets import isDarkTheme, themeColor

from app.ui_texts import tr as tr_catalog
from log.log import log


def dropped_preset_file_paths(mime_data) -> list[str]:
    """Возвращает уникальные локальные TXT-файлы из данных перетаскивания."""
    if mime_data is None:
        return []
    try:
        if not mime_data.hasUrls():
            return []
        urls = mime_data.urls()
    except Exception:
        return []

    paths: list[str] = []
    seen: set[str] = set()
    for url in urls:
        try:
            if not url.isLocalFile():
                continue
            path = str(url.toLocalFile() or "").strip()
        except Exception:
            continue
        if not path or not path.lower().endswith(".txt") or not os.path.isfile(path):
            continue
        path_key = os.path.normcase(os.path.normpath(path))
        if path_key in seen:
            continue
        seen.add(path_key)
        paths.append(path)
    return paths


class PresetFileDropOverlay(QWidget):
    """Рисует полноэкранную подсказку, пока над окном держат TXT-файл."""

    def __init__(
        self,
        window: QWidget,
        *,
        language_resolver: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(window)
        self._language_resolver = language_resolver or (lambda: "ru")
        self._overlay_opacity = 0.0
        self._title = ""
        self._subtitle = ""
        self._hide_after_animation = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

        self._animation = QPropertyAnimation(self, b"overlayOpacity", self)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.finished.connect(self._on_animation_finished)

    def sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())

    def show_hint(self, paths: list[str]) -> None:
        if not paths:
            self.hide_hint()
            return

        try:
            language = self._language_resolver()
        except Exception:
            language = "ru"
        self._title = tr_catalog(
            "common.preset_drop.title",
            language=language,
            default="Отпустите, чтобы импортировать пресет",
        )
        if len(paths) == 1:
            self._subtitle = tr_catalog(
                "common.preset_drop.single",
                language=language,
                default="TXT-файл: {file_name}",
            ).format(file_name=os.path.basename(paths[0]))
        else:
            self._subtitle = tr_catalog(
                "common.preset_drop.multiple",
                language=language,
                default="Будут импортированы TXT-файлы: {count}",
            ).format(count=len(paths))

        self.sync_geometry()
        self._hide_after_animation = False
        self.show()
        self.raise_()
        self._animate_to(1.0, duration=160)

    def hide_hint(self) -> None:
        if self.isHidden():
            return
        self._hide_after_animation = True
        self._animate_to(0.0, duration=120)

    def hide_immediately(self) -> None:
        self._animation.stop()
        self._hide_after_animation = False
        self._set_overlay_opacity(0.0)
        self.hide()

    def _animate_to(self, value: float, *, duration: int) -> None:
        if self._animation.state() == QPropertyAnimation.State.Running:
            self._animation.stop()
        self._animation.setDuration(duration)
        self._animation.setStartValue(self._overlay_opacity)
        self._animation.setEndValue(value)
        self._animation.start()

    def _on_animation_finished(self) -> None:
        if self._hide_after_animation and self._overlay_opacity <= 0.001:
            self.hide()
            self._hide_after_animation = False

    def _get_overlay_opacity(self) -> float:
        return self._overlay_opacity

    def _set_overlay_opacity(self, value: float) -> None:
        self._overlay_opacity = max(0.0, min(1.0, float(value)))
        self.update()

    overlayOpacity = pyqtProperty(  # noqa: N815 (Qt property name)
        float,
        fget=_get_overlay_opacity,
        fset=_set_overlay_opacity,
    )

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        del event
        if self._overlay_opacity <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
        )
        painter.setOpacity(self._overlay_opacity)

        dark = isDarkTheme()
        accent = QColor(themeColor())
        veil = QColor(7, 12, 20, 190) if dark else QColor(244, 249, 255, 210)
        accent_wash = QColor(accent)
        accent_wash.setAlpha(45 if dark else 34)
        painter.fillRect(self.rect(), veil)
        painter.fillRect(self.rect(), accent_wash)

        margin = 32.0
        card_width = max(240.0, min(620.0, float(self.width()) - margin * 2))
        card_height = min(230.0, max(180.0, float(self.height()) - margin * 2))
        card = QRectF(
            (self.width() - card_width) / 2,
            (self.height() - card_height) / 2,
            card_width,
            card_height,
        )

        glow = card.adjusted(-10, -10, 10, 10)
        glow_color = QColor(accent)
        glow_color.setAlpha(34)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow_color)
        painter.drawRoundedRect(glow, 28, 28)

        card_color = QColor(31, 35, 42, 246) if dark else QColor(255, 255, 255, 248)
        border_color = QColor(accent)
        border_color.setAlpha(215)
        painter.setBrush(card_color)
        painter.setPen(QPen(border_color, 2))
        painter.drawRoundedRect(card, 22, 22)

        badge_size = 54.0
        badge = QRectF(
            card.center().x() - badge_size / 2,
            card.top() + 28,
            badge_size,
            badge_size,
        )
        badge_color = QColor(accent)
        badge_color.setAlpha(42)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(badge_color)
        painter.drawEllipse(badge)

        badge_font = QFont(self.font())
        badge_font.setPixelSize(15)
        badge_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(badge_font)
        painter.setPen(accent)
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, "TXT")

        text_color = QColor(247, 249, 252) if dark else QColor(27, 31, 38)
        title_font = QFont(self.font())
        title_font.setPixelSize(20)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(text_color)
        title_rect = QRectF(card.left() + 24, badge.bottom() + 17, card.width() - 48, 31)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignCenter, self._title)

        subtitle_color = QColor(190, 196, 207) if dark else QColor(94, 101, 113)
        subtitle_font = QFont(self.font())
        subtitle_font.setPixelSize(14)
        painter.setFont(subtitle_font)
        painter.setPen(subtitle_color)
        subtitle = painter.fontMetrics().elidedText(
            self._subtitle,
            Qt.TextElideMode.ElideMiddle,
            max(100, int(card.width() - 64)),
        )
        subtitle_rect = QRectF(card.left() + 32, title_rect.bottom() + 8, card.width() - 64, 24)
        painter.drawText(subtitle_rect, Qt.AlignmentFlag.AlignCenter, subtitle)
        painter.end()


class WindowPresetFileDropFilter(QObject):
    """Направляет TXT-файлы текущей странице, если она умеет их импортировать."""

    def __init__(
        self,
        window,
        *,
        target_resolver: Callable[[], object | None],
        language_resolver: Callable[[], str] | None = None,
        overlay: PresetFileDropOverlay | object | None = None,
    ) -> None:
        super().__init__(window if isinstance(window, QObject) else None)
        self._window = window
        self._target_resolver = target_resolver
        self.overlay = overlay
        if self.overlay is None and isinstance(window, QWidget):
            self.overlay = PresetFileDropOverlay(
                window,
                language_resolver=language_resolver,
            )

    def _belongs_to_window(self, watched) -> bool:
        if watched is self._window:
            return True
        try:
            return watched.window() is self._window
        except Exception:
            return False

    def _import_action(self):
        try:
            target = self._target_resolver()
        except Exception:
            return None
        action = getattr(target, "import_dropped_preset_files", None)
        return action if callable(action) else None

    def eventFilter(self, watched, event):  # noqa: N802 (Qt override)
        if not self._belongs_to_window(watched):
            return False

        event_type = event.type()
        if event_type == QEvent.Type.DragLeave:
            self._hide_overlay()
            return False
        if event_type not in {
            QEvent.Type.DragEnter,
            QEvent.Type.DragMove,
            QEvent.Type.Drop,
        }:
            return False

        import_action = self._import_action()
        if import_action is None:
            self._hide_overlay()
            return False

        paths = dropped_preset_file_paths(event.mimeData())
        if not paths:
            self._hide_overlay()
            return False

        if event_type == QEvent.Type.Drop:
            try:
                if not bool(import_action(paths)):
                    self._hide_overlay()
                    return False
            except Exception as exc:
                log(f"Не удалось передать TXT-файл на импорт: {exc}", "ERROR")
                self._hide_overlay()
                return False
            self._hide_overlay()
        else:
            self._show_overlay(paths)

        event.acceptProposedAction()
        return True

    def _show_overlay(self, paths: list[str]) -> None:
        action = getattr(self.overlay, "show_hint", None)
        if callable(action):
            action(paths)

    def _hide_overlay(self) -> None:
        action = getattr(self.overlay, "hide_hint", None)
        if callable(action):
            action()


__all__ = [
    "PresetFileDropOverlay",
    "WindowPresetFileDropFilter",
    "dropped_preset_file_paths",
]
