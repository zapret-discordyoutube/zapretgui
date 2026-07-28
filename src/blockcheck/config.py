"""BlockCheck configuration — timeouts, markers, DNS servers, errno codes."""

from __future__ import annotations

from settings.mode import RELATIVE_EXE_PATH_WINWS2

# ---------------------------------------------------------------------------
# Timeouts (seconds)
# ---------------------------------------------------------------------------
HTTPS_TIMEOUT = 10
STUN_TIMEOUT = 5
PING_TIMEOUT = 3
PING_COUNT = 2
DNS_TIMEOUT = 5
DOH_TIMEOUT = 8
ISP_PAGE_TIMEOUT = 8
TCP_16_20_TIMEOUT = 15
BASELINE_TIMEOUT = 4

# Проба сертификата в DNS-проверке: нужен только вердикт хендшейка, данные не
# читаются, поэтому таймаут короткий.
CERT_PROBE_TIMEOUT = 3

# Отдельный таймаут на установку TCP-соединения внутри HTTPS-пробы. Общий
# бюджет пробы (``HTTPS_TIMEOUT``) делится между несколькими адресами хоста,
# поэтому ждать на каждом полный таймаут нельзя.
HTTPS_CONNECT_TIMEOUT = 4

# Сколько адресов хоста пробуем, прежде чем признать пробу неудачной. Раньше
# перебирались все записи подряд, каждая с полным таймаутом: хост с четырьмя
# адресами стоил 40 секунд на одну пробу.
HTTPS_MAX_ADDRESSES = 2

# Верхняя граница на весь прогон. Превышение помечает недопроверенные цели как
# недостоверные, а не подвешивает GUI.
RUN_DEADLINE_SECONDS = 180

# ---------------------------------------------------------------------------
# Retries
# ---------------------------------------------------------------------------
# Повтор нужен только чтобы отличить настоящий DPI-обрыв на 16-20 КБ от разовой
# сетевой помехи, поэтому вторая попытка делается лишь при подозрении.
TCP_16_20_RETRIES = 2
DNS_RETRIES = 2

# ---------------------------------------------------------------------------
# TCP target selection
# ---------------------------------------------------------------------------
TCP_TARGET_MAX_COUNT = 6
TCP_TARGETS_PER_PROVIDER = 2

# ---------------------------------------------------------------------------
# TCP 16-20 KB block detection
# ---------------------------------------------------------------------------
TCP_BLOCK_RANGE_MIN = 15_000   # 15 KB
TCP_BLOCK_RANGE_MAX = 21_000   # 21 KB

# ---------------------------------------------------------------------------
# ISP block page markers (body content)
# ---------------------------------------------------------------------------
ISP_BODY_MARKERS: list[str] = [
    "eais.rkn.gov.ru",
    "rkn.gov.ru",
    "nap.rkn.gov.ru",
    "blocklist.rkn.gov.ru",
    "Роскомнадзор",
    "Roskomnadzor",
    "blocked by",
    "заблокирован",
    "ограничен доступ",
    "access denied",
    "access restricted",
    "is blocked",
    "web filter",
    "content filter",
    "warning: this site",
    "этот ресурс заблокирован",
    "federalnyj-zakon",
    "149-fz",
    "zapret-info",
]

# ---------------------------------------------------------------------------
# ISP redirect markers (URL patterns)
# ---------------------------------------------------------------------------
ISP_REDIRECT_MARKERS: list[str] = [
    "warning.rt.ru",
    "block.mts.ru",
    "blocked.beeline.ru",
    "block.megafon.ru",
    "zapret.",
    "blackhole.",
    "sorm.",
]

# ---------------------------------------------------------------------------
# DNS servers for integrity check
# ---------------------------------------------------------------------------
DNS_UDP_SERVERS: list[str] = [
    "8.8.8.8",         # Google
    "1.1.1.1",         # Cloudflare
    "77.88.8.8",       # Yandex
    "9.9.9.9",         # Quad9
]

DOH_SERVERS: list[dict[str, str]] = [
    {"name": "Google", "url": "https://dns.google/resolve"},
    {"name": "Cloudflare", "url": "https://cloudflare-dns.com/dns-query"},
]

# ---------------------------------------------------------------------------
# DNS check domains
# ---------------------------------------------------------------------------
DNS_CHECK_DOMAINS: list[str] = [
    "discord.com",
    "youtube.com",
    "rutracker.org",
    "linkedin.com",
    "telegram.org",
]

# ---------------------------------------------------------------------------
# Windows errno codes for DPI classification
# ---------------------------------------------------------------------------
WINDOWS_ERRNO_RESET = 10054         # WSAECONNRESET
WINDOWS_ERRNO_TIMEOUT = 10060       # WSAETIMEDOUT
WINDOWS_ERRNO_REFUSED = 10061       # WSAECONNREFUSED
WINDOWS_ERRNO_HOST_UNREACH = 10065  # WSAEHOSTUNREACH
WINDOWS_ERRNO_NET_UNREACH = 10051   # WSAENETUNREACH

# ---------------------------------------------------------------------------
# Thread pool
# ---------------------------------------------------------------------------
# Нагрузка чисто сетевая, потоки почти всё время спят в ожидании сокета —
# прежние 4 воркера растягивали ~300 проб на минуты.
DEFAULT_PARALLEL = 16
MAX_PARALLEL = 32

# ---------------------------------------------------------------------------
# Strategy scanner
# ---------------------------------------------------------------------------
STRATEGY_PROBE_TIMEOUT = 5        # seconds per HTTPS connect + TLS handshake
STRATEGY_RESPONSE_TIMEOUT = 3     # seconds to read HTTP response after TLS ok
STRATEGY_STARTUP_WAIT = 1.0       # seconds to wait for winws2 startup
STRATEGY_KILL_TIMEOUT = 4         # seconds to wait for winws2 shutdown
WINWS2_EXE_RELATIVE = RELATIVE_EXE_PATH_WINWS2
PROBE_TEMP_PRESET = "blockcheck_probe.txt"
PROBE_TEMP_HOSTLIST = "blockcheck_probe_hosts.txt"

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
PREFLIGHT_DNS_TIMEOUT = 3
PREFLIGHT_TCP_TIMEOUT = 2
PREFLIGHT_HTTP_TIMEOUT = 2
PREFLIGHT_PING_COUNT = 1
PREFLIGHT_PING_TIMEOUT = 2

# Known ISP block IPs (shared — also used by dns_checker.py)
KNOWN_BLOCK_IPS: set[str] = {
    "127.0.0.1",
    "0.0.0.0",
    "10.10.10.10",
    "195.82.146.214",    # Ростелеком
    "81.19.72.32",       # МТС
    "213.180.193.250",   # Билайн
    "217.169.80.229",    # Мегафон
    "62.33.207.196",     # РКН
    "62.33.207.197",     # РКН
    "62.33.207.198",     # РКН
}
