"""Итог проверки DNS-серверов для человека: одна фраза, пояснение и находки.

Выводы делает ``dns.server_check.judge_report``; здесь они только
раскладываются для показа: короткий заголовок отдельно, подробности отдельно.
Чистые функции над ``ServerCheckReport``: ни сети, ни Qt.
"""

from __future__ import annotations

from dataclasses import dataclass

from dns.server_check import (
    CODE_BYPASS_RUNNING,
    CODE_DEAD,
    CODE_INTERCEPTED,
    CODE_SPOOFED,
    CODE_UNSTABLE,
    LEVEL_FAIL,
    LEVEL_WARN,
    STATE_FAIL,
    STATE_OK,
    TRANSPORT_ICMP,
    Finding,
    Observation,
    ServerCheckReport,
)

KIND_OK = "ok"
KIND_NOTES = "notes"
KIND_WARN = "warn"
KIND_FAIL = "fail"
KIND_STOPPED = "stopped"
KIND_EMPTY = "empty"

# Заголовки находок, у которых начало текста для заголовка не годится.
_TITLES = {
    CODE_BYPASS_RUNNING: "Проверка шла вместе с программами обхода",
    CODE_INTERCEPTED: "Обычные DNS-запросы перехватываются по дороге",
}
# Текст таких находок остаётся в подробностях целиком.
_KEEP_WHOLE = frozenset({CODE_BYPASS_RUNNING})


@dataclass(frozen=True, slots=True)
class VerdictItem:
    level: str
    title: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Tally:
    """Сколько адресов отвечает и сколько молчит — для счётчиков хода проверки."""

    good: int = 0
    silent: int = 0


@dataclass(frozen=True, slots=True)
class Verdict:
    kind: str
    title: str
    detail: str = ""
    items: tuple[VerdictItem, ...] = ()
    # Поможет ли шифрованный DNS: тогда уместна кнопка в «Настройку DNS».
    suggests_encrypted_dns: bool = False


def _sentence(text: str) -> str:
    text = text.strip()
    return text[:1].upper() + text[1:]


def split_finding(finding: Finding) -> VerdictItem:
    """Находка пишется как «что случилось: подробности» — делим её по первому двоеточию."""
    if finding.code in _KEEP_WHOLE:
        return VerdictItem(finding.level, _TITLES[finding.code], finding.text)
    head, separator, tail = finding.text.partition(": ")
    title = _TITLES.get(finding.code) or head.rstrip(".")
    return VerdictItem(finding.level, title, _sentence(tail) if separator else "")


def _answers(row: Observation) -> bool:
    return any(cell.state == STATE_OK for transport, cell in row.cells if transport != TRANSPORT_ICMP)


def _is_silent(row: Observation) -> bool:
    cells = [cell for transport, cell in row.cells if transport != TRANSPORT_ICMP]
    return any(cell.state == STATE_FAIL for cell in cells) and not _answers(row)


def tally(report: ServerCheckReport) -> Tally:
    """Сколько адресов уже ответило и сколько молчит — для счётчиков, пока проверка идёт."""
    return Tally(
        good=sum(1 for row in report.rows if _answers(row)),
        silent=sum(1 for row in report.rows if _is_silent(row)),
    )


def build_verdict(report: ServerCheckReport) -> Verdict:
    """Главная фраза итога. Вызывать для законченной проверки."""
    done, total = len(report.rows), report.total
    if report.stopped:
        return Verdict(
            KIND_STOPPED,
            "Проверка остановлена",
            f"В карточках ниже — то, что успели узнать: {done} из {total} адресов. Выводы делаются только по полной проверке.",
        )
    if not report.rows:
        return Verdict(KIND_EMPTY, "Проверять нечего", "В списке нет ни одного DNS-сервера.")

    items = tuple(split_finding(finding) for finding in report.findings)
    codes = {finding.code for finding in report.findings}
    levels = {finding.level for finding in report.findings}
    if CODE_INTERCEPTED in codes:
        return Verdict(
            KIND_FAIL,
            "Обычный DNS перехватывают — нужен шифрованный",
            "На запросы без шифрования отвечает не выбранный вами сервер, а посредник: провайдер или роутер. "
            "Шифрованный DNS он не может ни прочитать, ни подменить.",
            items,
            suggests_encrypted_dns=True,
        )
    if CODE_SPOOFED in codes:
        return Verdict(
            KIND_FAIL,
            "Ответы обычного DNS подменяют",
            "Про часть сайтов без шифрования приходит неправда: «сайта нет» или адрес заглушки. "
            "Шифрованный DNS отдаёт настоящие адреса.",
            items,
            suggests_encrypted_dns=True,
        )
    if LEVEL_WARN in levels or LEVEL_FAIL in levels:
        only_shaky = all(
            finding.code == CODE_UNSTABLE for finding in report.findings if finding.level in (LEVEL_WARN, LEVEL_FAIL)
        )
        return Verdict(
            KIND_WARN,
            "Часть серверов отвечает через раз" if only_shaky else "Часть способов связи закрыта",
            "Подмены не видно, но не всякий сервер и не всяким способом доступен. "
            "Выбирайте серверы с пометкой «Работает».",
            items,
        )
    if codes & {CODE_DEAD, CODE_UNSTABLE}:
        return Verdict(
            KIND_NOTES,
            "Серьёзных проблем нет",
            "Ответы никто не перехватывает и не подменяет. Несколько серверов недоступны — их просто не выбирайте.",
            items,
        )
    return Verdict(
        KIND_OK,
        "С DNS всё в порядке 👍",
        "Ответы никто не перехватывает и не подменяет, шифрованный DNS доступен.",
        items,
    )


__all__ = [
    "KIND_EMPTY",
    "KIND_FAIL",
    "KIND_NOTES",
    "KIND_OK",
    "KIND_STOPPED",
    "KIND_WARN",
    "Tally",
    "Verdict",
    "VerdictItem",
    "build_verdict",
    "split_finding",
    "tally",
]
