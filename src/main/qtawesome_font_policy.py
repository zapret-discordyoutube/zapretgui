"""Единая политика наборов иконок qtawesome для runtime и сборщика."""

from __future__ import annotations

from types import ModuleType
from typing import Iterable, Sequence


# Поиск по исходникам должен подтверждать, что все используемые префиксы
# перечислены здесь. Сборщик оставляет данные только этих четырёх наборов.
QT_AWESOME_ALLOWED_PREFIXES = ("fa5s", "fa5b", "mdi", "ri")


def select_qtawesome_bundles(
    bundles: Iterable[Sequence[str]],
) -> tuple[tuple[str, ...], ...]:
    """Оставляет только разрешённые наборы и проверяет полноту политики."""
    normalized = tuple(tuple(str(value) for value in bundle) for bundle in bundles)
    by_prefix = {
        bundle[0]: bundle
        for bundle in normalized
        if len(bundle) >= 3 and bundle[0]
    }
    missing = [prefix for prefix in QT_AWESOME_ALLOWED_PREFIXES if prefix not in by_prefix]
    if missing:
        raise RuntimeError(
            "qtawesome не содержит обязательные наборы иконок: "
            + ", ".join(missing)
        )
    return tuple(by_prefix[prefix] for prefix in QT_AWESOME_ALLOWED_PREFIXES)


def configure_qtawesome_module(module: ModuleType) -> tuple[str, ...]:
    """Ограничивает qtawesome до наборов, реально используемых ZapretGUI."""
    bundles = getattr(module, "_BUNDLED_FONTS", None)
    if not isinstance(bundles, (tuple, list)):
        raise RuntimeError("qtawesome не предоставляет список _BUNDLED_FONTS")

    selected = select_qtawesome_bundles(bundles)
    module._BUNDLED_FONTS = selected
    return tuple(bundle[0] for bundle in selected)


__all__ = [
    "QT_AWESOME_ALLOWED_PREFIXES",
    "configure_qtawesome_module",
    "select_qtawesome_bundles",
]
