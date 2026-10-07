# dns/dns_providers.py
"""
Список DNS провайдеров для UI

Значок ("icon") рисует profile.ui.profile_icon: кроме имён Font Awesome
("fa5s.*", "fa5b.*") подходят фирменные логотипы "simple:<имя>:<буквы>" и свои
SVG "own:<имя>:<буквы>". У серверов из раздела «Для ИИ» значок и цвет те же,
что у одноимённого DNS-профиля в «Редакторе hosts» (hosts/ui/profile_icons.py):
один сервер — один значок во всей программе.

"doh" — адрес шифрованного DNS поверх HTTPS, "dot" — имя сервера для
шифрованного DNS поверх TLS (порт 853): по этому имени проверяется его
сертификат. "dot" указан только там, где сервер действительно отвечает по
этому способу (проверено запросом 2026-10-07); у остальных проверка DoT не
делается, чтобы не выдавать «не поддерживает» за «заблокирован».
"""

DNS_PROVIDERS = {
    "Популярные": {
        "Cloudflare": {
            "ipv4": ["1.1.1.1", "1.0.0.1"],
            "ipv6": ["2606:4700:4700::1111", "2606:4700:4700::1001"],
            "desc": "Быстрый и приватный",
            "icon": "simple:cloudflare:CF",
            "color": "#f48120",
            "doh": "https://cloudflare-dns.com/dns-query",
            "dot": "cloudflare-dns.com"
        },
        "Google DNS": {
            "ipv4": ["8.8.8.8", "8.8.4.4"],
            "ipv6": ["2001:4860:4860::8888", "2001:4860:4860::8844"],
            "desc": "Надёжный",
            "icon": "fa5b.google",
            "color": "#4285f4",
            "doh": "https://dns.google/dns-query",
            "dot": "dns.google"
        },
        "Dns.SB": {
            "ipv4": ["185.222.222.222", "45.11.45.11"],
            "ipv6": ["2a09::", "2a11::"],
            "desc": "Без цензуры",
            "icon": "fa5s.unlock-alt",
            "color": "#00bcd4",
            "doh": "https://doh.sb/dns-query",
            "dot": "dot.sb"
        },
    },
    "Безопасные": {
        "Quad9": {
            "ipv4": ["9.9.9.9", "149.112.112.112"],
            "ipv6": ["2620:fe::fe", "2620:fe::9"],
            "desc": "Антивирус",
            "icon": "simple:quad9:Q9",
            "color": "#e91e63",
            "doh": "https://dns.quad9.net/dns-query",
            "dot": "dns.quad9.net"
        },
        "AdGuard": {
            "ipv4": ["94.140.14.14", "94.140.15.15"],
            "ipv6": ["2a10:50c0::ad1:ff", "2a10:50c0::ad2:ff"],
            "desc": "Без рекламы",
            "icon": "simple:adguard:AG",
            "color": "#68bc71",
            "doh": "https://dns.adguard.com/dns-query",
            "dot": "dns.adguard-dns.com"
        },
        "OpenDNS": {
            "ipv4": ["208.67.222.222", "208.67.220.220"],
            "ipv6": ["2620:119:35::35", "2620:119:53::53"],
            "desc": "Фильтрация",
            "icon": "own:opendns:OD",
            "color": "#fe7702",
            "doh": "https://doh.opendns.com/dns-query",
            "dot": "dns.opendns.com"
        },
        "dnsdoh.art": {
            "ipv4": ["194.180.189.33", "194.180.189.33"],
            "ipv6": [],
            "desc": "Максимальная приватность",
            "icon": "fa5s.lock",
            "color": "#9c27b0",
            "doh": "https://dnsdoh.art:444/dns-query",
            "dot": "dnsdoh.art"
        }
    },
    "Для ИИ": {
        "Xbox DNS": {
            "ipv4": ["111.88.96.54", "111.88.96.55"],
            "ipv6": ["2a00:ab00:1233:26::50", "2a00:ab00:1233:26::51"],
            "desc": "ChatGPT",
            "icon": "fa5b.xbox",
            "color": "#107C10",
            "doh": "https://xbox-dns.ru/dns-query",
            "dot": "xbox-dns.ru"
        },
        "Xbox DNS v2": {
            "ipv4": ["87.228.47.200", "87.228.47.201"],
            "ipv6": [],
            "desc": "ChatGPT",
            "icon": "fa5b.xbox",
            "color": "#2EA043",
            "doh": "https://xbox-dns.ru/dns-query"
        },
        "Xbox DNS (old)": {
            "ipv4": ["176.99.11.77", "80.78.247.254"],
            "ipv6": [],
            "desc": "ChatGPT",
            "icon": "fa5s.gamepad",
            "color": "#7A9A01",
            "doh": "https://xbox-dns.ru/dns-query"
        },
        "Comss DNS": {
            "ipv4": ["83.220.169.155", "212.109.195.93"],
            "ipv6": [],
            "desc": "ChatGPT",
            "icon": "fa5s.shield-alt",
            "color": "#2F80ED",
            "doh": "https://dns.comss.one/dns-query",
            "dot": "dns.comss.one"
        },
        "dns.malw.link": {
            "ipv4": ["95.216.204.218", "80.253.249.40"],
            "ipv6": ["2a01:4f9:c014:6dac::1", "2a12:bec4:1460:5b7::2"],
            "desc": "ChatGPT",
            "icon": "fa5s.bug",
            "color": "#E5484D",
            "doh": "https://dns.malw.link/dns-query"
        },
        # Адреса — у официального имени DoH dns.astracat.network: опубликованный
        # на сайте 85.209.2.112 на обычные DNS-запросы не отвечает (проверено 2026-09-29).
        "AstraCat": {
            "ipv4": ["135.106.217.200", "135.106.197.22"],
            "ipv6": [],
            "desc": "ChatGPT, без рекламы",
            "icon": "fa5s.cat",
            "color": "#F59E0B",
            "doh": "https://dns.astracat.network/dns-query",
            "dot": "dns.astracat.network"
        },
        # Российская пара из официального geohide.ru/static/metadata/servers.json.
        # Подменяет ответы только для сайтов из своего списка (ChatGPT, Grok, Notion…).
        "GeoHide": {
            "ipv4": ["193.233.112.67", "193.233.112.68"],
            "ipv6": [],
            "desc": "ChatGPT, Grok, Notion",
            "icon": "fa5s.globe-europe",
            "color": "#8B5CF6",
            "doh": "https://geohide.ru/dns-query",
            "dot": "geohide.ru"
        },
    }
}

