"""Итоговая проверка в настоящем браузере: Edge (или Chrome) без окна открывает сайт.

Подбор и BlockCheck открывают сайты запросом «как Chrome» (``utils.chrome_https``):
тот же отпечаток рукопожатия, но это всё же не браузер. После «Применить»
сайт открывается ещё и настоящим браузером, который стоит на компьютере:
своим DNS, своим стеком и своими заголовками — ровно так, как его откроет
человек. Edge есть на любой Windows 10/11; нет его — берётся Chrome; нет
ни того, ни другого — проверки нет, и об этом говорится прямо.

Браузер запускается без окна и с пустым временным профилем: ни истории, ни
расширений, ни своих фоновых запросов (синхронизация, обновления
компонентов). Итог читается из того, что печатает сам браузер: страница —
открылось; «Page load failed: net::ERR_…» — не открылось и почему (эти строки
браузер пишет в свой журнал, поэтому он включается ключом ``--enable-logging=stderr``);
«Page load timed out» — не дождались. Ни страницы, ни ошибки — браузер не ответил
(упал, его выключила политика, напечатал что-то незнакомое): это сбой самой
проверки, а не сайта, и код выхода с последней строкой вывода идут в журнал.

QUIC (UDP 443) отключается, когда проверяется обычное соединение: иначе
браузер ушёл бы на QUIC, и проверка была бы не о той стратегии.
"""

from __future__ import annotations

import ntpath
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

__all__ = [
    "BROWSER_FAILED",
    "BROWSER_MISSING",
    "BROWSER_NO_ANSWER",
    "BROWSER_OK",
    "BROWSER_TIMEOUT",
    "BrowserVisit",
    "browser_title",
    "find_browser",
    "find_browsers",
    "open_in_browser",
]

BROWSER_OK = "ok"
BROWSER_FAILED = "failed"
BROWSER_TIMEOUT = "timeout"
BROWSER_MISSING = "missing"
# Браузер завершился без страницы и без ошибки: о сайте он ничего не сказал.
BROWSER_NO_ANSWER = "no_answer"

# Сколько ждать загрузки страницы и сколько сверху — на запуск и выход браузера.
PAGE_TIMEOUT_S = 20.0
START_EXTRA_S = 15.0
# Сколько ждать, пока браузер, напечатавший итог, выйдет сам и допишет журнал сети.
VERDICT_EXIT_S = 3.0

_NET_ERROR = re.compile(r"Page load failed: net::(ERR_[A-Z0-9_]+)")
# Сколько знаков последней строки вывода сохранять для журнала.
DETAIL_CHARS = 200
_TIMED_OUT = "Page load timed out"

# Где искать: сначала Edge (есть всегда), потом Chrome.
_CANDIDATES = (
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
)

# Понятные слова к кодам ошибок браузера.
_ERROR_TEXT = {
    "ERR_CONNECTION_RESET": "соединение сброшено",
    "ERR_CONNECTION_CLOSED": "соединение закрыто без ответа",
    "ERR_CONNECTION_REFUSED": "сервер отказал в подключении",
    "ERR_CONNECTION_TIMED_OUT": "сервер не отвечает",
    "ERR_TIMED_OUT": "сервер не отвечает",
    "ERR_SSL_PROTOCOL_ERROR": "ошибка шифрования",
    "ERR_CERT_COMMON_NAME_INVALID": "сертификат выдан другому сайту",
    "ERR_CERT_AUTHORITY_INVALID": "сертификат выдан неизвестным центром",
    "ERR_NAME_NOT_RESOLVED": "адрес сайта не найден",
    "ERR_ADDRESS_UNREACHABLE": "до адреса нет дороги",
    "ERR_EMPTY_RESPONSE": "сервер ничего не ответил",
}


