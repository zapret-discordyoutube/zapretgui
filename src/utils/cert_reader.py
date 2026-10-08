"""Кому и кем выдан сертификат — из его двоичной записи (DER), без сторонних библиотек.

Python отдаёт разобранный сертификат только после успешной проверки. Здесь же
он нужен как раз тогда, когда проверка не прошла: чтобы назвать, кто ответил
вместо сайта. Поэтому запись разбирается вручную — ровно настолько, чтобы
достать имена владельца и издателя и список сайтов, для которых он выдан.

Читается только начало сертификата (до расширений), подпись не проверяется:
это справка «что написано в сертификате», а не доверие к нему.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["CertNames", "read_names"]

_OID_COMMON_NAME = bytes.fromhex("550403")
_OID_ORGANIZATION = bytes.fromhex("55040a")
_OID_ALT_NAMES = bytes.fromhex("551d11")
_SEQUENCE, _SET, _OID, _OCTETS = 0x30, 0x31, 0x06, 0x04
_VERSION_TAG, _EXTENSIONS_TAG = 0xA0, 0xA3
_DNS_NAME_TAG = 0x82


@dataclass(frozen=True, slots=True)
class CertNames:
    # Владелец: имя (CN) и организация (O).
    subject: str = ""
    subject_org: str = ""
    # Издатель — кто выдал сертификат.
    issuer: str = ""
    issuer_org: str = ""
    # Сайты, для которых сертификат выдан.
    names: tuple[str, ...] = ()
    # Владелец и издатель совпадают: сертификат выдан сам себе.
    self_signed: bool = False


def _item(data: bytes, offset: int) -> tuple[int, bytes, int]:
    """(метка, содержимое, где начинается следующий элемент)."""
    tag = data[offset]
    length = data[offset + 1]
    start = offset + 2
    if length & 0x80:
        count = length & 0x7F
        if not 1 <= count <= 4:
            raise ValueError("длина элемента записана не так, как в сертификатах")
        length = int.from_bytes(data[start : start + count], "big")
        start += count
    end = start + length
    if end > len(data):
        raise ValueError("элемент выходит за конец записи")
    return tag, data[start:end], end


def _items(data: bytes) -> list[tuple[int, bytes]]:
    found, offset = [], 0
    while offset < len(data):
        tag, body, offset = _item(data, offset)
        found.append((tag, body))
    return found


def _text(tag: int, body: bytes) -> str:
    # BMPString — двухбайтные знаки; остальные виды строк читаются как UTF-8.
    return body.decode("utf-16-be" if tag == 0x1E else "utf-8", errors="replace").strip()


def _name(body: bytes) -> tuple[str, str]:
    """(CN, O) из записи имени."""
    common = organization = ""
    for set_tag, part in _items(body):
        if set_tag != _SET:
            continue
        for _tag, attribute in _items(part):
            fields = _items(attribute)
            if len(fields) < 2 or fields[0][0] != _OID:
                continue
            if fields[0][1] == _OID_COMMON_NAME:
                common = _text(*fields[1])
            elif fields[0][1] == _OID_ORGANIZATION:
                organization = _text(*fields[1])
    return common, organization


def _alt_names(extensions: bytes) -> tuple[str, ...]:
    for _tag, wrapped in _items(extensions):
        for _tag2, extension in _items(wrapped):
            fields = _items(extension)
            if not fields or fields[0] != (_OID, _OID_ALT_NAMES):
                continue
            value = next((body for tag, body in fields[1:] if tag == _OCTETS), b"")
            names = [
                body.decode("ascii", errors="replace").lower()
                for _seq, listing in _items(value)
                for tag, body in _items(listing)
                if tag == _DNS_NAME_TAG
            ]
            return tuple(dict.fromkeys(names))
    return ()


def read_names(der: bytes | None) -> CertNames | None:
    """Имена из сертификата. None — записи нет или она не разбирается."""
    if not der:
        return None
    try:
        _tag, certificate, _end = _item(bytes(der), 0)
        _tag, body, _end = _item(certificate, 0)
        fields = _items(body)
        if fields and fields[0][0] == _VERSION_TAG:
            fields = fields[1:]
        # Номер, алгоритм подписи, издатель, срок, владелец, ключ, [расширения].
        issuer, issuer_org = _name(fields[2][1])
        subject, subject_org = _name(fields[4][1])
        extensions = next((value for tag, value in fields[6:] if tag == _EXTENSIONS_TAG), b"")
        names = _alt_names(extensions) if extensions else ()
    except (ValueError, IndexError):
        return None
    return CertNames(
        subject=subject,
        subject_org=subject_org,
        issuer=issuer,
        issuer_org=issuer_org,
        names=names or ((subject.lower(),) if "." in subject else ()),
        self_signed=fields[2][1] == fields[4][1],
    )
