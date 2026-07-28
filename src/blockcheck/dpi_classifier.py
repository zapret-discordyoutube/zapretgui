"""Таксономия сетевых ошибок: исключение сокета → короткий код и описание.

Агрегация результатов переехала в :mod:`blockcheck.verdict.signatures`:
решение о блокировке нельзя принимать по одной цели в отрыве от состояния
сети, а прежний ``DPIClassifier`` именно это и делал.
"""

from __future__ import annotations

from blockcheck.config import (
    WINDOWS_ERRNO_HOST_UNREACH,
    WINDOWS_ERRNO_NET_UNREACH,
    WINDOWS_ERRNO_REFUSED,
    WINDOWS_ERRNO_RESET,
    WINDOWS_ERRNO_TIMEOUT,
)


# ---------------------------------------------------------------------------
# SSL / TLS error classification
# ---------------------------------------------------------------------------

def classify_ssl_error(error: Exception, bytes_read: int = 0) -> tuple[str, str, int]:
    """Classify an ssl.SSLError into (label, detail, bytes_read).

    Returns a tuple of:
    - label: short error classification string
    - detail: human-readable description
    - bytes_read: estimated bytes transferred before error
    """
    msg = str(error).lower()

    # Connection reset during TLS handshake — classic DPI signature
    if "connection reset" in msg or "connection was reset" in msg:
        return "TLS_RESET", "TCP RST during TLS handshake (DPI)", 0

    # EOF during handshake — another DPI pattern
    if "eof occurred" in msg or "unexpected eof" in msg:
        if bytes_read == 0:
            return "TLS_EOF_EARLY", "EOF during handshake (DPI or firewall)", 0
        return "TLS_EOF_DATA", "EOF after partial data", bytes_read

    # Certificate errors — MITM detection
    if "certificate verify failed" in msg:
        if "self signed" in msg or "self-signed" in msg:
            return "TLS_MITM_SELF", "Self-signed cert (possible MITM proxy)", 0
        if "unable to get local issuer" in msg:
            return "TLS_MITM_UNKNOWN_CA", "Unknown CA (possible MITM)", 0
        return "TLS_CERT_ERR", "Certificate verification failed", 0

    # Version / cipher mismatch
    if "unsupported" in msg or "no protocols available" in msg:
        return "TLS_UNSUPPORTED", "TLS version not supported by server", 0
    if "version" in msg:
        return "TLS_VERSION", "TLS version mismatch", 0
    if "handshake failure" in msg or "sslv3 alert handshake" in msg:
        return "TLS_HANDSHAKE", "Handshake failure", 0

    # Alert-based
    if "alert" in msg:
        if "internal error" in msg:
            return "TLS_ALERT_INTERNAL", "Server internal error alert", 0
        if "unrecognized_name" in msg:
            return "TLS_SNI_REJECT", "SNI rejected by server", 0
        return "TLS_ALERT", f"TLS alert: {msg[:80]}", 0

    # Timeout during SSL
    if "timed out" in msg:
        return "TLS_TIMEOUT", "TLS handshake timeout (possible DPI)", 0

    return "TLS_ERR", f"SSL error: {msg[:100]}", bytes_read


# ---------------------------------------------------------------------------
# TCP connect error classification
# ---------------------------------------------------------------------------

def classify_connect_error(error: Exception, bytes_read: int = 0) -> tuple[str, str, int]:
    """Classify a connection-level error."""
    msg = str(error).lower()
    errno = getattr(error, "errno", None) or getattr(error, "winerror", 0)

    if isinstance(error, ConnectionResetError) or errno == WINDOWS_ERRNO_RESET:
        return "TCP_RESET", "Connection reset by remote (DPI or firewall)", 0

    if isinstance(error, ConnectionRefusedError) or errno == WINDOWS_ERRNO_REFUSED:
        return "TCP_REFUSED", "Connection refused", 0

    if errno == WINDOWS_ERRNO_TIMEOUT or "timed out" in msg:
        return "TCP_TIMEOUT", "TCP connection timeout", 0

    if errno == WINDOWS_ERRNO_HOST_UNREACH:
        return "HOST_UNREACH", "Host unreachable", 0

    if errno == WINDOWS_ERRNO_NET_UNREACH:
        return "NET_UNREACH", "Network unreachable", 0

    if "connection aborted" in msg:
        return "TCP_ABORT", "Connection aborted", 0

    return "CONNECT_ERR", f"Connection error: {msg[:100]}", bytes_read


# ---------------------------------------------------------------------------
# Read / response error classification
# ---------------------------------------------------------------------------

def classify_read_error(error: Exception, bytes_read: int = 0) -> tuple[str, str, int]:
    """Classify an error during response reading."""
    msg = str(error).lower()

    if "reset" in msg:
        return "READ_RESET", f"Reset after {bytes_read}B read (DPI mid-stream)", bytes_read

    if "timed out" in msg:
        return "READ_TIMEOUT", f"Read timeout after {bytes_read}B", bytes_read

    if "broken pipe" in msg or "connection aborted" in msg:
        return "READ_BROKEN", f"Broken pipe after {bytes_read}B", bytes_read

    return "READ_ERR", f"Read error: {msg[:80]}", bytes_read
