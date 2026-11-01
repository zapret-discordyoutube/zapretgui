"""Проверка сайта после «Применить» — уже через настоящий пресет.

Подбор проверял стратегию в своём маленьком профиле. В пресете пользователя
рядом другие профили: выше может стоять общий, который перехватит сайт, фейки
могут быть объявлены иначе, перехват WinDivert — шире. Поэтому, когда Zapret
запустился с новой стратегией, сайт открывается ещё раз тем же способом, что
и в подборе (те же адреса из разных подсетей и проверка объёма), и человек
сразу видит, работает ли стратегия у него, а не «проверьте сами».

Открылся — сайт открывает ещё и настоящий браузер без окна (Edge или Chrome,
``blockcheck.browser_check``): своим DNS и своим стеком, как его откроет человек.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QThread, pyqtSignal

from log.log import log

# Сколько ждать, пока Zapret запустится после «Применить».
START_WAIT_SECONDS = 25.0
# Работающий Zapret перечитывает изменённый пресет сам; столько даём ему на это.
RESTART_SETTLE_SECONDS = 4.0
# После запуска — пауза, чтобы перехват точно встал.
READY_SETTLE_SECONDS = 1.5
POLL_SECONDS = 0.5

CHECK_OK = "ok"
CHECK_FAILED = "failed"
# Проверить не вышло (Zapret не запустился, адреса нет, проверку сняли).
CHECK_SKIPPED = "skipped"
# Проба сайт открыла, а настоящий браузер — нет.
CHECK_BROWSER_FAILED = "browser_failed"
# Проба сайт открыла, а браузер завершился, ничего не сказав: проверить в браузере не вышло.
CHECK_BROWSER_UNKNOWN = "browser_unknown"


@dataclass(frozen=True, slots=True)
class ApplyCheck:
    code: str
    text: str = ""


def check_companions(hosts, *, cancel) -> ApplyCheck:
    """Остальные имена сервиса (видеосервер, превью) открываются так же, как и сам сайт. ``CHECK_OK`` — все."""
    from blockcheck.strategy_search import probes
    from blockcheck.strategy_search import verdict as rules

    for host in hosts:
        addresses, error = probes.resolve_target_addresses(host, 443)
        stubs = set(probes.stub_addresses(addresses))
        picked = [address for address in addresses if address not in stubs][:2]
        if error or not picked:
            continue
        outcomes = probes.probe_https_addresses(host, picked, cancel=cancel)
        if cancel.cancelled:
            return ApplyCheck(CHECK_SKIPPED, "проверку остановили")
        if not rules.attempt_passed(outcomes):
            return ApplyCheck(CHECK_FAILED, f"{host}: {rules.attempt_reason(outcomes)}")
    return ApplyCheck(CHECK_OK)


def check_site(host: str, *, cancel, companions=()) -> ApplyCheck:
    """Открыть сайт так же, как подбор: адреса из разных подсетей, объём по соединению; затем остальные имена сервиса."""
    from blockcheck.strategy_search import probes
    from blockcheck.strategy_search import verdict as rules

    addresses, error = probes.resolve_target_addresses(host, 443)
    if error:
        return ApplyCheck(CHECK_SKIPPED, error)
    stubs = set(probes.stub_addresses(addresses))
    picked = rules.spread_addresses([address for address in addresses if address not in stubs])
    if not picked:
        return ApplyCheck(CHECK_SKIPPED, f"DNS отдаёт для {host} только адрес-заглушку")
    outcomes = probes.probe_https_addresses(host, picked, cancel=cancel)
    if rules.attempt_passed(outcomes):
        outcomes = [
            probes.probe_volume(host, address, outcome, cancel=cancel) if rules.needs_volume(outcome) else outcome
            for address, outcome in zip(picked, outcomes)
        ]
    if cancel.cancelled:
        return ApplyCheck(CHECK_SKIPPED, "проверку остановили")
    if rules.attempt_passed(outcomes):
        other = check_companions(companions, cancel=cancel)
        if other.code != CHECK_OK:
            return other
        unknown = any(outcome.volume == rules.VOLUME_UNKNOWN for outcome in outcomes)
        return ApplyCheck(CHECK_OK, "объём не проверен: сайт отвечает коротко" if unknown else "")
    return ApplyCheck(CHECK_FAILED, rules.attempt_reason(outcomes))


def wait_until_running(is_running: Callable[[], bool], *, restarting: bool, sleep, cancelled, clock=time.monotonic) -> bool:
    if restarting:
        sleep(RESTART_SETTLE_SECONDS)
    deadline = clock() + START_WAIT_SECONDS
    while clock() < deadline:
        if cancelled():
            return False
        try:
            if is_running():
                sleep(READY_SETTLE_SECONDS)
                return not cancelled()
        except Exception:
            return False
        sleep(POLL_SECONDS)
    return False


def run_apply_check(host: str, *, is_running, restarting: bool, cancel, open_browser=None, companions=()) -> ApplyCheck:
    """Дождаться Zapret и проверить сайт; при провале — ещё раз чуть позже; открылся — и в браузере.

    Второй заход нужен, если работающий Zapret перечитал пресет позже, чем мы
    начали проверку: первая проба тогда шла ещё со старым пресетом.
    """
    if not wait_until_running(is_running, restarting=restarting, sleep=cancel.sleep, cancelled=lambda: cancel.cancelled):
        return ApplyCheck(CHECK_SKIPPED, "Zapret не запустился")
    check = check_site(host, cancel=cancel, companions=companions)
    if check.code == CHECK_FAILED:
        if not cancel.sleep(RESTART_SETTLE_SECONDS):
            return ApplyCheck(CHECK_SKIPPED, "проверку остановили")
        check = check_site(host, cancel=cancel, companions=companions)
    if check.code != CHECK_OK:
        return check
    return _confirm_in_browser(host, check, cancel=cancel, open_browser=open_browser)


def _browser_visits(host: str, *, cancel, open_browser=None):
    """Открытия сайта браузером, пока какое-то не скажет о сайте: молчащий браузер — ещё раз, затем другой браузер."""
    from blockcheck import browser_check

    if open_browser is not None:
        attempts = [lambda: open_browser(host, cancelled=lambda: cancel.cancelled)] * 2
    else:
        attempts = [
            lambda path=path: browser_check.open_in_browser(host, cancelled=lambda: cancel.cancelled, browser=path)
            for path in browser_check.find_browsers()
            for _ in range(2)
        ]
    visits = []
    for attempt in attempts:
        visits.append(attempt())
        if cancel.cancelled or visits[-1].state != browser_check.BROWSER_NO_ANSWER:
            break
    return visits


def _confirm_in_browser(host: str, check: ApplyCheck, *, cancel, open_browser=None) -> ApplyCheck:
    """Проба открыла сайт — подтверждает настоящий браузер. Нет браузера — остаётся итог пробы.

    Браузер, который завершился, ничего не сказав о сайте, проверку не прошёл и не провалил: так молчит
    и Edge, у которого сайт не открылся (на win10 он молча выходил и на несуществующем имени). Поэтому
    это отдельный итог — «проверить в браузере не вышло» — с тем, что браузер вернул.
    """
    from blockcheck import browser_check

    visits = _browser_visits(host, cancel=cancel, open_browser=open_browser)
    for visit in visits:
        if visit.details:
            log(f"Проверка {host} в браузере {visit.browser}: {visit.state}, {visit.details}", "INFO")
    if cancel.cancelled:
        return ApplyCheck(CHECK_SKIPPED, "проверку остановили")
    if not visits or visits[-1].state == browser_check.BROWSER_MISSING:
        return check
    visit = visits[-1]
    if visit.ok:
        note = f"открылся и в браузере {visit.browser}"
        return ApplyCheck(CHECK_OK, f"{check.text}; {note}" if check.text else note)
    if visit.state == browser_check.BROWSER_NO_ANSWER:
        silent = list(dict.fromkeys(item.browser for item in visits))
        return ApplyCheck(CHECK_BROWSER_UNKNOWN, f"{', '.join(silent)}: {visit.text} ({visit.details})")
    return ApplyCheck(CHECK_BROWSER_FAILED, f"{visit.browser}: {visit.text}")


class StrategyApplyCheckWorker(QThread):
    """Фоновая проверка после «Применить»: итог приходит сигналом в поток окна."""

    completed = pyqtSignal(str, str, str)  # host, code, text

    def __init__(self, host: str, *, is_running, restarting: bool, companions=(), parent=None) -> None:
        super().__init__(parent)
        from utils.socket_cancel import SocketCancel

        self._host = str(host or "")
        self._is_running = is_running
        self._restarting = bool(restarting)
        self._companions = tuple(str(name) for name in companions or ())
        self._cancel = SocketCancel()

    def cancel(self) -> None:
        self._cancel.cancel()

    def run(self) -> None:
        try:
            check = run_apply_check(
                self._host, is_running=self._is_running, restarting=self._restarting, cancel=self._cancel, companions=self._companions
            )
        except Exception as error:  # проверка — подсказка, её сбой не должен ронять окно
            check = ApplyCheck(CHECK_SKIPPED, str(error))
        self.completed.emit(self._host, check.code, check.text)


def apply_check_message(host: str, code: str, text: str) -> tuple[str, str, str] | None:
    """(вид, заголовок, текст) уведомления; None — сказать нечего."""
    if code == CHECK_OK:
        body = f"{host} открывается через ваш пресет — стратегия работает."
        if text:
            body += f" ({text})"
        return "success", "Проверено в вашем пресете", body
    if code == CHECK_FAILED:
        return (
            "warning",
            "В вашем пресете сайт не открылся",
            f"{host}: {text or 'не открылся'}. В подборе стратегия работала — значит, сайт перехватывает "
            "другой профиль выше в пресете или пресет объявляет фейки иначе. Откройте пресет и поднимите "
            "профиль сайта наверх или запустите подбор ещё раз.",
        )
    if code == CHECK_BROWSER_FAILED:
        return (
            "warning",
            "В браузере сайт не открылся",
            f"{host}: проба программы через ваш пресет сайт открыла, а браузер без окна — нет ({text}). "
            "Браузер мог получить от DNS другой адрес сайта или пойти через свой прокси. Откройте сайт в "
            "браузере сами; если не открывается — запустите подбор ещё раз.",
        )
    if code == CHECK_BROWSER_UNKNOWN:
        return (
            "info",
            "Проверить в браузере не вышло",
            f"{host}: проба программы через ваш пресет сайт открыла, а браузер без окна не ответил ({text}). "
            "Это сбой проверки, а не ответ сайта. Откройте сайт в своём браузере; если не открывается — "
            "запустите подбор ещё раз.",
        )
    return None
