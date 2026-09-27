from __future__ import annotations

"""Диагностика: какая версия объявлена в публичном Telegram-канале.

Только строка в таблице серверов. Telegram никогда не создаёт предложение
обновиться и не даёт ни ссылку, ни контрольную сумму. Токенов нет: читается
публичная страница ``t.me/s/<канал>``.
"""

import re
from typing import Any

from log.log import log

from ..channel_utils import normalize_update_channel
from .http import new_session, short_error


TELEGRAM_CHANNELS = {
    "stable": "zapretnetdiscordyoutube",
    "dev": "zapretguidev",
}
# Telegram не участвует в выборе выпуска, поэтому при блокировке t.me
# диагностика не должна долго занимать поток.
TELEGRAM_TIMEOUT = (3, 5)

# Версия берётся только из имени установщика (``Zapret2Setup_DEV_21_1_5_78.exe``):
# произвольные числа из текста поста — даты, IP-адреса — версией не считаются.
_INSTALLER_NAME_RE = re.compile(r"Zapret2Setup(?:_DEV)?_(\d+(?:_\d+){2,})\.exe", re.IGNORECASE)


def get_telegram_version_info(channel: str = "stable") -> dict[str, Any] | None:
    """Последняя объявленная в канале версия или None."""
    channel_name = TELEGRAM_CHANNELS[normalize_update_channel(channel)]
    session = new_session()
    try:
        response = session.get(f"https://t.me/s/{channel_name}", timeout=TELEGRAM_TIMEOUT)
        if response.status_code != 200:
            return None
        html = response.text
    except Exception as exc:
        log(f"Telegram @{channel_name}: {short_error(exc)}", "📱 TG")
        return None
    finally:
        session.close()

    names = _INSTALLER_NAME_RE.findall(html)
    if not names:
        return None
    version = names[-1].replace("_", ".")
    return {"version": version, "source": f"Telegram @{channel_name}", "channel": channel_name}


__all__ = [
    "TELEGRAM_CHANNELS",
    "get_telegram_version_info",
]
