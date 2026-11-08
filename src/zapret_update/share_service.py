"""Раздача обновлений на этом компьютере: когда включать, как открыть и закрыть.

Раздаёт, пока выполнено всё из списка:

1. человек не выключил «Делиться обновлениями с компьютерами в моей сети»;
2. у программы есть подписанный список выпуска своей версии
   (``system/update/installed-manifest.json``): без него неизвестно, какие файлы
   можно отдавать. Он появляется после обновления по файлам;
3. компьютер сейчас в частной или доменной сети, а не в публичном Wi-Fi;
4. нашлись порты (сервер файлов — любой свободный, ответчик — свой).

Брандмауэр Windows: правило «входящие» для этой программы только из своей
подсети (``remoteip=localsubnet``) и только в частных и доменных сетях. Без
него Windows при первой раздаче показал бы человеку окно с вопросом.

Ничего здесь не бросает ошибок наружу: раздача — подарок соседям, программа
без неё работает так же.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

from .plan import HashCache
from .share_discover import DISCOVERY_PORT_GUI, Responder
from .share_serve import InstalledFiles, ShareServer

FIREWALL_RULE_NAME = "Zapret: update sharing (local network)"
FIREWALL_RULE_NAME_INTERNET = "Zapret: update sharing (internet)"
PRIVATE_CATEGORIES = frozenset({"private", "domainauthenticated"})
CREATE_NO_WINDOW = 0x08000000


def lan_allowed(profile_lines: Iterable[str]) -> bool:
    """Есть ли подключённая частная или доменная сеть.

    Строки вида ``Private:Internet`` (категория сети : связь). Публичная сеть
    сама по себе раздачу не включает; если есть и публичная, и частная, правило
    брандмауэра всё равно пускает только частную.
    """
    for line in profile_lines:
        category, _, connectivity = str(line).strip().partition(":")
        if category.strip().lower() in PRIVATE_CATEGORIES and connectivity.strip().lower() not in {"", "disconnected"}:
            return True
    return False


class SharingService:
    """Две независимые раздачи: по локальной сети (``enabled``) и по интернету (``internet_enabled``)."""

    def __init__(
        self,
        *,
        root: Path | None,
        enabled: Callable[[], bool],
        load_installed: Callable[[], object | None],
        product: str,
        current_version: str | None = None,
        adopt: Callable[[], object | None] | None = None,
        exe_path: Path | None = None,
        private_network: Callable[[], bool] = lambda: True,
        open_firewall: Callable[[Path], bool] | None = None,
        discovery_port: int = DISCOVERY_PORT_GUI,
        host: str = "0.0.0.0",
        allow_loopback: bool = False,
        report: Callable[[str], None] = lambda _text: None,
        internet_enabled: Callable[[], bool] = lambda: False,
        internet=None,
    ) -> None:
        self._root, self._enabled, self._load_installed = root, enabled, load_installed
        self._product, self._exe_path = product, exe_path
        self._current_version, self._adopt = current_version, adopt
        self._private_network, self._open_firewall = private_network, open_firewall
        self._discovery_port, self._host, self._allow_loopback = discovery_port, host, allow_loopback
        self._report = report
        self._internet_enabled, self._internet = internet_enabled, internet
        self._lock = threading.Lock()
        self._server: ShareServer | None = None
        self._responder: Responder | None = None

    @property
    def running(self) -> bool:
        return self._server is not None or self.internet_running

    @property
    def internet_running(self) -> bool:
        return self._internet is not None and self._internet.running

    @property
    def port(self) -> int | None:
        return self._server.port if self._server is not None else None

    @property
    def discovery_port(self) -> int | None:
        return self._responder.port if self._responder is not None else None

    def _wanted(self) -> tuple[bool, bool]:
        try:
            lan = bool(self._enabled())
        except Exception as exc:  # noqa: BLE001
            self._report(f"Раздача обновлений: настройка не прочиталась ({exc})")
            lan = False
        try:
            net = bool(self._internet_enabled()) and self._internet is not None
        except Exception as exc:  # noqa: BLE001
            self._report(f"Раздача по интернету: настройка не прочиталась ({exc})")
            net = False
        return lan, net

    def refresh(self) -> bool:
        """Приводит раздачу в соответствие с настройками; ``True`` — что-то раздаёт."""
        with self._lock:
            lan, net = self._wanted()
            if not lan:
                self._stop_locked("выключена в настройках" if self._server is not None else "")
            if not net:
                self._stop_internet_locked("выключена в настройках" if self.internet_running else "")
            if not ((lan and self._server is None) or (net and not self.internet_running)):
                return self.running
        # Список версии берётся без замка: это поход в сеть, а замок нужен
        # выключателю, который человек может щёлкнуть в это время.
        try:
            installed = self._installed_list()
        except Exception as exc:  # noqa: BLE001
            self._report(f"Раздача обновлений: список установленной версии не прочитался ({exc})")
            return self.running
        with self._lock:
            lan, net = self._wanted()  # человек мог выключить раздачу, пока шёл поход в сеть
            if lan and self._server is None:
                try:
                    self._start_locked(installed)
                except Exception as exc:  # noqa: BLE001 — раздача не должна ронять программу
                    self._stop_locked("")
                    self._report(f"Раздача обновлений не запустилась: {exc}")
            if net and not self.internet_running:
                try:
                    self._start_internet_locked(installed)
                except Exception as exc:  # noqa: BLE001
                    self._stop_internet_locked("")
                    self._report(f"Раздача по интернету не запустилась: {exc}")
            return self.running

    def _installed_list(self):
        """Подписанный список запущенной версии: свой или взятый у источников."""
        if self._root is None:
            return None
        installed = self._load_installed()
        current = self._current_version
        fits = installed is not None and (not current or installed.version == current)
        if not fits and self._adopt is not None:
            adopted = self._adopt()
            if adopted is not None:
                return adopted
        return installed if fits else None

    def stop(self) -> None:
        with self._lock:
            self._stop_locked("")
            self._stop_internet_locked("")

    def _ready(self, installed, what: str) -> bool:
        """Общие условия обеих раздач: известна папка, есть подписанный список, сеть частная."""
        if self._root is None:
            return False
        if installed is None:
            self._report(f"{what}: нет подписанного списка установленной версии — раздавать нечего")
            return False
        if not self._private_network():
            self._report(f"{what}: сеть не частная (публичный Wi-Fi или неизвестна) — не раздаём")
            return False
        return True

    def _files(self, installed) -> InstalledFiles:
        return InstalledFiles(self._root, installed.files, HashCache(self._root / ".update" / "hashes.json"))

    def _start_locked(self, installed) -> bool:
        if not self._ready(installed, "Раздача обновлений"):
            return False
        if self._open_firewall is not None and self._exe_path is not None:
            if not self._open_firewall(self._exe_path):
                self._report("Раздача обновлений: правило брандмауэра не добавилось, работаем без него")
        server = ShareServer(self._files(installed), host=self._host, allow_loopback=self._allow_loopback)
        try:
            responder = Responder(
                self._product, installed.version, server.port,
                host=self._host, port=self._discovery_port, allow_loopback=self._allow_loopback,
            )
        except OSError:
            server.server_close()
            raise
        server.start()
        responder.start()
        self._server, self._responder = server, responder
        self._report(f"Раздача обновлений включена: версия {installed.version}, порт {server.port}")
        return True

    def _start_internet_locked(self, installed) -> bool:
        if not self._ready(installed, "Раздача по интернету"):
            return False
        return bool(self._internet.start(self._files(installed), installed.version))

    def _stop_locked(self, why: str) -> None:
        server, responder = self._server, self._responder
        self._server = self._responder = None
        for part in (responder, server):
            if part is not None:
                try:
                    part.stop()
                except Exception:  # noqa: BLE001
                    pass
        if why and server is not None:
            self._report(f"Раздача обновлений остановлена: {why}")

    def _stop_internet_locked(self, why: str) -> None:
        if self._internet is not None and self._internet.running:
            try:
                self._internet.stop()
            except Exception:  # noqa: BLE001
                pass
            if why:
                self._report(f"Раздача по интернету остановлена: {why}")


# ── Windows ────────────────────────────────────────────────────────────

def _run_windows(cmd, *, timeout: float = 15):
    """Запуск без окна с захватом вывода.

    Свой запуск, а не ``run_hidden`` Zapret 2 GUI: тот ставит флаг
    ``DETACHED_PROCESS``, и PowerShell с ним ничего не пишет в перехваченный
    канал (найдено на win10: проверка сети всегда видела пустой вывод).
    """
    import subprocess

    options: dict = {}
    if sys.platform == "win32":
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = 0
        options = {"creationflags": CREATE_NO_WINDOW, "startupinfo": info}
    return subprocess.run(
        cmd, capture_output=True, text=True, errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, **options
    )


def _system32() -> Path:
    return Path(os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows") / "System32"


def windows_private_network(run=None) -> bool:
    """Спрашивает у Windows категории подключённых сетей; не вышло — «нет»."""
    if sys.platform != "win32":
        return False
    run = run or _run_windows
    script = (
        "Get-NetConnectionProfile | ForEach-Object "
        "{ $_.NetworkCategory.ToString() + ':' + $_.IPv4Connectivity.ToString() }"
    )
    powershell = str(_system32() / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    try:
        done = run([powershell, "-NoProfile", "-NonInteractive", "-Command", script], timeout=15)
    except Exception:  # noqa: BLE001
        return False
    if getattr(done, "returncode", 1) != 0:
        return False
    return lan_allowed(str(getattr(done, "stdout", "") or "").splitlines())


def windows_open_firewall(
    exe: Path, run=None, *, name: str = FIREWALL_RULE_NAME, remote: str = "localsubnet", port: int | None = None
) -> bool:
    """Правило «входящие» для программы в частных и доменных сетях.

    По умолчанию только из своей подсети (раздача по локальной сети). Раздача по
    интернету просит ``remote="any"`` и свой порт: отдельное правило, не шире нужного.
    """
    if sys.platform != "win32":
        return False
    run = run or _run_windows
    netsh = str(_system32() / "netsh.exe")
    base = [netsh, "advfirewall", "firewall"]
    try:
        shown = run([*base, "show", "rule", f"name={name}", "verbose"], timeout=15)
        text = str(getattr(shown, "stdout", "") or "").lower()
        already = (
            getattr(shown, "returncode", 1) == 0
            and str(exe).lower() in text
            and remote.lower() in text
            and (port is None or str(port) in text)
        )
        if already:
            return True
        run([*base, "delete", "rule", f"name={name}"], timeout=15)
        command = [*base, "add", "rule", f"name={name}", "dir=in", "action=allow", f"program={exe}",
                   "enable=yes", "profile=private,domain", f"remoteip={remote}"]
        if port is not None:
            command += ["protocol=TCP", f"localport={int(port)}"]
        added = run(command, timeout=15)
    except Exception:  # noqa: BLE001
        return False
    return getattr(added, "returncode", 1) == 0


__all__ = [
    "FIREWALL_RULE_NAME",
    "FIREWALL_RULE_NAME_INTERNET",
    "SharingService",
    "lan_allowed",
    "windows_open_firewall",
    "windows_private_network",
]
