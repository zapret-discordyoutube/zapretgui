"""Голосовые звонки: доходит ли UDP до STUN-серверов.

Discord и Telegram звонят по UDP. STUN-сервер — самый простой способ узнать,
проходит ли UDP: отправляем короткий запрос и ждём любой ответ.

Раньше «ответ пришёл, но разобрать не удалось» записывалось как «заблокирован»,
хотя сам факт ответа и значит, что UDP проходит. Здесь любой ответ — «UDP
работает», а «звонки могут не работать» пишется, только если не ответил ни
один сервер.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass

from diagnostics.verdict import Level

__all__ = ["VoiceReport", "VoiceServer", "check_voice", "summarize_voice"]

# Коды ``SingleTestResult.error_code`` из ``blockcheck.stun_tester``.
_ANSWERED_CODES = {"", "PARSE_ERR"}


@dataclass(frozen=True, slots=True)
class VoiceServer:
    name: str
    host: str
    answered: bool
    text: str


@dataclass(frozen=True, slots=True)
class VoiceReport:
    level: Level
    headline: str
    servers: tuple[VoiceServer, ...]
    advice: tuple[str, ...] = ()


def _parse_target(value: str) -> tuple[str, int]:
    raw = str(value or "")
    if raw.upper().startswith("STUN:"):
        raw = raw[5:]
    host, _, port = raw.rpartition(":")
    try:
        return host, int(port)
    except ValueError:
        return raw, 3478


def _describe(result) -> tuple[bool, str]:
    status = str(getattr(getattr(result, "status", None), "value", getattr(result, "status", "")) or "").lower()
    code = str(getattr(result, "error_code", "") or "")
    if status == "ok":
        return True, "отвечает"
    if code in _ANSWERED_CODES:
        return True, "отвечает (ответ необычный, но UDP проходит)"
    if code == "TIMEOUT":
        return False, "не ответил — UDP до него не доходит"
    if code == "DNS_ERR":
        return False, "не удалось узнать адрес сервера"
    if code == "RESET":
        return False, "сервер отклонил запрос (порт закрыт)"
    return False, str(getattr(result, "detail", "") or "ошибка проверки")


def check_voice(submit: Callable[..., Future], wait: Callable[[Future], object]) -> tuple[VoiceServer, ...]:
    """Опрашивает все STUN-серверы параллельно. ``submit``/``wait`` — от прогона."""
    from blockcheck.data_lists import STUN_TARGETS
    from blockcheck.stun_tester import test_stun

    planned = []
    for target in STUN_TARGETS:
        host, port = _parse_target(target["value"])
        planned.append((target["name"], host, submit(test_stun, host, port)))

    servers: list[VoiceServer] = []
    for name, host, future in planned:
        try:
            answered, text = _describe(wait(future))
        except Exception as exc:  # отдельный сервер не должен ронять отчёт
            answered, text = False, f"ошибка проверки ({exc})"
        servers.append(VoiceServer(name=name, host=host, answered=answered, text=text))
    return tuple(servers)


def summarize_voice(servers: tuple[VoiceServer, ...]) -> VoiceReport:
    """Итог по звонкам. Серверы Telegram судятся отдельно: если молчат только
    они, это блокировка звонков Telegram, а не «UDP вообще не проходит»."""
    if not servers:
        return VoiceReport(Level.UNKNOWN, "Голосовые серверы не проверялись", servers)
    telegram = [item for item in servers if "telegram" in f"{item.name} {item.host}".lower()]
    general = [item for item in servers if item not in telegram]
    general_ok = any(item.answered for item in general) if general else True
    telegram_ok = any(item.answered for item in telegram) if telegram else True
    strategy_advice = (
        "Подберите стратегию для звонков: «Подбор стратегии» → «Голосовые звонки Discord и Telegram (STUN)».",
    )
    if not general_ok:
        return VoiceReport(
            Level.FAIL,
            "Голосовые звонки могут не работать: UDP до голосовых серверов не проходит",
            servers,
            strategy_advice,
        )
    if not telegram_ok:
        return VoiceReport(
            Level.WARN,
            "Звонки в Telegram могут не работать: его голосовые серверы не отвечают по UDP",
            servers,
            strategy_advice,
        )
    answered = sum(1 for item in servers if item.answered)
    return VoiceReport(Level.OK, f"Голосовые звонки: UDP проходит (ответили {answered} из {len(servers)} серверов)", servers)
