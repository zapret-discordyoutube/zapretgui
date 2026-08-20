"""HTTPS-запросы с безопасным запасным разрешением домена.

Обычный запрос сначала использует системный DNS. Если Windows не смогла
разрешить имя, получила тайм-аут соединения из-за ложного DNS-ответа или
попала на узел с чужим сертификатом, домен разрешается через DoH по
проверяемому HTTPS к IP Cloudflare. Полученный адрес используется только для
TCP-соединения: URL, Host, SNI и проверка сертификата остаются доменными.

Так сервер может менять IP без новой сборки приложения, а запасной путь не
ослабляет TLS и не превращает текущий IP сервиса в источник истины.
"""

from __future__ import annotations

from collections.abc import Iterable
import ipaddress
import socket
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter


_DOH_ENDPOINTS = (
    "https://1.1.1.1/dns-query",
    "https://1.0.0.1/dns-query",
)
_CACHE_MIN_TTL = 15.0
_CACHE_MAX_TTL = 300.0
_cache_lock = threading.Lock()
_address_cache: dict[str, tuple[float, tuple[str, ...]]] = {}


def clear_https_dns_cache(hostname: str | None = None) -> None:
    with _cache_lock:
        if hostname is None:
            _address_cache.clear()
        else:
            _address_cache.pop(str(hostname).strip().rstrip(".").lower(), None)


def is_name_resolution_error(exc: BaseException) -> bool:
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        class_name = type(current).__name__.lower()
        if isinstance(current, socket.gaierror) or "nameresolution" in class_name:
            return True
        for linked in (current.__cause__, current.__context__):
            if isinstance(linked, BaseException):
                pending.append(linked)
        for value in getattr(current, "args", ()):
            if isinstance(value, BaseException):
                pending.append(value)
    return False


