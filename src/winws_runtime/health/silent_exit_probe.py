# winws_runtime/health/silent_exit_probe.py
"""Почему winws/winws2 умер, не сказав ни слова — по проверяемым фактам.

Когда процесс завершается с кодом 1 и не оставляет диагностики, объяснение
приходится искать вне его вывода. Соблазн выдать пользователю правдоподобную
версию («это антивирус») здесь вреден: угаданная причина уводит от настоящей
ничуть не хуже, чем строка версии в тексте ошибки.

Поэтому модуль ничего не предполагает, а проверяет:

- исчез ли с диска сам исполняемый файл (карантин — это факт, а не гипотеза);
- целы ли файлы WinDivert;
- есть ли в журнале Windows запись об аварийном завершении процесса
  (она, наоборот, *опровергает* версию про внешнее закрытие — процесс упал сам);
- срабатывал ли Defender по пути программы;
- присутствует ли в системе антивирус (только как подозрение, не как причина).

Отчёт всегда несёт и обратную сторону — список того, что проверено и оказалось
в норме: «причина не установлена, проверено то-то» честнее и полезнее для
поддержки, чем уверенная выдумка.

Пробы стоят десятки-сотни миллисекунд и вызываются только на пути отказа;
результат кешируется на короткое время, чтобы серия повторных попыток не
платила за одно и то же несколько раз.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import List, Optional

from log.log import log


_PROBE_CACHE_TTL_SECONDS = 15.0

# Окна поиска в журналах: авария процесса ищется вокруг только что случившегося
# отказа, срабатывание антивируса могло произойти чуть раньше — при подготовке
# запуска.
_APPLICATION_ERROR_WINDOW_MINUTES = 2
_DEFENDER_WINDOW_MINUTES = 5


class _ProbeUnavailable:
    """Проба не состоялась: ни находки, ни права сказать «здесь всё в норме»."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - только для отладки
        return "PROBE_UNAVAILABLE"


PROBE_UNAVAILABLE = _ProbeUnavailable()


@dataclass(frozen=True)
class SilentExitFinding:
    """Одна находка. `confirmed` — установленный факт, иначе подозрение."""

    fact: str
    solution: str = ""
    confirmed: bool = False


@dataclass(frozen=True)
class SilentExitReport:
    findings: tuple[SilentExitFinding, ...] = field(default=())
    verified: tuple[str, ...] = field(default=())

    @property
    def confirmed(self) -> tuple[SilentExitFinding, ...]:
        return tuple(finding for finding in self.findings if finding.confirmed)

    @property
    def suspected(self) -> tuple[SilentExitFinding, ...]:
        return tuple(finding for finding in self.findings if not finding.confirmed)

    def log_summary(self) -> str:
        facts = "; ".join(
            f"{'FACT' if finding.confirmed else 'SUSPECT'}: {finding.fact}"
            for finding in self.findings
        )
        checked = "; ".join(self.verified)
        if facts and checked:
            return f"{facts} | проверено и в норме: {checked}"
        return facts or (f"проверено и в норме: {checked}" if checked else "проверок не выполнено")


_cached_report: Optional[SilentExitReport] = None
_cached_key: tuple[str, str] = ("", "")
_cached_at: float = 0.0


def reset_silent_exit_probe_cache() -> None:
    """Сбрасывает кеш проб (тесты и явные повторные диагностики)."""
    global _cached_report, _cached_key, _cached_at
    _cached_report = None
    _cached_key = ("", "")
    _cached_at = 0.0


def _probe_executable_missing(exe_path: str) -> Optional[SilentExitFinding]:
    path = str(exe_path or "").strip()
    if not path:
        return None
    if os.path.isfile(path):
        return None
    return SilentExitFinding(
        fact=f"исполняемый файл исчез с диска ({os.path.basename(path) or path})",
        solution=(
            "Восстановите файл из карантина антивируса, добавьте папку программы "
            "в исключения и переустановите программу"
        ),
        confirmed=True,
    )


def _probe_windivert_files_missing() -> Optional[SilentExitFinding]:
    from winws_runtime.health.winws_exit_diagnosis import _check_windivert_files

    missing = _check_windivert_files()
    if not missing:
        return None
    return SilentExitFinding(
        fact=f"отсутствуют файлы WinDivert: {', '.join(missing)}",
        solution=(
            "Восстановите файлы из карантина антивируса, добавьте папку программы "
            "в исключения и переустановите программу"
        ),
        confirmed=True,
    )


def _probe_application_error_event(process_name: str) -> Optional[SilentExitFinding]:
    """Запись Application Error/WER = процесс упал сам, а не был закрыт извне."""
    from utils.windows_event_log import get_recent_application_error_messages

    name = str(process_name or "").strip()
    if not name:
        return None

    messages = get_recent_application_error_messages(
        process_name=name,
        minutes_back=_APPLICATION_ERROR_WINDOW_MINUTES,
        max_events=1,
    )
    if not messages:
        return None

    first_line = next(
        (line.strip() for line in str(messages[0] or "").splitlines() if line.strip()),
        "",
    )
    return SilentExitFinding(
        fact=f"Windows зарегистрировала аварийное завершение процесса: {first_line[:200]}",
        solution="Это сбой самого winws, а не блокировка. Сохраните лог и сообщите о проблеме",
        confirmed=True,
    )


