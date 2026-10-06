from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class StrategyTechniqueVisual:
    key: str
    label: str
    icon_name: str
    color: str
    description: str


@dataclass(frozen=True)
class StrategyVisual:
    technique_keys: tuple[str, ...]
    icon_name: str
    color: str
    colors: tuple[str, ...]
    label: str
    description: str


_UNKNOWN_TECHNIQUE = StrategyTechniqueVisual(
    key="custom",
    label="Своя",
    icon_name="fa5s.question",
    color="#9aa6b2",
    description="готовая стратегия не распознана по --lua-desync",
)

_TECHNIQUES: dict[str, StrategyTechniqueVisual] = {
    "fake": StrategyTechniqueVisual(
        key="fake",
        label="Fake",
        icon_name="fa5s.magic",
        color="#ff6b6b",
        description="подмена пакета",
    ),
    "split": StrategyTechniqueVisual(
        key="split",
        label="Split",
        icon_name="fa5s.cut",
        color="#f59e0b",
        description="разделение данных",
    ),
    "multisplit": StrategyTechniqueVisual(
        key="multisplit",
        label="MultiSplit",
        icon_name="fa5s.columns",
        color="#4cc2ff",
        description="несколько точек разделения данных",
    ),
    "disorder": StrategyTechniqueVisual(
        key="disorder",
        label="Disorder",
        icon_name="fa5s.random",
        color="#58d17a",
        description="перестановка частей данных",
    ),
    "multidisorder": StrategyTechniqueVisual(
        key="multidisorder",
        label="MultiDisorder",
        icon_name="fa5s.random",
        color="#58d17a",
        description="перестановка нескольких частей данных",
    ),
    "syndata": StrategyTechniqueVisual(
        key="syndata",
        label="Syndata",
        icon_name="fa5s.database",
        color="#aeb8c5",
        description="добавление данных в SYN",
    ),
    "send": StrategyTechniqueVisual(
        key="send",
        label="Send",
        icon_name="fa5s.paper-plane",
        color="#76d0ff",
        description="дополнительная отправка данных",
    ),
    "udplen": StrategyTechniqueVisual(
        key="udplen",
        label="UDPLen",
        icon_name="fa5s.ruler",
        color="#b58cff",
        description="изменение длины UDP-пакета",
    ),
    "oob": StrategyTechniqueVisual(
        key="oob",
        label="OOB",
        icon_name="fa5s.external-link-alt",
        color="#ff78b7",
        description="отправка out-of-band данных",
    ),
    "tcpseg": StrategyTechniqueVisual(
        key="tcpseg",
        label="TCPSeg",
        icon_name="fa5s.code-branch",
        color="#f6c945",
        description="изменение TCP-сегментации",
    ),
    "pass": StrategyTechniqueVisual(
        key="pass",
        label="Pass",
        icon_name="fa5s.minus",
        color="#8f9aa6",
        description="строка без активного desync-действия",
    ),
    "hostfakesplit": StrategyTechniqueVisual(
        key="hostfakesplit",
        label="HostFakeSplit",
        icon_name="fa5s.user-secret",
        color="#ff8f5a",
        description="разделение по имени сайта с поддельным именем между частями",
    ),
    "fakedsplit": StrategyTechniqueVisual(
        key="fakedsplit",
        label="FakedSplit",
        icon_name="fa5s.clone",
        color="#ffa94d",
        description="разделение данных с поддельными копиями частей",
    ),
    "fakeddisorder": StrategyTechniqueVisual(
        key="fakeddisorder",
        label="FakedDisorder",
        icon_name="fa5s.random",
        color="#8bd450",
        description="перестановка частей данных с поддельными копиями",
    ),
    "fakemultisplit": StrategyTechniqueVisual(
        key="fakemultisplit",
        label="FakeMultiSplit",
        icon_name="fa5s.layer-group",
        color="#6fb8ff",
        description="несколько точек разделения с поддельными частями",
    ),
    "fakemultidisorder": StrategyTechniqueVisual(
        key="fakemultidisorder",
        label="FakeMultiDisorder",
        icon_name="fa5s.random",
        color="#7fd99a",
        description="перестановка нескольких частей с поддельными частями",
    ),
    "drop": StrategyTechniqueVisual(
        key="drop",
        label="Drop",
        icon_name="fa5s.ban",
        color="#e5484d",
        description="отбрасывание пакета",
    ),
    "wssize": StrategyTechniqueVisual(
        key="wssize",
        label="WSSize",
        icon_name="fa5s.compress-arrows-alt",
        color="#5eead4",
        description="уменьшение TCP-окна, чтобы сервер отвечал мелкими частями",
    ),
    "pktmod": StrategyTechniqueVisual(
        key="pktmod",
        label="PktMod",
        icon_name="fa5s.sliders-h",
        color="#c4a7ff",
        description="изменение полей текущего пакета",
    ),
    "tamper": StrategyTechniqueVisual(
        key="tamper",
        label="Tamper",
        icon_name="fa5s.pen",
        color="#e0b86b",
        description="изменение содержимого пакета без разделения",
    ),
}

