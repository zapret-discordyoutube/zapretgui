"""Кнопки «исправить» в уведомлении проверки после «Применить».

Проверка доказала причину (``browser_diagnosis``) — человеку не нужно идти в
реестр или в настройки самому:

* ECH и QUIC — «Выключить ECH/QUIC в <браузер>» для браузера по умолчанию, если
  программа умеет это в нём (``browser_switch``); после — «Вернуть как было»;
* прокси — «Выключить прокси» (общее действие уведомлений ``disable_proxy``);
* IPv6 — «Открыть сетевые подключения»: галочку IPv6 человек снимает сам,
  программа систему молча не меняет.

Кнопка — это действие ``browser_fix`` со значением ``<действие>:<браузер>:<имя>``; его
выполняет центр уведомлений окна в фоне (``run_fix``), итог — новое уведомление.
"""

from __future__ import annotations

import subprocess

from app_notifications import notification_action
from blockcheck import browser_switch
from blockcheck.default_browser import DefaultBrowser

__all__ = ["ACTION_KIND", "FixOutcome", "fix_buttons", "run_fix"]

ACTION_KIND = "browser_fix"
# Действия: «<off|restore>_<ech|quic>» и открыть сетевые подключения.
OFF = "off"
RESTORE = "restore"
OPEN_ADAPTERS = "open_adapters"


class FixOutcome(tuple):
    """(получилось, текст, кнопки следующего уведомления)."""

    def __new__(cls, ok: bool, text: str, buttons: list[dict] | None = None):
        return super().__new__(cls, (bool(ok), str(text), list(buttons or ())))


def _value(what: str, browser: DefaultBrowser | None = None) -> str:
    if browser is None or not browser.known:
        return what
    return f"{what}:{browser.family}:{browser.title}"


def switch_button(what: str, browser: DefaultBrowser) -> dict:
    """«Выключить ECH/QUIC в <браузер>»; браузер не тот — пусто."""
    if not browser.known or not browser_switch.can_switch(browser.family):
        return {}
    name = browser_switch.FEATURES[what].name
    return notification_action(ACTION_KIND, f"Выключить {name} в {browser.title}", value=_value(f"{OFF}_{what}", browser))


def fix_buttons(fixes, *, browser: DefaultBrowser, ipv6: bool) -> list[dict]:
    """Кнопки для доказанных причин (``browser_diagnosis.FIX_*``)."""
    from blockcheck import browser_diagnosis as bd

    buttons: list[dict] = []
    if bd.FIX_ECH in fixes:
        buttons.append(switch_button(browser_switch.ECH, browser))
    if bd.FIX_PROXY in fixes:
        buttons.append(notification_action("disable_proxy", "Выключить прокси"))
    if bd.FIX_ADDRESS in fixes and ipv6:
        buttons.append(notification_action(ACTION_KIND, "Открыть сетевые подключения", value=OPEN_ADAPTERS))
    return [button for button in buttons if button]


def _open_adapters() -> FixOutcome:
    from winws_runtime.public import CREATE_NO_WINDOW

    subprocess.Popen(["control.exe", "ncpa.cpl"], creationflags=CREATE_NO_WINDOW)
    return FixOutcome(
        True,
        "Откроются сетевые подключения. Нажмите правой кнопкой на своё подключение → «Свойства» → снимите галочку "
        "«IP версии 6 (TCP/IPv6)» → «ОК».",
    )


def run_fix(value: str, *, registry=None, profiles=None) -> FixOutcome:
    """Выполнить кнопку. Вызывается в фоне, не в потоке окна."""
    what, _sep, rest = str(value or "").partition(":")
    family, _sep, title = rest.partition(":")
    extra = {"registry": registry} if registry is not None else {}
    if profiles is not None:
        extra["profiles"] = profiles
    action, _sep, feature = what.partition("_")
    if action == OFF and feature in browser_switch.FEATURES:
        result = browser_switch.turn_off(feature, family, title, **extra)
        undo = [notification_action(ACTION_KIND, "Вернуть как было", value=f"{RESTORE}_{feature}:{family}:{title}")] if result.ok else []
        return FixOutcome(result.ok, result.text, undo)
    if action == RESTORE and feature in browser_switch.FEATURES:
        result = browser_switch.restore(feature, family, title, **extra)
        return FixOutcome(result.ok, result.text)
    if what == OPEN_ADAPTERS:
        try:
            return _open_adapters()
        except OSError as error:
            return FixOutcome(False, f"Не вышло открыть сетевые подключения: {error}")
    return FixOutcome(False, "Неизвестное действие")
