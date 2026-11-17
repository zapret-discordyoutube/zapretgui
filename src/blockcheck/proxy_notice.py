"""Системный прокси перед любой проверкой: уведомление с кнопкой «Отключить прокси».

Пока в Windows включён прокси (его ставят браузер, расширения и программы для обхода), часть
запросов проверки уходит через него, а не напрямую. Тогда сайт «открывается» без всякого обхода
Zapret: BlockCheck говорит «всё работает», а «Подбор стратегии» — «подбирать нечего» (жалоба
2026-11-17: после выключения прокси в браузере проверка заработала как надо).

Поэтому при запуске любой проверки (BlockCheck, подбор стратегии, проверка домена, DNS-серверы,
DNS подмена) программа смотрит настройки прокси и говорит об этом. Само уведомление и кнопка те же,
что при запуске программы (``startup.check_start``): действие ``disable_proxy`` выполняет центр
уведомлений окна.
"""

from __future__ import annotations

import time

__all__ = ["ProxyState", "detect", "notice", "notify_if_system_proxy"]

SOURCE = "blockcheck.proxy"
# Не чаще, чем раз в столько секунд: человек запускает проверки подряд, а напоминать каждый раз незачем.
REPEAT_AFTER_S = 120.0

_last_shown: float | None = None


class ProxyState(tuple):
    """(ручной прокси «адрес:порт» или пусто, адрес скрипта автонастройки PAC или пусто)."""

    def __new__(cls, manual: str = "", pac: str = ""):
        return super().__new__(cls, (str(manual or "").strip(), str(pac or "").strip()))

    @property
    def manual(self) -> str:
        return self[0]

    @property
    def pac(self) -> str:
        return self[1]

    @property
    def enabled(self) -> bool:
        return bool(self.manual or self.pac)


def detect() -> ProxyState:
    """Что включено в настройках прокси Windows. Не вышло прочитать — прокси нет."""
    try:
        from platforms import current as current_platform

        manual, pac = current_platform().system_settings.proxy_settings()
    except Exception:
        return ProxyState()
    return ProxyState(manual, pac)


def notice(state: ProxyState | None = None) -> dict | None:
    """Уведомление о включённом прокси или None, если прокси нет."""
    from app_notifications import advisory_notification, notification_action

    state = detect() if state is None else state
    if not state.enabled:
        return None
    if state.manual:
        content = (
            f"В Windows включён прокси ({state.manual}). Пока он включён, сайты в проверке могут открываться "
            "через него, а не через обход Zapret: программа решит, что всё работает, хотя это не так. "
            "Отключите прокси и запустите проверку снова."
        )
        # Кнопка гасит только ручной прокси; со скриптом автонастройки она бы ничего не изменила.
        buttons = [notification_action("disable_proxy", "Отключить прокси")]
    else:
        content = (
            f"В Windows включена автонастройка прокси (скрипт {state.pac}). Пока она включена, сайты в проверке "
            "могут открываться через прокси, а не через обход Zapret. Выключите её в «Параметры → "
            "Сеть и Интернет → Прокси» и запустите проверку снова."
        )
        buttons = []
    return advisory_notification(
        level="warning",
        title="Включён системный прокси",
        content=content,
        source=SOURCE,
        presentation="infobar",
        queue="immediate",
        # С кнопкой уведомление ждёт человека: иначе оно исчезнет, пока он читает.
        duration=-1 if buttons else 15000,
        buttons=buttons,
        dedupe_key=SOURCE,
    )


def notify_if_system_proxy(notify=None, *, window=None, now=time.monotonic) -> bool:
    """Говорит о включённом прокси перед проверкой. True — уведомление показано.

    ``notify`` — приём уведомлений окна (``advisory_notification``); без него показывается
    обычная плашка в окне ``window``.
    """
    global _last_shown
    payload = notice()
    if payload is None:
        _last_shown = None
        return False
    moment = float(now())
    if _last_shown is not None and moment - _last_shown < REPEAT_AFTER_S:
        return False
    _last_shown = moment
    if callable(notify):
        notify(payload)
        return True
    if window is not None:
        from ui.fluent_widgets import InfoBarHelper

        InfoBarHelper.warning(window, payload["title"], payload["content"])
        return True
    return False
