"""Окно подробностей по одному DNS-серверу: открывается нажатием на карточку.

Карточка показывает вывод коротко; здесь — всё, что о сервере узнали: каждый
адрес, каждый способ связи с причиной отказа, выводы целыми фразами, кто на
самом деле выполняет запросы и что сервер ответил про контрольные сайты
обычным и шифрованным путём.
"""

from __future__ import annotations

from html import escape

import dns.server_check_plans as plans
from dns.server_check import LEVEL_FAIL, LEVEL_OK, LEVEL_WARN
from dns.ui.server_check_cards import _cell_color, status_color
from ui.log_report_dialog import show_log_report_dialog
from ui.theme import get_theme_tokens

_MARKS = {LEVEL_FAIL: "✗", LEVEL_WARN: "!", LEVEL_OK: "✓"}
_FINDING_STATUS = {LEVEL_FAIL: plans.CARD_NETWORK, LEVEL_WARN: plans.CARD_PARTIAL, LEVEL_OK: plans.CARD_OK}


def _muted() -> str:
    return "#5f6368" if get_theme_tokens().is_light else "#a0a4ab"


def _cell_html(text: str, level: str) -> str:
    if level == plans.CELL_MUTED:
        return f'<span style="color:{_muted()}">{escape(text)}</span>'
    return f'<span style="color:{_cell_color(level).name()}; font-weight:600">{escape(text)}</span>'


def _address_html(address: plans.AddressDetails) -> str:
    muted = _muted()
    color = status_color(address.status).name() if address.status != plans.CARD_SILENT else muted
    parts = [
        f'<p style="font-size:15px; font-weight:600; margin-top:22px; margin-bottom:6px">{escape(address.address)}'
        f'&nbsp;&nbsp;<span style="color:{color}; font-weight:400">{escape(plans.CARD_TITLES[address.status])}</span></p>',
        '<table cellspacing="0" cellpadding="5">',
    ]
    for title, text, level, reason in address.cells:
        # Причину не повторяем, если она уже стоит в самой ячейке.
        extra = reason if reason and reason != text else ""
        parts.append(
            f'<tr><td style="color:{muted}">{escape(title)}</td><td>{_cell_html(text, level)}</td>'
            f'<td style="color:{muted}">{escape(extra)}</td></tr>'
        )
    parts.append("</table>")
    for level, text in address.findings:
        mark_color = status_color(_FINDING_STATUS[level]).name() if level in _FINDING_STATUS else muted
        parts.append(
            f'<p style="margin-top:6px; margin-bottom:0"><span style="color:{mark_color}; font-weight:600">'
            f'{_MARKS.get(level, "·")}</span>&nbsp;{escape(text)}</p>'
        )
    for line in address.who:
        parts.append(f'<p style="color:{muted}; margin-top:6px; margin-bottom:0">{escape(line)}</p>')
    if address.domains:
        differs = status_color(plans.CARD_NETWORK).name()
        parts.append(
            f'<p style="color:{muted}; margin-top:12px; margin-bottom:2px">Что сервер ответил про контрольные сайты</p>'
            f'<table cellspacing="0" cellpadding="5"><tr><td style="color:{muted}">Сайт</td>'
            f'<td style="color:{muted}">Обычным путём</td><td style="color:{muted}">Шифрованным</td></tr>'
        )
        for domain, plain, secure, different in address.domains:
            plain_html = f'<span style="color:{differs}; font-weight:600">{escape(plain)}</span>' if different else escape(plain)
            parts.append(f"<tr><td>{escape(domain)}</td><td>{plain_html}</td><td>{escape(secure)}</td></tr>")
        parts.append("</table>")
    return "".join(parts)


def details_html(details: plans.ServerDetails) -> str:
    card = details.card
    color = status_color(card.status).name() if card.status != plans.CARD_SILENT else _muted()
    facts = " · ".join(part for part in (f"адресов: {len(details.addresses)}", card.best) if part)
    head = (
        f'<p style="font-size:20px; font-weight:600; margin-bottom:2px">{escape(card.server)}'
        f'&nbsp;&nbsp;<span style="color:{color}">{escape(plans.CARD_TITLES[card.status])}</span></p>'
        f'<p style="color:{_muted()}; margin-top:0">{escape(facts)}</p>'
        f'<p style="margin-top:8px">{escape(plans.CARD_HINTS[card.status])}</p>'
    )
    if card.note:
        head += f'<p style="margin-top:4px"><b>Что замечено:</b> {escape(card.note)}</p>'
    body = "".join(_address_html(address) for address in details.addresses)
    return f"<div style=\"font-family:'Segoe UI', sans-serif; font-size:13px\">{head}{body}</div>"


def show_server_details(parent, details: plans.ServerDetails) -> None:
    show_log_report_dialog(
        parent,
        title=f"DNS-сервер: {details.card.server}",
        text=details.text,
        html=details_html(details),
        description="Все адреса сервера, время ответа каждым способом связи и выводы целиком.",
    )


__all__ = ["details_html", "show_server_details"]
