"""Журнал исправлений видео YouTube: что человек нажал и помогло ли это в его сети.

У каждого своё: одному закрытие кэшей провайдера возвращает видео, у другого видео
после этого замирает совсем (так писали на форуме). Поэтому программа не угадывает,
а учится на результате. Нажатая кнопка (``blockcheck.ui.page._on_youtube_fix``)
записывает, сколько видеосерверов отвечало перед исправлением и в какой сети.
Следующая проверка сравнивает с тем, что стало, и говорит «помогло», «стало хуже —
верните» или «без перемен». Исправление, которое в этой сети не помогло, второй раз
не предлагается.

Сеть узнаётся по номерам сетей (AS) кэшей провайдера из обхода: дома и в мобильной
сети они разные, и уроки одной сети не переносятся на другую.

Чистые функции (``summary``, ``network_key``, ``judge``, ``tried_without_luck``) отделены
от чтения и записи настроек (``load``, ``record``, ``settle``).
"""

from __future__ import annotations

from datetime import datetime

__all__ = [
    "VERDICT_HELPED",
    "VERDICT_SAME",
    "VERDICT_WORSE",
    "judge",
    "load",
    "network_key",
    "pending",
    "record",
    "settle",
    "summary",
    "sweep_from_report",
    "tried_without_luck",
]

VERDICT_HELPED = "helped"
VERDICT_WORSE = "worse"
VERDICT_SAME = "same"

_ANSWERS = {"ok", "cut_window"}


def summary(sweep: object) -> dict:
    """Сколько адресов видеосерверов ответило из проверенных."""
    nodes = [node for node in (sweep or ()) if isinstance(node, dict)]
    return {"answering": sum(1 for node in nodes if node.get("code") in _ANSWERS), "total": len(nodes)}


def network_key(sweep: object) -> str:
    """Сеть по номерам сетей кэшей провайдера. Пусто — кэшей провайдера в обходе нет."""
    asns = {str(node.get("asn")) for node in (sweep or ()) if isinstance(node, dict) and node.get("provider") and node.get("asn")}
    return ",".join(f"AS{asn}" for asn in sorted(asns))


def sweep_from_report(report: object) -> list[dict]:
    """Обход видеосерверов из готового отчёта (``report["services"]`` — список или словарь сервисов)."""
    if not isinstance(report, dict):
        return []
    services = report.get("services")
    items = services.values() if isinstance(services, dict) else (services or ())
    found: list[dict] = []
    for service in items:
        for target in (service.get("targets") or ()) if isinstance(service, dict) else ():
            if isinstance(target, dict):
                found.extend(node for node in target.get("video_sweep") or () if isinstance(node, dict))
    return found


def _share(counts: dict) -> float | None:
    total = int(counts.get("total") or 0)
    return None if total <= 0 else int(counts.get("answering") or 0) / total


def judge(before: dict, after: dict) -> str:
    """Помогло ли: доля отвечающих видеосерверов до и после. Пусто — сравнить не с чем."""
    old, new = _share(before), _share(after)
    if old is None or new is None:
        return ""
    if new > old:
        return VERDICT_HELPED
    if new < old:
        return VERDICT_WORSE
    return VERDICT_SAME


def pending(journal: list[dict]) -> dict | None:
    """Последнее исправление без итога: его и оценит эта проверка."""
    return next((entry for entry in reversed(journal) if not entry.get("verdict")), None)


def tried_without_luck(journal: list[dict], action: str, network: str) -> bool:
    """Это исправление в этой сети уже пробовали, и оно не помогло: второй раз не предлагать."""
    return any(
        entry.get("action") == action and entry.get("network") == network and entry.get("verdict") in (VERDICT_WORSE, VERDICT_SAME)
        for entry in journal
    )


def load() -> list[dict]:
    try:
        from settings.store import get_video_fix_journal

        return list(get_video_fix_journal())
    except Exception:
        return []


def record(action: str, target: str, *, before: dict, network: str, when: datetime | None = None) -> None:
    """Записать нажатое исправление с тем, что было до него."""
    from settings.store import set_video_fix_journal

    entry = {
        "action": action,
        "target": target,
        "time": (when or datetime.now()).isoformat(timespec="seconds"),
        "network": network,
        "before": dict(before),
        "verdict": "",
    }
    set_video_fix_journal([*load(), entry])


def settle(verdict: str) -> None:
    """Записать итог последнего исправления без итога."""
    if not verdict:
        return
    from settings.store import set_video_fix_journal

    journal = load()
    entry = pending(journal)
    if entry is None:
        return
    entry["verdict"] = verdict
    set_video_fix_journal(journal)
