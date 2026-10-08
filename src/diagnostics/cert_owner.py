"""Кто ответил вместо сайта: чей сертификат пришёл, когда он не прошёл проверку.

«Чужой сертификат» — это не блокировка: соединение установилось, но с кем-то
другим. Что делать, зависит от того, кто это, и по одной ошибке проверки этого
не понять. Поэтому сертификат запрашивается ещё раз, без проверки, и читается
как справка: кому он выдан и кем.

Что из этого следует:

- издатель — антивирус или фильтр на компьютере → он просматривает шифрованные
  соединения; лечится в его настройках;
- издатель — отладочный прокси → он запущен и стоит между браузером и сайтом;
- издатель — российский государственный центр → сайт выдал такой сертификат
  сам, браузеру его нужно установить;
- сертификат настоящий, но выдан другому сайту, а адрес взят из файла hosts →
  запись в hosts ведёт на чужой сервер (посредник сменился или умер);
- то же без hosts → вместо сайта отвечает чужой сервер по дороге (заглушка
  провайдера, перехват);
- сертификат выдан сам себе → заглушка или самодельный посредник.

Сбор (``collect``) отделён от вывода (``judge``).
"""

from __future__ import annotations

import socket
import ssl
from dataclasses import dataclass

from utils.cert_reader import CertNames, read_names
from utils.socket_cancel import SocketCancel, close_quietly

__all__ = [
    "CERT_ANTIVIRUS",
    "CERT_DEBUG_PROXY",
    "CERT_HOSTS",
    "CERT_OTHER_SITE",
    "CERT_SELF_SIGNED",
    "CERT_STATE",
    "CERT_UNKNOWN",
    "CERT_UNKNOWN_ISSUER",
    "CertVerdict",
    "collect",
    "judge",
]

TIMEOUT_S = 4.0

CERT_ANTIVIRUS = "antivirus"
CERT_DEBUG_PROXY = "debug_proxy"
CERT_STATE = "state_ca"
CERT_HOSTS = "hosts"
CERT_OTHER_SITE = "other_site"
CERT_SELF_SIGNED = "self_signed"
CERT_UNKNOWN_ISSUER = "unknown_issuer"
# Сертификат прочитать не удалось: кто ответил, неизвестно.
CERT_UNKNOWN = "unknown"

# Слово в имени издателя → как назвать программу. Порядок важен: первое совпадение.
_ANTIVIRUS = (
    ("kaspersky", "Kaspersky"),
    ("eset", "ESET"),
    ("avast", "Avast"),
    ("avg ", "AVG"),
    ("bitdefender", "Bitdefender"),
    ("dr.web", "Dr.Web"),
    ("drweb", "Dr.Web"),
    ("adguard", "AdGuard"),
    ("norton", "Norton"),
    ("mcafee", "McAfee"),
    ("sophos", "Sophos"),
    ("fortinet", "Fortinet (фильтр сети)"),
    ("fortigate", "Fortinet (фильтр сети)"),
    ("zscaler", "Zscaler (фильтр сети)"),
    ("netskope", "Netskope (фильтр сети)"),
    ("cisco umbrella", "Cisco Umbrella (фильтр сети)"),
    ("kerio", "Kerio (фильтр сети)"),
    ("usergate", "UserGate (фильтр сети)"),
    ("traffic inspector", "Traffic Inspector (фильтр сети)"),
)
_DEBUG_PROXIES = (
    ("fiddler", "Fiddler"),
    ("mitmproxy", "mitmproxy"),
    ("charles proxy", "Charles"),
    ("burp", "Burp Suite"),
    ("portswigger", "Burp Suite"),
    ("http toolkit", "HTTP Toolkit"),
    ("proxyman", "Proxyman"),
)
_STATE_CA = ("russian trusted",)


@dataclass(frozen=True, slots=True)
class CertVerdict:
    code: str
    # Одной фразой: кто ответил вместо сайта.
    text: str
    # Что с этим делать. Пусто — совет общий.
    advice: str = ""
    # Что написано в сертификате — для подробностей.
    subject: str = ""
    issuer: str = ""
    names: tuple[str, ...] = ()


def collect(host: str, ip: str, *, cancel: SocketCancel | None = None) -> CertNames | None:
    """Сертификат, который отдаёт адрес ``ip`` для сайта ``host``, — без проверки. None — получить не удалось."""
    token = cancel or SocketCancel()
    sock: socket.socket | None = None
    wrapped: ssl.SSLSocket | None = None
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        sock = socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM)
        if not token.track(sock):
            return None
        sock.settimeout(TIMEOUT_S)
        sock.connect((ip, 443))
        wrapped = context.wrap_socket(sock, server_hostname=host)
        token.track(wrapped)
        return read_names(wrapped.getpeercert(binary_form=True))
    except (OSError, ssl.SSLError, ValueError):
        return None
    finally:
        for item in (wrapped, sock):
            if item is not None:
                token.release(item)
                close_quietly(item)


