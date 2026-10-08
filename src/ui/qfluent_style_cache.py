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

Заодно учёт стилей виджетов обходится без подписок и перехватчиков событий
на каждом виджете (см. ``_install_lean_registration``).
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
    """Учёт стилей виджетов без подписок и перехватчиков на каждом виджете.

    Библиотека для каждого виджета со стилем делала три вещи, и все три
    стоили дорого на каждом виджете и на каждом его событии:

    * подключала сигнал ``destroyed``, чтобы убрать виджет из своего списка.
      Список и так держит виджеты слабыми ссылками и сам забывает ушедшие, а
      мёртвые записи библиотека выбрасывает при смене темы
      (``updateStyleSheet`` ловит RuntimeError). Подключение сигнала отпускает
      общий замок Python: рядом с занятым фоновым потоком это было самым
      дорогим местом сборки страницы (замер: 69 таких подключений из 323 на
      главной странице, по интервалу переключения на каждое);
    * ставила перехватчик событий, который ждал смены «своего стиля» виджета
      (``setCustomStyleSheet``), чтобы применить его;
    * ставила второй перехватчик — для «ленивой» смены темы: стиль скрытого
      виджета обновлялся при его первой перерисовке.

    Два перехватчика — это два вызова функции на Python на КАЖДОЕ событие
    виджета: перерисовку, движение мыши, смену размера. Замер: сборка страниц
    с ними на 20–30 % дольше, открытие — на 10–15 %.

    Здесь «свой стиль» применяется прямо там, где его задают, а смена темы
    всегда обновляет стили сразу (ленивой сменой программа не пользуется).

    Тело ``_register`` повторяет StyleSheetManager.register из qfluentwidgets
    1.11.2 без подписки и перехватчиков; при обновлении библиотеки сверить с
    исходником.
    """
    compose = fluent_style.StyleSheetCompose
    custom = fluent_style.CustomStyleSheet
    file_sheet = fluent_style.StyleSheetFile

    def _register(self, source, widget, reset=True):
        if isinstance(source, str):
            source = file_sheet(source)

        if widget not in self.widgets:
            self.widgets[widget] = compose([source, custom(widget)])

        if not reset:
            self.source(widget).add(source)
        else:
            self.widgets[widget] = compose([source, custom(widget)])

    fluent_style.StyleSheetManager.register = _register

    def _set_custom_qss(sheet, key: str, qss: str):
        widget = sheet.widget
        if widget:
            changed = (widget.property(key) or "") != (qss or "")
            widget.setProperty(key, qss)
            if changed:
                # То же, что делал перехватчик по событию смены свойства.
                fluent_style.addStyleSheet(widget, custom(widget))
        return sheet

    def _set_light_style_sheet(self, qss: str):
        return _set_custom_qss(self, custom.LIGHT_QSS_KEY, qss)

    def _set_dark_style_sheet(self, qss: str):
        return _set_custom_qss(self, custom.DARK_QSS_KEY, qss)

    custom.setLightStyleSheet = _set_light_style_sheet
    custom.setDarkStyleSheet = _set_dark_style_sheet

    original_update = fluent_style.updateStyleSheet

    def _update_style_sheet(lazy=False):
        # Ленивое обновление опиралось на перехватчик перерисовки, которого
        # больше нет: стили обновляются сразу.
        original_update(False)

    fluent_style.updateStyleSheet = _update_style_sheet


__all__ = ["clear_qfluent_style_cache", "install_qfluent_style_cache"]
