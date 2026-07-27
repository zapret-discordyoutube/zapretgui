"""HTTPS/TLS tester — extracted from blockcheck2.py with DPI classification."""

import re
import socket
import ssl
import time

from blockcheck.config import HTTPS_TIMEOUT
from blockcheck.dpi_classifier import classify_connect_error, classify_ssl_error
from blockcheck.models import SingleTestResult, TestStatus, TestType
from utils.net_resolve import DEFAULT_DNS_TIMEOUT, DNSTimeoutError, resolve_addrinfo


def _normalize_ip_family(ip_family: str | None) -> str:
    family = str(ip_family or "auto").strip().lower()
    if family in ("ipv4", "ip4", "v4", "4"):
        return "ipv4"
    if family in ("ipv6", "ip6", "v6", "6"):
        return "ipv6"
    return "auto"


def _resolve_connect_addrs(
    host: str, port: int, ip_family: str, dns_timeout: float = DEFAULT_DNS_TIMEOUT,
) -> list[tuple[int, tuple]]:
    if ip_family == "ipv4":
        family = socket.AF_INET
    elif ip_family == "ipv6":
        family = socket.AF_INET6
    else:
        family = socket.AF_UNSPEC

    infos = resolve_addrinfo(
        host, port,
        timeout=dns_timeout,
        family=family,
        socktype=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    addrs: list[tuple[int, tuple]] = []
    for info in infos:
        addr_family, _socktype, _proto, _canonname, sockaddr = info
        addrs.append((addr_family, sockaddr))
    return addrs


def test_https(
    host: str,
    port: int = 443,
    timeout: int = HTTPS_TIMEOUT,
    tls_version: str | None = None,
    ip_family: str = "auto",
) -> SingleTestResult:
    """Test HTTPS connection with optional TLS version pinning.

    Args:
        host: Target hostname
        port: Target port (default 443)
        timeout: Connection timeout in seconds
        tls_version: "1.2" or "1.3" to pin, None for any

    Returns:
        SingleTestResult with DPI-aware error classification
    """
    test_type = TestType.HTTP
    if tls_version == "1.2":
        test_type = TestType.TLS_12
    elif tls_version == "1.3":
        test_type = TestType.TLS_13

    start = time.time()
    bytes_read = 0
    family = _normalize_ip_family(ip_family)

    try:
        connect_addrs = _resolve_connect_addrs(host, port, family, dns_timeout=timeout)
    except DNSTimeoutError as e:
        return SingleTestResult(
            target_name=host, test_type=test_type,
            status=TestStatus.TIMEOUT, error_code="DNS_TIMEOUT",
            time_ms=round((time.time() - start) * 1000, 2),
            detail=str(e),
            raw_data={"ip_family": family},
        )
    except socket.gaierror as e:
        return SingleTestResult(
            target_name=host, test_type=test_type,
            status=TestStatus.ERROR, error_code="DNS_ERR",
            time_ms=round((time.time() - start) * 1000, 2),
            detail=f"DNS resolution failed: {str(e)[:80]}",
            raw_data={"ip_family": family},
        )
    except Exception as e:
        return SingleTestResult(
            target_name=host, test_type=test_type,
            status=TestStatus.ERROR, error_code="RESOLVE_ERR",
            time_ms=round((time.time() - start) * 1000, 2),
            detail=f"Resolve error: {str(e)[:80]}",
            raw_data={"ip_family": family},
        )

    if not connect_addrs:
        return SingleTestResult(
            target_name=host, test_type=test_type,
            status=TestStatus.UNSUPPORTED, error_code="NO_ADDR",
            time_ms=round((time.time() - start) * 1000, 2),
            detail=f"No addresses for requested family: {family}",
            raw_data={"ip_family": family},
        )

    last_exception: Exception | None = None

    for addr_family, sockaddr in connect_addrs:
        sock = None
        ssock = None
        try:
            sock = socket.socket(addr_family, socket.SOCK_STREAM)
            sock.settimeout(timeout)

            context = ssl.create_default_context()
            if tls_version == "1.2":
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                context.maximum_version = ssl.TLSVersion.TLSv1_2
            elif tls_version == "1.3":
                context.minimum_version = ssl.TLSVersion.TLSv1_3
                context.maximum_version = ssl.TLSVersion.TLSv1_3

            ssock = context.wrap_socket(sock, server_hostname=host)
            ssock.connect(sockaddr)

            actual_tls = ssock.version()

            request = (
                f"GET / HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"Connection: close\r\n"
                f"User-Agent: Mozilla/5.0\r\n\r\n"
            )
            ssock.sendall(request.encode())

            response = b""
            while len(response) < 2048:
                chunk = ssock.recv(4096)
                if not chunk:
                    break
                response += chunk
            bytes_read = len(response)

            ssock.close()

            elapsed = (time.time() - start) * 1000

            # Parse HTTP status
            status_code = None
            first_line = response.decode("utf-8", errors="ignore").split("\r\n")[0]
            match = re.search(r"HTTP/\d\.?\d?\s+(\d{3})", first_line)
            if match:
                status_code = int(match.group(1))

            resolved_family = "ipv6" if addr_family == socket.AF_INET6 else "ipv4"
            connected_ip = str(sockaddr[0]) if sockaddr else ""

            return SingleTestResult(
                target_name=host, test_type=test_type,
                status=TestStatus.OK, time_ms=round(elapsed, 2),
                detail=f"{actual_tls} HTTP {status_code or '?'}",
                raw_data={
                    "tls_version": actual_tls,
                    "status_code": status_code,
                    "bytes_read": bytes_read,
                    "ip_family": resolved_family,
                    "connected_ip": connected_ip,
                },
            )

        except ssl.SSLError as e:
            last_exception = e
            continue
        except socket.timeout as e:
            last_exception = e
            continue
        except (ConnectionResetError, ConnectionRefusedError, OSError) as e:
            last_exception = e
            continue
        except Exception as e:
            last_exception = e
            continue
        finally:
            if ssock is not None:
                try:
                    ssock.close()
                except Exception:
                    pass
            elif sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    if isinstance(last_exception, ssl.SSLError):
        e = last_exception
        elapsed = (time.time() - start) * 1000
        label, detail, _ = classify_ssl_error(e, bytes_read)

        # TLS version unsupported is not a failure
        if label == "TLS_UNSUPPORTED":
            return SingleTestResult(
                target_name=host, test_type=test_type,
                status=TestStatus.UNSUPPORTED, error_code=label,
                time_ms=round(elapsed, 2), detail=detail,
                raw_data={"ip_family": family},
            )

        return SingleTestResult(
            target_name=host, test_type=test_type,
            status=TestStatus.FAIL, error_code=label,
            time_ms=round(elapsed, 2), detail=detail,
            raw_data={"ip_family": family},
        )

    if isinstance(last_exception, socket.timeout):
        return SingleTestResult(
            target_name=host, test_type=test_type,
            status=TestStatus.TIMEOUT, error_code="TIMEOUT",
            time_ms=round((time.time() - start) * 1000, 2),
            detail="Connection timeout",
            raw_data={"ip_family": family},
        )

    if isinstance(last_exception, (ConnectionResetError, ConnectionRefusedError, OSError)):
        e = last_exception
        elapsed = (time.time() - start) * 1000
        label, detail, _ = classify_connect_error(e, bytes_read)
        return SingleTestResult(
            target_name=host, test_type=test_type,
            status=TestStatus.FAIL, error_code=label,
            time_ms=round(elapsed, 2), detail=detail,
            raw_data={"ip_family": family},
        )

    if last_exception is not None:
        e = last_exception
        return SingleTestResult(
            target_name=host, test_type=test_type,
            status=TestStatus.ERROR, error_code="ERROR",
            time_ms=round((time.time() - start) * 1000, 2),
            detail=str(e)[:100],
            raw_data={"ip_family": family},
        )

    return SingleTestResult(
        target_name=host, test_type=test_type,
        status=TestStatus.ERROR, error_code="NO_RESULT",
        time_ms=round((time.time() - start) * 1000, 2),
        detail="No connection attempts completed",
        raw_data={"ip_family": family},
    )
