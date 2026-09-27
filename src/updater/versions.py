from __future__ import annotations

"""Единственное сравнение версий обновлятора.

Версии ZapretGUI — только числа через точку (``21.1.5.67``), иногда с
префиксом ``v``. Сравниваются они как числа, а не как строки: строковое
сравнение считало бы ``21.1.10`` старше ``21.1.9``. Неверная запись версии —
ошибка, а не повод сравнить «как получится».
"""


def normalize_version(ver_str: str) -> str:
    """Версия без префикса ``v``. ValueError, если это не числа через точку."""
    value = str(ver_str or "").strip()
    if value.startswith(("v", "V")):
        value = value[1:]
    parts = value.split(".")
    if len(parts) < 2 or any(not part.isdigit() for part in parts):
        raise ValueError(f"Invalid version format: {value}")
    return value


def version_key(ver_str: str) -> tuple[int, ...]:
    """Числовой ключ версии без хвостовых нулей: ``1.2`` равно ``1.2.0``."""
    parts = [int(part) for part in normalize_version(ver_str).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def compare_versions(v1: str, v2: str) -> int:
    """-1, 0 или 1. ValueError, если одна из версий записана неверно."""
    first = version_key(v1)
    second = version_key(v2)
    return (first > second) - (first < second)


__all__ = ["compare_versions", "normalize_version", "version_key"]
