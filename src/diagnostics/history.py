"""История проверок сети: что запомнить о прогоне и что изменилось с прошлого раза.

Одна проверка отвечает на вопрос «что сейчас». История отвечает на вопрос
«что изменилось»: сайт, который вчера открывался, а сегодня нет, — самый
верный признак новой блокировки.

Здесь только чистые функции. Хранит историю ``settings.store`` (общая база
настроек программы): запись короткая — итог и состояние каждого сервиса.
Полный текст прогона лежит в журнале проверки, запись на него ссылается.
"""

from __future__ import annotations

import json
from datetime import datetime

__all__ = [
    "KIND_BLOCKCHECK",
    "REPORT_FORMAT",
    "blockcheck_entry",
    "describe_changes",
    "format_time",
    "previous_run",
    "report_json",
]

KIND_BLOCKCHECK = "blockcheck"
# Название и номер формата выгрузки: по ним чужая программа поймёт, что перед ней.
REPORT_FORMAT = "zapretgui.blockcheck/1"

_SCOPE_TITLES = {"main": "Discord и YouTube", "all": "Все сайты"}
_OPEN = ("ok", "warn")


def _overall_level(report: dict) -> str:
    levels = [str(problem.get("level") or "") for problem in report.get("problems") or ()]
    for level in ("fail", "warn", "unknown"):
        if level in levels:
            return level
    return "ok"


def blockcheck_entry(report: dict, *, log_file: str = "", when: datetime | None = None) -> dict:
    """Короткая запись о прогоне BlockCheck для истории."""
    when = when or datetime.now()
    problems = [str(problem.get("text") or "") for problem in report.get("problems") or ()]
    states = {
        str(service.get("label") or service.get("key") or ""): str(service.get("level") or "unknown")
        for service in report.get("services") or ()
        if not service.get("control")
    }
    level = _overall_level(report)
    if level == "ok":
        headline = "Всё открывается"
    else:
        headline = problems[0] if problems else "Проверка не дала ответа"
    return {
        "kind": KIND_BLOCKCHECK,
        "time": when.isoformat(timespec="seconds"),
        "title": _SCOPE_TITLES.get(str(report.get("scope") or ""), str(report.get("scope") or "")),
        "level": level,
        "headline": headline,
        "problems": problems,
        "states": {name: state for name, state in states.items() if name},
        "log_file": str(log_file or ""),
    }


def previous_run(history: list[dict], entry: dict) -> dict | None:
    """Последний прошлый прогон того же вида и с тем же набором сайтов."""
    for run in reversed(history):
        if run.get("kind") == entry.get("kind") and run.get("title") == entry.get("title"):
            return run
    return None


def format_time(value: str) -> str:
    """«07.10 20:15» из времени записи; при неразборчивом времени — как есть."""
    try:
        return datetime.fromisoformat(str(value)).strftime("%d.%m %H:%M")
    except ValueError:
        return str(value)


def describe_changes(previous: dict | None, current: dict) -> list[str]:
    """Что изменилось с прошлой проверки, по одному предложению на сервис.

    Сравнивается только «открывается / не открывается». Сервис, которого в
    одной из проверок не было или про который ответа не получили, не
    сравнивается: неизвестное — не перемена.
    """
    if previous is None:
        return []
    before, after = previous.get("states") or {}, current.get("states") or {}
    stopped: list[str] = []
    recovered: list[str] = []
    for name, state in after.items():
        old = before.get(name)
        if old in _OPEN and state == "fail":
            stopped.append(name)
        elif old == "fail" and state in _OPEN:
            recovered.append(name)
    changes: list[str] = []
    if stopped:
        changes.append(f"перестали открываться: {', '.join(stopped)}")
    if recovered:
        changes.append(f"снова открываются: {', '.join(recovered)}")
    return changes


def report_json(report: dict, entry: dict, *, app_version: str = "") -> str:
    """Полный отчёт в строгом виде — для разбора другой программой и для отправки."""
    document = {
        "format": REPORT_FORMAT,
        "app_version": str(app_version or ""),
        "time": entry.get("time", ""),
        "title": entry.get("title", ""),
        "level": entry.get("level", ""),
        "report": report,
    }
    return json.dumps(document, ensure_ascii=False, indent=1, default=str)
