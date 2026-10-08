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

_SCOPE_TITLES = {"main": "Discord и YouTube", "all": "Все сайты", "full": "Полная проверка"}
_OPEN = ("ok", "warn")


def _overall_level(report: dict) -> str:
    levels = [str(problem.get("level") or "") for problem in report.get("problems") or ()]
    for level in ("fail", "warn", "unknown"):
        if level in levels:
            return level
    return "ok"


def blockcheck_entry(report: dict, *, log_file: str = "", when: datetime | None = None, preset: str = "") -> dict:
    """Короткая запись о прогоне BlockCheck для истории.

    ``preset`` — название выбранного пресета: по нему сравнение «с Zapret и без» называет, что именно проверялось.
    """
    from diagnostics.compare import zapret_mark

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
        # При каких условиях шла проверка — для сравнения «с Zapret и без».
        "zapret": zapret_mark(report.get("zapret_running")),
        "preset": str(preset or ""),
        # Только программы, которые стояли на дороге проверки: запущенный, но не включённый VPN не в счёт.
        "tools": bool(report.get("tools_in_path")),
        # Как ведёт себя фильтр (код из ``filter_habits``) — подсказка подбору стратегий.
        "habit": str((report.get("habits") or {}).get("code") or ""),
    }


KIND_DOMAIN = "domain"
KIND_DNS = "dns"
KIND_SERVERS = "servers"


def dns_check_entry(results: dict, *, when: datetime | None = None) -> dict | None:
    """Запись о проверке DNS подмены. None — проверку остановили или она не дала данных."""
    domains = dict(results.get("domains") or {})
    if results.get("stopped") or not domains:
        return None
    levels = {"ok": "ok", "local": "ok", "spoofed": "fail"}
    states = {str(host): levels.get(str(item.get("state") or ""), "unknown") for host, item in domains.items()}
    spoofed = [host for host, level in states.items() if level == "fail"]
    unknown = [host for host, level in states.items() if level == "unknown"]
    if spoofed:
        level, headline = "fail", f"Подменяются адреса: {', '.join(spoofed)}"
    elif unknown:
        level, headline = "warn", f"Подмены не видно, но не удалось проверить: {', '.join(unknown)}"
    else:
        level, headline = "ok", "DNS отвечает честно"
    return {
        "kind": KIND_DNS,
        "time": (when or datetime.now()).isoformat(timespec="seconds"),
        "title": "DNS подмена",
        "level": level,
        "headline": headline,
        "problems": [f"{host}: {domains[host].get('reason') or 'адрес подменён'}" for host in spoofed],
        "states": states,
        "log_file": "",
    }


def server_check_entry(report, *, when: datetime | None = None) -> dict | None:
    """Запись о проверке DNS-серверов. None — проверку остановили или она не закончилась."""
    if not getattr(report, "finished", False) or getattr(report, "stopped", False) or not report.rows:
        return None
    findings = list(report.findings)
    order = {"fail": 0, "warn": 1}
    worst = min(findings, key=lambda item: order.get(str(item.level), 2), default=None)
    level = str(worst.level) if worst is not None and str(worst.level) in order else "ok"
    headline = (worst.title or worst.text) if worst is not None and level != "ok" else "Замечаний нет"
    return {
        "kind": KIND_SERVERS,
        "time": (when or datetime.now()).isoformat(timespec="seconds"),
        "title": f"Адресов проверено: {len(report.rows)}",
        "level": level,
        "headline": str(headline),
        "problems": [str(item.title or item.text) for item in findings if str(item.level) in order],
        "states": {},
        "log_file": "",
    }


def domain_entry(target: str, level: str, headline: str, problems=(), *, when: datetime | None = None) -> dict:
    """Запись о проверке одного домена: что проверяли, чем кончилось и что нашли."""
    return {
        "kind": KIND_DOMAIN,
        "time": (when or datetime.now()).isoformat(timespec="seconds"),
        "title": str(target),
        "level": level if level in ("ok", "warn", "fail") else "unknown",
        "headline": str(headline),
        "problems": [str(item) for item in problems],
        "states": {},
        "log_file": "",
    }


def history_rows(runs) -> list[tuple[str, str, str]]:
    """Прошлые проверки для экрана, свежие сверху: (уровень, «что · когда», итог)."""
    rows = []
    for run in reversed(list(runs or ())):
        title = str(run.get("title") or "")
        when = format_time(str(run.get("time") or ""))
        rows.append((str(run.get("level") or "unknown"), " · ".join(part for part in (title, when) if part), str(run.get("headline") or "")))
    return rows


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
