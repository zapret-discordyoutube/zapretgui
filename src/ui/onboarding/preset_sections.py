"""Части пресета для обучающего тура: что подсветить в редакторе и что сказать.

Тур не помнит номера строк: каждый раз он разбирает текст, который сейчас
открыт в редакторе, тем же разборщиком, что и вся программа
(profile.parser.preset_text_outline). Пресет поменяли или переписали —
тур покажет то, что в нём есть сейчас. Части, которой в пресете нет, у
шага нет цели, и тур его пропускает.
"""

from __future__ import annotations

from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QWidget

from profile.parser import PresetOutline, ProfileOutline, preset_text_outline

SECTION_PREFIX = "section:"

PRESET_SECTIONS: tuple[str, ...] = (
    "header",
    "lua_init",
    "engine_options",
    "interception",
    "blobs",
    "profile",
    "profile_name",
    "profile_match",
    "profile_packets",
    "profile_strategy",
    "profile_new",
)

_VALUE_LIMIT = 70


def section_name(target_name: str) -> str:
    """"section:blobs" → "blobs"; всё прочее — пустая строка."""
    name = str(target_name or "")
    if not name.startswith(SECTION_PREFIX):
        return ""
    section = name[len(SECTION_PREFIX):]
    return section if section in PRESET_SECTIONS else ""


def build_outline(text: str, *, zapret2: bool) -> PresetOutline:
    return preset_text_outline(text, engine="winws2" if zapret2 else "winws1")


def example_profile_index(outline: PresetOutline) -> int:
    """Профиль для разбора: первый, у которого есть имя, условия и стратегия."""
    for index, profile in enumerate(outline.profiles):
        if profile.name and profile.match and profile.strategy:
            return index
    return 0 if outline.profiles else -1


def _example(outline: PresetOutline) -> tuple[int, ProfileOutline | None]:
    index = example_profile_index(outline)
    return index, (outline.profiles[index] if index >= 0 else None)


def section_lines(outline: PresetOutline, section: str) -> tuple[int, ...]:
    """Номера строк части (с нуля). Пусто — такой части в пресете нет."""
    if section in {"header", "lua_init", "engine_options", "interception", "blobs"}:
        return tuple(getattr(outline, section))
    index, profile = _example(outline)
    if profile is None:
        return ()
    if section == "profile":
        return profile.lines
    if section == "profile_new":
        following = outline.profiles[index + 1] if index + 1 < len(outline.profiles) else None
        if following is not None and following.new_line is not None:
            return (following.new_line,)
        return ()
    field = section.removeprefix("profile_")
    return tuple(getattr(profile, field, ()))


def _clip(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= _VALUE_LIMIT else text[: _VALUE_LIMIT - 1] + "…"


def _option_value(line: str) -> str:
    _name, _, value = str(line or "").strip().partition("=")
    return value.strip()


def _profile_name(outline: PresetOutline, profile: ProfileOutline | None) -> str:
    if profile is None:
        return ""
    for index in profile.name:
        line = outline.lines[index].strip()
        if line.startswith("--name=") or line.startswith("--comment="):
            return _option_value(line)
    return ""


def section_text_values(outline: PresetOutline, section: str) -> dict[str, str]:
    """Живые значения для текста шага: сколько, какие, как называется."""
    lines = outline.lines
    index, profile = _example(outline)
    if section == "header":
        for line_index in outline.header:
            line = lines[line_index].strip()
            if line.lower().startswith("# preset:"):
                return {"name": _clip(line.split(":", 1)[1].strip())}
        return {}
    if section in {"lua_init", "blobs"}:
        found = getattr(outline, section)
        values = {"count": str(len(found))}
        if found and section == "blobs":
            values["example"] = _clip(_option_value(lines[found[0]]).split(":", 1)[0])
        return values
    if section == "engine_options":
        return {"lines": _clip(", ".join(lines[i].strip() for i in outline.engine_options))}
    if section == "interception":
        values: dict[str, str] = {}
        for line_index in outline.interception:
            line = lines[line_index].strip().lower()
            if line.startswith("--wf-tcp") and "tcp" not in values:
                values["tcp"] = _clip(_option_value(lines[line_index]))
            elif line.startswith("--wf-udp") and "udp" not in values:
                values["udp"] = _clip(_option_value(lines[line_index]))
        return values
    if profile is None:
        return {}
    name = _profile_name(outline, profile)
    if section in {"profile", "profile_name"}:
        return {"count": str(len(outline.profiles)), "name": name}
    if section == "profile_match":
        return {"match": _clip(", ".join(lines[i].strip() for i in profile.match))}
    if section == "profile_packets":
        return {"packets": _clip(", ".join(lines[i].strip() for i in profile.packets))}
    if section == "profile_strategy":
        first = lines[profile.strategy[0]].strip() if profile.strategy else ""
        technique = _option_value(first).split(":", 1)[0] if first.startswith("--lua-desync=") else first
        return {"technique": _clip(technique), "count": str(len(profile.strategy))}
    if section == "profile_new":
        following = outline.profiles[index + 1] if index + 1 < len(outline.profiles) else None
        return {"next": _profile_name(outline, following)}
    return {}


def editor_lines_rect(editor, lines: tuple[int, ...]) -> tuple[QWidget, QRect] | None:
    """Строки редактора как цель тура: (редактор, прямоугольник строк).

    Прямоугольник на всю ширину редактора вместе с номерами строк и обрезан
    по видимой части — длинную часть видно сверху, пока она не кончится.
    """
    if not lines or editor is None:
        return None
    document = editor.document()
    first = document.findBlockByNumber(min(lines))
    last = document.findBlockByNumber(max(lines))
    if not first.isValid() or not last.isValid():
        return None
    offset = editor.contentOffset()
    top = editor.blockBoundingGeometry(first).translated(offset).top()
    bottom = editor.blockBoundingGeometry(last).translated(offset).bottom()
    viewport = editor.viewport()
    rect = QRect(0, viewport.y() + int(top), editor.width(), max(0, int(bottom - top)))
    rect = rect.intersected(QRect(0, viewport.y(), editor.width(), viewport.height()))
    if rect.height() < 6:
        return None
    return editor, rect


# Сколько строк оставить над частью, чтобы было видно, что идёт перед ней.
SCROLL_CONTEXT_LINES = 3


def scroll_editor_to_line(editor, line: int) -> None:
    """Прокрутить так, чтобы строка была у верхнего края, с запасом сверху.

    Меняется только прокрутка: текст и курсор не трогаются.
    """
    bar = editor.verticalScrollBar()
    bar.setValue(max(bar.minimum(), min(bar.maximum(), int(line) - SCROLL_CONTEXT_LINES)))


__all__ = [
    "PRESET_SECTIONS",
    "SECTION_PREFIX",
    "build_outline",
    "editor_lines_rect",
    "example_profile_index",
    "scroll_editor_to_line",
    "section_lines",
    "section_name",
    "section_text_values",
]
