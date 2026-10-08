"""Отчёт из дата-классов — в JSON и обратно.

Зачем. Итог проверки хранится дата-классами, а экран рисует из них карточки.
Чтобы прошлую проверку открыть теми же карточками, а не сырым текстом, её
отчёт надо сохранить и потом восстановить в те же дата-классы.

``to_plain`` превращает дата-класс в словари и списки (кортежи становятся
списками). ``from_plain`` собирает его обратно по объявленным типам полей:
вложенные дата-классы, кортежи, «значение или None». Формат терпим к
переменам: неизвестные поля в сохранённом файле пропускаются, а недостающие
берутся по умолчанию — старый файл читается новой версией программы.
"""

from __future__ import annotations

import dataclasses
import types
import typing

__all__ = ["from_plain", "to_plain"]


def to_plain(value):
    """Дата-классы, кортежи и словари — в то, что понимает JSON."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_plain(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_plain(item) for key, item in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _restore(kind, value):
    if value is None or kind is typing.Any:
        return value
    origin = typing.get_origin(kind)
    if origin is typing.Union or origin is types.UnionType:
        options = [item for item in typing.get_args(kind) if item is not type(None)]
        # «Значение или None»: вариант один. Если их больше, тип не угадываем — отдаём как есть.
        return _restore(options[0], value) if len(options) == 1 else value
    if origin is tuple:
        args = typing.get_args(kind)
        if not isinstance(value, (list, tuple)):
            return ()
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_restore(args[0], item) for item in value)
        return tuple(_restore(item_kind, item) for item_kind, item in zip(args, value)) if args else tuple(value)
    if origin is list:
        args = typing.get_args(kind)
        return [_restore(args[0], item) for item in value] if args and isinstance(value, list) else list(value or ())
    if isinstance(kind, type) and dataclasses.is_dataclass(kind):
        return from_plain(kind, value)
    return value


def from_plain(cls, data):
    """Собирает дата-класс ``cls`` из словаря. None — в ``data`` не словарь или не хватает обязательного поля."""
    if not isinstance(data, dict):
        return None
    hints = typing.get_type_hints(cls)
    values = {}
    for field in dataclasses.fields(cls):
        if field.name in data:
            values[field.name] = _restore(hints.get(field.name, typing.Any), data[field.name])
    try:
        return cls(**values)
    except TypeError:
        return None
