from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import List


_APPLICATION_LOG = "Application"
_SUPPORTED_SOURCES = {"Application Error", "Windows Error Reporting"}

# Канал Defender живёт в новом Windows Event Log API (EvtQuery), старый
# OpenEventLog его не видит. 1116 — угроза обнаружена, 1117 — принято действие
# (удаление/карантин): ровно те события, что объясняют исчезнувший winws2.exe.
_DEFENDER_CHANNEL = "Microsoft-Windows-Windows Defender/Operational"
_DEFENDER_DETECTION_EVENT_IDS = (1116, 1117)
_DEFENDER_DATA_RE = re.compile(
    r"<Data Name=['\"](?P<name>[^'\"]+)['\"]>(?P<value>[^<]*)</Data>",
    re.IGNORECASE,
)


def _event_time_is_older(event_time, cutoff: datetime) -> bool:
    try:
        return bool(event_time < cutoff)
    except Exception:
        return False


def _read_event_message(event) -> str:
    try:
        import win32evtlogutil

        text = str(win32evtlogutil.SafeFormatMessage(event, _APPLICATION_LOG) or "").strip()
        if text:
            return text
    except Exception:
        pass

    try:
        inserts = list(getattr(event, "StringInserts", None) or ())
        text = "\n".join(str(item or "").strip() for item in inserts if str(item or "").strip())
        if text:
            return text
    except Exception:
        pass

    return ""


def _summarize_defender_event(event_xml: str, *, path_marker: str) -> str:
    """Достаёт из XML события Defender имя угрозы, путь и принятое действие.

    Возвращает "" — если событие не относится к нужному пути или разобрать
    его не удалось: лучше промолчать, чем сообщить чужую находку.
    """
    fields = {
        match.group("name").strip().lower(): match.group("value").strip()
        for match in _DEFENDER_DATA_RE.finditer(str(event_xml or ""))
    }
    if not fields:
        return ""

    path = fields.get("path") or fields.get("file name") or ""
    marker = str(path_marker or "").strip().casefold()
    if marker and marker not in str(path).casefold():
        return ""

    threat = fields.get("threat name") or fields.get("name") or ""
    action = fields.get("action name") or fields.get("action") or ""

    parts = [part for part in (threat, action, path) if part]
    return " | ".join(parts)


def get_recent_defender_detections(
    *,
    minutes_back: int,
    max_events: int,
    path_marker: str = "",
) -> List[str] | None:
    """Читает недавние срабатывания Windows Defender по указанному пути.

    Нужен ровно для одного вопроса: не Defender ли удалил/заблокировал файл,
    который мы только что пытались запустить.

    Возвращает список найденного, пустой список — если журнал прочитан и
    срабатываний нет, и None — если прочитать не удалось (нет pywin32, канала
    или прав). Разница принципиальна: «проверено и чисто» и «проверить не
    вышло» нельзя показывать пользователю одинаково.
    """
    try:
        import win32evtlog
    except Exception:
        return None

    window_ms = max(1, int(minutes_back)) * 60 * 1000
    event_filter = " or ".join(f"EventID={code}" for code in _DEFENDER_DETECTION_EVENT_IDS)
    query = (
        f"*[System[({event_filter}) and "
        f"TimeCreated[timediff(@SystemTime) <= {window_ms}]]]"
    )

    results: List[str] = []
    handle = None
    opened = False
    try:
        handle = win32evtlog.EvtQuery(
            _DEFENDER_CHANNEL,
            win32evtlog.EvtQueryChannelPath | win32evtlog.EvtQueryReverseDirection,
            query,
            None,
        )
        opened = True
        while len(results) < int(max_events):
            events = win32evtlog.EvtNext(handle, int(max_events))
            if not events:
                break
            for event in events:
                try:
                    event_xml = win32evtlog.EvtRender(event, win32evtlog.EvtRenderEventXml)
                except Exception:
                    continue
                summary = _summarize_defender_event(event_xml, path_marker=path_marker)
                if summary:
                    results.append(summary)
                    if len(results) >= int(max_events):
                        break
    except Exception:
        # Успели что-то прочитать — отдаём найденное; не открыли канал вовсе —
        # честно говорим, что проверка не состоялась.
        return results if (opened and results) else None
    finally:
        try:
            if handle is not None:
                handle.Close()
        except Exception:
            pass

    return results


def get_recent_application_error_messages(*, process_name: str, minutes_back: int, max_events: int) -> List[str]:
    """Читает недавние события Application Error / WER для указанного процесса через Windows Event Log API."""
    normalized_process = str(process_name or "").strip().lower()
    if not normalized_process:
        return []

    import win32evtlog

    cutoff = datetime.now() - timedelta(minutes=max(1, int(minutes_back)))
    flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
    messages: List[str] = []

    handle = None
    try:
        handle = win32evtlog.OpenEventLog(None, _APPLICATION_LOG)
        while len(messages) < int(max_events):
            events = win32evtlog.ReadEventLog(handle, flags, 0)
            if not events:
                break

            for event in events:
                generated = getattr(event, "TimeGenerated", None)
                if generated is not None and _event_time_is_older(generated, cutoff):
                    return messages

                source = str(getattr(event, "SourceName", "") or "").strip()
                if source not in _SUPPORTED_SOURCES:
                    continue

                message = _read_event_message(event)
                if not message:
                    continue

                if normalized_process not in message.lower():
                    continue

                messages.append(message)
                if len(messages) >= int(max_events):
                    break
    except Exception:
        return messages
    finally:
        try:
            if handle is not None:
                import win32evtlog

                win32evtlog.CloseEventLog(handle)
        except Exception:
            pass

    return messages