def _covers(names: tuple[str, ...], host: str) -> bool:
    host = host.lower().rstrip(".")
    for name in names:
        if name == host:
            return True
        if name.startswith("*.") and host.count(".") >= 2 and host.split(".", 1)[1] == name[2:]:
            return True
    return False


def _known(issuer: str, table) -> str:
    text = f"{issuer} ".lower()
    return next((title for word, title in table if word in text), "")


def judge(cert: CertNames | None, host: str, *, problem: str = "", from_hosts: bool = False) -> CertVerdict:
    """Кто ответил вместо сайта ``host``.

    ``problem`` — что сказала проверка сертификата (``tls_probe``), ``from_hosts`` —
    адрес взят из файла hosts.
    """
    if cert is None:
        return CertVerdict(
            CERT_UNKNOWN,
            f"вместо сайта отвечает кто-то другой ({problem or 'сертификат не прошёл проверку'}); "
            "прочитать сам сертификат не удалось",
        )
    issuer = " / ".join(part for part in (cert.issuer, cert.issuer_org) if part) or "не указан"
    subject = cert.subject or (cert.names[0] if cert.names else "не указан")
    facts = {"subject": subject, "issuer": issuer, "names": cert.names}

    antivirus = _known(issuer, _ANTIVIRUS)
    if antivirus:
        return CertVerdict(
            CERT_ANTIVIRUS,
            f"шифрованное соединение просматривает {antivirus}: сертификат выдан им, а не сайту",
            f"Это не блокировка провайдера. В настройках {antivirus} отключите проверку защищённых (HTTPS/SSL) "
            "соединений или добавьте сайт в исключения — иначе браузер будет считать сайт подделкой.",
            **facts,
        )
    proxy = _known(issuer, _DEBUG_PROXIES)
    if proxy:
        return CertVerdict(
            CERT_DEBUG_PROXY,
            f"между компьютером и сайтом стоит отладочный прокси {proxy}: сертификат выдан им",
            f"Закройте {proxy} или уберите его из настроек прокси Windows.",
            **facts,
        )
    if _known(issuer, tuple((word, word) for word in _STATE_CA)):
        return CertVerdict(
            CERT_STATE,
            f"сайт отдаёт сертификат российского государственного центра ({cert.issuer})",
            "Это не перехват: сайт сам перешёл на такой сертификат. Браузер примет его после установки "
            "корневого сертификата Минцифры, либо откройте сайт в Яндекс Браузере.",
            **facts,
        )
    if cert.self_signed:
        where = "запись в файле hosts ведёт на сервер, который" if from_hosts else "вместо сайта отвечает сервер, который"
        return CertVerdict(
            CERT_SELF_SIGNED,
            f"{where} показывает самодельный сертификат (выдан сам себе: {subject})",
            _ADVICE_HOSTS if from_hosts else _ADVICE_ROAD,
            **facts,
        )
    if cert.names and not _covers(cert.names, host):
        shown = ", ".join(cert.names[:3]) + (" и другим" if len(cert.names) > 3 else "")
        if from_hosts:
            return CertVerdict(
                CERT_HOSTS,
                f"запись в файле hosts ведёт на чужой сервер: его сертификат выдан сайту {shown}, а не {host}",
                _ADVICE_HOSTS,
                **facts,
            )
        return CertVerdict(
            CERT_OTHER_SITE,
            f"вместо сайта отвечает чужой сервер: его сертификат выдан сайту {shown}, а не {host}",
            _ADVICE_ROAD,
            **facts,
        )
    return CertVerdict(
        CERT_UNKNOWN_ISSUER,
        f"сертификат сайта не прошёл проверку ({problem or 'причина не названа'}); выдан: {issuer}",
        "Проверьте дату и время Windows: при неверных часах любой сертификат выглядит просроченным. "
        "Если часы верны — соединение перехватывает программа на компьютере или сеть.",
        **facts,
    )


_ADVICE_HOSTS = (
    "Адрес этого сайта записан в файле hosts и больше не подходит: сервер-посредник сменился или перестал "
    "работать. Откройте «Редактор hosts», выключите и снова включите этот сервис или выберите для него "
    "другой профиль — запишется рабочий адрес."
)
_ADVICE_ROAD = (
    "Так выглядит заглушка провайдера или перехват соединения по дороге. Включите шифрованный DNS "
    "(«Настройка DNS») и проверьте, не включён ли в системе прокси."
)
