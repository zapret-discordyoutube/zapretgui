from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from ui.pages.about_page_tabs_build import build_about_page_tabs


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_stack_height_follows_current_tab_not_tallest() -> None:
    _app()
    widgets = build_about_page_tabs(tr_fn=lambda _key, default: default, on_switch_tab=lambda _index: None)

    tall = QWidget()
    tall.setFixedHeight(2000)
    widgets.help_layout.addWidget(tall)
    short = QWidget()
    short.setFixedHeight(100)
    widgets.kvn_layout.addWidget(short)

    stack = widgets.stacked_widget
    stack.setCurrentWidget(widgets.help_tab)
    assert stack.sizeHint().height() >= 2000

    stack.setCurrentWidget(widgets.kvn_tab)
    assert stack.sizeHint().height() < 500
    assert stack.minimumSizeHint().height() < 500