def doh_templates() -> dict[str, str]:
    """{адрес сервера: шаблон DoH} для всех серверов списка (IPv4 и IPv6).

    Windows 11 по этим шаблонам сама шифрует DNS-запросы к известным
    серверам, а при недоступности DoH откатывается на обычный DNS.
    """
    templates: dict[str, str] = {}
    for group in DNS_PROVIDERS.values():
        for data in group.values():
            template = str(data.get("doh") or "").strip()
            if not template:
                continue
            for address in (*data.get("ipv4", ()), *data.get("ipv6", ())):
                templates.setdefault(str(address).strip(), template)
    return templates


# Старые адреса DNS из списка выше и их новые замены.
# Если пользователь когда-то выбрал этот DNS и адрес остался в настройках
# сетевого адаптера, программа при запуске сама меняет его на новый.
# При каждой смене адресов провайдера добавлять сюда пару «старый → новый».
OUTDATED_DNS_ADDRESS_REPLACEMENTS = {
    # Xbox DNS: стандартные адреса сменились на .54/.55
    "111.88.96.50": "111.88.96.54",
    "111.88.96.51": "111.88.96.55",
    # dns.malw.link: старые серверы больше не отвечают
    "84.21.189.133": "95.216.204.218",
    "64.188.98.242": "80.253.249.40",
    "2a12:bec4:1460:d5::2": "2a01:4f9:c014:6dac::1",
    "2a01:ecc0:2c1:2::2": "2a12:bec4:1460:5b7::2",
}
