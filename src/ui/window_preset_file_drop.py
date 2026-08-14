"""Перетаскивание файлов preset-ов в главное окно."""

from __future__ import annotations

import os
import weakref
from collections.abc import Callable

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon, InfoBar, InfoBarPosition, isDarkTheme

from app.ui_texts import tr as tr_catalog
from log.log import log
from ui.windows_file_drop import windows_dropped_file_paths


# Сколько держать плашку «файл принят» после броска, пока идёт импорт.
ACCEPTED_FLASH_DURATION_MS = 1400


def valid_preset_file_paths(file_paths) -> list[str]:
    """Оставляет уникальные существующие TXT- и ZIP-файлы preset-ов."""
    paths: list[str] = []
    seen: set[str] = set()
    for file_path in file_paths or ():
        path = str(file_path or "").strip()
        if not path or not path.lower().endswith((".txt", ".zip")) or not os.path.isfile(path):
            continue
        path_key = os.path.normcase(os.path.normpath(path))
        if path_key in seen:
            continue
        seen.add(path_key)
        paths.append(path)
    return paths


def dropped_preset_file_paths(mime_data) -> list[str]:
    """Возвращает уникальные локальные TXT- и ZIP-файлы из перетаскивания."""
    if mime_data is None:
        return []
    try:
        if not mime_data.hasUrls():
            return []
        urls = mime_data.urls()
    except Exception:
        return []

    local_paths: list[str] = []
    for url in urls:
        try:
            if not url.isLocalFile():
                continue
            path = str(url.toLocalFile() or "").strip()
        except Exception:
            continue
        local_paths.append(path)
    return valid_preset_file_paths(local_paths)


class PresetFileDropOverlay(QWidget):
    """Показывает штатную Fluent-подсказку поверх окна при переносе TXT."""

    def __init__(
        self,
        window: QWidget,
        *,
        language_resolver: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(window)
        self._language_resolver = language_resolver or (lambda: "ru")
        self._title = ""
        self._subtitle = ""
        self._visual_opacity = 0.0
        self._hide_after_animation = False
        self.setObjectName("presetFileDropOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._backdrop = QWidget(self)
        self._backdrop.setObjectName("presetFileDropBackdrop")
        self._backdrop.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self._backdrop_effect = QGraphicsOpacityEffect(self._backdrop)
        self._backdrop_effect.setOpacity(0.0)
        self._backdrop.setGraphicsEffect(self._backdrop_effect)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(48, 48, 48, 48)
        self._layout.addStretch(2)
        self._info_bar = InfoBar(
            FluentIcon.DOCUMENT,
            "",
            "",
            orient=Qt.Orientation.Horizontal,
            isClosable=False,
            duration=-1,
            position=InfoBarPosition.NONE,
            parent=self,
        )
        self._info_bar.setMinimumWidth(480)
        self._info_bar.setMaximumWidth(720)
        self._layout.addWidget(
            self._info_bar,
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )
        self._layout.addStretch(3)

        self._info_bar.opacityEffect.setOpacity(0.0)
        self._animation = QVariantAnimation(self)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._set_visual_opacity)
        self._animation.finished.connect(self._on_animation_finished)
        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self.hide_hint)
        self.hide()

    def sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
            self._backdrop.setGeometry(self.rect())
            self._backdrop.lower()

    def show_hint(self, paths: list[str]) -> None:
        self._show_for_paths(paths, "common.preset_drop.title", "Отпустите файл для импорта")

    def show_accepted(self, paths: list[str]) -> None:
        """Короткая плашка в момент броска: файл принят и уходит на импорт."""
        if self._show_for_paths(paths, "common.preset_drop.accepted", "Файл принят — импортирую…"):
            self._auto_hide_timer.start(ACCEPTED_FLASH_DURATION_MS)

    def show_hover_hint(self) -> None:
        """Подсказка наведения, когда имя файла ещё неизвестно (Windows)."""
        language = self._resolve_language()
        self._auto_hide_timer.stop()
        self._apply_texts_and_show(
            tr_catalog(
                "common.preset_drop.title",
                language=language,
                default="Отпустите файл для импорта",
            ),
            tr_catalog(
                "common.preset_drop.any_file",
                language=language,
                default="TXT preset or ZIP archive" if language == "en" else "TXT-пресет или ZIP-архив",
            ),
        )

    def hide_hover_hint(self) -> None:
        """Скрывает подсказку наведения, не трогая плашку «файл принят»."""
        if self._auto_hide_timer.isActive():
            return
        self.hide_hint()

    def _resolve_language(self) -> str:
        try:
            return self._language_resolver()
        except Exception:
            return "ru"

    def _show_for_paths(self, paths: list[str], title_key: str, default_title: str) -> bool:
        if not paths:
            self.hide_hint()
            return False

        self._auto_hide_timer.stop()
        language = self._resolve_language()
        title = tr_catalog(
            title_key,
            language=language,
            default=default_title,
        )
        if len(paths) == 1:
            subtitle = tr_catalog(
                "common.preset_drop.single",
                language=language,
                default="{file_name}",
            ).format(file_name=os.path.basename(paths[0]))
        else:
            subtitle = tr_catalog(
                "common.preset_drop.file_count",
                language=language,
                default=(
                    "Preset files: {count}"
                    if language == "en"
                    else "Количество файлов пресетов: {count}"
                ),
            ).format(count=len(paths))
        self._apply_texts_and_show(title, subtitle)
        return True

    def _apply_texts_and_show(self, title: str, subtitle: str) -> None:
        self._title = title
        self._subtitle = subtitle
        self._info_bar.title = self._title
        self._info_bar.content = self._subtitle
        self._info_bar.titleLabel.setText(self._title)
        self._info_bar.contentLabel.setText(self._subtitle)
        self._info_bar.titleLabel.setVisible(bool(self._title))
        self._info_bar.contentLabel.setVisible(bool(self._subtitle))
        self._info_bar.setAccessibleName(self._title)
        self._info_bar.setAccessibleDescription(self._subtitle)
        self._info_bar.adjustSize()
        self._refresh_backdrop()
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
        self._auto_hide_timer.stop()
        self._animation.stop()
        self._hide_after_animation = False
        self._set_visual_opacity(0.0)
        self.hide()

    def _animate_to(self, value: float, *, duration: int) -> None:
        if self._animation.state() == QVariantAnimation.State.Running:
            self._animation.stop()
        self._animation.setDuration(duration)
        self._animation.setStartValue(self._visual_opacity)
        self._animation.setEndValue(value)
        self._animation.start()

    def _set_visual_opacity(self, value) -> None:
        self._visual_opacity = max(0.0, min(1.0, float(value)))
        self._backdrop_effect.setOpacity(self._visual_opacity)
        self._info_bar.opacityEffect.setOpacity(self._visual_opacity)

    def _on_animation_finished(self) -> None:
        if self._hide_after_animation and self._visual_opacity <= 0.001:
            self.hide()
            self._hide_after_animation = False

    def _refresh_backdrop(self) -> None:
        color = "rgba(0, 0, 0, 68)" if isDarkTheme() else "rgba(255, 255, 255, 105)"
        self._backdrop.setStyleSheet(
            "#presetFileDropBackdrop {"
            f" background-color: {color};"
            " }"
        )


