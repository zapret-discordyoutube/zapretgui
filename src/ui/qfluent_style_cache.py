"""Запас готовых текстов стилей qfluentwidgets.

Каждый виджет qfluentwidgets при создании получает свой текст стиля (QSS):
библиотека открывает файл стиля из ресурсов, читает его и подставляет в него
цвета темы и шрифты. Для каждой кнопки и каждой надписи — заново, хотя файл
один и тот же, а цвета не менялись. Замер: на сборку одной страницы
приходилось 60–150 чтений одних и тех же файлов, а подстановка каждый раз
семь раз пересчитывала оттенки цвета темы.

Здесь те же две функции отдают уже готовый текст: создание надписи стало
быстрее примерно на треть, кнопки — на десятую, целой страницы — на 14%.
Важнее другое: каждое обращение к Qt отпускает общий замок Python, и рядом с
занятым фоновым потоком оно стоит целый интервал переключения. Три обращения
на чтение файла (открыть, прочитать, закрыть) на каждый виджет пропадают.

Текст получается тот же самый, до символа: запоминается результат библиотеки,
а ключ — всё, от чего он зависит (сам шаблон, тема, цвет темы, шрифты).
Запоминаются только файлы из ресурсов Qt (путь ``:/...``): они не меняются,
пока программа работает. Файлы с диска идут старым путём — их могут заменить.

Заодно учёт стилей виджетов обходится без подписки на удаление каждого
виджета (см. ``_install_lean_registration``).
"""

from __future__ import annotations

from collections import OrderedDict


_PATCH_MARK = "_zapretgui_style_cache_installed"
# Свои стили виджетов (setCustomStyleSheet) бывают разными у каждого виджета,
# поэтому запас ограничен: редко нужные тексты вытесняются.
_MAX_RENDERED = 512

_file_texts: dict[str, str] = {}
_rendered: OrderedDict = OrderedDict()


def clear_qfluent_style_cache() -> None:
    _file_texts.clear()
    _rendered.clear()


def install_qfluent_style_cache() -> None:
    """Один раз подменяет чтение и сборку стилей qfluentwidgets на запас готовых."""
    try:
        import qfluentwidgets.common.style_sheet as fluent_style
        from qfluentwidgets.common.config import qconfig
    except Exception:
        return

    if bool(getattr(fluent_style.renderQss, _PATCH_MARK, False)):
        return

    original_read = fluent_style.getStyleSheetFromFile
    original_render = fluent_style.renderQss

    def _get_style_sheet_from_file(file):
        if not isinstance(file, str) or not file.startswith(":/"):
            return original_read(file)
        text = _file_texts.get(file)
        if text is None:
            text = original_read(file)
            _file_texts[file] = text
        return text

    def _render_qss(qss):
        # Всё, от чего зависит подстановка: тема (светлая или тёмная меняет
        # оттенки), сам цвет темы и список шрифтов.
        key = (
            qss,
            qconfig.theme,
            qconfig._cfg.themeColor.value.rgba(),
            tuple(qconfig.fontFamilies.value),
        )
        text = _rendered.get(key)
        if text is None:
            text = original_render(qss)
            _rendered[key] = text
            while len(_rendered) > _MAX_RENDERED:
                _rendered.popitem(last=False)
        else:
            _rendered.move_to_end(key)
        return text

    setattr(_render_qss, _PATCH_MARK, True)
    fluent_style.getStyleSheetFromFile = _get_style_sheet_from_file
    fluent_style.renderQss = _render_qss
    _install_lean_registration(fluent_style)


def _install_lean_registration(fluent_style) -> None:
    """Учёт стилей виджетов без подписки на удаление каждого виджета.

    Библиотека для каждого виджета со стилем подключала сигнал ``destroyed``,
    чтобы убрать его из своего списка. Список и так держит виджеты слабыми
    ссылками и сам забывает ушедшие, а мёртвые записи библиотека выбрасывает
    при смене темы (``updateStyleSheet`` ловит RuntimeError). Подключение же
    сигнала отпускает общий замок Python: рядом с занятым фоновым потоком это
    было самым дорогим местом сборки страницы (замер: 69 таких подключений из
    323 на главной странице, по интервалу переключения на каждое).

    Тело повторяет StyleSheetManager.register из qfluentwidgets 1.11.2 без
    строки с подпиской; при обновлении библиотеки сверить с исходником.
    """
    compose = fluent_style.StyleSheetCompose
    custom = fluent_style.CustomStyleSheet
    file_sheet = fluent_style.StyleSheetFile
    custom_watcher = fluent_style.CustomStyleSheetWatcher
    dirty_watcher = fluent_style.DirtyStyleSheetWatcher

    def _register(self, source, widget, reset=True):
        if isinstance(source, str):
            source = file_sheet(source)

        if widget not in self.widgets:
            widget.installEventFilter(custom_watcher(widget))
            widget.installEventFilter(dirty_watcher(widget))
            self.widgets[widget] = compose([source, custom(widget)])

        if not reset:
            self.source(widget).add(source)
        else:
            self.widgets[widget] = compose([source, custom(widget)])

    fluent_style.StyleSheetManager.register = _register


__all__ = ["clear_qfluent_style_cache", "install_qfluent_style_cache"]
