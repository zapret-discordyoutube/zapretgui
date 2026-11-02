"""Свой DNS-сервер проекта — Zapret DNS. Всё о нём пишется только здесь.

Сервер переезжает: первый адрес закрыли в России по IP (2026-10-09), второй Google
считал Россией и не пускал к Gemini (переезд в США 2026-11-01). Чтобы переезд был
одной строкой, имя, адреса и страна сервера записаны в этом файле, а остальная
программа берёт их отсюда: плитка «Zapret DNS», замена старых адресов на
адаптерах, профиль hosts «Zapret DNS» (в каталоге вместо адреса стоит метка
``CATALOG_ADDRESS_MARKER``), проверка BlockCheck «снаружи» и тексты.

Адреса ниже — встроенные. Работающая программа узнаёт настоящий адрес по имени
сама (``dns.zapret_dns_server``): спрашивает эталонные DoH и проверяет, что по
адресу отвечает сервер с сертификатом этого имени. Поэтому следующий переезд
дойдёт до людей и без новой версии, а встроенный адрес нужен, пока имя не
удалось спросить. Серверная сторона — проект ``/home/codex-pve/zapret-dns``
(там адрес тоже в одном месте: ``server/etc/zapret-dns/server.env``).
"""

from __future__ import annotations

HOST = "dns.zapret.moe"
# Встроенный адрес сервера (США, с 2026-11-01). IPv6 у сервера нет.
IPV4: tuple[str, ...] = ("38.180.241.191",)
IPV6: tuple[str, ...] = ()

# Прежние адреса сервера: у кого они остались на адаптере, программа меняет их на
# нынешний (dns.dns_providers.OUTDATED_DNS_ADDRESS_REPLACEMENTS), а прежние IPv6 —
# убирает, если у нынешнего сервера IPv6 нет. При переезде старый адрес переносится сюда.
FORMER_IPV4: tuple[str, ...] = (
    "144.31.82.230",  # SERV.HOST, Финляндия: закрыт в России по IP в первый же день, 2026-10-09
    "83.217.211.149",  # Fortis Host, Нидерланды: Google считал его Россией, 2026-10-09 — 2026-11-01
)
FORMER_IPV6: tuple[str, ...] = (
    "2a01:ecc0:500:8e::2",
    "2a03:afc0:9::235d",
)

# Страна сервера — для текстов («проверит сервер в США», «закрыт для США»).
COUNTRY_RU_IN = "в США"
COUNTRY_RU_FOR = "для США"
COUNTRY_EN_IN = "in the USA"

DOH_URL = f"https://{HOST}/dns-query"
DOT_HOST = HOST
SITE_URL = f"https://{HOST}/"

# Метка вместо адреса в строках профиля ``zapret_dns`` каталога hosts:
# hosts.catalog_repository подставляет на её место нынешний адрес сервера.
CATALOG_PROFILE_ID = "zapret_dns"
CATALOG_ADDRESS_MARKER = "@zapret-dns@"

__all__ = [
    "CATALOG_ADDRESS_MARKER",
    "CATALOG_PROFILE_ID",
    "COUNTRY_EN_IN",
    "COUNTRY_RU_FOR",
    "COUNTRY_RU_IN",
    "DOH_URL",
    "DOT_HOST",
    "FORMER_IPV4",
    "FORMER_IPV6",
    "HOST",
    "IPV4",
    "IPV6",
    "SITE_URL",
]
