"""Запас разобранных значков qfluentwidgets.

qfluentwidgets на КАЖДУЮ перерисовку заново читает файл значка из ресурсов,
разбирает его как SVG и только потом рисует: ``drawSvgIcon`` создаёт новый
``QSvgRenderer``, ``writeSvg`` перечитывает файл и перекрашивает его через
``QDomDocument``, а ``FluentIconBase.icon()`` собирает новый ``QIcon``. Значки
при этом не меняются. Замер на win10: курсор над боковым меню — 9,7% ядра,
и около 70% отрисовки меню уходило на этот разбор.

Здесь те же три функции отдают уже разобранный результат. Рисует по-прежнему
``QSvgRenderer`` — картинка на экране та же до пикселя, пропадает только
повторный разбор файла.

Запоминаются только значки из ресурсов Qt (путь ``:/...``) и готовый текст
SVG: они не меняются, пока программа работает. Файлы с диска идут старым
путём — их могут заменить.
"""

from __future__ import annotations

from collections import OrderedDict


_PATCH_MARK = "_zapretgui_icon_cache_installed"
_MAX_RENDERERS = 512
_MAX_SVG_TEXTS = 512
_MAX_ICONS = 512

_renderers: OrderedDict = OrderedDict()
_svg_texts: OrderedDict = OrderedDict()
_icons: OrderedDict = OrderedDict()


def _remember(cache: OrderedDict, key, value, limit: int) -> None:
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > limit:
        cache.popitem(last=False)


def _is_qt_resource(path) -> bool:
    return isinstance(path, str) and path.startswith(":/")


def clear_qfluent_icon_cache() -> None:
    _renderers.clear()
    _svg_texts.clear()
    _icons.clear()


def install_qfluent_icon_cache(app=None) -> None:
    """Один раз подменяет разбор значков qfluentwidgets на запас готовых."""
    try:
        import qfluentwidgets.common.icon as fluent_icon
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QIcon
        from PyQt6.QtSvg import QSvgRenderer
    except Exception:
        return

    if bool(getattr(fluent_icon.drawSvgIcon, _PATCH_MARK, False)):
        return

    original_draw_svg_icon = fluent_icon.drawSvgIcon
    original_write_svg = fluent_icon.writeSvg
    original_icon = fluent_icon.FluentIconBase.icon

    def _draw_svg_icon(icon, painter, rect):
        if _is_qt_resource(icon) or isinstance(icon, bytes):
            renderer = _renderers.get(icon)
            if renderer is None:
                renderer = QSvgRenderer(icon)
                _remember(_renderers, icon, renderer, _MAX_RENDERERS)
            else:
                _renderers.move_to_end(icon)
            renderer.render(painter, QRectF(rect))
            return
        original_draw_svg_icon(icon, painter, rect)

    def _write_svg(iconPath, indexes=None, **attributes):  # noqa: N803 - имя как в qfluentwidgets
        if not _is_qt_resource(iconPath):
            return original_write_svg(iconPath, indexes, **attributes)
        try:
            key = (
                iconPath,
                tuple(indexes) if indexes else None,
                tuple(sorted(attributes.items())),
            )
            cached = _svg_texts.get(key)
        except TypeError:
            # Значение атрибута нельзя использовать как ключ — считаем как раньше.
            return original_write_svg(iconPath, indexes, **attributes)
        if cached is None:
            cached = original_write_svg(iconPath, indexes, **attributes)
            _remember(_svg_texts, key, cached, _MAX_SVG_TEXTS)
        else:
            _svg_texts.move_to_end(key)
        return cached

    def _icon(self, theme=fluent_icon.Theme.AUTO, color=None):
        if color:
            return original_icon(self, theme, color)
        path = self.path(theme)
        if not _is_qt_resource(path):
            return original_icon(self, theme, color)
        cached = _icons.get(path)
        if cached is None:
            cached = QIcon(path)
            _remember(_icons, path, cached, _MAX_ICONS)
        else:
            _icons.move_to_end(path)
        # Копия делит данные с запасом и отделяется сама, если её изменят.
        return QIcon(cached)

    setattr(_draw_svg_icon, _PATCH_MARK, True)
    fluent_icon.drawSvgIcon = _draw_svg_icon
    fluent_icon.writeSvg = _write_svg
    fluent_icon.FluentIconBase.icon = _icon

    if app is not None:
        try:
            # Разобранные значки — объекты Qt: отпускаем их до разрушения QApplication.
            app.aboutToQuit.connect(clear_qfluent_icon_cache)
        except Exception:
            pass


__all__ = ["clear_qfluent_icon_cache", "install_qfluent_icon_cache"]
