"""Сравнение двух проверок: с включённым Zapret и без него.

Одна проверка показывает сеть в одном состоянии. Что именно даёт обход, видно
только из пары: тот же набор сайтов, проверенный с Zapret и без. Здесь такая
пара находится в истории сама и раскладывается по сервисам:

- без Zapret не открывался, с ним открылся — обход помог;
- не открывался ни так, ни так — выбранный пресет этот сервис не чинит;
- открывался и без Zapret — обход ему не нужен;
- без Zapret открывался, а с ним перестал — пресет мешает.

«Пресет не чинит» — не то же самое, что «Zapret не может починить»: пресет
решает, для каких сайтов и какой обход включён. Поэтому вывод всегда называет
пресет и советует другой пресет или подбор стратегии, а не ставит крест.

Сравнивается только «открывается / не открывается». Сервис, про который в
одной из проверок ответа нет, в сравнение не попадает: неизвестное — не итог.

Здесь только чистые функции: записи истории приходят снаружи.
"""

from __future__ import annotations

from datetime import datetime, timedelta

__all__ = [
    "GROUP_TITLES",
    "MAX_AGE",
    "ZAPRET_OFF",
    "ZAPRET_ON",
    "compare_runs",
    "counterpart",
    "lines",
    "zapret_mark",
]

ZAPRET_ON = "on"
ZAPRET_OFF = "off"
# Старше этого прошлую проверку не берём: блокировки меняются, и разницу дало бы время, а не обход.
MAX_AGE = timedelta(days=3)

_OPEN = ("ok", "warn")
NOTE_PRESET = (
    "Это итог для выбранного пресета, а не для Zapret вообще: пресет решает, для каких сайтов и какой обход "
    "включён. Для сайтов, которые не открылись, попробуйте другой пресет или «Подбор стратегии»."
)
NOTE_TOOLS = (
    "В одной из двух проверок работал VPN или другая программа обхода — разница может быть из-за них, а не из-за Zapret."
)


def zapret_mark(zapret_running: bool | None) -> str:
    """Отметка для записи истории. Пусто — узнать, запущен ли Zapret, не удалось."""
    if zapret_running is None:
        return ""
    return ZAPRET_ON if zapret_running else ZAPRET_OFF


def _when(run: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(run.get("time") or ""))
    except ValueError:
        return None


def counterpart(history: list[dict], entry: dict) -> dict | None:
    """Последняя проверка того же набора сайтов в противоположном состоянии Zapret."""
    mark = entry.get("zapret")
    if mark not in (ZAPRET_ON, ZAPRET_OFF):
        return None
    wanted = ZAPRET_OFF if mark == ZAPRET_ON else ZAPRET_ON
    now = _when(entry)
    for run in reversed(history):
        if run.get("kind") != entry.get("kind") or run.get("title") != entry.get("title"):
            continue
        if run.get("zapret") != wanted:
            continue
        then = _when(run)
        if now is None or then is None or now - then > MAX_AGE:
            # История идёт от старых к новым: раньше этой записи только ещё более старые.
            return None
        return run
    return None


def compare_runs(entry: dict, other: dict | None) -> dict | None:
    """Что даёт обход, по сервисам. None — сравнивать не с чем или нечего."""
    if other is None:
        return None
    current_on = entry.get("zapret") == ZAPRET_ON
    with_zapret, without = (entry, other) if current_on else (other, entry)
    on_states, off_states = with_zapret.get("states") or {}, without.get("states") or {}
    helped: list[str] = []
    not_helped: list[str] = []
    fine_anyway: list[str] = []
    broken: list[str] = []
    # Порядок сервисов — как в текущей проверке.
    for name in entry.get("states") or {}:
        on, off = on_states.get(name), off_states.get(name)
        if off == "fail" and on in _OPEN:
            helped.append(name)
        elif off == "fail" and on == "fail":
            not_helped.append(name)
        elif off in _OPEN and on in _OPEN:
            fine_anyway.append(name)
        elif off in _OPEN and on == "fail":
            broken.append(name)
    if not (helped or not_helped or fine_anyway or broken):
        return None
    preset = str(with_zapret.get("preset") or "")
    named = f"пресет «{preset}»" if preset else "выбранный пресет"
    if broken:
        level, headline = "warn", f"С Zapret перестали открываться: {', '.join(broken)}"
    elif not_helped and helped:
        level, headline = "warn", f"{named[:1].upper()}{named[1:]} чинит не всё: помог {len(helped)}, не помог {len(not_helped)}"
    elif not_helped:
        level, headline = "fail", f"{named[:1].upper()}{named[1:]} не помог ни одному закрытому сервису"
    elif helped:
        level, headline = "ok", f"{named[:1].upper()}{named[1:]} чинит всё, что было закрыто"
    else:
        level, headline = "ok", "Всё открывается и без Zapret — обход этим сервисам не нужен"
    notes = [NOTE_PRESET] if (not_helped or broken) else []
    if entry.get("tools") or other.get("tools"):
        notes.append(NOTE_TOOLS)
    return {
        "level": level,
        "headline": headline,
        "preset": preset,
        # В какой из двух проверок работал Zapret: в этой ("current") или в прошлой ("past").
        "zapret_in": "current" if current_on else "past",
        "other_time": str(other.get("time") or ""),
        "helped": helped,
        "not_helped": not_helped,
        "fine_anyway": fine_anyway,
        "broken": broken,
        "notes": notes,
    }


_GROUPS = (
    ("helped", "✅", "Обход помог"),
    ("not_helped", "❌", "Не открываются и с обходом"),
    ("broken", "⚠️", "Открывались без Zapret, а с ним перестали"),
    ("fine_anyway", "ℹ️", "Открываются и без Zapret"),
)
GROUP_TITLES = {key: title for key, _icon, title in _GROUPS}
_LEVEL_ICONS = {"ok": "✅", "warn": "⚠️", "fail": "❌"}


def lines(result: dict | None, *, format_time=str) -> list[str]:
    """Сравнение строками для текста отчёта."""
    if not result:
        return []
    state = "без Zapret" if result.get("zapret_in") == "current" else "с Zapret"
    out = ["", f"━━━━━━━━ С Zapret и без (прошлая проверка {state} — {format_time(result.get('other_time') or '')}) ━━━━━━━━"]
    out.append(f"{_LEVEL_ICONS.get(str(result.get('level')), 'ℹ️')} {result.get('headline')}")
    for key, icon, title in _GROUPS:
        names = result.get(key) or ()
        if names:
            out.append(f"{icon} {title}: {', '.join(names)}")
    out.extend(f"ℹ️ {note}" for note in result.get("notes") or ())
    return out
