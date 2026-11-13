"""Что можно сделать с видео YouTube: подбор исправлений по обходу видеосерверов (``video_sweep``).

У разных людей видео ломается по-разному: у одного молчит кэш провайдера, а серверы Google
отвечают; у другого режут ответ сервера, и он приходит только с малым окном приёма; у третьего
по IPv6 всё работает, а по IPv4 нет. Поэтому исправление не одно на всех: здесь по фактам
обхода выбираются только те, что подходят этому человеку, по порядку пользы.

Модуль — чистая функция над строками отчёта (``report_text.target_report["video_sweep"]``): ни
сети, ни слов. Слова для экрана и текста отчёта задаёт ``service_view.video_fix_rows``, кнопки
карточки берут из того же списка ключ исправления и его данные (например, группы узлов).
"""

from __future__ import annotations

from collections.abc import Iterable

__all__ = [
    "FIX_BLOCK_CACHES",
    "FIX_PREFER_IPV6",
    "FIX_QUIC_OFF",
    "FIX_WAIT_RELOAD",
    "FIX_WSSIZE_PRESET",
    "recommend",
]

# Закрыть в hosts молчащие кэши провайдера: плеер перейдёт на серверы Google, которые отвечают.
FIX_BLOCK_CACHES = "block_caches"
# Выключить QUIC (UDP 443) для YouTube: видео пойдёт по TCP, где работают приёмы Zapret и wssize.
FIX_QUIC_OFF = "quic_off"
# Перейти на пресет с малым окном приёма (wssize): ответ сервера режут, но с окном в 1 байт он приходит.
FIX_WSSIZE_PRESET = "wssize_preset"
# По IPv6 узлы отвечают, по IPv4 нет: стоит, чтобы Windows предпочитала IPv6.
FIX_PREFER_IPV6 = "prefer_ipv6"
# Видео идёт через кэш провайдера: если ролика в кэше нет, помогает подождать и обновить страницу.
FIX_WAIT_RELOAD = "wait_reload"

_ANSWERS = {"ok", "cut_window"}


def _nodes(sweep: object) -> list[dict]:
    return [node for node in (sweep or ()) if isinstance(node, dict)]


def _failing_provider_groups(nodes: Iterable[dict]) -> list[str]:
    """Группы узлов провайдера, у которых не ответил ни один адрес. Узлы самого Google сюда не попадают никогда."""
    by_group: dict[str, list[dict]] = {}
    for node in nodes:
        group = str(node.get("group") or "")
        if group and node.get("provider"):
            by_group.setdefault(group, []).append(node)
    return [group for group, items in by_group.items() if not any(item.get("code") in _ANSWERS for item in items)]


def recommend(sweep: object, *, quic: str = "") -> list[dict]:
    """Исправления по порядку пользы: ``{"key": FIX_*, ...данные}``. Пусто — обход ничего не показал.

    ``quic`` — код QUIC видеосервера (``quic_probe``): «blocked_by_name» значит, что QUIC режут.
    """
    nodes = _nodes(sweep)
    if not nodes:
        return []
    fixes: list[dict] = []
    google_answers = any(not node.get("provider") and node.get("owner") and node.get("code") in _ANSWERS for node in nodes)
    groups = _failing_provider_groups(nodes)
    # Закрывать кэш имеет смысл, только если есть куда уйти: хоть один сервер Google отвечает.
    if groups and google_answers:
        fixes.append({"key": FIX_BLOCK_CACHES, "groups": groups})
    window = any(node.get("code") == "cut_window" for node in nodes)
    # wssize действует только на TCP: при включённом QUIC видео идёт мимо него. Поэтому QUIC — раньше пресета.
    if window or quic == "blocked_by_name":
        fixes.append({"key": FIX_QUIC_OFF})
    if window:
        fixes.append({"key": FIX_WSSIZE_PRESET})
    v4 = [node for node in nodes if node.get("family") == "IPv4"]
    v6 = [node for node in nodes if node.get("family") == "IPv6"]
    if v4 and v6 and any(node.get("code") == "ok" for node in v6) and not any(node.get("code") == "ok" for node in v4):
        fixes.append({"key": FIX_PREFER_IPV6})
    if any(node.get("provider") for node in nodes) and not all(node.get("code") == "ok" for node in nodes):
        fixes.append({"key": FIX_WAIT_RELOAD})
    return fixes
