"""Build-helper верхних секций для Zapret2ModeControlPage."""

from __future__ import annotations

from dataclasses import dataclass

from presets.ui.control.shared_builders import build_mode_status_section_common


@dataclass(slots=True)
class Zapret2StatusWidgets:
    card: object
    status_dot: object
    status_title: object
    status_desc: object
    close_btn: object
    progress_bar: object
    loading_label: object
    uptime_label: object


def build_winws2_pages_status_section(
    *,
    tr_fn,
    strong_body_label_cls,
    caption_label_cls,
    indeterminate_progress_bar_cls,
    close_button_cls,
    on_toggle,
    on_close,
    parent,
) -> Zapret2StatusWidgets:
    widgets = build_mode_status_section_common(
        tr_fn=tr_fn,
        strong_body_label_cls=strong_body_label_cls,
        caption_label_cls=caption_label_cls,
        indeterminate_progress_bar_cls=indeterminate_progress_bar_cls,
        close_button_cls=close_button_cls,
        checking_key="page.winws2_control.status.checking",
        checking_default="Проверка...",
        detecting_key="page.winws2_control.status.detecting",
        detecting_default="Определение состояния процесса",
        on_toggle=on_toggle,
        on_close=on_close,
        parent=parent,
    )

    return Zapret2StatusWidgets(
        card=widgets.card,
        status_dot=widgets.status_dot,
        status_title=widgets.status_title,
        status_desc=widgets.status_desc,
        close_btn=widgets.close_btn,
        progress_bar=widgets.progress_bar,
        loading_label=widgets.loading_label,
        uptime_label=widgets.uptime_label,
    )
