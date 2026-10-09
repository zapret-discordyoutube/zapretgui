"""Что показать в окне-продолжении и где: собирается в окне программы.

Окно-продолжение рисует отдельный процесс PowerShell, пока ``Zapret.exe``
заменяется (см. ``updater.install.splash``). Чтобы подмена была незаметной,
оно встаёт ровно на место окна обновления и берёт его цвета, тексты на языке
программы, шутки и логотип. Всё это собирается здесь, в потоке интерфейса,
в тот момент, когда пользователь нажал «Обновить».
"""

from __future__ import annotations

import random

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QPoint, QRect
from PyQt6.QtGui import QColor, QGuiApplication
from PyQt6.QtWidgets import QApplication, QWidget

from updater.install.splash import RestartSplashSpec
from updater.ui import plans
from updater.ui.fun_texts import phrases as fun_phrases

# Слои главного окна программы (qfluentwidgets): фон страницы и карточка на
# нём. Окно-продолжение рисуется теми же цветами — оно выглядит частью
# программы, а не отдельным окном со своей палитрой.
_DARK_PAGE = QColor("#272727")
_DARK_CARD = QColor("#323232")
_LIGHT_PAGE = QColor("#f9f9f9")
_LIGHT_CARD = QColor("#ffffff")
_LOGO_SIDE = 128
# Маленькая карточка автоматического обновления (логические пиксели). Высота
# рассчитана на две строки фразы; скрипт окна рисует макет в этих же числах.
COMPACT_WIDTH = 460
COMPACT_HEIGHT = 190


def _solid(value: str, over: QColor, fallback: str) -> str:
    """Полупрозрачный цвет темы, смешанный с фоном: окну нужен сплошной ``#rrggbb``."""
    from ui.theme import to_qcolor

    color = to_qcolor(value, fallback)
    alpha = color.alphaF()
    mixed = QColor(
        round(color.red() * alpha + over.red() * (1 - alpha)),
        round(color.green() * alpha + over.green() * (1 - alpha)),
        round(color.blue() * alpha + over.blue() * (1 - alpha)),
    )
    return mixed.name()


def splash_colors(tokens) -> dict[str, str]:
    page = _LIGHT_PAGE if tokens.is_light else _DARK_PAGE
    card = _LIGHT_CARD if tokens.is_light else _DARK_CARD
    return {
        "background": page.name(),
        "card": card.name(),
        "border": "#e0e0e0" if tokens.is_light else "#3d3d3d",
        "foreground": _solid(tokens.fg, page, "#ffffff"),
        "muted": _solid(tokens.fg_muted, page, "#9aa0a6"),
        "accent": _solid(tokens.accent_hex, page, "#60cdff"),
        "track": "#e3e3e3" if tokens.is_light else "#3d3d3d",
        "on_accent": _solid(tokens.accent_fg, QColor(_solid(tokens.accent_hex, page, "#60cdff")), "#000000"),
    }


def to_physical_rect(rect: QRect, *, screen) -> tuple[int, int, int, int]:
    """Логические координаты Qt → физические пиксели экрана.

    В Windows Qt хранит левый верхний угол экрана в физических пикселях, а
    размеры внутри экрана — в логических: смещение от угла умножается на
    масштаб экрана.
    """
    if screen is None:
        return rect.x(), rect.y(), rect.width(), rect.height()
    ratio = float(screen.devicePixelRatio() or 1.0)
    origin = screen.geometry().topLeft()
    x = origin.x() + round((rect.x() - origin.x()) * ratio)
    y = origin.y() + round((rect.y() - origin.y()) * ratio)
    return x, y, round(rect.width() * ratio), round(rect.height() * ratio)


def _logo_png(side: int = _LOGO_SIDE) -> bytes:
    icon = QApplication.windowIcon()
    if icon is None or icon.isNull():
        return b""
    pixmap = icon.pixmap(side, side)
    if pixmap.isNull():
        return b""
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def _shuffled(items) -> list[str]:
    items = list(items)
    random.shuffle(items)
    return items


def _place(dialog_widget: QWidget | None, host: QWidget) -> QRect:
    """Место окна обновления; у скрытого окна — такое же по центру программы."""
    if dialog_widget is not None and dialog_widget.isVisible():
        top_left = dialog_widget.mapToGlobal(QPoint(0, 0))
        return QRect(top_left, dialog_widget.size())
    frame = host.frameGeometry()
    width = max(720, min(1180, int(frame.width() * 0.72)))
    height = max(460, min(860, int(frame.height() * 0.78)))
    return QRect(
        frame.x() + (frame.width() - width) // 2,
        frame.y() + (frame.height() - height) // 2,
        width,
        height,
    )


def _place_compact(host: QWidget) -> QRect:
    """Маленькая карточка — по центру окна программы."""
    frame = host.frameGeometry()
    return QRect(
        frame.x() + (frame.width() - COMPACT_WIDTH) // 2,
        frame.y() + (frame.height() - COMPACT_HEIGHT) // 2,
        COMPACT_WIDTH,
        COMPACT_HEIGHT,
    )


def build_restart_splash_spec(
    host: QWidget,
    *,
    dialog_widget: QWidget | None,
    current_version: str,
    target_version: str,
    language: str,
    compact: bool = False,
) -> RestartSplashSpec:
    """``compact`` — программа ставит обновление сама: вместо окна на месте
    окна обновления показывается маленькая карточка по центру программы."""
    from ui.theme import get_theme_tokens

    def t(key: str, default: str) -> str:
        return plans.update_flow_text(language, f"restart.{key}", default)

    rect = _place_compact(host) if compact else _place(dialog_widget, host)
    screen = host.screen() or QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
    x, y, width, height = to_physical_rect(rect, screen=screen)
    if compact:
        title = t("compact.title", "Обновляем Zapret")
        subtitle = t("compact.subtitle_template", "Ставим версию {version}").format(version=target_version)
    else:
        title = t("title_template", "Обновляем Zapret до v{version}").format(version=target_version)
        subtitle = t("subtitle_template", "v{current}  →  v{target}   ·   программа откроется сама").format(
            current=current_version, target=target_version
        )
    return RestartSplashSpec(
        x=x,
        y=y,
        width=width,
        height=height,
        layout="compact" if compact else "full",
        title=title,
        subtitle=subtitle,
        stages=(
            t("stage.closing", "Закрываем старую версию"),
            t("stage.installing_template", "Устанавливаем v{version}").format(version=target_version),
            t("stage.starting", "Открываем новую версию"),
        ),
        footer=t("footer", "Окно закроется само, когда откроется новая версия"),
        window_title=t("window_title", "Zapret — обновление"),
        files_template=t("files_template", "{done} из {total} файлов"),
        statuses=(
            t("status.done", "Готово"),
            t("status.active", "Выполняется"),
            t("status.waiting", "Ожидает"),
        ),
        # Окно показывает шутки по порядку: перемешиваем здесь, чтобы каждое
        # обновление начиналось с другой и они не повторялись.
        jokes=tuple(_shuffled(fun_phrases("restarting", language))),
        colors=splash_colors(get_theme_tokens()),
        logo_png=_logo_png(),
    )


__all__ = ["COMPACT_HEIGHT", "COMPACT_WIDTH", "build_restart_splash_spec", "splash_colors", "to_physical_rect"]