@dataclass(frozen=True, slots=True)
class BrowserVisit:
    state: str
    # Код ошибки браузера (ERR_…) — для журнала; ``text`` — для человека.
    error: str = ""
    text: str = ""
    browser: str = ""
    # Сколько прошло от запуска браузера до итога: по разнице с QUIC и без него видна цена отката (``diagnostics.quic_fallback``).
    elapsed_ms: float = 0.0
    # Для журнала: код выхода и последняя строка вывода, когда браузер не ответил.
    details: str = ""
    # Факты из журнала сети браузера (``browser_netlog.NetFacts``), если его просили писать.
    net: object = None

    @property
    def title(self) -> str:
        """Имя браузера для человека: Edge, Chrome."""
        return browser_title(self.browser)

    @property
    def ok(self) -> bool:
        return self.state == BROWSER_OK


def find_browsers() -> list[str]:
    """Пути ко всем найденным браузерам: Edge первым, потом Chrome; по одному пути на браузер."""
    found: list[str] = []
    for candidate in _CANDIDATES:
        path = os.path.expandvars(candidate)
        if "%" in path or not os.path.isfile(path):
            continue
        if ntpath.basename(path).lower() not in {ntpath.basename(known).lower() for known in found}:
            found.append(path)
    return found


def find_browser() -> str:
    """Путь к Edge или Chrome; пусто — браузера нет."""
    browsers = find_browsers()
    return browsers[0] if browsers else ""


def error_text(code: str) -> str:
    """Ошибка браузера (ERR_…) словами; незнакомая — как есть."""
    return _ERROR_TEXT.get(code, code)


def browser_title(name: str) -> str:
    return {"msedge.exe": "Edge", "chrome.exe": "Chrome"}.get(str(name or "").lower(), str(name or "браузер"))


def browser_command(browser: str, url: str, profile_dir: str, *, quic: bool, extra: tuple[str, ...] = (), net_log: str = "") -> list[str]:
    command = [
        browser,
        "--headless=new",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-sync",
        "--disable-background-networking",
        "--disable-component-update",
        f"--user-data-dir={profile_dir}",
        f"--timeout={int(PAGE_TIMEOUT_S * 1000)}",
        # «Page load failed» и «Page load timed out» браузер пишет в свой журнал, а он без этого
        # ключа выключен: ошибка сети выглядела бы как молчание. Уровень 2 — только ошибки.
        "--enable-logging=stderr",
        "--log-level=2",
    ]
    if not quic:
        command.append("--disable-quic")
    if net_log:
        # Журнал сети: к какому адресу шёл браузер, прятал ли имя (ECH), через какой DNS и прокси (``browser_netlog``).
        command.append(f"--log-net-log={net_log}")
    command += [*extra, "--dump-dom", url]
    return command


def _last_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1][:DETAIL_CHARS] if lines else ""


def no_answer_details(output: str, exit_code: int | None) -> str:
    """Что сказать в журнал о браузере, который не ответил: код выхода и последняя строка вывода."""
    if exit_code is None:
        code = "неизвестен"
    else:
        # Падение Windows отдаёт код вида 0xC0000005: так его и показываем.
        code = f"0x{exit_code & 0xFFFFFFFF:08X}" if exit_code < 0 or exit_code > 0xFFFF else str(exit_code)
    line = _last_line(output)
    return f"код выхода {code}; " + (f"последняя строка: {line}" if line else "вывод пуст")


def judge_output(output: str, browser: str, exit_code: int | None = None) -> BrowserVisit:
    """Итог по тому, что напечатал браузер."""
    name = ntpath.basename(browser)
    match = _NET_ERROR.search(output)
    if match:
        code = match.group(1)
        return BrowserVisit(BROWSER_FAILED, code, _ERROR_TEXT.get(code, code), name)
    if _TIMED_OUT in output:
        return BrowserVisit(BROWSER_TIMEOUT, "", f"страница не загрузилась за {PAGE_TIMEOUT_S:g} с", name)
    if "<html" in output.lower():
        return BrowserVisit(BROWSER_OK, browser=name)
    details = no_answer_details(output, exit_code)
    return BrowserVisit(BROWSER_NO_ANSWER, "", "браузер завершился, ничего не сказав о сайте", name, details=details)


def _hidden_popen(command: list[str], output) -> subprocess.Popen:
    from winws_runtime.public import CREATE_NO_WINDOW

    return subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)


