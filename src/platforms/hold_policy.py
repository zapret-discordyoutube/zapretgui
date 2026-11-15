"""How much of a hidden window's memory to ask the system to keep. Pure numbers, no system calls."""

from __future__ import annotations

_MB = 1 << 20
# Сколько удерживать: меньше — игра не стоит свеч, больше — занимаем чужую память.
HOLD_MIN_BYTES = 24 * _MB
HOLD_MAX_BYTES = 320 * _MB
# Запас сверху к тому, что программа держала в момент ухода окна.
HOLD_MARGIN_BYTES = 12 * _MB
# Удержание ничего нового не занимает, оно лишь не даёт системе отобрать то, что программа
# уже держит. Но системе должен остаться запас: если после удержания свободной памяти
# окажется меньше этого, ничего не удерживаем (ПК и так тесно). Раньше стояла жёсткая
# отметка «свободно меньше 1 ГБ — не удерживать»: на слабом ПК, где отбор страниц и бьёт
# сильнее всего, защита не включалась вовсе.
HOLD_SPARE_BYTES = 256 * _MB
# Малый минимум, к которому возвращается рабочий набор, когда хватку отпускаем.


def hold_size_for(working_set_bytes: int | None, available_bytes: int | None) -> int | None:
    """Сколько удерживать при таком рабочем наборе и такой свободной памяти, либо None — не надо."""
    if not working_set_bytes or working_set_bytes < HOLD_MIN_BYTES:
        return None
    size = min(working_set_bytes + HOLD_MARGIN_BYTES, HOLD_MAX_BYTES)
    if available_bytes is not None and available_bytes < size + HOLD_SPARE_BYTES:
        return None
    return size
