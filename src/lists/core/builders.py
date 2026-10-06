"""Общая сборка итоговых файлов списков."""

from __future__ import annotations

from itertools import chain

from lists.core.files import write_text_file


def dedup_preserve_order(items: list[str]) -> list[str]:
    """Убирает повторы, сохраняя исходный порядок строк."""
    # dict помнит порядок вставки, а сам проход идёт внутри интерпретатора:
    # для списка на сто тысяч строк это в разы быстрее цикла на Python.
    return list(dict.fromkeys(items))


def merge_base_and_user(base_entries: list[str], user_entries: list[str]) -> list[str]:
    """Объединяет базовые и пользовательские строки, сохраняя базу первой."""
    return list(dict.fromkeys(chain(base_entries, user_entries)))


def build_combined_content(base_entries: list[str], user_entries: list[str]) -> str:
    """Собирает текст итогового файла из базовых и пользовательских строк."""
    combined = merge_base_and_user(base_entries, user_entries)
    return "\n".join(combined) + ("\n" if combined else "")


def write_combined_file(final_path: str, base_entries: list[str], user_entries: list[str]) -> None:
    """Собирает и записывает итоговый файл."""
    write_text_file(final_path, build_combined_content(base_entries, user_entries))
