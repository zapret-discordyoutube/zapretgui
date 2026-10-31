"""Общие файловые helper-функции для списков."""

from __future__ import annotations

import os
import tempfile


def normalize_newlines(text: str) -> str:
    """Приводит переводы строк к `\\n` и добавляет финальный перевод строки."""
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if normalized and not normalized.endswith("\n"):
        normalized += "\n"
    return normalized


def decode_list_text(data: bytes) -> str:
    """Текст списка из байтов файла.

    Программа пишет UTF-8, но список могли сохранить и в Блокноте: с меткой
    BOM в начале или в старой кодировке Windows (cp1251). Метку снимаем, иначе
    она прилипнет к первому адресу; cp1251 читаем как есть, а не как «пусто»,
    иначе первое же сохранение затрёт записи пользователя.
    """
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1251", errors="replace")


def read_text_file(path: str) -> str:
    """Читает текстовый файл списка (UTF-8, UTF-8 с BOM или cp1251)."""
    with open(path, "rb") as f:
        return decode_list_text(f.read())


def read_text_file_safe(path: str) -> str | None:
    """Читает текстовый файл и возвращает `None`, если файла нет или он недоступен."""
    try:
        return read_text_file(path)
    except OSError:
        return None


def _file_content_matches(path: str, normalized: str) -> bool:
    try:
        with open(path, "rb") as f:
            data = f.read()
        # Сравниваем байты: файл в cp1251 или с BOM должен переписаться в UTF-8.
        return data == normalized.encode("utf-8")
    except Exception:
        return False


def write_text_file(path: str, content: str) -> None:
    """Записывает текстовый файл в UTF-8 и создаёт родительскую папку при необходимости."""
    normalized = normalize_newlines(content)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    if _file_content_matches(path, normalized):
        return

    temp_path = ""
    try:
        fd, temp_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(path)}.",
            suffix=".tmp",
            dir=directory or None,
            text=True,
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(normalized)
        os.replace(temp_path, path)
    except Exception:
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
        raise


def ensure_user_file_exists(user_path: str) -> bool:
    """Гарантирует наличие user-файла внутри `lists`."""
    try:
        os.makedirs(os.path.dirname(user_path), exist_ok=True)

        if os.path.exists(user_path):
            return True

        write_text_file(user_path, "")
        return True
    except Exception:
        return False


def prepare_user_file(
    user_path: str,
    *,
    error_message: str | None = None,
    log_func=None,
) -> bool:
    """Готовит user-файл и пишет лог при ошибке."""
    try:
        return ensure_user_file_exists(user_path)
    except Exception as exc:
        if error_message and log_func is not None:
            log_func(f"{error_message}: {exc}", "ERROR")
        return False
