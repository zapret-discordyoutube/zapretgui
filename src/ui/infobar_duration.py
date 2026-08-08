from __future__ import annotations

from typing import Any


INFOBAR_MIN_DURATION_MS = 5000
_FACTORY_NAMES = ("success", "info", "warning", "error")
_PATCHED_ATTR = "_zapret_min_duration_installed"
_ORIGINAL_ATTR_TEMPLATE = "_zapret_min_duration_original_{name}"
_DURATION_ARG_INDEX = 4


def _coerce_duration(value: Any, minimum_ms: int = INFOBAR_MIN_DURATION_MS) -> int:
    try:
        duration = int(value)
    except (TypeError, ValueError):
        return int(minimum_ms)
    if duration < 0:
        return duration
    return max(duration, int(minimum_ms))


def _with_min_duration(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    minimum_ms: int = INFOBAR_MIN_DURATION_MS,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    next_kwargs = dict(kwargs)
    if len(args) > _DURATION_ARG_INDEX:
        next_args = list(args)
        next_args[_DURATION_ARG_INDEX] = _coerce_duration(
            next_args[_DURATION_ARG_INDEX],
            minimum_ms,
        )
        return tuple(next_args), next_kwargs

    next_kwargs["duration"] = _coerce_duration(
        next_kwargs.get("duration", minimum_ms),
        minimum_ms,
    )
    return args, next_kwargs


def install_infobar_min_duration(
    info_bar_cls: type | None = None,
    minimum_ms: int = INFOBAR_MIN_DURATION_MS,
) -> None:
    """Один раз задаёт нижнюю границу времени для плашек InfoBar.

    В qfluentwidgets по умолчанию любая плашка живёт около секунды — этого не
    хватает, чтобы заметить даже предупреждение. Здесь success/info/warning/
    error не закрываются быстрее заданного минимума; постоянные плашки с
    duration < 0 и явно заданные длинные значения не меняются.
    """
    if info_bar_cls is None:
        from qfluentwidgets import InfoBar

        info_bar_cls = InfoBar

    if bool(getattr(info_bar_cls, _PATCHED_ATTR, False)):
        return

    for name in _FACTORY_NAMES:
        original = getattr(info_bar_cls, name, None)
        if original is None:
            continue

        def factory_with_min_duration(cls, *args, _original=original, **kwargs):
            next_args, next_kwargs = _with_min_duration(args, kwargs, minimum_ms)
            return _original(*next_args, **next_kwargs)

        setattr(info_bar_cls, _ORIGINAL_ATTR_TEMPLATE.format(name=name), original)
        setattr(info_bar_cls, name, classmethod(factory_with_min_duration))

    setattr(info_bar_cls, _PATCHED_ATTR, True)
