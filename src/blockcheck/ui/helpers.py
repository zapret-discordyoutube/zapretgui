"""Плашки своих доменов BlockCheck: добавить, убрать, собрать список."""

from __future__ import annotations


def add_domain_chip(*, domain: str, flow_widget, flow_layout, chip_cls, on_removed) -> None:
    chip = chip_cls(domain, parent=flow_widget)
    chip.removed.connect(on_removed)
    index = max(0, flow_layout.count() - 1)
    flow_layout.insertWidget(index, chip)


def remove_domain_chip(*, domain: str, flow_layout, chip_cls) -> bool:
    for i in range(flow_layout.count()):
        item = flow_layout.itemAt(i)
        if item and item.widget() and isinstance(item.widget(), chip_cls):
            if getattr(item.widget(), "_domain", None) == domain:
                widget = item.widget()
                flow_layout.removeWidget(widget)
                widget.deleteLater()
                return True
    return False


def collect_extra_domains(*, flow_layout, chip_cls) -> list[str]:
    domains: list[str] = []
    for i in range(flow_layout.count()):
        item = flow_layout.itemAt(i)
        if item and item.widget() and isinstance(item.widget(), chip_cls):
            domains.append(item.widget()._domain)
    return domains
