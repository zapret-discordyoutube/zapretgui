"""Разбор текста пресета так, как его разбирает winws2 (getopt_long_only).

Здесь только структура: какие опции написаны, где их значения, какие профили,
фейки, lua-файлы и шаблоны объявлены. Сами проверки — в ``diagnostics.py``,
подсказки — в ``completion.py``; оба модуля работают с одним результатом.

Правила getopt, важные для пресета:

- принимаются и ``--опция``, и ``-опция``, а также однозначное сокращение
  имени (``--hostlist-e`` = ``--hostlist-exclude``);
- обязательное значение без ``=`` берётся из СЛЕДУЮЩЕГО аргумента, даже если
  это ``--new`` — тогда профиль молча пропадает;
- необязательное значение передаётся только через ``=``; ``--new имя`` на
  отдельной строке — имя теряется;
- аргумент, который не начинается с ``-``, winws2 пропускает.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import posixpath
import threading

from profile.winws2_preset_source import WINWS2_LUA_INIT_PATHS

from .options import OptionSpec, resolve_windows_option
from .tokens import Token, tokenize
from .values import parse_blob_value


@dataclass(slots=True)
class ParsedOption:
    token: Token
    spec: OptionSpec | None
    written_name: str
    dashes: int
    value: str | None = None
    value_line: int = -1
    value_start: int = -1
    value_end: int = -1
    value_token: Token | None = None
    profile_index: int = 0
    candidates: tuple[OptionSpec, ...] = ()

    @property
    def name_start(self) -> int:
        return self.token.start

    @property
    def name_end(self) -> int:
        return self.token.start + self.dashes + len(self.written_name)


@dataclass(slots=True)
class ProfileInfo:
    index: int
    line: int
    options: list[ParsedOption] = field(default_factory=list)

    @property
    def name(self) -> str:
        for option in self.options:
            if option.spec is not None and option.spec.name in {"name", "new"} and option.value:
                return option.value
        return ""

    @property
    def skipped(self) -> bool:
        return any(option.spec is not None and option.spec.name == "skip" for option in self.options)


@dataclass(slots=True)
class PresetAnalysis:
    text: str
    fragment: bool
    tokens: list[Token]
    options: list[ParsedOption] = field(default_factory=list)
    stray_tokens: list[Token] = field(default_factory=list)
    # Значения необязательных опций на отдельной строке: (опция, потерянный аргумент).
    lost_values: list[tuple[ParsedOption, Token]] = field(default_factory=list)
    end_of_options: Token | None = None
    profiles: list[ProfileInfo] = field(default_factory=list)
    blobs: dict[str, ParsedOption] = field(default_factory=dict)
    lua_init_files: list[str] = field(default_factory=list)
    lua_init_unknown: bool = False
    templates: dict[str, ParsedOption] = field(default_factory=dict)

    def options_named(self, name: str) -> list[ParsedOption]:
        return [option for option in self.options if option.spec is not None and option.spec.name == name]

    @property
    def loaded_lua_files(self) -> frozenset[str]:
        """Имена lua-файлов, которые winws2 загрузит (без папки, в нижнем регистре).

        Обязательный блок ``--lua-init`` сохранение дописывает само, поэтому
        его файлы считаются загруженными всегда.
        """
        mandatory = {posixpath.basename(path).lower() for path in WINWS2_LUA_INIT_PATHS}
        return frozenset(mandatory | {name.lower() for name in self.lua_init_files})


def _is_option_token(text: str) -> bool:
    return text.startswith("-") and len(text) > 1 and text != "--"


def lua_init_file_name(value: str) -> str:
    """Имя файла из ``--lua-init=@путь`` (пусто, если это lua-код строкой)."""
    raw = str(value or "").strip().strip('"').strip("'")
    if not raw.startswith("@"):
        return ""
    path = raw[1:].strip().strip('"').strip("'").replace("\\", "/")
    return posixpath.basename(path)


_CACHE_LIMIT = 4
_cache: "OrderedDict[tuple[str, bool], PresetAnalysis]" = OrderedDict()
_cache_lock = threading.Lock()


def analyze_winws2_text(text: str, *, fragment: bool = False) -> PresetAnalysis:
    """Разбор с маленьким кэшем: проверка, подсказки и описания при наведении
    спрашивают про один и тот же текст подряд. Результат не изменяют."""
    key = (str(text or ""), bool(fragment))
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return cached
    analysis = _analyze(key[0], fragment=key[1])
    with _cache_lock:
        _cache[key] = analysis
        while len(_cache) > _CACHE_LIMIT:
            _cache.popitem(last=False)
    return analysis


def _analyze(text: str, *, fragment: bool) -> PresetAnalysis:
    tokens = tokenize(text)
    analysis = PresetAnalysis(text=str(text or ""), fragment=bool(fragment), tokens=tokens)
    profile = ProfileInfo(index=0, line=tokens[0].line if tokens else 0)
    analysis.profiles.append(profile)

    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token.text == "--":
            analysis.end_of_options = token
            break
        if not _is_option_token(token.text):
            analysis.stray_tokens.append(token)
            continue

        dashes = 2 if token.text.startswith("--") else 1
        body = token.text[dashes:]
        name, separator, value = body.partition("=")
        spec, candidates = resolve_windows_option(name)
        option = ParsedOption(
            token=token,
            spec=spec,
            written_name=name,
            dashes=dashes,
            candidates=candidates,
        )
        if separator:
            option.value = value
            option.value_line = token.line
            option.value_start = token.start + dashes + len(name) + 1
            option.value_end = token.end

        if spec is not None and not separator:
            following = tokens[index] if index < len(tokens) else None
            if spec.arg == "required" and following is not None:
                option.value = following.text
                option.value_line = following.line
                option.value_start = following.start
                option.value_end = following.end
                option.value_token = following
                index += 1
            elif spec.arg == "optional" and following is not None and not _is_option_token(following.text) \
                    and following.text != "--":
                analysis.lost_values.append((option, following))
                index += 1

        if spec is not None and spec.name == "new":
            profile = ProfileInfo(index=len(analysis.profiles), line=token.line)
            analysis.profiles.append(profile)
        option.profile_index = profile.index
        profile.options.append(option)
        analysis.options.append(option)

        if spec is None or option.value is None:
            continue
        if spec.name == "blob":
            declaration = parse_blob_value(option.value)
            if declaration.name and declaration.name not in analysis.blobs:
                analysis.blobs[declaration.name] = option
        elif spec.name == "lua-init":
            file_name = lua_init_file_name(option.value)
            if file_name:
                analysis.lua_init_files.append(file_name)
            else:
                analysis.lua_init_unknown = True
        elif spec.name in {"template"} and option.value:
            analysis.templates.setdefault(option.value, option)
        elif spec.name == "name":
            if any(o.spec is not None and o.spec.name == "template" for o in profile.options):
                analysis.templates.setdefault(option.value, option)
    return analysis


__all__ = [
    "ParsedOption",
    "PresetAnalysis",
    "ProfileInfo",
    "analyze_winws2_text",
    "lua_init_file_name",
]
