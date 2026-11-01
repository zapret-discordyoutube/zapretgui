"""Общая сборка итоговых файлов списков."""

from __future__ import annotations

from itertools import chain

from lists.core.files import write_text_file


def dedup_preserve_order(items: list[str]) -> list[str]:
    """Убирает повторы, сохраняя исходный порядок строк."""
    # dict помнит порядок вставки, а сам проход идёт внутри интерпретатора:
    # для списка на сто тысяч строк это в разы быстрее цикла на Python.
    return list(dict.fromkeys(items))


def merge_user_over_base(base_entries: list[str], user_entries: list[str]) -> list[str]:
    """Объединяет строки: сначала пользовательские, потом базовые.

    Порядок важен для движка: в одном hostlist домен хранится один раз и
    работает его ПЕРВАЯ запись (nfq2/pools.c, ADD_HOSTLIST_POOL). Записи
    пользователя идут первыми, поэтому его ``!youtube.com`` или
    ``^youtube.com`` перебивает встроенное ``youtube.com``, а не теряется
    за ним. Подробно — ``lists.core.hostlist_syntax``.
    """
    return list(dict.fromkeys(chain(user_entries, base_entries)))


def build_combined_content(base_entries: list[str], user_entries: list[str]) -> str:
    """Собирает текст итогового файла из базовых и пользовательских строк."""
    combined = merge_user_over_base(base_entries, user_entries)
    return "\n".join(combined) + ("\n" if combined else "")


def write_combined_file(final_path: str, base_entries: list[str], user_entries: list[str]) -> None:
    """Собирает и записывает итоговый файл."""
    write_text_file(final_path, build_combined_content(base_entries, user_entries))
