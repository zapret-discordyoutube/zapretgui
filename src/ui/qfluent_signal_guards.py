"""Центральная защита от висячих подписок qfluentwidgets на theme-сигналы.

Каждый BackgroundAnimationWidget (CardWidget и наследники) и каждый
FluentLabelBase (BodyLabel, CaptionLabel и т.д.) подписываются в
конструкторе на глобальный ``qconfig.themeChanged`` на всё время жизни
процесса. В обычном CPython PyQt распознаёт receiver у bound-метода и
сам разрывает соединение при удалении C++-объекта, но в Nuitka-сборке
receiver у compiled-method не определяется, соединение переживает
виджет, и следующая смена темы бьёт по мёртвой обёртке::

    RuntimeError: wrapped C/C++ object of type CardWidget has been deleted

У FluentLabelBase подписка сделана через lambda — такую PyQt не
отписывает никогда (утечка даже без Nuitka); краха там не видно только
из-за ``@exceptionHandler()`` на ``setTextColor``.

Решение — не полагаться на per-widget подписки вовсе: при старте
(``ensure_qt_runtime``) конструкторы патчатся так, что виджет снимает
свою подписку и регистрируется в едином диспетчере. Диспетчер держит
слабые ссылки, на каждую смену темы обходит живых подписчиков и молча
выбрасывает мёртвых. Одно соединение на процесс вместо тысяч на виджеты.
"""

from __future__ import annotations

import weakref

from log.log import log

_INSTALLED = False


class _ThemeSubscriberRegistry:
    """Единый диспетчер theme-обновлений для короткоживущих виджетов.

    Держит weakref'ы: обёртка виджета с C++-родителем живёт, пока жив
    C++-объект (sip держит ссылку при передаче владения), поэтому живые
    виджеты из реестра не выпадают. Мёртвые (умерла обёртка или C++)
    вычищаются при следующей эмиссии.
    """

    def __init__(self, name: str, invoke) -> None:
        self._name = name
        self._invoke = invoke
        self._refs: list[weakref.ref] = []

    def register(self, widget) -> None:
        try:
            self._refs.append(weakref.ref(widget))
        except TypeError:
            pass

    def __len__(self) -> int:
        return len(self._refs)

    def on_theme_changed(self, *_args) -> None:
        from PyQt6 import sip

        alive: list[weakref.ref] = []
        for ref in self._refs:
            widget = ref()
            if widget is None:
                continue
            try:
                if sip.isdeleted(widget):
                    continue
            except TypeError:
                continue
            alive.append(ref)
            try:
                self._invoke(widget)
            except RuntimeError:
                # C++-объект умер прямо во время эмиссии (например, слот
                # раньше по списку удалил родителя) — выбрасываем молча.
                continue
            except Exception as exc:  # noqa: BLE001 — смена темы не должна падать
                log(f"Ошибка theme-обновления {self._name}: {exc}", "DEBUG")
        self._refs = alive


_card_registry: _ThemeSubscriberRegistry | None = None
_label_registry: _ThemeSubscriberRegistry | None = None


def _patch_background_animation_widget(qconfig) -> None:
    """CardWidget и наследники: подписка через диспетчер вместо bound-метода."""
    global _card_registry
    from qfluentwidgets.common.animation import BackgroundAnimationWidget

    _card_registry = _ThemeSubscriberRegistry(
        "BackgroundAnimationWidget",
        lambda widget: widget._updateBackgroundColor(),
    )
    qconfig.themeChanged.connect(_card_registry.on_theme_changed)

    original_init = BackgroundAnimationWidget.__init__

    def _guarded_init(self, *args, **kwargs) -> None:
        original_init(self, *args, **kwargs)
        try:
            qconfig.themeChanged.disconnect(self._updateBackgroundColor)
        except (TypeError, RuntimeError):
            # Будущая версия библиотеки может убрать собственную подписку —
            # тогда снимать нечего, диспетчер всё равно берёт виджет на себя.
            pass
        _card_registry.register(self)

    BackgroundAnimationWidget.__init__ = _guarded_init

    # Страховка: если родная подписка каким-то путём уцелела (не совпал
    # compiled-method при disconnect), мёртвый виджет не должен ронять
    # смену темы.
    original_update = BackgroundAnimationWidget._updateBackgroundColor

    def _guarded_update(self) -> None:
        from PyQt6 import sip

        if sip.isdeleted(self):
            return
        original_update(self)

    BackgroundAnimationWidget._updateBackgroundColor = _guarded_update


def _patch_fluent_label_base(qconfig) -> None:
    """FluentLabelBase: убирает неотписываемую per-instance lambda.

    Тело повторяет FluentLabelBase._init из qfluentwidgets 1.11.2 без
    строки с lambda-подпиской; при апгрейде библиотеки сверить с
    исходником (sentinel-тест на новые qconfig-подписки это подскажет).
    """
    global _label_registry
    from qfluentwidgets.components.widgets.label import FluentLabelBase
    from qfluentwidgets.common.style_sheet import FluentStyleSheet

    _label_registry = _ThemeSubscriberRegistry(
        "FluentLabelBase",
        lambda label: label.setTextColor(label.lightColor, label.darkColor),
    )
    qconfig.themeChanged.connect(_label_registry.on_theme_changed)

    def _guarded_init(self):
        FluentStyleSheet.LABEL.apply(self)
        self.setFont(self.getFont())
        self.setTextColor()
        _label_registry.register(self)
        self.customContextMenuRequested.connect(self._onContextMenuRequested)
        return self

    FluentLabelBase._init = _guarded_init


def install_qfluent_theme_signal_guards() -> None:
    """Ставит патчи один раз, строго до создания первых fluent-виджетов."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from qfluentwidgets.common.config import qconfig

    try:
        _patch_background_animation_widget(qconfig)
    except Exception as exc:  # noqa: BLE001 — защита не должна валить старт
        log(f"Не удалось поставить guard BackgroundAnimationWidget: {exc}", "WARNING")
    try:
        _patch_fluent_label_base(qconfig)
    except Exception as exc:  # noqa: BLE001
        log(f"Не удалось поставить guard FluentLabelBase: {exc}", "WARNING")
