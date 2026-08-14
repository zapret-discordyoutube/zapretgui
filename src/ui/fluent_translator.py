"""Локализация встроенных строк qfluentwidgets.

Контекстные меню текстовых полей («Вырезать», «Копировать», «Выбрать все»)
и кнопки fluent-диалогов приходят из самой библиотеки и по умолчанию
английские. Библиотека везёт готовые переводы (`qfluentwidgets.ru_RU.qm`),
их достаточно подключить переводчиком к QApplication.
"""

from __future__ import annotations

from PyQt6.QtCore import QLocale
from PyQt6.QtWidgets import QApplication

from app.ui_texts import normalize_language

_LOCALES = {
    "ru": QLocale.Language.Russian,
    "en": QLocale.Language.English,
}

# Переводчик обязан пережить установку: QApplication держит на него только
# слабую связь, а сборка мусора вернула бы английские строки.
_state: dict[str, object] = {"translator": None, "language": ""}


def installed_language() -> str:
    return str(_state.get("language") or "")


def resolve_ui_language() -> str:
    """Язык интерфейса из прогретых настроек (по умолчанию русский)."""
    try:
        from settings.appearance import peek_warmed_ui_language

        warmed = peek_warmed_ui_language()
        if warmed:
            return normalize_language(warmed)
    except Exception:
        pass
    try:
        from settings.appearance import load_ui_language

        return normalize_language(load_ui_language().language)
    except Exception:
        return normalize_language(None)


def install_fluent_translator(app=None, language: str | None = None) -> bool:
    """Ставит переводчик qfluentwidgets под язык интерфейса."""
    application = app or QApplication.instance()
    if application is None:
        return False

    target = normalize_language(language) if language else resolve_ui_language()
    if target == installed_language() and _state.get("translator") is not None:
        return True

    try:
        from qfluentwidgets import FluentTranslator
    except Exception:
        return False

    previous = _state.get("translator")
    if previous is not None:
        try:
            application.removeTranslator(previous)
        except Exception:
            pass
        _state["translator"] = None
        _state["language"] = ""

    try:
        translator = FluentTranslator(QLocale(_LOCALES.get(target, QLocale.Language.Russian)))
        application.installTranslator(translator)
    except Exception:
        return False

    _state["translator"] = translator
    _state["language"] = target
    return True
