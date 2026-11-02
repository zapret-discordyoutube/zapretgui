"""Проверка сайта после «Применить» — уже через настоящий пресет.

Подбор проверял стратегию в своём маленьком профиле. В пресете пользователя
рядом другие профили: выше может стоять общий, который перехватит сайт, фейки
могут быть объявлены иначе, перехват WinDivert — шире. Поэтому, когда Zapret
запустился с новой стратегией, сайт открывается ещё раз тем же способом, что
и в подборе (те же адреса из разных подсетей и проверка объёма), и человек
сразу видит, работает ли стратегия у него, а не «проверьте сами».

Открылся — сайт открывает ещё и настоящий браузер без окна (Edge или Chrome,
``blockcheck.browser_check``): своим DNS и своим стеком, как его откроет человек.

Браузер не открыл — программа не гадает, а ищет причину делом
(``blockcheck.browser_diagnosis``): по журналу сети неудачного открытия видно,
чем браузер отличался от пробы (ECH, другой адрес, свой DNS, прокси, части
страницы с других имён), и сайт открывается браузером ещё раз с одним
изменением. Открылся — причина доказана, и человеку говорится, что сделать.
Так было с www.castingcall.club на win10: Edge повторил приветствие с ECH.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace

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
# Браузер не открыл сайт, а с изменением (``fixes``) открыл: причина доказана.
CHECK_BROWSER_FIXED = "browser_fixed"
# До сайта браузер достучался, а страница ждала части с других имён (``hosts``), и они не открылись.
CHECK_BROWSER_PARTS = "browser_parts"


@dataclass(frozen=True, slots=True)
class ApplyCheck:
    code: str
    text: str = ""
    # Адреса сайта, которые проба открыла через пресет.
    addresses: tuple[str, ...] = ()
    # Браузер для человека (Edge, Chrome) и найденное им: что помогло, куда он шёл сам, через какой прокси, какие имена не открылись.
    browser: str = ""
    fixes: tuple[str, ...] = ()
    address: str = ""
    probe_address: str = ""
    proxy: str = ""
    hosts: tuple[str, ...] = ()
    secure_dns: bool = False
    # Изменения, с которыми браузер открывал сайт ещё раз (``browser_diagnosis.FIX_*``).
    tried: tuple[str, ...] = ()


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
        return ApplyCheck(CHECK_OK, "объём не проверен: сайт отвечает коротко" if unknown else "", addresses=tuple(picked))
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


def _browser_visits(host: str, *, cancel, open_browser):
    """Открытия сайта браузером, пока какое-то не скажет о сайте: молчащий браузер — ещё раз, затем другой браузер.

    Первое открытие каждым браузером пишет журнал сети: если сайт не откроется, по нему ищется причина.
    """
    from blockcheck import browser_check

    if open_browser is not None:
        attempts = [lambda: open_browser(host, cancelled=lambda: cancel.cancelled, net_log=True)] * 2
    else:
        attempts = [
            lambda path=path: browser_check.open_in_browser(host, cancelled=lambda: cancel.cancelled, browser=path, net_log=True)
            for path in browser_check.find_browsers()
            for _ in range(2)
        ]
    visits = []
    for attempt in attempts:
        visits.append(attempt())
        # Молчащий браузер повторяется, но не тот, чей журнал сети уже показал, что соединения с сайтом не прошли.
        if cancel.cancelled or visits[-1].state != browser_check.BROWSER_NO_ANSWER or _site_failed_in_log(visits[-1], host):
            break
    return visits


def _site_failed_in_log(visit, host: str) -> str:
    """Браузер молчал, а журнал сети показал соединения с сайтом, и ни одно не прошло: причина словами; пусто — не так."""
    from blockcheck import browser_check, browser_netlog

    connections = visit.net.for_host(host) if visit.net is not None else ()
    if not connections or any(connection.ok and connection.received for connection in connections):
        return ""
    errors = [connection.error for connection in connections if connection.error and connection.error != browser_netlog.HUNG]
    if errors:
        return browser_check.error_text(errors[-1])
    return "соединения с сайтом не ответили"


def _confirm_in_browser(host: str, check: ApplyCheck, *, cancel, open_browser=None) -> ApplyCheck:
    """Проба открыла сайт — подтверждает настоящий браузер; не открыл — ищется причина. Нет браузера — итог пробы.

    Браузер, который завершился, ничего не сказав о сайте, проверку не прошёл и не провалил: так молчит
    и Edge, у которого сайт не открылся (на win10 он молча выходил и на несуществующем имени). Поэтому
    это отдельный итог — «проверить в браузере не вышло».
    """
    from blockcheck import browser_check, browser_diagnosis

    visits = _browser_visits(host, cancel=cancel, open_browser=open_browser)
    for visit in visits:
        if visit.details:
            log(f"Проверка {host} в браузере {visit.browser}: {visit.state}, {visit.details}", "INFO")
    if cancel.cancelled:
        return ApplyCheck(CHECK_SKIPPED, "проверку остановили")
    if not visits or visits[-1].state == browser_check.BROWSER_MISSING:
        return check
    visit = visits[-1]
    found = {"addresses": check.addresses, "browser": visit.title}
    if visit.ok:
        return ApplyCheck(CHECK_OK, check.text, **found)
    if visit.state == browser_check.BROWSER_NO_ANSWER:
        failed_in_log = _site_failed_in_log(visit, host)
        if not failed_in_log:
            return ApplyCheck(CHECK_BROWSER_UNKNOWN, visit.text, **found)
        # Браузер не сказал, а журнал показал: до сайта он не достучался. Это неудача с фактами — ищется причина.
        visit = replace(visit, state=browser_check.BROWSER_FAILED, text=failed_in_log)

    browser_path = next((path for path in browser_check.find_browsers() if path.lower().endswith(visit.browser.lower())), None)

    def again(args: tuple[str, ...]):
        if open_browser is not None:
            return open_browser(host, cancelled=lambda: cancel.cancelled, extra=args, net_log=True)
        return browser_check.open_in_browser(host, cancelled=lambda: cancel.cancelled, browser=browser_path, extra=args, net_log=True)

    result = browser_diagnosis.diagnose(host, visit, probe_addresses=check.addresses, visit=again, cancelled=lambda: cancel.cancelled)
    log(f"Поиск причины для {host} в браузере {visit.browser}: {result.code}, изменения {result.fixes}, пробовали {result.tried}", "INFO")
    if cancel.cancelled:
        return ApplyCheck(CHECK_SKIPPED, "проверку остановили")
    found.update(
        address=result.address, probe_address=result.probe_address, proxy=result.proxy, secure_dns=result.secure_dns, tried=result.tried_fixes
    )
    if result.code == browser_diagnosis.DIAG_FIXED:
        return ApplyCheck(CHECK_BROWSER_FIXED, visit.text, fixes=result.fixes, **found)
    if result.code == browser_diagnosis.DIAG_PARTS:
        return ApplyCheck(CHECK_BROWSER_PARTS, visit.text, hosts=result.hosts, **found)
    if result.code == browser_diagnosis.DIAG_UNCLEAR:
        return ApplyCheck(CHECK_BROWSER_UNKNOWN, visit.text, **found)
    return ApplyCheck(CHECK_BROWSER_FAILED, result.reason or visit.text, **found)


class StrategyApplyCheckWorker(QThread):
    """Фоновая проверка после «Применить»: итог приходит сигналом в поток окна."""

    completed = pyqtSignal(str, object)  # host, ApplyCheck

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
        self.completed.emit(self._host, check)


# Что сделать человеку, когда причина в браузере доказана (``browser_diagnosis.FIX_*``).
def _step_ech() -> str:
    from diagnostics.filter_habits import ECH_OFF_STEPS

    return f"выключите в своём браузере ECH: {ECH_OFF_STEPS}"


STEP_SECURE_DNS = (
    "выключите в своём браузере защищённый DNS: в его настройках найдите поиском «DNS» и выключите "
    "«Использовать безопасный DNS» (в Firefox — «DNS через HTTPS» → «Выкл.»)"
)
STEP_PROXY = "выключите прокси: в Параметрах Windows найдите поиском «прокси» и выключите прокси-сервер — или добавьте сайт в исключения прокси"
STEP_IPV6 = "в свойствах сетевого подключения Windows снимите галочку «IP версии 6 (TCP/IPv6)»"
STEP_OTHER_ADDRESS = "запустите подбор ещё раз: сайт отдаёт браузеру и другие адреса, и стратегия должна открывать их тоже"


def _fix_lines(check: ApplyCheck) -> tuple[list[str], list[str]]:
    """(что изменили — сайт открылся; что сделать) по доказанным причинам."""
    from blockcheck import browser_diagnosis as bd

    changes: list[str] = []
    steps: list[str] = []
    for fix in check.fixes:
        if fix == bd.FIX_ECH:
            changes.append("браузер пошёл на тот же адрес, но без ECH (так браузер прячет имя сайта; с ECH соединение не проходит)")
            steps.append(_step_ech())
        elif fix == bd.FIX_ADDRESS:
            own = f" (сам браузер шёл на {check.address})" if check.address else ""
            changes.append(f"браузеру дали адрес {check.probe_address}, который проверила программа{own}")
            if ":" in check.address:
                steps.append(STEP_IPV6)
            elif check.secure_dns:
                # Другой адрес браузеру дал его шифрованный DNS: так по журналу сети.
                steps.append(STEP_SECURE_DNS)
            else:
                steps.append(STEP_OTHER_ADDRESS)
        elif fix == bd.FIX_PROXY:
            via = f" {check.proxy}" if check.proxy else ""
            changes.append(f"браузер пошёл напрямую, без прокси{via} из настроек Windows")
            steps.append(STEP_PROXY)
    return changes, list(dict.fromkeys(steps))


def _tried_text(check: ApplyCheck) -> str:
    from blockcheck import browser_diagnosis as bd

    words = {
        bd.FIX_ECH: "без ECH",
        bd.FIX_ADDRESS: f"по адресу {check.probe_address} из проверки" if check.probe_address else "по адресу из проверки",
        bd.FIX_PROXY: "без прокси",
    }
    return ", ".join(words[fix] for fix in check.tried if fix in words)


def apply_check_message(host: str, check: ApplyCheck) -> tuple[str, str, str] | None:
    """(вид, заголовок, текст) уведомления; None — сказать нечего.

    Каждый текст говорит, что проверено, что нашлось и что сделать дальше.
    """
    code, text, browser = check.code, check.text, check.browser or "браузер"
    probe = "проба программы через ваш пресет сайт открыла"
    why = f" ({text})" if text else ""
    if code == CHECK_OK:
        checked = f"Проверено пробой программы и браузером {check.browser}." if check.browser else "Проверено пробой программы."
        note = f" {text[:1].upper()}{text[1:]}." if text else ""
        return "success", "Стратегия работает", f"{host} открывается через ваш пресет. {checked}{note} Можно пользоваться."
    if code == CHECK_FAILED:
        return (
            "warning",
            "В вашем пресете сайт не открылся",
            f"{host}: {text or 'не открылся'}. В подборе эта стратегия сайт открывала, а в вашем пресете — нет. "
            "Скорее всего, сайт забирает другой профиль, который стоит в пресете выше. Что сделать: на странице "
            "«Настройка пресета» откройте «Порядок в пресете», перетащите профиль сайта выше остальных и снова нажмите "
            "«Применить». Не помогло — запустите подбор ещё раз.",
        )
    if code == CHECK_BROWSER_FIXED:
        changes, steps = _fix_lines(check)
        if len(changes) == 1:
            found = f"Программа открыла сайт браузером ещё раз — и он открылся, когда {changes[0]}."
        else:
            found = (
                "Программа открывала сайт браузером ещё раз, меняя по одному, — не помогло; открылся, только когда "
                f"изменили всё сразу: {'; '.join(changes)}."
            )
        return (
            "warning",
            "Найдено, почему браузер не открывал сайт",
            f"{host}: {probe}, а {browser} без окна — нет{why}. {found} Что сделать: {'; '.join(steps)}. "
            "Затем откройте сайт в своём браузере.",
        )
    if code == CHECK_BROWSER_PARTS:
        names = ", ".join(check.hosts[:3]) + (" и другие" if len(check.hosts) > 3 else "")
        return (
            "warning",
            "Сайт открывается не целиком",
            f"{host}: {probe}, и {browser} до сайта достучался, но страница не загрузилась: она ждёт части с других "
            f"имён — {names}, а они не открываются. Что сделать: на вкладке «Подбор стратегии» подберите стратегию "
            f"для {check.hosts[0]} и нажмите «Применить».",
        )
    if code == CHECK_BROWSER_FAILED:
        return (
            "warning",
            "В браузере сайт не открылся",
            f"{host}: {probe}, а {browser} без окна — нет{why}. "
            + (
                f"Программа открыла сайт браузером ещё раз — {_tried_text(check)} — не помогло: режется само соединение браузера. "
                if check.tried
                else ""
            )
            + "Что сделать: откройте сайт в своём браузере. Не открывается — запустите подбор ещё раз.",
        )
    if code == CHECK_BROWSER_UNKNOWN:
        if check.tried:
            body = (
                f"{host}: {probe}, а {browser} без окна — нет{why}. Программа открывала сайт браузером ещё раз — "
                f"{_tried_text(check)}, — но браузер не отвечал ни страницей, ни ошибкой, и причину найти не вышло "
                "(подробности в журнале программы)."
            )
        else:
            body = (
                f"{host}: {probe}. {browser} без окна не ответил ни страницей, ни ошибкой — о сайте это ничего не говорит "
                "(подробности в журнале программы)."
            )
        return (
            "info",
            "Проверить в браузере не вышло",
            body + " Что сделать: откройте сайт в своём браузере. Открылся — стратегия работает. Не открылся — запустите подбор ещё раз.",
        )
    return None