def _probe_defender_detection(exe_path: str):
    from utils.windows_event_log import get_recent_defender_detections

    folder = os.path.dirname(str(exe_path or "").strip())
    detections = get_recent_defender_detections(
        minutes_back=_DEFENDER_WINDOW_MINUTES,
        max_events=2,
        path_marker=folder,
    )
    if detections is None:
        # Журнал недоступен — молчим о нём вовсе, а не рапортуем «чисто».
        return PROBE_UNAVAILABLE
    if not detections:
        return None
    return SilentExitFinding(
        fact=f"Windows Defender сработал по файлам программы: {detections[0][:200]}",
        solution=(
            "Восстановите файл из журнала защиты Windows и добавьте папку программы "
            "в исключения Defender"
        ),
        confirmed=True,
    )


def _probe_antivirus_present() -> Optional[SilentExitFinding]:
    from winws_runtime.health.antivirus_detection import _detect_active_antivirus

    antivirus = _detect_active_antivirus()
    if not antivirus:
        return None
    return SilentExitFinding(
        fact=f"в системе активен антивирус ({antivirus}) — он мог закрыть процесс",
        solution="Проверьте карантин антивируса и добавьте папку программы в его исключения",
        confirmed=False,
    )


def _run_probes(exe_path: str, process_name: str) -> SilentExitReport:
    findings: List[SilentExitFinding] = []
    verified: List[str] = []

    # Неприменимую пробу нельзя записывать в «проверено и в норме»: без пути к
    # exe мы не знаем, на месте ли он, и обязаны молчать об этом.
    checks: list[tuple[str, object]] = []
    if exe_path:
        checks.append(("файлы программы на месте", lambda: _probe_executable_missing(exe_path)))
    checks.append(("файлы WinDivert на месте", _probe_windivert_files_missing))
    if process_name:
        checks.append(
            (
                "в журнале Windows нет записи об аварийном завершении",
                lambda: _probe_application_error_event(process_name),
            )
        )
    if exe_path:
        checks.append(
            ("Defender по файлам программы не срабатывал", lambda: _probe_defender_detection(exe_path))
        )
    checks.append(("сторонний антивирус не обнаружен", _probe_antivirus_present))

    for verified_text, probe in checks:
        try:
            finding = probe()
        except Exception as exc:
            # Не смогли проверить — эта проверка просто не существует в отчёте.
            log(f"Silent exit probe '{verified_text}' failed: {exc}", "DEBUG")
            continue
        if finding is PROBE_UNAVAILABLE:
            continue
        if finding is None:
            verified.append(verified_text)
        else:
            findings.append(finding)

    return SilentExitReport(findings=tuple(findings), verified=tuple(verified))


def probe_silent_exit(*, exe_path: str, process_name: str = "") -> SilentExitReport:
    """Собирает факты о молчаливом отказе. Никогда не бросает исключений."""
    global _cached_report, _cached_key, _cached_at

    path = str(exe_path or "").strip()
    name = str(process_name or "").strip() or os.path.basename(path)
    key = (path, name)

    now = time.monotonic()
    if _cached_report is not None and _cached_key == key and now - _cached_at < _PROBE_CACHE_TTL_SECONDS:
        return _cached_report

    try:
        report = _run_probes(path, name)
    except Exception as exc:
        log(f"Silent exit probe failed: {exc}", "DEBUG")
        report = SilentExitReport()

    _cached_report = report
    _cached_key = key
    _cached_at = now
    return report


def format_silent_exit_message(
    report: SilentExitReport,
    *,
    exe_name: str,
    exit_code,
    lifetime_seconds: float | None = None,
) -> str:
    """Строит текст для пользователя строго по тому, что удалось установить."""
    executable = str(exe_name or "winws").strip() or "winws"
    try:
        code = int(exit_code)
    except (TypeError, ValueError):
        code = -1

    lifetime = ""
    if lifetime_seconds is not None:
        try:
            lifetime = f", прожив {float(lifetime_seconds):.1f} с"
        except (TypeError, ValueError):
            lifetime = ""

    base = f"{executable} завершился с кодом {code}{lifetime}, не выдав ни одного сообщения"

    confirmed = report.confirmed if report is not None else ()
    if confirmed:
        finding = confirmed[0]
        message = f"{base}. Найдена причина: {finding.fact}"
        if finding.solution:
            message = f"{message}. Что сделать: {finding.solution}"
        return message

    checked = ", ".join(report.verified) if report is not None and report.verified else ""
    suspected = report.suspected if report is not None else ()
    if suspected:
        finding = suspected[0]
        message = base
        if checked:
            message = f"{message}. Проверено: {checked}"
        message = f"{message}. Вероятная причина: {finding.fact}"
        if finding.solution:
            message = f"{message}. Что сделать: {finding.solution}"
        return message

    message = f"{base}. Причина не установлена"
    if checked:
        message = f"{message} — проверено: {checked}"
    return (
        f"{message}. Полный вывод winws и диагностика сохранены в логе программы. "
        "Что сделать: попробуйте запустить ещё раз, а при повторе пришлите лог разработчику"
    )