def _valid_public_addresses(values: Iterable[Any]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        try:
            address = ipaddress.ip_address(str(value or "").strip())
        except ValueError:
            continue
        if not address.is_global:
            continue
        normalized = str(address)
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _cached_addresses(hostname: str) -> tuple[str, ...]:
    with _cache_lock:
        cached = _address_cache.get(hostname)
        if cached is None:
            return ()
        expires_at, addresses = cached
        if time.monotonic() >= expires_at:
            _address_cache.pop(hostname, None)
            return ()
        return addresses


def resolve_hostname_via_doh(
    hostname: str,
    *,
    timeout: float = 5.0,
) -> tuple[str, ...]:
    """Получить текущие A/AAAA домена через DoH без DNS bootstrap-цикла."""

    host = str(hostname or "").strip().rstrip(".").lower()
    if not host:
        return ()
    cached = _cached_addresses(host)
    if cached:
        return cached

    query_timeout = max(0.5, min(float(timeout), 8.0))
    for endpoint in _DOH_ENDPOINTS:
        session = requests.Session()
        session.trust_env = False
        try:
            response = session.get(
                endpoint,
                params={"name": host, "type": "A"},
                headers={
                    "Accept": "application/dns-json",
                    "User-Agent": "ZapretGUI-Secure-DNS/1.0",
                },
                timeout=query_timeout,
                verify=True,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or int(payload.get("Status", -1)) != 0:
                continue
            answers = payload.get("Answer")
            if not isinstance(answers, list):
                continue
            addresses = _valid_public_addresses(
                answer.get("data")
                for answer in answers
                if isinstance(answer, dict) and int(answer.get("type", 0) or 0) in {1, 28}
            )
            if not addresses:
                continue
            ttl_values = [
                int(answer.get("TTL", 0) or 0)
                for answer in answers
                if isinstance(answer, dict)
                and int(answer.get("type", 0) or 0) in {1, 28}
                and str(answer.get("data") or "") in addresses
            ]
            ttl = min(ttl_values) if ttl_values else int(_CACHE_MIN_TTL)
            cache_ttl = max(_CACHE_MIN_TTL, min(float(ttl), _CACHE_MAX_TTL))
            with _cache_lock:
                _address_cache[host] = (time.monotonic() + cache_ttl, addresses)
            return addresses
        except (requests.RequestException, ValueError, TypeError):
            continue
        finally:
            session.close()
    return ()


class ResolvedHttpsAdapter(HTTPAdapter):
    """Соединяет requests с IP, сохраняя доменную TLS-идентичность."""

    def __init__(self, *, hostname: str, resolved_ip: str, port: int = 443) -> None:
        self.hostname = str(hostname or "").strip().lower()
        self.resolved_ip = str(ipaddress.ip_address(resolved_ip))
        self.resolved_port = int(port)
        super().__init__()

    def get_connection_with_tls_context(
        self,
        request,
        verify,
        proxies=None,
        cert=None,
    ):
        host_params, pool_kwargs = self.build_connection_pool_key_attributes(
            request,
            verify,
            cert,
        )
        pool_kwargs = dict(pool_kwargs)
        pool_kwargs["server_hostname"] = self.hostname
        pool_kwargs["assert_hostname"] = self.hostname
        return self.poolmanager.connection_from_host(
            host=self.resolved_ip,
            port=int(host_params.get("port") or self.resolved_port),
            scheme="https",
            pool_kwargs=pool_kwargs,
        )

    def add_headers(self, request, **kwargs) -> None:
        request.headers["Host"] = self.hostname


def _connect_timeout_seconds(timeout: Any) -> float:
    value = timeout[0] if isinstance(timeout, tuple) and timeout else timeout
    try:
        return max(0.5, min(float(value), 8.0))
    except (TypeError, ValueError):
        return 5.0


def _may_retry_after_dns_route_failure(exc: BaseException) -> bool:
    if isinstance(exc, (requests.exceptions.ConnectTimeout, requests.exceptions.SSLError)):
        return True
    return isinstance(exc, requests.ConnectionError) and is_name_resolution_error(exc)


def _close_adapter_with_response(response: requests.Response, adapter: HTTPAdapter) -> None:
    original_close = response.close
    closed = False

    def close() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        try:
            original_close()
        finally:
            adapter.close()

    response.close = close


def request_with_dns_fallback(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout: Any,
    **kwargs,
) -> requests.Response:
    """Выполнить запрос и при безопасной ранней ошибке повторить через DoH.

    Повтор разрешён только до отправки HTTP-запроса: при ошибке DNS, тайм-ауте
    TCP-соединения или ошибке TLS-рукопожатия. Read timeout и общий обрыв уже
    начатого HTTP-запроса не повторяются, что защищает мутационные POST.
    """

    first_error: requests.RequestException | None = None
    try:
        return session.request(method, url, timeout=timeout, **kwargs)
    except requests.RequestException as primary_error:
        if not _may_retry_after_dns_route_failure(primary_error):
            raise
        first_error = primary_error

    assert first_error is not None

    parsed = urlsplit(str(url or ""))
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise first_error
    if kwargs.get("verify", True) is False:
        raise first_error

    hostname = parsed.hostname.lower()
    port = int(parsed.port or 443)
    addresses = resolve_hostname_via_doh(
        hostname,
        timeout=_connect_timeout_seconds(timeout),
    )
    if not addresses:
        raise first_error

    prefix = f"https://{hostname}/"
    previous_adapter = session.adapters.get(prefix)
    last_error: BaseException = first_error
    for address in addresses:
        adapter = ResolvedHttpsAdapter(
            hostname=hostname,
            resolved_ip=address,
            port=port,
        )
        session.mount(prefix, adapter)
        response: requests.Response | None = None
        try:
            response = session.request(method, url, timeout=timeout, **kwargs)
        except (
            requests.exceptions.ConnectTimeout,
            requests.exceptions.SSLError,
        ) as exc:
            last_error = exc
        finally:
            session.adapters.pop(prefix, None)
            if previous_adapter is not None:
                session.mount(prefix, previous_adapter)
        if response is not None:
            if kwargs.get("stream"):
                _close_adapter_with_response(response, adapter)
            else:
                adapter.close()
            return response
        adapter.close()
    clear_https_dns_cache(hostname)
    raise last_error


__all__ = [
    "ResolvedHttpsAdapter",
    "clear_https_dns_cache",
    "is_name_resolution_error",
    "request_with_dns_fallback",
    "resolve_hostname_via_doh",
]
