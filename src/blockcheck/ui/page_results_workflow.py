"""Workflow отрисовки результатов BlockCheck."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QTableWidgetItem

from blockcheck.ui.helpers import (
    build_family_tooltip,
    build_target_detail_text,
    format_result_detail,
    sort_results_by_family,
    truncate_detail,
)
from blockcheck.ui.dpi_labels import (
    DPI_BADGE_COLORS,
    DPI_LABELS_RU,
    INCONCLUSIVE_LABELS_RU,
    NEUTRAL_COLOR,
)
from ui.accessibility import set_item_accessible_text, set_state_text
from ui.widgets.fluent_item_tooltip import set_fluent_item_tooltip


_BLOCKCHECK_RESULT_TABLE_ACCESSIBILITY_INSTALLED = "blockcheckResultTableAccessibilityInstalled"


def make_readonly_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


def find_table_row(table, name: str) -> int:
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item and item.text() == name:
            return row
    return -1


def find_tcp_row(tcp_table, target_id: str) -> int:
    if tcp_table is None:
        return -1
    for row in range(tcp_table.rowCount()):
        item = tcp_table.item(row, 0)
        if item and item.text() == target_id:
            return row
    return -1


def result_display_rank(result) -> int:
    from blockcheck.models import TestStatus

    if result.status == TestStatus.FAIL:
        return 0
    if result.status == TestStatus.ERROR:
        return 1
    if result.status == TestStatus.TIMEOUT:
        return 2
    if result.status == TestStatus.UNSUPPORTED:
        return 3
    return 4


def set_status_cell(table, row: int, col: int, result) -> None:
    from blockcheck.models import TestStatus

    if result.status == TestStatus.OK:
        text = "OK"
        if result.time_ms:
            text += f" ({result.time_ms:.0f}ms)"
        item = make_readonly_item(text)
        item.setForeground(QColor("#52c477"))
    elif result.status == TestStatus.UNSUPPORTED:
        item = make_readonly_item("UNSUP")
        item.setForeground(QColor("#e0a854"))
    elif result.status == TestStatus.TIMEOUT:
        item = make_readonly_item("TIMEOUT")
        item.setForeground(QColor("#e0a854"))
    else:
        text = result.error_code or "FAIL"
        item = make_readonly_item(text)
        item.setForeground(QColor("#e05454"))

    set_fluent_item_tooltip(item, result.detail)
    table.setItem(row, col, item)


def set_dualstack_status_cell(table, row: int, col: int, results: list) -> None:
    from blockcheck.models import TestStatus

    if not results:
        return

    if len(results) == 1:
        set_status_cell(table, row, col, results[0])
        return

    sorted_results = sort_results_by_family(results)
    ok_results = [result for result in sorted_results if result.status == TestStatus.OK]
    non_ok_results = [result for result in sorted_results if result.status != TestStatus.OK]

    if ok_results and non_ok_results:
        item = make_readonly_item("MIXED")
        item.setForeground(QColor("#e0a854"))
    elif ok_results:
        best_ok = min(ok_results, key=lambda result: result.time_ms or 999999.0)
        text = "OK"
        if best_ok.time_ms:
            text += f" ({best_ok.time_ms:.0f}ms)"
        item = make_readonly_item(text)
        item.setForeground(QColor("#52c477"))
    elif all(result.status == TestStatus.TIMEOUT for result in sorted_results):
        item = make_readonly_item("TIMEOUT")
        item.setForeground(QColor("#e0a854"))
    elif all(result.status == TestStatus.UNSUPPORTED for result in sorted_results):
        item = make_readonly_item("UNSUP")
        item.setForeground(QColor("#e0a854"))
    else:
        primary = sorted(non_ok_results, key=result_display_rank)[0] if non_ok_results else sorted_results[0]
        text = primary.error_code or "FAIL"
        item = make_readonly_item(text)
        item.setForeground(QColor("#e05454"))

    set_fluent_item_tooltip(item, build_family_tooltip(sorted_results))
    table.setItem(row, col, item)


def tcp_status_text_and_color(result) -> tuple[str, str]:
    from blockcheck.models import TestStatus

    if result.status == TestStatus.OK:
        return "OK", "#52c477"
    if result.status == TestStatus.TIMEOUT:
        return "TIMEOUT", "#e0a854"
    if result.error_code == "TCP_16_20":
        return "DETECTED", "#e05454"
    if result.status == TestStatus.UNSUPPORTED:
        return "UNSUP", "#e0a854"
    if result.status == TestStatus.ERROR:
        return "ERROR", "#e05454"
    return (result.error_code or "FAIL"), "#e05454"


def update_tcp_result_table(*, target_result, tcp_table, tcp_section_label) -> None:
    from blockcheck.models import TestType

    if tcp_table is None:
        return
    ensure_blockcheck_result_table_current_row_accessibility(tcp_table, fallback_column=0)

    tcp_tests = sorted(
        [test for test in target_result.tests if test.test_type == TestType.TCP_16_20],
        key=lambda test: (test.raw_data or {}).get("target_id") or test.target_name or "",
    )
    if not tcp_tests:
        return

    if tcp_section_label is not None:
        tcp_section_label.setVisible(True)
    tcp_table.setVisible(True)

    for test in tcp_tests:
        raw = test.raw_data or {}
        target_id = str(raw.get("target_id") or test.target_name or "-")
        asn_raw = str(raw.get("asn") or "").strip()
        provider = str(raw.get("provider") or "-")

        if asn_raw:
            asn_text = asn_raw.upper() if asn_raw.upper().startswith("AS") else f"AS{asn_raw}"
        else:
            asn_text = "-"

        row = find_tcp_row(tcp_table, target_id)
        if row == -1:
            row = tcp_table.rowCount()
            tcp_table.insertRow(row)

        status_text, color = tcp_status_text_and_color(test)
        detail_text = format_result_detail(test)
        row_accessible_text = _tcp_row_accessible_text(
            target_id=target_id,
            asn_text=asn_text,
            provider=provider,
            status_text=status_text,
            detail_text=detail_text,
        )
        tcp_table.setItem(row, 0, _tcp_accessible_item(target_id, row_accessible_text))
        tcp_table.setItem(row, 1, _tcp_accessible_item(asn_text, row_accessible_text))
        tcp_table.setItem(row, 2, _tcp_accessible_item(provider, row_accessible_text))

        status_item = make_readonly_item(status_text)
        status_item.setForeground(QColor(color))
        set_fluent_item_tooltip(status_item, detail_text)
        set_item_accessible_text(status_item, row_accessible_text, description=detail_text)
        tcp_table.setItem(row, 3, status_item)

        bytes_received = raw.get("bytes_received")
        if isinstance(bytes_received, int) and bytes_received > 0:
            detail_text += f" | {bytes_received}B"
        detail_item = make_readonly_item(truncate_detail(detail_text))
        detail_item.setForeground(QColor("#9aa0a6"))
        set_fluent_item_tooltip(detail_item, detail_text)
        set_item_accessible_text(detail_item, row_accessible_text, description=detail_text)
        tcp_table.setItem(row, 4, detail_item)
        if tcp_table.currentRow() == row:
            _update_blockcheck_result_table_current_row_accessibility(tcp_table, row, tcp_table.currentColumn(), 0)


def ensure_blockcheck_result_table_current_row_accessibility(table, *, fallback_column: int) -> None:
    if table is None:
        return
    try:
        if bool(table.property(_BLOCKCHECK_RESULT_TABLE_ACCESSIBILITY_INSTALLED)):
            return
    except Exception:
        pass
    try:
        table.currentCellChanged.connect(
            lambda current_row, current_column, _previous_row, _previous_column, current_table=table, fallback=fallback_column: (
                _update_blockcheck_result_table_current_row_accessibility(
                    current_table,
                    current_row,
                    current_column,
                    fallback,
                )
            )
        )
        table.setProperty(_BLOCKCHECK_RESULT_TABLE_ACCESSIBILITY_INSTALLED, True)
    except Exception:
        pass


def _update_blockcheck_result_table_current_row_accessibility(
    table,
    row: int,
    column: int,
    fallback_column: int,
) -> None:
    if table is None:
        return
    row_text = ""
    try:
        item = table.item(int(row), int(column))
        if item is not None:
            row_text = str(item.data(Qt.ItemDataRole.AccessibleTextRole) or "").strip()
    except Exception:
        row_text = ""
    if not row_text:
        try:
            item = table.item(int(row), int(fallback_column))
            if item is not None:
                row_text = str(item.data(Qt.ItemDataRole.AccessibleTextRole) or "").strip()
        except Exception:
            row_text = ""
    if row_text:
        set_state_text(table, row_text)


def _tcp_accessible_item(text: str, accessible_text: str) -> QTableWidgetItem:
    item = make_readonly_item(text)
    set_item_accessible_text(item, accessible_text)
    return item


def _tcp_row_accessible_text(
    *,
    target_id: str,
    asn_text: str,
    provider: str,
    status_text: str,
    detail_text: str,
) -> str:
    parts = [
        f"TCP {str(target_id or '-').strip() or '-'}",
        str(asn_text or "-").strip() or "-",
        str(provider or "-").strip() or "-",
        f"статус {str(status_text or '-').strip() or '-'}",
    ]
    detail = str(detail_text or "").strip()
    if detail:
        parts.append(detail)
    return ", ".join(parts)


def _dpi_cell(target_result) -> QTableWidgetItem:
    """Ячейка колонки DPI: сигнатура, причина «без вывода» или прочерк.

    Причина окрашена нейтрально намеренно: недоступный хост не должен выглядеть
    в таблице так же, как обнаруженная блокировка. Пока цель не оценена
    (``not_probed``), показываем прочерк, а не пугающий текст.
    """
    from blockcheck.models import DPIClassification, InconclusiveReason, TargetOutcome

    cls = target_result.classification
    if cls != DPIClassification.NONE:
        item = make_readonly_item(DPI_LABELS_RU.get(cls.value, cls.value))
        item.setForeground(QColor(DPI_BADGE_COLORS.get(cls.value, NEUTRAL_COLOR)[0]))
        set_fluent_item_tooltip(item, target_result.classification_detail)
        return item

    reason = target_result.inconclusive_reason
    if (
        target_result.outcome == TargetOutcome.INCONCLUSIVE
        and reason not in (InconclusiveReason.NONE, InconclusiveReason.NOT_PROBED)
    ):
        item = make_readonly_item(INCONCLUSIVE_LABELS_RU.get(reason.value, "Без вывода"))
        item.setForeground(QColor(NEUTRAL_COLOR[0]))
        set_fluent_item_tooltip(item, target_result.classification_detail)
        return item

    return make_readonly_item("—")


def update_target_result_table(*, target_result, table, tcp_table, tcp_section_label) -> None:
    from blockcheck.models import TestStatus, TestType

    ensure_blockcheck_result_table_current_row_accessibility(table, fallback_column=0)
    tests = target_result.tests

    if str(target_result.value).startswith("TCP:"):
        update_tcp_result_table(
            target_result=target_result,
            tcp_table=tcp_table,
            tcp_section_label=tcp_section_label,
        )
        return

    row = find_table_row(table, target_result.name)
    if row == -1:
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, make_readonly_item(target_result.name))

    http_tests = [test for test in tests if test.test_type == TestType.HTTP]
    if http_tests:
        set_dualstack_status_cell(table, row, 1, http_tests)

    tls12 = [test for test in tests if test.test_type == TestType.TLS_12]
    tls13 = [test for test in tests if test.test_type == TestType.TLS_13]
    if tls12:
        tls13_ok = any(result.status == TestStatus.OK for result in tls13)
        tls12_all_fail = all(result.status != TestStatus.OK for result in tls12)
        if tls12_all_fail and tls13_ok:
            detail = build_family_tooltip(tls12)
            detail += "\nDPI блокирует SNI в TLS 1.2; сайт работает через TLS 1.3"
            item = make_readonly_item("DPI 1.2")
            item.setForeground(QColor("#e0a854"))
            set_fluent_item_tooltip(item, detail)
            table.setItem(row, 2, item)
        else:
            set_dualstack_status_cell(table, row, 2, tls12)

    if tls13:
        set_dualstack_status_cell(table, row, 3, tls13)

    dns_tests = [
        test for test in tests
        if test.test_type in (TestType.DNS_UDP, TestType.DNS_DOH)
    ]
    isp_tests = [test for test in tests if test.test_type == TestType.ISP_PAGE]
    if dns_tests:
        set_status_cell(table, row, 4, dns_tests[0])
    elif isp_tests:
        set_status_cell(table, row, 4, isp_tests[0])

    table.setItem(row, 5, _dpi_cell(target_result))

    ping = [test for test in tests if test.test_type == TestType.PING]
    if ping:
        set_dualstack_status_cell(table, row, 6, ping)

    stun = [test for test in tests if test.test_type == TestType.STUN]
    if stun and not http_tests:
        set_status_cell(table, row, 1, stun[0])

    detail_text = build_target_detail_text(tests)
    detail_cell = make_readonly_item(truncate_detail(detail_text))
    detail_cell.setForeground(QColor("#9aa0a6"))
    set_fluent_item_tooltip(detail_cell, detail_text)
    table.setItem(row, 7, detail_cell)
    _apply_target_row_accessibility(table, row, detail_text=detail_text)
    if table.currentRow() == row:
        _update_blockcheck_result_table_current_row_accessibility(table, row, table.currentColumn(), 0)


_TARGET_TABLE_ACCESSIBLE_COLUMNS = {
    1: "HTTP",
    2: "TLS 1.2",
    3: "TLS 1.3",
    4: "DNS/ISP",
    5: "DPI",
    6: "Ping",
    7: "детали",
}


def _apply_target_row_accessibility(table, row: int, *, detail_text: str) -> None:
    target_item = table.item(row, 0)
    target_name = str(target_item.text() if target_item is not None else "").strip()
    parts = [target_name] if target_name else []

    for col, label in _TARGET_TABLE_ACCESSIBLE_COLUMNS.items():
        item = table.item(row, col)
        if item is None:
            continue
        value = str(item.text() or "").strip()
        if not value or value == "—":
            continue
        if col == 7 and detail_text:
            value = str(detail_text or "").strip()
        if value:
            parts.append(f"{label} {value}")

    accessible_text = ", ".join(parts)
    if not accessible_text:
        return

    description = str(detail_text or "").strip() or None
    for col in range(table.columnCount()):
        item = table.item(row, col)
        if item is not None:
            set_item_accessible_text(item, accessible_text, description=description)
