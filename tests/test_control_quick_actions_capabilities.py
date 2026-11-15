"""The control page leaves out Windows-only tiles and groups on systems that lack them."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from platforms import Cap


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _tr(_key, default):
    return default


def _build(qapp, capabilities):
    from presets.ui.control import quick_actions

    fake = SimpleNamespace(has=lambda name: name in capabilities)
    noop = lambda: None  # noqa: E731
    with patch.object(quick_actions, "current_platform", return_value=fake):
        return quick_actions.build_quick_actions(
            tr_fn=_tr,
            text_prefix="page.control",
            on_open_onboarding_tour=noop,
            on_open_internet_cleanup=noop,
            on_open_folder=noop,
            on_open_docs=noop,
            on_open_git=noop,
        )


def test_the_network_reset_tile_is_built_only_where_the_system_has_it(qapp) -> None:
    with_reset = _build(qapp, {Cap.NETWORK_RESET})
    without = _build(qapp, set())
    assert with_reset.internet_cleanup_card is not None
    assert without.internet_cleanup_card is None
    for widgets in (with_reset, without):
        assert widgets.tour_card is not None and widgets.folder_card is not None
        assert widgets.docs_card is not None and widgets.git_card is not None


def test_language_update_skips_a_tile_that_was_not_built() -> None:
    from presets.ui.control import quick_actions

    quick_actions.apply_quick_actions_language(
        tr_fn=_tr, text_prefix="page.control", title_label=None, tour_card=None, internet_cleanup_card=None
    )


@pytest.mark.parametrize("page", ["zapret1", "zapret2"])
def test_both_control_pages_skip_the_windows_group_without_the_capability(page) -> None:
    import importlib

    module = importlib.import_module(f"presets.ui.control.{page}.page")
    source = inspect.getsource(module)
    start = source.index("def _build_windows_settings_block")
    body = source[start : source.index("\n    def ", start + 10)]
    assert "Cap.DEFENDER" in body and "return" in body.split("Cap.DEFENDER", 1)[1].split("\n", 3)[1]