# Точные имена стандартных функций --lua-desync (zapret-lib.lua / zapret-antidpi.lua
# и расширения из lua-init). Проверяются раньше поиска по подстроке, иначе,
# например, hostfakesplit попадал в Split, а fakeddisorder — в MultiDisorder.
_EXACT_LUA_DESYNC_TECHNIQUES: dict[str, str] = {
    "pass": "pass",
    "send": "send",
    "syndata": "syndata",
    "udplen": "udplen",
    "oob": "oob",
    "tcpseg": "tcpseg",
    "fake": "fake",
    "multisplit": "multisplit",
    "multidisorder": "multidisorder",
    "multidisorder_legacy": "multidisorder",
    "hostfakesplit": "hostfakesplit",
    "fakedsplit": "fakedsplit",
    "fakeddisorder": "fakeddisorder",
    "fakemultisplit": "fakemultisplit",
    "fakemultidisorder": "fakemultidisorder",
    "drop": "drop",
    "wssize": "wssize",
    "pktmod": "pktmod",
    "dht_dn": "tamper",
    "http_domcase": "tamper",
    "http_hostcase": "tamper",
    "http_methodeol": "tamper",
    "http_unixeol": "tamper",
}


def describe_strategy_visual(args_text: str) -> StrategyVisual:
    techniques = tuple(_extract_techniques(args_text))
    if not techniques:
        return _visual_from_techniques(())
    return _visual_from_techniques(techniques)


def _visual_from_techniques(technique_keys: tuple[str, ...]) -> StrategyVisual:
    visuals = [_TECHNIQUES[key] for key in technique_keys if key in _TECHNIQUES]
    if not visuals:
        return StrategyVisual(
            technique_keys=(),
            icon_name=_UNKNOWN_TECHNIQUE.icon_name,
            color=_UNKNOWN_TECHNIQUE.color,
            colors=(_UNKNOWN_TECHNIQUE.color,),
            label=_UNKNOWN_TECHNIQUE.label,
            description=_UNKNOWN_TECHNIQUE.description,
        )

    primary = visuals[0]
    labels = [visual.label for visual in visuals]
    descriptions = [f"{visual.label}: {visual.description}" for visual in visuals]
    return StrategyVisual(
        technique_keys=tuple(visual.key for visual in visuals),
        icon_name=primary.icon_name,
        color=primary.color,
        colors=tuple(visual.color for visual in visuals),
        label=" + ".join(labels),
        description="; ".join(descriptions),
    )


def strategy_technique_keys(args_text: str) -> tuple[str, ...]:
    """Способы обхода стратегии по её строкам --lua-desync, в порядке следования."""
    return tuple(_extract_techniques(args_text))


def _extract_techniques(args_text: str) -> list[str]:
    result: list[str] = []
    for value in re.findall(r"(?:^|\s)--lua-desync=([a-zA-Z0-9_-]+)", str(args_text or "")):
        key = _map_lua_desync_value(value)
        if key and key not in result:
            result.append(key)
    return result


def _map_lua_desync_value(value: str) -> str | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    exact = _EXACT_LUA_DESYNC_TECHNIQUES.get(raw)
    if exact:
        return exact
    if raw.startswith("hostfakesplit"):
        return "hostfakesplit"
    if "syndata" in raw:
        return "syndata"
    if "udplen" in raw:
        return "udplen"
    if "oob" in raw:
        return "oob"
    if "multidisorder" in raw or "fakeddisorder" in raw:
        return "multidisorder"
    if "disorder" in raw:
        return "disorder"
    if "multisplit" in raw:
        return "multisplit"
    if "split" in raw:
        return "split"
    if "tcpseg" in raw:
        return "tcpseg"
    if "fake" in raw:
        return "fake"
    return None
