"""Эталонные DNS-серверы: кому программа верит, когда сравнивает ответы.

Запрос к ним идёт шифрованным (DNS поверх HTTPS) и прямо по адресу, без имени:
системный DNS в этом не участвует, а сертификат сервера проверяется по его
адресу. Такой ответ провайдер не может незаметно подменить по пути.

Серверов несколько и у разных владельцев: провайдер может закрыть любой из
них (так уже бывало с 8.8.8.8), и эталон не должен пропадать вместе с ним.
Проверки сообщают, какой именно сервер недоступен.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["REFERENCE_RESOLVERS", "ReferenceResolver"]


@dataclass(frozen=True, slots=True)
class ReferenceResolver:
    label: str
    address: str


REFERENCE_RESOLVERS: tuple[ReferenceResolver, ...] = (
    ReferenceResolver("Cloudflare", "1.1.1.1"),
    ReferenceResolver("Google", "8.8.8.8"),
    ReferenceResolver("AdGuard", "94.140.14.140"),
    ReferenceResolver("OpenDNS", "208.67.222.222"),
)
