"""Обрыв «на 16 КБ»: одно правило для всех проверок BlockCheck и подбора стратегий.

ТСПУ считает не тело страницы, а всё, что сервер прислал по соединению:
рукопожатие TLS с сертификатом (несколько килобайт), заголовки ответа и только
потом тело. Поэтому обрыв «на 16 КБ» в одном теле выглядит как 8–12 КБ. Окно
``limits.FREEZE_*`` задано в единицах фильтра, и проверки, которые меряют только
тело, сначала переводят свой счёт в эти единицы (``filter_bytes``).

Где оборвалось — ``cut_place``:

- ``CUT_WINDOW`` — внутри окна: блокировка по объёму, так режет ТСПУ;
- ``CUT_EARLY`` — раньше окна: ответ сорван, но на обычный обрыв ТСПУ это не
  похоже. Сломан ли сайт сам, показывает только сверка из-за рубежа
  (``outside_check``, страница целиком);
- ``CUT_LATE`` — позже окна: окно пройдено, обрыва «16 КБ» нет.
"""

from __future__ import annotations

from diagnostics.limits import FREEZE_MAX_BYTES, FREEZE_MIN_BYTES

__all__ = [
    "CUT_EARLY",
    "CUT_LATE",
    "CUT_WINDOW",
    "TLS_HANDSHAKE_ESTIMATE",
    "WINDOW_TEXT",
    "cut_place",
    "filter_bytes",
    "passed_window",
    "whole_past_window",
    "result_bytes",
    "result_cut",
]

CUT_WINDOW = "window"
CUT_EARLY = "early"
CUT_LATE = "late"
# Окно словами — для текстов проверок: «12–36 КБ».
WINDOW_TEXT = f"{FREEZE_MIN_BYTES // 1000}–{FREEZE_MAX_BYTES // 1000} КБ"

# Сколько байт рукопожатия TLS (в основном сертификат сервера) прибавлять, когда
# проверка его не измерила: у обычных сайтов цепочка сертификатов — 3–6 КБ.
TLS_HANDSHAKE_ESTIMATE = 4_000


def filter_bytes(body: int, *, headers: int = 0, handshake: int = 0, tls: bool = True) -> int:
    """Сколько байт насчитал фильтр к моменту, когда пришло ``body`` байт тела.

    ``handshake`` — измеренные байты рукопожатия TLS; 0 — не измерены, тогда
    берётся оценка. ``tls=False`` — обычный HTTP: рукопожатия нет.
    """
    extra = (handshake if handshake > 0 else TLS_HANDSHAKE_ESTIMATE) if tls else 0
    return max(0, int(body)) + max(0, int(headers)) + extra


def cut_place(received: int) -> str:
    """Где по счёту фильтра оборвался ответ: ``CUT_EARLY``, ``CUT_WINDOW`` или ``CUT_LATE``."""
    if received < FREEZE_MIN_BYTES:
        return CUT_EARLY
    if received <= FREEZE_MAX_BYTES:
        return CUT_WINDOW
    return CUT_LATE


def passed_window(received: int) -> bool:
    """Соединение уже провезло больше окна: обрыва «16 КБ» на нём нет."""
    return received > FREEZE_MAX_BYTES


def result_bytes(result) -> int:
    """Счёт фильтра для ответа ``tls_probe.ProbeResult`` (HTTPS)."""
    received = int(getattr(result, "received_size", 0) or 0) or int(result.body_size or 0)
    return filter_bytes(received, handshake=int(getattr(result, "handshake_size", 0) or 0))


def result_cut(result) -> str:
    """Где оборвался ответ HTTPS; пусто — тело не обрывалось."""
    if result is None or not result.body_cut:
        return ""
    return cut_place(result_bytes(result))


def whole_past_window(result) -> bool:
    """Ответ HTTPS пришёл целиком и сам больше окна: отдельная проверка объёма не нужна."""
    return result is not None and not result.body_cut and passed_window(result_bytes(result))
