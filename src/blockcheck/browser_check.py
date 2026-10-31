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
открылось; «Page load failed: net::ERR_…» — не открылось и почему;
«Page load timed out» — не дождались.

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

__all__ = ["BROWSER_FAILED", "BROWSER_MISSING", "BROWSER_OK", "BROWSER_TIMEOUT", "BrowserVisit", "find_browser", "open_in_browser"]

BROWSER_OK = "ok"
BROWSER_FAILED = "failed"
BROWSER_TIMEOUT = "timeout"
BROWSER_MISSING = "missing"

# Сколько ждать загрузки страницы и сколько сверху — на запуск и выход браузера.
PAGE_TIMEOUT_S = 20.0
START_EXTRA_S = 15.0

_NET_ERROR = re.compile(r"Page load failed: net::(ERR_[A-Z0-9_]+)")
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

    @property
    def ok(self) -> bool:
        return self.state == BROWSER_OK


def find_browser() -> str:
    """Путь к Edge или Chrome; пусто — браузера нет."""
    for candidate in _CANDIDATES:
        path = os.path.expandvars(candidate)
        if "%" not in path and os.path.isfile(path):
            return path
    return ""


def browser_command(browser: str, url: str, profile_dir: str, *, quic: bool) -> list[str]:
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
    ]
    if not quic:
        command.append("--disable-quic")
    command += ["--dump-dom", url]
    return command


def judge_output(output: str, browser: str) -> BrowserVisit:
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
    return BrowserVisit(BROWSER_FAILED, "", "браузер ничего не показал", name)


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


def open_in_browser(
    host: str,
    *,
    quic: bool = False,
    path: str = "/",
    cancelled: Callable[[], bool] = lambda: False,
    popen=_hidden_popen,
    browser: str | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> BrowserVisit:
    """Открыть ``https://host/path`` в настоящем браузере без окна и сказать, что вышло.

    Вывод браузера пишется в файл, а не в трубу: страница бывает большой, и полная
    труба остановила бы браузер. «Стоп» (``cancelled``) гасит браузер сразу.
    """
    browser = find_browser() if browser is None else browser
    if not browser:
        return BrowserVisit(BROWSER_MISSING, text="на компьютере нет ни Edge, ни Chrome")
    name = ntpath.basename(browser)
    profile_dir = tempfile.mkdtemp(prefix="zapret-browser-check-")
    output_path = os.path.join(profile_dir, "output.txt")
    job = None
    process = None
    started = clock()
    try:
        with open(output_path, "wb") as output:
            process = popen(browser_command(browser, f"https://{host}{path or '/'}", profile_dir, quic=quic), output)
            job = _kill_on_close_job(process)
            deadline = clock() + PAGE_TIMEOUT_S + START_EXTRA_S
            while process.poll() is None:
                if cancelled():
                    return BrowserVisit(BROWSER_TIMEOUT, "", "проверку остановили", name)
                if clock() >= deadline:
                    return BrowserVisit(BROWSER_TIMEOUT, "", "браузер не ответил вовремя", name)
                time.sleep(0.2)
        with open(output_path, "rb") as output:
            visit = judge_output(output.read().decode("utf-8", errors="replace"), browser)
        return replace(visit, elapsed_ms=(clock() - started) * 1000)
    except OSError as error:
        return BrowserVisit(BROWSER_MISSING, text=f"браузер не запустился: {error}")
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
        shutil.rmtree(profile_dir, ignore_errors=True)