class WindowPresetFileDropFilter(QObject):
    """Направляет TXT/ZIP текущей странице, если она умеет их импортировать."""

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
        self._drop_delegate_ref = None
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

    def set_drop_delegate(self, delegate) -> None:
        """Временный приёмник drop-ов (модальный диалог импорта) вместо страницы.

        Держится weakref-ом: если диалог удалён без снятия делегата,
        фильтр автоматически возвращается к обычному поведению.
        """
        if delegate is None:
            self._drop_delegate_ref = None
            return
        try:
            self._drop_delegate_ref = weakref.ref(delegate)
        except TypeError:
            self._drop_delegate_ref = lambda: delegate

    def _drop_delegate_action(self):
        ref = self._drop_delegate_ref
        delegate = ref() if ref is not None else None
        action = getattr(delegate, "handle_dropped_preset_files", None)
        return action if callable(action) else None

    def _import_action(self):
        delegate_action = self._drop_delegate_action()
        if delegate_action is not None:
            return delegate_action
        try:
            target = self._target_resolver()
        except Exception:
            return None
        action = getattr(target, "import_dropped_preset_files", None)
        return action if callable(action) else None

    def import_file_paths(self, file_paths) -> bool:
        """Передаёт файлы текущей странице через общий путь импорта."""
        paths = valid_preset_file_paths(file_paths)
        import_action = self._import_action()
        if not paths or import_action is None:
            self._hide_overlay()
            return False
        try:
            imported = bool(import_action(paths))
        except Exception as exc:
            log(f"Не удалось передать файл пресета на импорт: {exc}", "ERROR")
            imported = False
        if imported:
            self._flash_accepted(paths)
        else:
            self._hide_overlay()
        return imported

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
            if not self.import_file_paths(paths):
                return False
        else:
            self._show_overlay(paths)

        event.acceptProposedAction()
        return True

    def show_hover_hint(self) -> bool:
        """Показывает подсказку наведения, если текущая страница умеет импорт."""
        if self._import_action() is None:
            return False
        action = getattr(self.overlay, "show_hover_hint", None)
        if not callable(action):
            return False
        action()
        return True

    def hide_hover_hint(self) -> None:
        action = getattr(self.overlay, "hide_hover_hint", None)
        if callable(action):
            action()
        else:
            self._hide_overlay()

    def _show_overlay(self, paths: list[str]) -> None:
        if self._drop_delegate_action() is not None:
            # Активен диалог импорта: он сам подсвечивает свою drop-зону.
            return
        action = getattr(self.overlay, "show_hint", None)
        if callable(action):
            action(paths)

    def _flash_accepted(self, paths: list[str]) -> None:
        if self._drop_delegate_action() is not None:
            self._hide_overlay()
            return
        action = getattr(self.overlay, "show_accepted", None)
        if callable(action):
            action(paths)
        else:
            self._hide_overlay()

    def _hide_overlay(self) -> None:
        action = getattr(self.overlay, "hide_hint", None)
        if callable(action):
            action()


def handle_native_preset_file_drop(window, message) -> bool:
    """Направляет WM_DROPFILES в тот же фильтр, что и обычный Qt Drop."""
    file_paths = windows_dropped_file_paths(message)
    if file_paths is None:
        return False
    event_filter = getattr(window, "_preset_file_drop_filter", None)
    import_file_paths = getattr(event_filter, "import_file_paths", None)
    if callable(import_file_paths):
        import_file_paths(file_paths)
    return True


__all__ = [
    "PresetFileDropOverlay",
    "WindowPresetFileDropFilter",
    "dropped_preset_file_paths",
    "handle_native_preset_file_drop",
    "valid_preset_file_paths",
]
