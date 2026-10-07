"""Что известно о стратегии каталога — всё, что нужно списку, в одном месте.

Считается по записи каталога: её названию, служебным полям и строкам
``--lua-desync``. От профиля, оценок и поиска не зависит, поэтому собирается
один раз на каталог.
"""

from __future__ import annotations

from dataclasses import dataclass

from profile.strategy_families import strategy_family_keys
from profile.strategy_shape import payload_badge_accessible_text, payload_badge_text
from profile.strategy_visuals import describe_strategy_visual, strategy_plain_label

NAME_DETAIL_SEPARATOR = " · "
_SOURCE_PREFIX = "из "
# Описания-заглушки из каталога человеку ничего не говорят.
_PLACEHOLDER_DESCRIPTIONS = ("автодобавлено", "потом опишу")
_BUILTIN_AUTHOR = "builtin"


@dataclass(frozen=True)
class StrategyFacts:
    strategy_id: str
    name: str
    # Название без уточнения («General ALT11 1.9.9») и уточнение («из Steam»).
    title: str
    detail: str
    # Первое слово названия («General») и источник из уточнения («Steam»).
    series: str
    source: str
    # Группа по способу обхода (profile.strategy_families).
    family_key: str
    # Способ простыми словами («подделка + нарезка») и именами функций winws2.
    plain_label: str
    technical_label: str
    technique_description: str
    args: str
    old_name: str
    # Пометка каталога: recommended, stock, experimental, caution, game, stable.
    label: str
    author: str
    description: str
    # Типы пакетов веток составной стратегии («TLS · HTTP»); пусто — обычная.
    payload_badge: str
    payload_badge_accessible: str
    # Всё, по чему стратегию находит поиск, одной строкой в нижнем регистре.
    search_text: str


def split_strategy_name(name: str) -> tuple[str, str]:
    """Название стратегии и уточнение после « · »."""
    base, separator, detail = str(name or "").partition(NAME_DETAIL_SEPARATOR)
    if not separator or not base.strip():
        return str(name or "").strip(), ""
    return base.strip(), detail.strip()


def strategy_series(name: str) -> str:
    words = split_strategy_name(name)[0].split()
    return words[0].rstrip(":,") if words else ""


def strategy_source(name: str) -> str:
    """Откуда взята стратегия: «Steam» из уточнения «из Steam»; пусто — не указано."""
    for part in str(name or "").split(NAME_DETAIL_SEPARATOR)[1:]:
        part = part.strip()
        if part.startswith(_SOURCE_PREFIX):
            return part[len(_SOURCE_PREFIX) :].strip()
    return ""


def _useful_description(description: object) -> str:
    text = str(description or "").strip()
    return "" if text.lower().startswith(_PLACEHOLDER_DESCRIPTIONS) else text


def build_strategy_facts(entries) -> dict[str, StrategyFacts]:
    """Сведения о каждой стратегии каталога: {id стратегии: StrategyFacts}."""
    entries = dict(entries or {})
    # Группа по способу считается по всему каталогу: редкий способ уходит в «Прочие».
    family_keys = strategy_family_keys(entries)
    facts: dict[str, StrategyFacts] = {}
    for raw_id, entry in entries.items():
        strategy_id = str(raw_id or "").strip()
        name = str(getattr(entry, "name", "") or strategy_id)
        args = str(getattr(entry, "args", "") or "")
        visual = getattr(entry, "visual", None) or describe_strategy_visual(args)
        technical_label = str(getattr(visual, "label", "") or "")
        technique_description = str(getattr(visual, "description", "") or "")
        plain_label = strategy_plain_label(getattr(visual, "technique_keys", ()), technical_label)
        title, detail = split_strategy_name(name)
        old_name = str(getattr(entry, "old_name", "") or "").strip()
        author = str(getattr(entry, "author", "") or "").strip()
        if author.lower() == _BUILTIN_AUTHOR:
            author = ""
        description = _useful_description(getattr(entry, "description", ""))
        payload_badge = payload_badge_text(getattr(entry, "payload_scopes", ()) or ())
        facts[strategy_id] = StrategyFacts(
            strategy_id=strategy_id,
            name=name,
            title=title,
            detail=detail,
            series=strategy_series(name),
            source=strategy_source(name),
            family_key=family_keys.get(raw_id, "other"),
            plain_label=plain_label,
            technical_label=technical_label,
            technique_description=technique_description,
            args=args,
            old_name=old_name,
            label=str(getattr(entry, "label", "") or "").strip().lower(),
            author=author,
            description=description,
            payload_badge=payload_badge,
            payload_badge_accessible=payload_badge_accessible_text(payload_badge),
            search_text=" ".join(
                (name, old_name, args, technical_label, technique_description, plain_label, description, author)
            ).lower(),
        )
    return facts