def _kill_on_close_job(process: subprocess.Popen):
    """Браузер запускает дочерние процессы: задание Windows гасит их вместе с ним."""
    from winws_runtime.engine import winapi

    handle = getattr(process, "_handle", None)
    if not handle or not winapi.is_available():
        return None
    job = None
    try:
        job = winapi.create_kill_on_close_job()
        winapi.assign_process_to_job(job, int(handle))
        return job
    except Exception:
        # Задание создалось, а браузер в него не сел (уже завершился):
        # его дескриптор закрываем, иначе он висит до выхода программы.
        if job is not None:
            winapi.close_handle(job)
        return None


def _read_output(path: str) -> str:
    with open(path, "rb") as output:
        return output.read().decode("utf-8", errors="replace")


def _has_verdict(output: str) -> bool:
    """Браузер уже сказал, что страница не открылась или не дождалась."""
    return bool(_NET_ERROR.search(output)) or _TIMED_OUT in output


def open_in_browser(
    host: str,
    *,
    quic: bool = False,
    path: str = "/",
    cancelled: Callable[[], bool] = lambda: False,
    popen=_hidden_popen,
    browser: str | None = None,
    clock: Callable[[], float] = time.monotonic,
    extra: tuple[str, ...] = (),
    net_log: bool = False,
) -> BrowserVisit:
    """Открыть ``https://host/path`` в настоящем браузере без окна и сказать, что вышло.

    Вывод браузера пишется в файл, а не в трубу: страница бывает большой, и полная
    труба остановила бы браузер. «Стоп» (``cancelled``) гасит браузер сразу.
    ``extra`` — ключи, которыми это открытие отличается от обычного (``browser_diagnosis``);
    ``net_log`` — записать журнал сети и вернуть факты из него в ``BrowserVisit.net``.
    """
    browser = find_browser() if browser is None else browser
    if not browser:
        return BrowserVisit(BROWSER_MISSING, text="на компьютере нет ни Edge, ни Chrome")
    name = ntpath.basename(browser)
    profile_dir = tempfile.mkdtemp(prefix="zapret-browser-check-")
    output_path = os.path.join(profile_dir, "output.txt")
    net_path = os.path.join(profile_dir, "net.json") if net_log else ""
    job = None
    process = None
    visit = None
    started = clock()
    try:
        with open(output_path, "wb") as output:
            command = browser_command(browser, f"https://{host}{path or '/'}", profile_dir, quic=quic, extra=tuple(extra), net_log=net_path)
            process = popen(command, output)
            job = _kill_on_close_job(process)
            deadline = clock() + PAGE_TIMEOUT_S + START_EXTRA_S
            while process.poll() is None:
                if cancelled():
                    visit = BrowserVisit(BROWSER_TIMEOUT, "", "проверку остановили", name)
                    break
                # Edge, напечатав «Page load failed», бывает, не выходит: итог берётся, как только он напечатан,
                # а браузеру даётся пара секунд выйти самому — иначе журнал сети останется без последних записей.
                if _has_verdict(_read_output(output_path)):
                    _wait_exit(process, VERDICT_EXIT_S)
                    break
                if clock() >= deadline:
                    # Браузер не сказал ничего — ни страницы, ни ошибки: о сайте это ничего не говорит.
                    last = _last_line(_read_output(output_path))
                    details = f"не завершился за {PAGE_TIMEOUT_S + START_EXTRA_S:g} с; " + (f"последняя строка: {last}" if last else "вывод пуст")
                    visit = BrowserVisit(BROWSER_NO_ANSWER, "", "браузер не ответил", name, details=details)
                    break
                time.sleep(0.2)
        if visit is None:
            visit = replace(judge_output(_read_output(output_path), browser, process.poll()), elapsed_ms=(clock() - started) * 1000)
    except OSError as error:
        visit = BrowserVisit(BROWSER_MISSING, text=f"браузер не запустился: {error}")
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        if job is not None:
            from winws_runtime.engine import winapi

            try:
                winapi.close_handle(job)
            except Exception:
                pass
    try:
        if net_path and visit.state != BROWSER_MISSING:
            from blockcheck import browser_netlog

            visit = replace(visit, net=browser_netlog.read_netlog(net_path, host))
        return visit
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)


def _wait_exit(process, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
