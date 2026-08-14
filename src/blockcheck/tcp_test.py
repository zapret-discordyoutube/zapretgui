"""TCP 16-20KB block detection — tests for DPI that drops connections at specific byte counts."""

import time

from blockcheck.config import (
    TCP_16_20_RETRIES,
    TCP_16_20_TIMEOUT,
    TCP_BLOCK_RANGE_MAX,
    TCP_BLOCK_RANGE_MIN,
)
from blockcheck.models import SingleTestResult, TestStatus, TestType


def check_tcp_16_20_single(
    url: str,
    timeout: int = TCP_16_20_TIMEOUT,
) -> SingleTestResult:
    """Single TCP 16-20KB test — download and check if connection drops in the 16-20KB range.

    Some DPI systems reset TCP connections after receiving 16-20KB of data.
    This test downloads content and checks if the transfer stops in that range.
    """
    start = time.time()

    try:
        import requests
    except ImportError:
        return SingleTestResult(
            target_name=url, test_type=TestType.TCP_16_20,
            status=TestStatus.ERROR, error_code="NO_REQUESTS",
            detail="requests not installed",
        )

    bytes_received = 0

    try:
        with requests.Session() as client:
            client.max_redirects = 5
            with client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "*/*",
                },
                timeout=timeout,
                verify=True,
                allow_redirects=True,
                stream=True,
            ) as resp:
                for chunk in resp.iter_content(chunk_size=1024):
                    bytes_received += len(chunk)
                    # We only need to check up to 25KB
                    if bytes_received > 25_000:
                        break

        elapsed = (time.time() - start) * 1000

        if bytes_received > TCP_BLOCK_RANGE_MAX:
            return SingleTestResult(
                target_name=url, test_type=TestType.TCP_16_20,
                status=TestStatus.OK, time_ms=round(elapsed, 2),
                detail=f"Received {bytes_received}B (no 16-20KB block)",
                raw_data={"bytes_received": bytes_received},
            )
        elif TCP_BLOCK_RANGE_MIN <= bytes_received <= TCP_BLOCK_RANGE_MAX:
            return SingleTestResult(
                target_name=url, test_type=TestType.TCP_16_20,
                status=TestStatus.FAIL, error_code="TCP_16_20",
                time_ms=round(elapsed, 2),
                detail=f"Connection dropped at {bytes_received}B (16-20KB range)",
                raw_data={"bytes_received": bytes_received},
            )
        else:
            return SingleTestResult(
                target_name=url, test_type=TestType.TCP_16_20,
                status=TestStatus.OK, time_ms=round(elapsed, 2),
                detail=f"Received {bytes_received}B",
                raw_data={"bytes_received": bytes_received},
            )

    except Exception as e:
        elapsed = (time.time() - start) * 1000
        error_msg = str(e).lower()

        # Check if the connection was reset in the 16-20KB range
        if bytes_received > 0 and TCP_BLOCK_RANGE_MIN <= bytes_received <= TCP_BLOCK_RANGE_MAX:
            if "reset" in error_msg or "aborted" in error_msg:
                return SingleTestResult(
                    target_name=url, test_type=TestType.TCP_16_20,
                    status=TestStatus.FAIL, error_code="TCP_16_20",
                    time_ms=round(elapsed, 2),
                    detail=f"RST at {bytes_received}B (16-20KB DPI block)",
                    raw_data={"bytes_received": bytes_received, "error": str(e)[:80]},
                )

        return SingleTestResult(
            target_name=url, test_type=TestType.TCP_16_20,
            status=TestStatus.ERROR, error_code="TCP_ERR",
            time_ms=round(elapsed, 2),
            detail=f"{str(e)[:80]} ({bytes_received}B received)",
            raw_data={"bytes_received": bytes_received},
        )


def check_tcp_16_20(
    url: str,
    retries: int = TCP_16_20_RETRIES,
    timeout: int = TCP_16_20_TIMEOUT,
) -> SingleTestResult:
    """TCP 16-20KB test — повтор только при подозрении.

    Повтор нужен ровно для одного: отличить настоящий DPI-обрыв на границе
    16-20 КБ от разовой сетевой помехи. Пока первая попытка в эту границу не
    попала, повторять нечего — раньше каждая цель безусловно проверялась трижды.
    """
    first = check_tcp_16_20_single(url, timeout)
    if first.error_code != "TCP_16_20":
        return first

    results = [first]
    for _ in range(max(0, retries - 1)):
        results.append(check_tcp_16_20_single(url, timeout))

    fail_16_20 = [item for item in results if item.error_code == "TCP_16_20"]
    if len(fail_16_20) >= 2:
        bytes_vals = [item.raw_data.get("bytes_received", 0) for item in fail_16_20]
        return SingleTestResult(
            target_name=url, test_type=TestType.TCP_16_20,
            status=TestStatus.FAIL, error_code="TCP_16_20",
            time_ms=fail_16_20[0].time_ms,
            detail=f"Consistent 16-20KB block ({len(fail_16_20)}/{len(results)} attempts, "
                   f"bytes: {', '.join(str(b) for b in bytes_vals)})",
            raw_data={
                "attempts": len(results),
                "failures": len(fail_16_20),
                "bytes": bytes_vals,
            },
        )

    # Подозрение не подтвердилось — отдаём успешную попытку, если она была.
    successful = next((item for item in results if item.status == TestStatus.OK), None)
    return successful or results[-1]
