from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

# Windows не даёт подменить файл, пока его держит открытым другой читатель
# (наш фоновый поток, антивирус, индексатор): os.replace падает с
# PermissionError. Такие блокировки короткие, поэтому несколько повторов с
# нарастающей паузой (суммарно < 1 с) почти всегда проходят.
_REPLACE_RETRY_DELAYS_SEC = (0.02, 0.05, 0.1, 0.15, 0.25, 0.4)


def _replace_with_retry(source: str, destination: str) -> None:
    for delay in _REPLACE_RETRY_DELAYS_SEC:
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            time.sleep(delay)
    os.replace(source, destination)


def atomic_write_text(path, content: str, *, encoding: str = "utf-8") -> None:
    """Пишет текст так, что читатель видит либо старый файл, либо новый целиком.

    Данные сначала уходят во временный файл в той же папке, затем одним
    ``os.replace`` подменяют целевой. При любой ошибке целевой файл остаётся
    нетронутым, а временный удаляется.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Подмена через os.replace обошла бы атрибут «только чтение» (на Linux
    # важны права папки, а не файла): защищённый пользователем файл не
    # трогаем, как не тронула бы его обычная запись.
    if path.exists() and not os.access(path, os.W_OK):
        raise PermissionError(13, "Файл доступен только для чтения", str(path))

    data = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    if data and not data.endswith("\n"):
        data += "\n"

    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{path.stem}_",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as handle:
            handle.write(data)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except Exception:
                pass
        _replace_with_retry(tmp_name, str(path))
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except Exception:
            pass


def read_preset_file_text(path) -> str:
    """Читает текстовый файл пресета: UTF-8 (с BOM или без), иначе cp1251.

    Старые пресеты, сохранённые в Блокноте Windows, бывают в cp1251. Чтение
    с ``errors="replace"`` превращало русские буквы в U+FFFD, а следующее
    сохранение навсегда записывало эту порчу в файл.
    """
    return decode_preset_bytes(Path(path).read_bytes())


def decode_preset_bytes(raw: bytes) -> str:
    """UTF-8, а cp1251 — только если файл по сути не UTF-8.

    Один битый байт в UTF-8 файле (обрезанный символ, вставка из другого
    файла) не повод читать весь файл как cp1251: иначе вся кириллица
    превратилась бы в «РњРѕР№», и следующее сохранение закрепило бы это.
    Кириллица в cp1251 почти никогда не складывается в корректные
    многобайтные последовательности UTF-8, поэтому признак надёжный:
    сравниваем, сколько не-ASCII символов UTF-8 прочитал правильно и сколько
    пришлось заменить."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    as_utf8 = raw.decode("utf-8-sig", errors="replace")
    replaced = as_utf8.count("\ufffd")
    decoded_non_ascii = sum(1 for char in as_utf8 if ord(char) > 127) - replaced
    if decoded_non_ascii >= replaced:
        return as_utf8
    return raw.decode("cp1251", errors="replace")
