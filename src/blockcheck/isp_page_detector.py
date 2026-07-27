"""ISP block page and HTTP injection detection."""

import re
import socket
import time

from blockcheck.config import (
    ISP_BODY_MARKERS,
    ISP_PAGE_TIMEOUT,
    ISP_REDIRECT_MARKERS,
)
from blockcheck.models import SingleTestResult, TestStatus, TestType
from utils.net_resolve import resolve_ipv4


def check_http_injection(
    domain: str,
    timeout: int = ISP_PAGE_TIMEOUT,
    resolved_ip: str | None = None,
) -> SingleTestResult:
    """Check for HTTP injection on port 80.

    Sends a plain HTTP GET and checks if the response is from the real server
    or an injected block page (common DPI technique).

    Подключаемся по IP: ``sock.connect((domain, 80))`` сначала уходит в
    неограниченный по времени резолв, на который ``settimeout`` не влияет.
    """
    start = time.time()
    sock = None

    try:
        host_ip = resolved_ip or resolve_ipv4(domain, timeout=timeout)
        if not host_ip:
            return SingleTestResult(
                target_name=domain, test_type=TestType.ISP_PAGE,
                status=TestStatus.ERROR, error_code="CONNECT_ERR",
                time_ms=round((time.time() - start) * 1000, 2),
                detail="нет IPv4-адреса для проверки порта 80",
            )

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host_ip, 80))

        request = (
            f"GET / HTTP/1.1\r\n"
            f"Host: {domain}\r\n"
            f"Connection: close\r\n"
            f"User-Agent: Mozilla/5.0\r\n\r\n"
        )
        sock.sendall(request.encode())

        response = b""
        while len(response) < 16384:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
            except socket.timeout:
                break

        elapsed = (time.time() - start) * 1000
        body = response.decode("utf-8", errors="ignore")

        # Check for ISP markers in body
        for marker in ISP_BODY_MARKERS:
            if marker.lower() in body.lower():
                return SingleTestResult(
                    target_name=domain, test_type=TestType.ISP_PAGE,
                    status=TestStatus.FAIL, error_code="HTTP_INJECT",
                    time_ms=round(elapsed, 2),
                    detail=f"HTTP injection detected (marker: {marker})",
                    raw_data={"marker": marker, "body_len": len(body)},
                )

        # Check for redirect to ISP block page
        location_match = re.search(r"Location:\s*(.+?)[\r\n]", body, re.IGNORECASE)
        if location_match:
            location = location_match.group(1).strip()
            for redir_marker in ISP_REDIRECT_MARKERS:
                if redir_marker in location.lower():
                    return SingleTestResult(
                        target_name=domain, test_type=TestType.ISP_PAGE,
                        status=TestStatus.FAIL, error_code="HTTP_INJECT",
                        time_ms=round(elapsed, 2),
                        detail=f"Redirect to ISP block page: {location}",
                        raw_data={"redirect": location},
                    )

        return SingleTestResult(
            target_name=domain, test_type=TestType.ISP_PAGE,
            status=TestStatus.OK, time_ms=round(elapsed, 2),
            detail="No HTTP injection detected",
        )

    except socket.timeout:
        return SingleTestResult(
            target_name=domain, test_type=TestType.ISP_PAGE,
            status=TestStatus.TIMEOUT, error_code="TIMEOUT",
            time_ms=round((time.time() - start) * 1000, 2),
            detail="HTTP connection timeout",
        )
    except (ConnectionResetError, ConnectionRefusedError, OSError) as e:
        return SingleTestResult(
            target_name=domain, test_type=TestType.ISP_PAGE,
            status=TestStatus.FAIL, error_code="CONNECT_ERR",
            time_ms=round((time.time() - start) * 1000, 2),
            detail=str(e)[:100],
        )
    except Exception as e:
        return SingleTestResult(
            target_name=domain, test_type=TestType.ISP_PAGE,
            status=TestStatus.ERROR, error_code="ERROR",
            time_ms=round((time.time() - start) * 1000, 2),
            detail=str(e)[:100],
        )
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def detect_isp_page(
    domain: str,
    timeout: int = ISP_PAGE_TIMEOUT,
) -> SingleTestResult:
    """Detect ISP block page via HTTPS with body inspection.

    Uses httpx to follow redirects and inspect the final page content.
    """
    start = time.time()

    try:
        import httpx
    except ImportError:
        return SingleTestResult(
            target_name=domain, test_type=TestType.ISP_PAGE,
            status=TestStatus.ERROR, error_code="NO_HTTPX",
            detail="httpx not installed",
        )

    try:
        with httpx.Client(
            timeout=timeout,
            verify=False,  # We want to see the page even with bad certs
            follow_redirects=True,
            max_redirects=5,
        ) as client:
            resp = client.get(f"https://{domain}/")
            elapsed = (time.time() - start) * 1000
            body = resp.text[:8192]

            # Check redirect chain for ISP markers
            for history_resp in resp.history:
                location = str(history_resp.headers.get("location", ""))
                for marker in ISP_REDIRECT_MARKERS:
                    if marker in location.lower():
                        return SingleTestResult(
                            target_name=domain, test_type=TestType.ISP_PAGE,
                            status=TestStatus.FAIL, error_code="ISP_PAGE",
                            time_ms=round(elapsed, 2),
                            detail=f"Redirected to ISP block page: {location}",
                            raw_data={"redirect": location},
                        )

            # Check body for ISP markers
            for marker in ISP_BODY_MARKERS:
                if marker.lower() in body.lower():
                    return SingleTestResult(
                        target_name=domain, test_type=TestType.ISP_PAGE,
                        status=TestStatus.FAIL, error_code="ISP_PAGE",
                        time_ms=round(elapsed, 2),
                        detail=f"ISP block page detected (marker: {marker})",
                        raw_data={"marker": marker, "status": resp.status_code},
                    )

            return SingleTestResult(
                target_name=domain, test_type=TestType.ISP_PAGE,
                status=TestStatus.OK, time_ms=round(elapsed, 2),
                detail=f"HTTP {resp.status_code}, no ISP page",
                raw_data={"status_code": resp.status_code},
            )

    except Exception as e:
        return SingleTestResult(
            target_name=domain, test_type=TestType.ISP_PAGE,
            status=TestStatus.ERROR, error_code="ISP_ERR",
            time_ms=round((time.time() - start) * 1000, 2),
            detail=str(e)[:100],
        )
