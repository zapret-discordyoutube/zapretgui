from __future__ import annotations

import unittest
from types import SimpleNamespace

from presets.ui.control.additional_settings_runtime import ControlTopSummaryState, create_top_summary_worker
from profile.service import ProfilePresetService
from profile.state import ProfileListItem, ProfileListPayload


def _item(name: str, *, enabled: bool = True, in_preset: bool = True, index: int = 0) -> ProfileListItem:
    return ProfileListItem(
        key=f"uid:{index}",
        persistent_key=f"uid:{index}",
        profile_index=index,
        display_name=name,
        enabled=enabled,
        in_preset=in_preset,
        strategy_id="s",
        strategy_name="S",
        match_lines=(),
        list_type="hostlist",
        rating="",
        favorite=False,
        group="common",
        group_name="",
        order=index,
    )


def _service(items, *, snapshot_file: str = "P.txt", selected_file: str = "P.txt") -> ProfilePresetService:
    service = ProfilePresetService.__new__(ProfilePresetService)
    service._launch_method = "direct_zapret2"
    service._presets = SimpleNamespace(get_selected_source_preset_file_name=lambda _method: selected_file)
    service._profile_list_snapshot = (
        None
        if items is None
        else ProfileListPayload(
            items=tuple(items), selected_preset_file_name=snapshot_file, selected_preset_name="P"
        )
    )
    return service


class EnabledProfileIconsSnapshotTests(unittest.TestCase):
    def test_same_service_gives_one_icon_and_only_enabled_profiles_count(self) -> None:
        service = _service(
            [
                _item("youtube", index=0),
                _item("googlevideo", index=1),
                _item("discord", index=2),
                _item("telegram", enabled=False, index=3),
                _item("steam", in_preset=False, index=4),
            ]
        )

        icons = service.get_enabled_profile_icons_snapshot()

        self.assertEqual([name for name, _color in icons], ["simple:youtube:YT", "simple:discord:DI"])
        self.assertEqual(icons[0][1], "#FF0000")

    def test_known_service_icons_go_before_initials(self) -> None:
        service = _service([_item("Мой редкий сайт", index=0), _item("discord", index=1)])

        names = [name for name, _color in service.get_enabled_profile_icons_snapshot()]

        self.assertEqual(names[0], "simple:discord:DI")
        self.assertTrue(names[1].startswith("profile-initials:"))

    def test_none_until_the_list_is_loaded_for_the_selected_preset(self) -> None:
        self.assertIsNone(_service(None).get_enabled_profile_icons_snapshot())
        stale = _service([_item("youtube")], snapshot_file="old.txt", selected_file="new.txt")
        self.assertIsNone(stale.get_enabled_profile_icons_snapshot())

    def test_no_enabled_profiles_is_an_empty_tuple(self) -> None:
        self.assertEqual(_service([_item("youtube", enabled=False)]).get_enabled_profile_icons_snapshot(), ())


class TopSummaryWorkerIconsTests(unittest.TestCase):
    def _load(self, **kwargs) -> ControlTopSummaryState:
        worker = create_top_summary_worker(
            1,
            lambda _method: ("Default v1", ""),
            lambda _method: 3,
            launch_method="direct_zapret2",
            **kwargs,
        )
        return worker._summary_loader()

    def test_worker_passes_icons_to_the_summary_state(self) -> None:
        icons = (("simple:youtube:YT", "#FF0000"),)
        state = self._load(get_enabled_profile_icons_snapshot=lambda _method: icons)

        self.assertEqual(state.profile_icons, icons)
        self.assertEqual(state.profile_count, 3)

    def test_missing_or_broken_icon_reader_leaves_the_strip_empty(self) -> None:
        def _broken(_method):
            raise RuntimeError("нет списка")

        self.assertEqual(self._load().profile_icons, ())
        self.assertEqual(self._load(get_enabled_profile_icons_snapshot=lambda _method: None).profile_icons, ())
        self.assertEqual(self._load(get_enabled_profile_icons_snapshot=_broken).profile_icons, ())


if __name__ == "__main__":
    unittest.main()
