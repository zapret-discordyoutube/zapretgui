"""Слова для страницы «Кто подключается»: событие журнала раздачи → строка таблицы.

Одни и те же слова у Zapret 2 GUI и Zapret KVN (общее ядро). Без Qt: так тексты проверяются
обычными тестами. Каждая функция получает ``tr(ключ, по_умолчанию)``; программа без каталога
переводов (Zapret KVN) передаёт ``lambda key, default: default``.
"""

from __future__ import annotations

import time
from collections.abc import Callable

Tr = Callable[[str, str], str]

DEFAULTS = {
    "page.sharing_activity.status.lan.on": "Раздача по локальной сети: включена, порт {port}",
    "page.sharing_activity.status.lan.off": "Раздача по локальной сети: не работает",
    "page.sharing_activity.status.lan.switched_off": "Раздача по локальной сети: выключена",
    "page.sharing_activity.status.net.switched_off": "Раздача через интернет: выключена",
    "page.sharing_activity.status.net.on": "Раздача через интернет: включена, ваш адрес для других — {address}",
    "page.sharing_activity.status.net.off": "Раздача через интернет: не работает",
    "page.sharing_activity.status.unavailable": "Раздача ещё не запускалась: она стартует через минуту после запуска программы.",
    "page.sharing_activity.summary": "Обращений: {events} · разных гостей: {visitors} · отдано: {given} · получено: {taken}",
    "page.sharing_activity.dir.in": "К вам",
    "page.sharing_activity.dir.out": "От вас",
    "page.sharing_activity.via.lan": "Локальная сеть",
    "page.sharing_activity.via.internet": "Интернет",
    "page.sharing_activity.what.search": "Вопрос в сети: у кого есть версия?",
    "page.sharing_activity.what.file": "Файл {name}",
    "page.sharing_activity.what.peers": "Обмен адресами",
    "page.sharing_activity.what.manifest": "Подписанный список выпуска",
    "page.sharing_activity.what.request": "Посторонний запрос",
    "page.sharing_activity.result.given": "отдано",
    "page.sharing_activity.result.taken": "получено",
    "page.sharing_activity.result.answered": "отвечено",
    "page.sharing_activity.result.interrupted": "прервано",
    "page.sharing_activity.result.refused": "отказано",
    "page.sharing_activity.result.busy": "заняты",
    "page.sharing_activity.result.not_found": "нет такого",
    "page.sharing_activity.result.failed": "сбой",
}


def size_text(size: int) -> str:
    if size <= 0:
        return "—"
    if size < 1024:
        return f"{size} Б"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} КБ"
    return f"{size / 1048576:.1f} МБ"


def clock_text(when: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(when))


def what_text(event, tr: Tr) -> str:
    key = f"page.sharing_activity.what.{event.what}"
    base = tr(key, DEFAULTS.get(key, DEFAULTS["page.sharing_activity.what.request"]))
    return base.format(name=event.detail or "?") if "{name}" in base else base


def result_text(event, tr: Tr) -> str:
    if event.result == "ok":
        key = (
            "page.sharing_activity.result.answered"
            if event.what in ("search", "peers", "manifest") and event.direction == "in"
            else "page.sharing_activity.result.given" if event.direction == "in" else "page.sharing_activity.result.taken"
        )
    else:
        key = f"page.sharing_activity.result.{event.result}"
    return tr(key, DEFAULTS.get(key, event.result))


def row_texts(event, tr: Tr) -> tuple[str, str, str, str, str, str, str]:
    """Время, кто к кому, откуда, адрес, что, размер, итог."""
    direction = "in" if event.direction == "in" else "out"
    via = "internet" if event.via == "internet" else "lan"
    return (
        clock_text(event.when),
        tr(f"page.sharing_activity.dir.{direction}", DEFAULTS[f"page.sharing_activity.dir.{direction}"]),
        tr(f"page.sharing_activity.via.{via}", DEFAULTS[f"page.sharing_activity.via.{via}"]),
        event.peer,
        what_text(event, tr),
        size_text(event.size),
        result_text(event, tr),
    )


def status_lines(snapshot: dict, tr: Tr) -> list[str]:
    if not snapshot.get("available"):
        return [tr("page.sharing_activity.status.unavailable", DEFAULTS["page.sharing_activity.status.unavailable"])]
    lines = []
    port = snapshot.get("lan_port")
    if port:
        lines.append(tr("page.sharing_activity.status.lan.on", DEFAULTS["page.sharing_activity.status.lan.on"]).format(port=port))
    else:
        key = "lan.off" if snapshot.get("lan_switch", True) else "lan.switched_off"
        lines.append(tr(f"page.sharing_activity.status.{key}", DEFAULTS[f"page.sharing_activity.status.{key}"]))
    if snapshot.get("internet_running"):
        text = tr("page.sharing_activity.status.net.on", DEFAULTS["page.sharing_activity.status.net.on"])
        lines.append(text.format(address=snapshot.get("internet_address") or "?"))
    else:
        key = "net.off" if snapshot.get("internet_switch", True) else "net.switched_off"
        lines.append(tr(f"page.sharing_activity.status.{key}", DEFAULTS[f"page.sharing_activity.status.{key}"]))
    return lines


def summary_text(summary: dict, tr: Tr) -> str:
    text = tr("page.sharing_activity.summary", DEFAULTS["page.sharing_activity.summary"])
    return text.format(
        events=summary.get("events", 0),
        visitors=summary.get("visitors", 0),
        given=size_text(summary.get("given_bytes", 0)),
        taken=size_text(summary.get("taken_bytes", 0)),
    )


__all__ = ["DEFAULTS", "clock_text", "result_text", "row_texts", "size_text", "status_lines", "summary_text", "what_text"]
