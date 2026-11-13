"""Закрыть в Windows hosts молчащие кэши YouTube у провайдера (GGC).

Кэш Google внутри сети провайдера иногда «молчит»: соединение есть, а видео не идёт. Плеер
YouTube упорно возвращается к нему. Если имена такого кэша ведут в hosts на ``127.0.0.2``,
соединение отказывает сразу, и плеер переходит на серверы самого Google. Помогает не всем,
поэтому это включает только сам человек кнопкой BlockCheck и так же одной кнопкой убирает.

Что закрывать, решает BlockCheck (``diagnostics.video_fixes``): только узлы в сети провайдера,
никогда узлы самого Google — на них плеер и уходит. Здесь только запись своего блока в hosts:
группа узла (``sn-xguxaxjvh-304e``) превращается в имена ``rr1…rr20`` на ``googlevideo.com`` и
``c.youtube.com`` и старые ``r1…r6``. Включено ли — видно по самому файлу hosts, а не по
настройке: файл и флаг не могут разойтись.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from log.log import log

__all__ = [
    "BLOCK_BEGIN",
    "BLOCK_END",
    "BLOCK_IP",
    "block_groups",
    "blocked_groups",
    "build_hosts_content",
    "names_for_group",
    "read_blocked_groups",
    "unblock_all",
]

BLOCK_BEGIN = "# >>> zapretgui:youtube-cache-block begin >>>"
BLOCK_END = "# <<< zapretgui:youtube-cache-block end <<<"
# Не 0.0.0.0 и не 127.0.0.1: отказ приходит сразу, и адрес не путается с локальными программами.
BLOCK_IP = "127.0.0.2"

# Сколько машин площадки закрывать: у узлов бывают rr1…rr20, у старых имён r1…r6.
_RR_COUNT = 20
_R_COUNT = 6
_GROUP = re.compile(r"^sn-[a-z0-9]+(?:-[a-z0-9]+)*$")
_ENTRY = re.compile(r"^\s*\S+\s+r{1,2}\d+---(sn-[a-z0-9-]+)\.(?:googlevideo\.com|c\.youtube\.com)\s*$", re.IGNORECASE)


def _clean(groups: Iterable[str]) -> list[str]:
    """Только настоящие группы узлов, без повторов, по порядку: чужое в hosts не пишется."""
    found: list[str] = []
    for group in groups:
        value = str(group or "").strip().lower()
        if _GROUP.match(value) and value not in found:
            found.append(value)
    return found


def names_for_group(group: str) -> tuple[str, ...]:
    """Все имена машин площадки. Пусто — строка не похожа на группу узла."""
    clean = _clean([group])
    if not clean:
        return ()
    value = clean[0]
    names = [f"rr{n}---{value}.googlevideo.com" for n in range(1, _RR_COUNT + 1)]
    names += [f"rr{n}---{value}.c.youtube.com" for n in range(1, _RR_COUNT + 1)]
    names += [f"r{n}---{value}.googlevideo.com" for n in range(1, _R_COUNT + 1)]
    return tuple(names)


def _split(content: str) -> tuple[list[str], list[str]]:
    """(строки файла без нашего блока, строки внутри нашего блока). Без строки конца блок кончается пустой строкой."""
    outside: list[str] = []
    inside: list[str] = []
    in_block = False
    for line in str(content or "").splitlines():
        if line.strip() == BLOCK_BEGIN:
            in_block = True
            continue
        if in_block and (line.strip() == BLOCK_END or not line.strip()):
            in_block = False
            continue
        (inside if in_block else outside).append(line)
    return outside, inside


def blocked_groups(content: str) -> tuple[str, ...]:
    """Какие группы уже закрыты нашим блоком."""
    _outside, inside = _split(content)
    return tuple(_clean(match.group(1) for line in inside if (match := _ENTRY.match(line))))


def read_blocked_groups() -> tuple[str, ...]:
    """Какие группы закрыты сейчас, по самому файлу hosts. Пусто — не закрыты или файл не прочитался."""
    try:
        return blocked_groups(_read(None) or "")
    except Exception:
        return ()


def build_hosts_content(content: str, groups: Iterable[str]) -> str:
    """Текст hosts с нашим блоком ровно для ``groups``. Пусто — блока нет. Чужие строки не трогаются."""
    outside, _inside = _split(content)
    while outside and not outside[-1].strip():
        outside.pop()
    clean = _clean(groups)
    lines = list(outside)
    if clean:
        if lines:
            lines.append("")
        lines.append(BLOCK_BEGIN)
        lines.append("# Молчащие кэши YouTube у провайдера: плеер уходит на серверы Google. Убрать — кнопкой в BlockCheck.")
        for group in clean:
            lines.extend(f"{BLOCK_IP} {name}" for name in names_for_group(group))
        lines.append(BLOCK_END)
    return "\n".join(lines) + "\n" if lines else ""


def _read(read_hosts_file: Callable[[], str | None] | None) -> str | None:
    if read_hosts_file is not None:
        return read_hosts_file()
    from hosts.hosts import safe_read_hosts_file

    return safe_read_hosts_file()


def _write(write_hosts_file: Callable[[str], bool] | None, content: str) -> bool:
    if write_hosts_file is not None:
        return bool(write_hosts_file(content))
    from hosts.hosts import invalidate_hosts_file_cache, safe_write_hosts_file

    ok = bool(safe_write_hosts_file(content))
    if ok:
        invalidate_hosts_file_cache()
    return ok


def _change(
    make_groups: Callable[[tuple[str, ...]], Iterable[str]],
    read_hosts_file: Callable[[], str | None] | None,
    write_hosts_file: Callable[[str], bool] | None,
) -> tuple[bool, str, tuple[str, ...]]:
    from hosts.commands import hosts_write_lock

    with hosts_write_lock():
        content = _read(read_hosts_file)
        if content is None:
            return False, "Не удалось прочитать файл hosts.", ()
        groups = tuple(_clean(make_groups(blocked_groups(content))))
        if not _write(write_hosts_file, build_hosts_content(content, groups)):
            return False, "Не удалось записать файл hosts. Возможно, нужны права администратора.", ()
    return True, "", groups


def block_groups(
    groups: Iterable[str],
    *,
    read_hosts_file: Callable[[], str | None] | None = None,
    write_hosts_file: Callable[[str], bool] | None = None,
) -> tuple[bool, str]:
    """Добавить группы к уже закрытым. (удалось, слова для человека)."""
    new = _clean(groups)
    if not new:
        return False, "Нечего закрывать: BlockCheck не назвал ни одного кэша провайдера."
    ok, error, result = _change(lambda old: [*old, *new], read_hosts_file, write_hosts_file)
    if not ok:
        return False, error
    log(f"YouTube cache block: закрыты группы {', '.join(result)}", "INFO")
    return True, (
        f"Кэши провайдера закрыты в hosts ({', '.join(new)}). Перезапустите браузер, чтобы он забыл старые адреса. "
        "Если видео стало хуже — нажмите «Вернуть кэши YouTube»."
    )


def unblock_all(
    *,
    read_hosts_file: Callable[[], str | None] | None = None,
    write_hosts_file: Callable[[str], bool] | None = None,
) -> tuple[bool, str]:
    """Убрать наш блок целиком. (удалось, слова для человека)."""
    ok, error, _result = _change(lambda _old: (), read_hosts_file, write_hosts_file)
    if not ok:
        return False, error
    log("YouTube cache block: блок убран из hosts", "INFO")
    return True, "Кэши YouTube снова открыты: записи ZapretGUI убраны из hosts. Перезапустите браузер."
