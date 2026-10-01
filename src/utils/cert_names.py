"""Имена сайтов из сертификата сервера.

Сертификат HTTPS перечисляет домены, для которых он выдан (поле SAN). Это
честный способ узнать «кто ещё живёт на этом адресе», не обращаясь к внешним
сервисам. Проверка сертификата здесь выключена нарочно: интересен как раз
чужой или подменённый сертификат, а стандартный ``ssl`` отдаёт разобранные
поля только у проверенного. Поэтому нужное поле достаётся из DER вручную.
"""

from __future__ import annotations

import socket
import ssl
from dataclasses import dataclass
from ipaddress import ip_address

# OID 2.5.29.17 (subjectAltName) и 2.5.4.3 (commonName) в кодировке DER.
_OID_SAN = b"\x06\x03\x55\x1d\x11"
_OID_CN = b"\x06\x03\x55\x04\x03"
_TAG_SEQUENCE = 0x30
_TAG_OCTET_STRING = 0x04
_TAG_BOOLEAN = 0x01
_TAG_DNS_NAME = 0x82
_TAG_IP_ADDRESS = 0x87
_STRING_TAGS = (0x0C, 0x13, 0x14, 0x16, 0x1E)

KIND_OK = "ok"
KIND_TIMEOUT = "timeout"
KIND_REFUSED = "refused"
KIND_TLS = "tls"
KIND_ERROR = "error"


@dataclass(frozen=True, slots=True)
class CertNames:
    kind: str
    names: tuple[str, ...] = ()
    common_name: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == KIND_OK


def _read_tlv(data: bytes, offset: int) -> tuple[int, int, int]:
    """Тег, начало и конец значения одного элемента DER."""
    if offset + 2 > len(data):
        raise ValueError("оборванный элемент")
    tag = data[offset]
    first = data[offset + 1]
    start = offset + 2
    if first & 0x80:
        count = first & 0x7F
        if count == 0 or count > 4 or start + count > len(data):
            raise ValueError("недопустимая длина")
        length = int.from_bytes(data[start : start + count], "big")
        start += count
    else:
        length = first
    end = start + length
    if end > len(data):
        raise ValueError("элемент выходит за границы")
    return tag, start, end


def subject_alt_names(der: bytes) -> tuple[str, ...]:
    """Домены и адреса из поля SAN сертификата в кодировке DER."""
    names: list[str] = []
    search_from = 0
    while True:
        position = der.find(_OID_SAN, search_from)
        if position < 0:
            break
        search_from = position + len(_OID_SAN)
        try:
            offset = search_from
            tag, start, end = _read_tlv(der, offset)
            if tag == _TAG_BOOLEAN:  # необязательная пометка «критичное расширение»
                tag, start, end = _read_tlv(der, end)
            if tag != _TAG_OCTET_STRING:
                continue
            tag, start, end = _read_tlv(der, start)
            if tag != _TAG_SEQUENCE:
                continue
            offset = start
            while offset < end:
                tag, value_start, value_end = _read_tlv(der, offset)
                value = der[value_start:value_end]
                if tag == _TAG_DNS_NAME:
                    name = value.decode("ascii", "replace").strip().lower()
                elif tag == _TAG_IP_ADDRESS and len(value) in (4, 16):
                    name = str(ip_address(value))
                else:
                    name = ""
                if name and name not in names:
                    names.append(name)
                offset = value_end
        except ValueError:
            continue
        if names:
            break
    return tuple(names)


def common_name(der: bytes) -> str:
    """Имя владельца сертификата (CN). В сертификате их два: издатель, затем владелец."""
    found: list[str] = []
    search_from = 0
    while True:
        position = der.find(_OID_CN, search_from)
        if position < 0:
            break
        search_from = position + len(_OID_CN)
        try:
            tag, start, end = _read_tlv(der, search_from)
        except ValueError:
            continue
        if tag not in _STRING_TAGS:
            continue
        encoding = "utf-16-be" if tag == 0x1E else "utf-8"
        found.append(der[start:end].decode(encoding, "replace").strip())
    if len(found) >= 2:
        return found[1]
    return found[0] if found else ""


def fetch_cert_names(ip: str, *, server_name: str | None = None, port: int = 443, timeout_s: float = 4.0) -> CertNames:
    """Подключается к адресу и читает имена из его сертификата. Исключений не бросает.

    ``server_name`` — имя сайта, которое называем серверу (SNI). Без него сервер
    отдаёт сертификат «по умолчанию» — он и показывает, чей это адрес.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        family = socket.AF_INET6 if ip_address(ip).version == 6 else socket.AF_INET
    except ValueError as exc:
        return CertNames(kind=KIND_ERROR, detail=str(exc))

    try:
        with socket.socket(family, socket.SOCK_STREAM) as raw:
            raw.settimeout(timeout_s)
            raw.connect((ip, port))
            with context.wrap_socket(raw, server_hostname=server_name or None) as tls:
                der = tls.getpeercert(binary_form=True) or b""
    except socket.timeout:
        return CertNames(kind=KIND_TIMEOUT, detail="сервер не ответил вовремя")
    except ConnectionRefusedError:
        return CertNames(kind=KIND_REFUSED, detail=f"порт {port} закрыт")
    except ssl.SSLError as exc:
        return CertNames(kind=KIND_TLS, detail=str(getattr(exc, "reason", "") or exc))
    except OSError as exc:
        return CertNames(kind=KIND_ERROR, detail=str(exc))

    if not der:
        return CertNames(kind=KIND_TLS, detail="сервер не прислал сертификат")
    return CertNames(kind=KIND_OK, names=subject_alt_names(der), common_name=common_name(der))


__all__ = [
    "KIND_ERROR",
    "KIND_OK",
    "KIND_REFUSED",
    "KIND_TIMEOUT",
    "KIND_TLS",
    "CertNames",
    "common_name",
    "fetch_cert_names",
    "subject_alt_names",
]
