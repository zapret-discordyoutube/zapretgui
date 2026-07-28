from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from profile.ui.profile_payload_controller import ProfilePayloadController


def _page(*, visible: bool, dirty: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        _profile_payload_reload_after_preset_switch_scheduled=True,
        _profile_payload_dirty=dirty,
        _is_cleanup_in_progress=Mock(return_value=False),
        _schedule_profiles_payload_request=Mock(),
        isVisible=Mock(return_value=visible),
    )


class ProfilePayloadHiddenPageReloadTests(unittest.TestCase):
    def test_visible_page_reloads_payload_after_preset_switch(self) -> None:
        page = _page(visible=True)

        ProfilePayloadController(page)._run_scheduled_profiles_payload_reload_after_preset_switch()

        page._schedule_profiles_payload_request.assert_called_once_with(force=True)
        self.assertFalse(page._profile_payload_reload_after_preset_switch_scheduled)

    def test_hidden_page_stays_dirty_instead_of_recomputing(self) -> None:
        page = _page(visible=False)

        ProfilePayloadController(page)._run_scheduled_profiles_payload_reload_after_preset_switch()

        page._schedule_profiles_payload_request.assert_not_called()
        self.assertTrue(page._profile_payload_dirty)

    def test_clean_page_does_not_reload(self) -> None:
        page = _page(visible=True, dirty=False)

        ProfilePayloadController(page)._run_scheduled_profiles_payload_reload_after_preset_switch()

        page._schedule_profiles_payload_request.assert_not_called()

    def test_page_activation_loads_payload_deferred_while_hidden(self) -> None:
        from profile.ui.preset_setup_page import Zapret2PresetSetupPage

        page = Zapret2PresetSetupPage.__new__(Zapret2PresetSetupPage)
        page.__dict__["_deferred_profile_payload_apply"] = None
        page.__dict__["_profile_payload_loaded_once"] = True
        page.__dict__["_profile_payload_dirty"] = True
        page._apply_deferred_profile_payload_after_show = Mock(return_value=False)
        page._profile_payload_refresh_is_blocked = Mock(return_value=False)
        page._mark_profiles_list_ready_after_page_switch = Mock()
        page._schedule_profiles_payload_request = Mock()

        Zapret2PresetSetupPage.on_page_activated(page)

        page._schedule_profiles_payload_request.assert_called_once_with()
        page._mark_profiles_list_ready_after_page_switch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
