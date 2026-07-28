from __future__ import annotations

"""Проверка поставки перед запуском DPI.

Раньше отсутствие движка обнаруживалось в конструкторе runner-а — в самом
конце цепочки запуска, и наружу шёл голый ``FileNotFoundError``. Теперь
проверка стоит перед запуском, знает, какой именно движок нужен выбранному
методу, и отдаёт готовое объяснение вместо «Переустановите программу».
"""

from dataclasses import dataclass, field
from typing import Any

from settings.mode import engine_for_launch_method_or_none


@dataclass(frozen=True, slots=True)
class InstallationPreflightResult:
    ok: bool
    message: str = ""
    cause: str = ""
    missing: tuple[str, ...] = ()
    report: Any = field(default=None)


_OK = InstallationPreflightResult(ok=True)


def check_installation_before_launch(launch_method: object) -> InstallationPreflightResult:
    """Быстрая проверка критичных файлов для выбранного метода запуска.

    Отсутствие манифеста (сборка без него, запуск из исходников) никогда не
    мешает запуску: проверять нечем — значит, претензий нет.
    """
    try:
        from install_integrity import describe_report, verify_fast
    except Exception:
        return _OK

    try:
        report = verify_fast()
    except Exception:
        return _OK

    if not report.checked:
        return _OK

    engine = engine_for_launch_method_or_none(launch_method)
    if not report.blocks_launch(engine):
        return _OK

    blocking = report.blocking_findings(engine)
    message = describe_report(report, repair_started=True)
    return InstallationPreflightResult(
        ok=False,
        message=message.as_line(),
        cause=report.cause.value,
        missing=tuple(finding.path for finding in blocking),
        report=report,
    )


__all__ = ["InstallationPreflightResult", "check_installation_before_launch"]
