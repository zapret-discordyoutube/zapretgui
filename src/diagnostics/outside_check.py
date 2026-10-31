"""«Жив ли сайт снаружи»: открывается ли из-за рубежа сайт, у которого на линии пользователя нет дороги.

Сайт, который не отвечает ни по адресу, ни по имени, может быть не заблокирован,
а просто выключен. Изнутри сети это не отличить. Поэтому проверка спрашивает
свой сервер проекта Zapret DNS в Нидерландах (служба ``zapret-dns-outside``):
он сам открывает сайт по HTTPS и отвечает, жив ли тот.

- Спрашиваются только встроенные сайты BlockCheck: свои домены пользователя на
  сервер не уходят.
- Адрес сервера узнаётся у эталонного DoH: сервер уже переезжал (его первый
  адрес закрыли в России), и проверка должна идти за ним без новой версии.
- Сервер отвечает и за себя (``self_ok``): если у него самого нет внешней
  сети, вывода нет.
"""

from __future__ import annotations

import http.client
import json
import socket
from collections.abc import Callable
from dataclasses import dataclass

from diagnostics.tls_probe import client_context
from utils.socket_cancel import SocketCancel, close_quietly

__all__ = ["MAX_HOSTS", "OUTSIDE_DOWN", "OUTSIDE_UP", "SERVER_HOST", "ask"]

SERVER_HOST = "dns.zapret.moe"
PATH = "/api/outside/v1/check"
MAX_HOSTS = 8
TIMEOUT_S = 30.0
MAX_ANSWER = 64 * 1024

OUTSIDE_UP = "up"
OUTSIDE_DOWN = "down"


@dataclass(frozen=True, slots=True)
class _Answer:
    results: dict[str, str]
    self_ok: bool


def _post(ip: str, body: bytes, cancel: SocketCancel) -> bytes | None:
    raw = socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM)
    secure = None
    try:
        if not cancel.track(raw):
            return None
        raw.settimeout(TIMEOUT_S)
        raw.connect((ip, 443))
        # wrap_socket забирает у raw его соединение: дальше «Стоп» обрывает только
        # сокет TLS. Его отмечаем до рукопожатия, иначе рукопожатие и чтение
        # ответа шли бы до таймаута (30 с на каждый адрес).
        secure = client_context().wrap_socket(raw, server_hostname=SERVER_HOST, do_handshake_on_connect=False)
        if not cancel.track(secure):
            return None
        secure.do_handshake()
        connection = http.client.HTTPSConnection(SERVER_HOST, 443, timeout=TIMEOUT_S, context=client_context())
        connection.sock = secure
        connection.request("POST", PATH, body=body, headers={"Content-Type": "application/json", "User-Agent": "ZapretGUI"})
        response = connection.getresponse()
        if response.status != 200:
            return None
        return response.read(MAX_ANSWER)
    except (OSError, http.client.HTTPException):
        return None
    finally:
        if secure is not None:
            cancel.release(secure)
            close_quietly(secure)
        cancel.release(raw)
        close_quietly(raw)


def _parse(data: bytes | None) -> _Answer | None:
    try:
        document = json.loads(data or b"")
        results = {
            str(item["host"]): OUTSIDE_UP if item.get("up") else OUTSIDE_DOWN
            for item in document.get("results") or ()
            if isinstance(item, dict) and item.get("host") and item.get("dns") != "fail"
        }
        return _Answer(results=results, self_ok=bool(document.get("self_ok")))
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def ask(
    hosts: list[str],
    *,
    server_ips: Callable[[], tuple[str, ...]],
    cancel: SocketCancel,
) -> dict[str, str]:
    """{сайт: OUTSIDE_UP | OUTSIDE_DOWN}; сайта нет в ответе — о нём ничего не известно.

    ``server_ips`` — адреса сервера (у эталонного DoH). Пустой словарь — спросить
    не удалось или сервер сам без сети.
    """
    hosts = list(dict.fromkeys(host for host in hosts if host))[:MAX_HOSTS]
    if not hosts or cancel.cancelled:
        return {}
    body = json.dumps({"v": 1, "hosts": hosts}).encode("utf-8")
    for ip in server_ips()[:2]:
        answer = _parse(_post(ip, body, cancel))
        if answer is not None:
            return answer.results if answer.self_ok else {}
        if cancel.cancelled:
            break
    return {}
