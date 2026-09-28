"""Сведения извне текста, которые нужны проверке и подсказкам.

- ``fake_values`` — реестр фейков: имя → значение для ``--blob=имя:значение``
  (``@bin/файл.bin`` или ``0xHEX``). Нужен, чтобы предложить объявление
  недостающего фейка;
- ``file_facts`` — последняя фоновая проверка файлов на диске;
- ``preset_text`` — полный текст пресета, когда редактируется текст одного
  профиля: из него берутся объявленные фейки, lua-файлы и шаблоны.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .file_refs import FileFacts


@dataclass(frozen=True, slots=True)
class LanguageContext:
    fake_values: Mapping[str, str] = field(default_factory=dict)
    file_facts: FileFacts | None = None
    preset_text: str | None = None


EMPTY_CONTEXT = LanguageContext()

__all__ = ["EMPTY_CONTEXT", "LanguageContext"]
