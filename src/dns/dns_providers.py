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

"encrypted_only": True — сервер принимает только шифрованные запросы (DoH,
DoT), а обычный DNS на порту 53 у него закрыт намеренно (проверено запросом
2026-10-08). Спрашивать такой сервер сама умеет только Windows 11, поэтому на
Windows без DoH плитка не применяется, а замер скорости и проверка обычного
DNS такие серверы пропускают: молчание порта 53 здесь не блокировка.

"dnssec": True — сервер проверяет подписи DNSSEC: на имя с заведомо
испорченной подписью (dnssec-failed.org) он отвечает отказом, а не адресом
(проверено запросом 2026-10-07). На плитке это метка «DNSSEC».

"status" — пометка состояния сервера в России (плитка показывает её цветом):
STATUS_BLOCKED — обычные запросы блокируются или перехватываются по дороге
(Google и Cloudflare — с августа 2026); STATUS_AT_RISK — пока работает, но уже
попадал под ограничения и может перестать отвечать. Пометка только
предупреждает: выбрать такой сервер по-прежнему можно.

Убранные серверы. dns.malw.link: оба опубликованных адреса не отвечают с начала
октября 2026 (так пишет и страница состояния самого сервиса), а оставшийся
узел отдаёт для сайтов ИИ адрес посредника, который тоже не работает. Вернуть
можно одной записью, когда сервис оживёт. «Xbox DNS (old)» — см. замены адресов
в конце файла.

Группа «Малоизвестные» — небольшие и нишевые серверы: из-за малой известности
они реже попадают под блокировки. Группа «Для ИИ» — серверы сообщества,
которые подменяют адреса закрытых для России сервисов; доверия к ним меньше,
чем к крупным компаниям, и страница пишет об этом рядом с названием группы.

Группа «Шифрованные» — не серверы, а режимы встроенного движка
dnscrypt-proxy (dns.local_proxy): ключ "local_proxy" называет режим, а адрес
у всех один — 127.0.0.1, сам этот компьютер. Выбор такой плитки сначала
запускает движок и только потом прописывает адаптеру 127.0.0.1. Замер
скорости, проверка серверов и поиск сервера по адресу эти записи пропускают
(network_providers()): какой режим работает, знает только сам движок.

