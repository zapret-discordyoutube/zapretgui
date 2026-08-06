"""
server_config.py
────────────────────────────────────────────────────────────────
Конфигурация серверов для балансировки нагрузки.
Адреса серверов загружаются из config/_build_secrets.py (генерируется при сборке).
"""

# ═══════════════════════════════════════════════════════════════
# СПИСОК VPS СЕРВЕРОВ (из _build_secrets при сборке, иначе пуст)
# ═══════════════════════════════════════════════════════════════

from config._build_secrets import UPDATE_SERVERS as VPS_SERVERS

# ═══════════════════════════════════════════════════════════════
# НАСТРОЙКИ БАЛАНСИРОВКИ
# ═══════════════════════════════════════════════════════════════

MAX_CONSECUTIVE_FAILURES = 3
SERVER_BLOCK_DURATION = 3600  # 1 час
FAST_RESPONSE_THRESHOLD = 2000  # 2 секунды
AUTO_SWITCH_TO_FASTER = True
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 15

# ═══════════════════════════════════════════════════════════════
# ОБРАТНАЯ СОВМЕСТИМОСТЬ
# ═══════════════════════════════════════════════════════════════

_primary_server = VPS_SERVERS[0] if VPS_SERVERS else None

if _primary_server:
    VPS_SERVER = _primary_server['host']
    HTTPS_PORT = _primary_server['https_port']
    HTTP_PORT = _primary_server['http_port']
    
    VPS_BASE_URLS = {
        'https': f"https://{VPS_SERVER}:{HTTPS_PORT}",
        'http': f"http://{VPS_SERVER}:{HTTP_PORT}"
    }
else:
    VPS_SERVER = None
    HTTPS_PORT = None
    HTTP_PORT = None
    VPS_BASE_URLS = {}

# ═══════════════════════════════════════════════════════════════
# НАСТРОЙКИ SSL
# ═══════════════════════════════════════════════════════════════

VERIFY_SSL = False

def should_verify_ssl() -> bool:
    return VERIFY_SSL