Основные адреса (первый в "ipv4") не должны повторяться: по нему программа
узнаёт, какой сервер стоит на адаптере. Это и остальные правила списка
проверяет catalog_problems().
"""

from __future__ import annotations

import ipaddress

STATUS_BLOCKED = "blocked"
STATUS_AT_RISK = "at_risk"
STATUSES = (STATUS_BLOCKED, STATUS_AT_RISK)

LOCAL_PROXY_GROUP = "Шифрованные"

DNS_PROVIDERS = {
    LOCAL_PROXY_GROUP: {
        "DNSCrypt": {
            "ipv4": ["127.0.0.1"],
            "ipv6": ["::1"],
            "desc": "Шифрует запросы",
            "icon": "fa5s.key",
            "color": "#22d3ee",
            "dnssec": True,
            "local_proxy": "dnscrypt",
        },
        "DNSCrypt анонимный": {
            "ipv4": ["127.0.0.1"],
            "ipv6": ["::1"],
            "desc": "Скрывает ваш адрес",
            "icon": "fa5s.user-secret",
            "color": "#a3e635",
            "dnssec": True,
            "local_proxy": "anonymized",
        },
        "ODoH": {
            "ipv4": ["127.0.0.1"],
            "ipv6": ["::1"],
            "desc": "Через HTTPS, пробный",
            "icon": "fa5s.mask",
            "color": "#f472b6",
            "dnssec": True,
            "local_proxy": "odoh",
        },
    },
    "Популярные": {
        "Cloudflare": {
            "ipv4": ["1.1.1.1", "1.0.0.1"],
            "ipv6": ["2606:4700:4700::1111", "2606:4700:4700::1001"],
            "desc": "Быстрый и приватный",
            "icon": "simple:cloudflare:CF",
            "color": "#f48120",
            "doh": "https://cloudflare-dns.com/dns-query",
            "dot": "cloudflare-dns.com",
            "dnssec": True,
            "status": STATUS_BLOCKED,
        },
        "Google DNS": {
            "ipv4": ["8.8.8.8", "8.8.4.4"],
            "ipv6": ["2001:4860:4860::8888", "2001:4860:4860::8844"],
            "desc": "Надёжный",
            "icon": "simple:google:G",
            "color": "#4285f4",
            "doh": "https://dns.google/dns-query",
            "dot": "dns.google",
            "dnssec": True,
            "status": STATUS_BLOCKED,
        },
        # Отвечает быстро и в России не блокируется, но это российский сервер:
        # запрещённые в России сайты он может не отдавать.
        "Яндекс DNS": {
            "ipv4": ["77.88.8.8", "77.88.8.1"],
            "ipv6": ["2a02:6b8::feed:0ff", "2a02:6b8:0:1::feed:0ff"],
            "desc": "Российский, быстрый",
            "icon": "fa5b.yandex",
            "color": "#fc3f1d",
            "doh": "https://common.dot.dns.yandex.net/dns-query",
            "dot": "common.dot.dns.yandex.net",
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
            "dot": "dns.quad9.net",
            "dnssec": True,
        },
        "AdGuard": {
            "ipv4": ["94.140.14.14", "94.140.15.15"],
            "ipv6": ["2a10:50c0::ad1:ff", "2a10:50c0::ad2:ff"],
            "desc": "Без рекламы",
            "icon": "simple:adguard:AG",
            "color": "#68bc71",
            "doh": "https://dns.adguard-dns.com/dns-query",
            "dot": "dns.adguard-dns.com",
            "dnssec": True,
            "status": STATUS_AT_RISK,
        },
        "AdGuard Семейный": {
            "ipv4": ["94.140.14.15", "94.140.15.16"],
            "ipv6": ["2a10:50c0::bad1:ff", "2a10:50c0::bad2:ff"],
            "desc": "Без рекламы и 18+",
            "icon": "simple:adguard:AG",
            "color": "#3f9d8c",
            "doh": "https://family.adguard-dns.com/dns-query",
            "dot": "family.adguard-dns.com",
            "dnssec": True,
            "status": STATUS_AT_RISK,
        },
        "OpenDNS": {
            "ipv4": ["208.67.222.222", "208.67.220.220"],
            "ipv6": ["2620:119:35::35", "2620:119:53::53"],
            "desc": "Фильтрация",
            "icon": "own:opendns:OD",
            "color": "#fe7702",
            "doh": "https://doh.opendns.com/dns-query",
            "dot": "dns.opendns.com",
            "dnssec": True,
            "status": STATUS_AT_RISK,
        },
        "Яндекс Безопасный": {
            "ipv4": ["77.88.8.88", "77.88.8.2"],
            "ipv6": ["2a02:6b8::feed:bad", "2a02:6b8:0:1::feed:bad"],
            "desc": "Антивирус, российский",
            "icon": "fa5b.yandex",
            "color": "#f5a623",
            "doh": "https://safe.dot.dns.yandex.net/dns-query",
            "dot": "safe.dot.dns.yandex.net",
        },
        "Яндекс Семейный": {
            "ipv4": ["77.88.8.7", "77.88.8.3"],
            "ipv6": ["2a02:6b8::feed:a11", "2a02:6b8:0:1::feed:a11"],
            "desc": "Без 18+, российский",
            "icon": "fa5b.yandex",
            "color": "#7b61ff",
            "doh": "https://family.dot.dns.yandex.net/dns-query",
            "dot": "family.dot.dns.yandex.net",
        },
        # Российская компания по кибербезопасности; закрывает только вредоносные
        # сайты, реестр блокировок не применяет (rutracker.org отдаёт настоящий адрес).
        "BI.ZONE": {
            "ipv4": ["185.191.32.7", "185.191.32.8"],
            "ipv6": [],
            "desc": "Антивирус, российский",
            "icon": "fa5s.shield-virus",
            "color": "#2dd4bf",
            "doh": "https://public.sdns.bi.zone/9c0205cf-e32e-418e-adc0-e2575ebcf394",
            "dot": "public.sdns.bi.zone",
            "dnssec": True,
        },
        "DNS4EU Защитный": {
            "ipv4": ["86.54.11.1", "86.54.11.201"],
            "ipv6": ["2a13:1001::86:54:11:1", "2a13:1001::86:54:11:201"],
            "desc": "Антивирус, Евросоюз",
            "icon": "simple:europeanunion:EU",
            "color": "#3b6fd4",
            "doh": "https://protective.joindns4.eu/dns-query",
            "dot": "protective.joindns4.eu",
            "dnssec": True,
        },
        "CleanBrowsing": {
            "ipv4": ["185.228.168.9", "185.228.169.9"],
            "ipv6": ["2a0d:2a00:1::2", "2a0d:2a00:2::2"],
            "desc": "Антивирус",
            "icon": "fa5s.broom",
            "color": "#38bdf8",
            "doh": "https://doh.cleanbrowsing.org/doh/security-filter/",
            "dot": "security-filter-dns.cleanbrowsing.org",
            "dnssec": True,
        },
        "Control D Без рекламы": {
            "ipv4": ["76.76.2.2", "76.76.10.2"],
            "ipv6": ["2606:1a40::2", "2606:1a40:1::2"],
            "desc": "Без рекламы и слежки",
            "icon": "fa5s.ban",
            "color": "#a78bfa",
            "doh": "https://freedns.controld.com/p2",
            "dnssec": True,
        },
    },
    "Малоизвестные": {
        "Dns.SB": {
            "ipv4": ["185.222.222.222", "45.11.45.11"],
            "ipv6": ["2a09::", "2a11::"],
            "desc": "Без цензуры",
            "icon": "fa5s.unlock-alt",
            "color": "#00bcd4",
            "doh": "https://doh.sb/dns-query",
            "dot": "dot.sb",
            "dnssec": True,
        },
        # Шаблон — с официального dnsdoh.art: DoH на обычном порту 443. Прежний
        # порт 444 молчит (проверено 2026-10-08). Рекламные имена сервер отдаёт
        # пустым адресом (doubleclick.net → 0.0.0.0), и отключить это нельзя.
        "dnsdoh.art": {
            "ipv4": ["194.180.189.33"],
            "ipv6": [],
            "desc": "Без рекламы и записи",
            "icon": "fa5s.lock",
            "color": "#9c27b0",
            "doh": "https://dnsdoh.art/dns-query",
            "dot": "dnsdoh.art",
            "dnssec": True,
        },
        "Control D": {
            "ipv4": ["76.76.2.0", "76.76.10.0"],
            "ipv6": ["2606:1a40::", "2606:1a40:1::"],
            "desc": "Без фильтров и записи",
            "icon": "fa5s.sliders-h",
            "color": "#8b5cf6",
            "doh": "https://freedns.controld.com/p0",
            "dnssec": True,
        },
        "DNS4EU": {
            "ipv4": ["86.54.11.100", "86.54.11.200"],
            "ipv6": ["2a13:1001::86:54:11:100", "2a13:1001::86:54:11:200"],
            "desc": "Без фильтров, Евросоюз",
            "icon": "simple:europeanunion:EU",
            "color": "#f5c518",
            "doh": "https://unfiltered.joindns4.eu/dns-query",
            "dot": "unfiltered.joindns4.eu",
            "dnssec": True,
        },
        # Имя для шифрования — из официальной справки docs.gcore.com; по нему
        # отвечают оба адреса и по DoH, и по DoT (проверено 2026-10-08).
        "Gcore": {
            "ipv4": ["95.85.95.85", "2.56.220.2"],
            "ipv6": ["2a03:90c0:999d::1", "2a03:90c0:9992::1"],
            "desc": "Быстрый, без записи",
            "icon": "simple:gcore:GC",
            "color": "#ff4c00",
            "doh": "https://gcoredns.com/dns-query",
            "dot": "gcoredns.com",
        },
        "DNS.Watch": {
            "ipv4": ["84.200.69.80", "84.200.70.40"],
            "ipv6": ["2001:1608:10:25::1c04:b12f", "2001:1608:10:25::9249:d69b"],
            "desc": "Без цензуры и записи",
            "icon": "fa5s.eye-slash",
            "color": "#4ade80",
            "dnssec": True,
        },
        "UltraDNS": {
            "ipv4": ["64.6.64.6", "64.6.65.6"],
            "ipv6": ["2620:74:1b::1:1", "2620:74:1c::2:2"],
            "desc": "Надёжный, без фильтров",
            "icon": "fa5s.bolt",
            "color": "#facc15",
            "dnssec": True,
        },
        # Регистратор чешских доменов .cz.
        "CZ.NIC": {
            "ipv4": ["193.17.47.1", "185.43.135.1"],
            "ipv6": ["2001:148f:ffff::1", "2001:148f:fffe::1"],
            "desc": "Без фильтров, Чехия",
            "icon": "fa5s.landmark",
            "color": "#60a5fa",
            "doh": "https://odvr.nic.cz/dns-query",
            "dot": "odvr.nic.cz",
            "dnssec": True,
        },
        # Регистратор канадских доменов .ca; режим Private — без фильтров.
        "CIRA Canadian Shield": {
            "ipv4": ["149.112.121.10", "149.112.122.10"],
            "ipv6": ["2620:10a:80bb::10", "2620:10a:80bc::10"],
            "desc": "Без фильтров, Канада",
            "icon": "fa5b.canadian-maple-leaf",
            "color": "#ef4444",
            "doh": "https://private.canadianshield.cira.ca/dns-query",
            "dot": "private.canadianshield.cira.ca",
            "dnssec": True,
        },
        # Общественная сеть Freifunk München (Германия).
        "Freifunk München": {
            "ipv4": ["5.1.66.255", "185.150.99.255"],
            "ipv6": ["2001:678:e68:f000::", "2001:678:ed0:f000::"],
            "desc": "Без цензуры и записи",
            "icon": "fa5s.broadcast-tower",
            "color": "#ec4899",
            "doh": "https://doh.ffmuc.net/dns-query",
            "dot": "dot.ffmuc.net",
            "dnssec": True,
        },
        # Некоммерческая организация из США, держит узлы сети I2P. Шифрованные
        # запросы принимает только первый адрес: 23.128.248.4 отказывает по портам
        # 443 и 853 (проверено 2026-10-08), обычный DNS отвечает на обоих.
        "StormyCloud": {
            "ipv4": ["23.128.248.2", "23.128.248.4"],
            "ipv6": ["2602:fc05::2", "2602:fc05::4"],
            "desc": "Без цензуры и записи",
            "icon": "fa5s.cloud-rain",
            "color": "#93c5fd",
            "doh": "https://dns.stormycloud.org/dns-query",
            "dnssec": True,
        },
        "dnsforge.de": {
            "ipv4": ["176.9.93.198", "176.9.1.117"],
            "ipv6": ["2a01:4f8:151:34aa::198", "2a01:4f8:141:316d::117"],
            "desc": "Без рекламы и записи",
            "icon": "fa5s.hammer",
            "color": "#fb923c",
            "doh": "https://dnsforge.de/dns-query",
            "dot": "dnsforge.de",
            "dnssec": True,
        },
        "Surfshark DNS": {
            "ipv4": ["194.169.169.169"],
            "ipv6": ["2a09:a707:169::"],
            "desc": "Без записи запросов",
            "icon": "simple:surfshark:SS",
            "color": "#1ebfbf",
            "doh": "https://dns.surfsharkdns.com/dns-query",
            "dot": "dns.surfsharkdns.com",
        },
        # Сервер фонда «Викимедиа» (Википедия). Обычный DNS не принимает вовсе.
        "Wikimedia DNS": {
            "ipv4": ["185.71.138.138"],
            "ipv6": ["2001:67c:930::1"],
            "desc": "Без фильтров, Википедия",
            "icon": "fa5b.wikipedia-w",
            "color": "#94a3b8",
            "doh": "https://wikimedia-dns.org/dns-query",
            "dot": "wikimedia-dns.org",
            "dnssec": True,
            "encrypted_only": True,
        },
    },
    "Для ИИ": {
        # Сайт xbox-dns.ru объявляет и DoT, но основной адрес 111.88.96.54 порт 853
        # не принимает (отвечает только .55, проверено 2026-10-08), поэтому "dot" нет.
        "Xbox DNS": {
            "ipv4": ["111.88.96.54", "111.88.96.55"],
            "ipv6": ["2a00:ab00:1233:26::50", "2a00:ab00:1233:26::51"],
            "desc": "ChatGPT",
            "icon": "fa5b.xbox",
            "color": "#107C10",
            "doh": "https://xbox-dns.ru/dns-query",
            "dnssec": True,
        },
        # Шифрованные запросы по имени xbox-dns.ru эти адреса обрывают (проверено
        # 2026-10-08 с Windows и из домашних сетей России), поэтому шаблона DoH нет.
        "Xbox DNS v2": {
            "ipv4": ["87.228.47.200", "87.228.47.201"],
            "ipv6": [],
            "desc": "ChatGPT",
            "icon": "fa5b.xbox",
            "color": "#2EA043",
            "dnssec": True,
        },
        # Кроме подмены адресов сайтов ИИ, рекламные имена отдаёт пустым адресом
        # (doubleclick.net → 0.0.0.0, проверено 2026-10-08).
        "Comss DNS": {
            "ipv4": ["83.220.169.155", "212.109.195.93"],
            "ipv6": [],
            "desc": "Нейросети, без рекламы",
            "icon": "fa5s.shield-alt",
            "color": "#2F80ED",
            "doh": "https://dns.comss.one/dns-query",
            "dot": "dns.comss.one"
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
            "dot": "dns.astracat.network",
            "dnssec": True,
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
        # Адреса и шаблон — с официального dns-ai.ru. Обычный DNS наружу не отдаёт.
        # Адреса сайтов ИИ подменяет только для домашних сетей России и Беларуси
        # (ChatGPT, Claude, Grok, Copilot, Manus — через свой узел, проверено из шести
        # российских сетей 2026-10-08; его узел для Google в тот день не открывался).
        # Рекламные имена отвечают пустым адресом (doubleclick.net → 0.0.0.0).
        "DNS-AI": {
            "ipv4": ["192.144.59.14", "186.246.49.127"],
            "ipv6": ["2a0d:8480:0:67c::14", "2a0a:2b41:0:500d::53"],
            "desc": "Нейросети, без рекламы",
            "icon": "fa5s.robot",
            "color": "#d946ef",
            "doh": "https://dns.dns-ai.ru/dns-query",
            "dot": "dns.dns-ai.ru",
            "dnssec": True,
            "encrypted_only": True,
        },
    }
}


def iter_providers():
    """(группа, название, данные) для каждого сервера списка."""
    for group, providers in DNS_PROVIDERS.items():
        for name, data in providers.items():
            yield group, name, data


def network_providers(providers: dict | None = None) -> dict:
    """Список без режимов встроенного движка: только настоящие серверы в сети."""
    source = DNS_PROVIDERS if providers is None else providers
    result: dict = {}
    for group, items in source.items():
        kept = {name: data for name, data in items.items() if not data.get("local_proxy")}
        if kept:
            result[group] = kept
    return result


def is_encrypted_only(data: dict) -> bool:
    """Сервер не отвечает на обычный DNS: спросить его можно только шифрованным запросом."""
    return bool(data.get("encrypted_only"))


def find_provider_by_address(address: str) -> tuple[str, str, dict] | None:
    """(группа, название, данные) сервера, которому принадлежит адрес (IPv4 или IPv6)."""
    wanted = str(address or "").strip().lower()
    if not wanted:
        return None
    for group, name, data in iter_providers():
        if data.get("local_proxy"):
            continue
        for item in (*data.get("ipv4", ()), *data.get("ipv6", ())):
            if str(item).strip().lower() == wanted:
                return group, name, data
    return None


def catalog_problems() -> list[str]:
    """Нарушения правил списка; у исправного списка — пусто.

    Проверяет то, от чего зависит работа программы: адреса — настоящие IPv4 и
    IPv6 без повторов, основной адрес не занят другим сервером, шифрованный
    DoH задан адресом https (у сервера только с шифрованием он обязателен),
    пометка состояния — из известных.
    """
    from dns.local_proxy_catalog import MODES

    problems: list[str] = []
    names: set[str] = set()
    owners: dict[str, str] = {}
    modes: set[str] = set()
    for group, name, data in iter_providers():
        if name in names:
            problems.append(f"{name}: название повторяется")
        names.add(name)
        mode = str(data.get("local_proxy", ""))
        if mode or group == LOCAL_PROXY_GROUP:
            # Режим встроенного движка: адрес у всех один — этот компьютер.
            if mode not in MODES or mode in modes or group != LOCAL_PROXY_GROUP:
                problems.append(f"{name}: неверный или повторный режим шифрованного DNS «{mode}»")
            modes.add(mode)
            if list(data.get("ipv4", ())) != ["127.0.0.1"] or list(data.get("ipv6", ())) != ["::1"]:
                problems.append(f"{name}: у режима шифрованного DNS адреса должны быть 127.0.0.1 и ::1")
            if data.get("doh") or data.get("dot") or data.get("status"):
                problems.append(f"{name}: у режима шифрованного DNS не бывает DoH, DoT и пометки состояния")
            if not str(data.get("desc", "")).strip() or not str(data.get("icon", "")).strip():
                problems.append(f"{name}: нет пояснения или значка")
            continue
        ipv4 = [str(item) for item in data.get("ipv4", ())]
        ipv6 = [str(item) for item in data.get("ipv6", ())]
        if not ipv4:
            problems.append(f"{name}: нет адреса IPv4")
        for version, addresses in ((4, ipv4), (6, ipv6)):
            if len(set(addresses)) != len(addresses):
                problems.append(f"{name}: адрес IPv{version} записан дважды")
            for address in addresses:
                try:
                    valid = ipaddress.ip_address(address).version == version
                except ValueError:
                    valid = False
                if not valid:
                    problems.append(f"{name}: «{address}» — не адрес IPv{version}")
                elif owners.setdefault(address, name) != name:
                    problems.append(f"{name}: адрес {address} уже у сервера {owners[address]}")
        doh = str(data.get("doh", ""))
        if doh and not doh.startswith("https://"):
            problems.append(f"{name}: адрес DoH должен начинаться с https://")
        dot = str(data.get("dot", ""))
        if dot and ("/" in dot or ":" in dot):
            problems.append(f"{name}: в «dot» нужно только имя сервера")
        if data.get("encrypted_only") and not doh:
            problems.append(f"{name}: сервер только с шифрованием должен иметь адрес DoH")
        if data.get("status", "") not in ("", *STATUSES):
            problems.append(f"{name}: неизвестная пометка состояния «{data.get('status')}»")
        if not str(data.get("desc", "")).strip() or not str(data.get("icon", "")).strip():
            problems.append(f"{name}: нет пояснения или значка")
    return problems


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
    # «Xbox DNS (old)»: серверы молчат с сентября 2026 — плитка убрана, а тех, у
    # кого эти адреса остались на адаптере, программа переводит на действующий Xbox DNS.
    "176.99.11.77": "111.88.96.54",
    "80.78.247.254": "111.88.96.55",
}
