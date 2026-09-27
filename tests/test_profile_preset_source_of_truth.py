"""GUI-действие меняет в пресете ровно то, что попросил пользователь.

Регрессии побочных эффектов: правка диапазонов внутри составной стратегии, чужие match-строки,
второй `--wf-udp-out`, молчаливое разрезание profile-ов, перестановка строк
wssize, удаление хвоста пресета, правка чужих/встроенных пресетов при
изменении пользовательского profile.
"""

from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from core.paths import AppPaths
from profile.parser import parse_preset_text
from profile.serializer import serialize_preset
from profile.service import ProfilePresetService
from settings.mode import ZAPRET1_MODE, ZAPRET2_MODE


class _PresetStore:
    def __init__(self, text: str = "", *, persist: bool = True) -> None:
        self.text = text
        self.persist = persist
        self.save_count = 0

    def get_selected_source_preset_manifest(self, _launch_method: str):
        return SimpleNamespace(file_name="selected.txt", name="selected")

    def read_selected_preset_source(self, launch_method: str):
        return self.text, self.get_selected_source_preset_manifest(launch_method)

    def save_selected_preset_source(self, _launch_method: str, text: str, **_kwargs) -> None:
        self.save_count += 1
        if self.persist:
            self.text = text


def _service(root: Path, store, launch_method: str = ZAPRET2_MODE) -> ProfilePresetService:
    (root / "system" / "templates").mkdir(parents=True, exist_ok=True)
    templates = root / "system" / "templates" / "all_profiles.txt"
    if not templates.exists():
        templates.write_text("", encoding="utf-8")
    feature = SimpleNamespace(_presets_feature=store, _app_paths=AppPaths(user_root=root, local_root=root))
    return ProfilePresetService(feature, launch_method)


def _profile_lines(text: str, index: int, engine: str = "winws2") -> list[str]:
    preset = parse_preset_text(text, engine=engine)
    return [segment.text for segment in preset.profiles[index].segments if segment.kind != "blank"]


_TWO_BRANCH_PROFILE = "\n".join(
    (
        "--name=YouTube",
        "--filter-tcp=80,443",
        "--hostlist=lists/youtube.txt",
        "--out-range=-d8",
        "--payload=tls_client_hello",
        "--lua-desync=fake:blob=tls_google",
        "--payload=http_req",
        "--out-range=-n4",
        "--lua-desync=multisplit:pos=2",
        "",
    )
)


class RangeEditTests(unittest.TestCase):
    def _edit(self, text: str, **kwargs) -> tuple[str, object]:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = _PresetStore(text)
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                service = _service(root, store)
                setup = service.get_profile_setup("profile:0")
                params = {
                    "filter_kind": setup.editable_filter_kind,
                    "filter_value": setup.editable_filter_value,
                    "in_range": setup.in_range,
                    "out_range": setup.out_range,
                }
                params.update(kwargs)
                result = service.update_winws2_editable_settings("profile:0", **params)
        return store.text, result

    def test_profile_range_edit_keeps_composite_strategy_and_its_inner_range(self) -> None:
        # Поля страницы правят только диапазон profile-а (до первой
        # --lua-desync); диапазон внутри составной стратегии не трогается,
        # и стратегия по-прежнему узнаётся как готовая.
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalogs = root / "system" / "strategy_catalogs" / "winws2"
            catalogs.mkdir(parents=True)
            (catalogs / "tcp.txt").write_text(
                "\n".join(
                    (
                        "[youtube_composite]",
                        "name = YouTube composite",
                        "--payload=tls_client_hello",
                        "--lua-desync=fake:blob=tls_google",
                        "--payload=http_req",
                        "--out-range=-n4",
                        "--lua-desync=multisplit:pos=2",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            store = _PresetStore(_TWO_BRANCH_PROFILE)
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                service = _service(root, store)
                setup = service.get_profile_setup("profile:0")
                self.assertEqual((setup.item.strategy_id, setup.out_range), ("youtube_composite", "-d8"))
                result = service.update_winws2_editable_settings(
                    "profile:0",
                    filter_kind=setup.editable_filter_kind,
                    filter_value=setup.editable_filter_value,
                    in_range=setup.in_range,
                    out_range="-n2",
                )
                after = service.get_profile_setup("profile:0")

        self.assertIsNotNone(result)
        self.assertEqual(store.text, _TWO_BRANCH_PROFILE.replace("--out-range=-d8", "--out-range=-n2"))
        self.assertEqual((after.item.strategy_id, after.out_range), ("youtube_composite", "-n2"))

    def test_profile_range_edit_on_composite_profile_adds_no_restore_lines(self) -> None:
        # Диапазон до первой --lua-desync — настройка всего profile-а: его
        # правка меняет одну строку, и в середину составной стратегии не
        # вставляются строки «вернуть прежнее значение» для следующих веток.
        text = "\n".join(
            (
                "--name=YouTube",
                "--filter-tcp=80,443",
                "--hostlist=lists/youtube.txt",
                "--out-range=-d8",
                "--payload=tls_client_hello",
                "--lua-desync=fake:blob=tls_google",
                "--payload=http_req",
                "--lua-desync=multisplit:pos=2",
                "",
            )
        )
        saved, result = self._edit(text, out_range="-n3")

        self.assertIsNotNone(result)
        self.assertEqual(saved, text.replace("--out-range=-d8", "--out-range=-n3"))

    def test_new_profile_range_goes_right_before_first_lua_desync(self) -> None:
        text = "\n".join(
            (
                "--name=YouTube",
                "--filter-tcp=80,443",
                "--hostlist=lists/youtube.txt",
                "--payload=tls_client_hello",
                "--lua-desync=fake:blob=tls_google",
                "--payload=http_req",
                "--out-range=-n4",
                "--lua-desync=multisplit:pos=2",
                "",
            )
        )
        saved, result = self._edit(text, out_range="-d8")

        self.assertIsNotNone(result)
        self.assertEqual(
            saved,
            text.replace(
                "--payload=tls_client_hello\n",
                "--payload=tls_client_hello\n--out-range=-d8\n",
            ),
        )

    def test_filter_only_edit_keeps_every_range_and_payload_line(self) -> None:
        text = "\n".join(
            (
                "--name=YouTube",
                "--filter-tcp=80,443",
                "--hostlist=lists/youtube.txt",
                "--in-range=x",
                "--out-range=a",
                "--payload=tls_client_hello",
                "--lua-desync=fake:blob=tls_google",
                "--payload=http_req",
                "--out-range=-n4",
                "--lua-desync=multisplit:pos=2",
                "",
            )
        )
        saved, result = self._edit(text, filter_value="lists/youtube-extra.txt")

        self.assertIsNotNone(result)
        self.assertEqual(saved, text.replace("--hostlist=lists/youtube.txt", "--hostlist=lists/youtube-extra.txt"))

    def test_range_edit_keeps_explicit_default_lines(self) -> None:
        text = "\n".join(
            (
                "--name=Site",
                "--filter-tcp=443",
                "--hostlist=lists/site.txt",
                "--in-range=x",
                "--out-range=-d8",
                "--lua-desync=fake:blob=tls_google",
                "",
            )
        )
        saved, _result = self._edit(text, out_range="-n4")

        self.assertEqual(saved, text.replace("--out-range=-d8", "--out-range=-n4"))

    def test_list_edit_keeps_other_primary_match_lines(self) -> None:
        text = "\n".join(
            (
                "--name=Site",
                "--filter-tcp=443",
                "--hostlist=lists/site.txt",
                "--hostlist-domains=example.com",
                "--lua-desync=fake:blob=tls_google",
                "",
            )
        )
        saved, result = self._edit(text, filter_value="lists/other.txt")

        self.assertIsNotNone(result)
        self.assertEqual(saved, text.replace("--hostlist=lists/site.txt", "--hostlist=lists/other.txt"))

    def test_list_kind_switch_replaces_only_edited_group_and_reads_back(self) -> None:
        from profile.editable_settings import (
            EditableProfileSettings,
            read_editable_profile_settings,
            with_editable_profile_settings,
        )

        text = "\n".join(
            (
                "--name=Site",
                "--filter-tcp=443",
                "--hostlist=lists/site.txt",
                "--hostlist-domains=example.com",
                "--lua-desync=fake:blob=tls_google",
                "",
            )
        )
        preset = parse_preset_text(text, engine="winws2")
        requested = EditableProfileSettings(filter_kind="ipset", filter_value="lists/ipset-site.txt")

        updated = with_editable_profile_settings(preset, 0, requested)
        actual = read_editable_profile_settings(updated.profiles[0])

        self.assertEqual(serialize_preset(updated), text.replace("--hostlist=lists/site.txt", "--ipset=lists/ipset-site.txt"))
        # Проверка после записи читает ту же группу, что правилась.
        self.assertEqual((actual.filter_kind, actual.filter_value), ("ipset", "lists/ipset-site.txt"))

    def test_settings_save_worker_returns_payload_with_saved_profile_range(self) -> None:
        from app.feature_facades.profile import ProfileFeature

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "system" / "templates").mkdir(parents=True)
            (root / "system" / "templates" / "all_profiles.txt").write_text("", encoding="utf-8")
            store = _PresetStore(_TWO_BRANCH_PROFILE)
            feature = ProfileFeature(_presets_feature=store, _app_paths=AppPaths(user_root=root, local_root=root))
            emitted: list[object] = []
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                worker = feature.create_profile_settings_save_worker(
                    1,
                    ZAPRET2_MODE,
                    profile_key="profile:0",
                    filter_kind="hostlist",
                    filter_value="lists/youtube.txt",
                    in_range="x",
                    out_range="-n2",
                )
                worker.saved.connect(lambda _request_id, _keys, result: emitted.append(result))
                worker.run()

        self.assertEqual(len(emitted), 1)
        payload = emitted[0].payload
        self.assertEqual((payload.in_range, payload.out_range), ("x", "-n2"))
        self.assertEqual(store.text, _TWO_BRANCH_PROFILE.replace("--out-range=-d8", "--out-range=-n2"))


class AutoSplitTests(unittest.TestCase):
    def test_unrelated_edit_keeps_multi_list_profile_byte_identical(self) -> None:
        multi_list_profile = "\n".join(
            (
                "--name=Исключения айпи (RU сайты)",
                "--filter-tcp=80,443-65535",
                "--ipset=lists/ipset-ru.txt",
                "--ipset=lists/ipset-dns.txt",
                "--hostlist-exclude=lists/list-exclude.txt",
                "--out-range=-d8",
                "--lua-desync=pass",
            )
        )
        other_profile = "\n".join(
            (
                "--name=YouTube",
                "--filter-tcp=80,443",
                "--hostlist=lists/youtube.txt",
                "--lua-desync=fake:blob=tls_google",
            )
        )
        text = f"{multi_list_profile}\n\n--new\n\n{other_profile}\n"
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = _PresetStore(text)
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                service = _service(root, store)
                listed = service.list_profiles()
                youtube_key = next(item.key for item in listed.items if item.profile_name == "YouTube")
                service.set_profile_enabled(youtube_key, False)

        self.assertEqual(len([item for item in listed.items if item.in_preset]), 2)
        self.assertTrue(store.text.startswith(multi_list_profile + "\n\n--new\n\n"))
        self.assertEqual(store.text, text.replace("--name=YouTube\n", "--name=YouTube\n--skip\n"))


class WssizeToggleTests(unittest.TestCase):
    def _profile_services(self, text: str):
        store = _PresetStore(text)
        return SimpleNamespace(_presets_feature=store), store

    def test_enabling_wssize_touches_only_enabled_tcp443_profiles_and_one_line(self) -> None:
        from profile.settings import get_wssize_enabled, set_wssize_enabled

        text = "\n".join(
            (
                "--name=YouTube",
                "--filter-tcp=80,443",
                "--hostlist=lists/youtube.txt",
                "--out-range=-d8",
                "--payload=tls_client_hello",
                "--lua-desync=fake:blob=tls_google",
                "# http ветка",
                "--payload=http_req",
                "--lua-desync=multisplit:pos=2",
                "",
                "--new",
                "",
                "--skip",
                "--name=Disabled",
                "--filter-tcp=443",
                "--hostlist=lists/disabled.txt",
                "--lua-desync=fake:blob=tls_google",
                "",
            )
        )
        services, store = self._profile_services(text)

        self.assertTrue(set_wssize_enabled(services, True, launch_method=ZAPRET2_MODE))
        enabled_text = store.text
        self.assertTrue(get_wssize_enabled(services, launch_method=ZAPRET2_MODE))
        self.assertTrue(set_wssize_enabled(services, False, launch_method=ZAPRET2_MODE))

        # Строка wssize — перед первым фильтром/стратегией (SYN имеет пейлоад
        # `empty`, после --payload=tls_client_hello wssize его бы не увидел).
        self.assertEqual(
            enabled_text,
            text.replace(
                "--hostlist=lists/youtube.txt\n",
                "--hostlist=lists/youtube.txt\n--lua-desync=wssize:wsize=1:scale=6\n",
            ),
        )
        # Выключенный profile не тронут, выключение — точный откат.
        self.assertEqual(store.text, text)

    def test_wssize_state_ignores_disabled_profiles(self) -> None:
        from profile.settings import get_wssize_enabled

        text = "\n".join(
            (
                "--skip",
                "--name=Disabled",
                "--filter-tcp=443",
                "--hostlist=lists/disabled.txt",
                "--lua-desync=wssize:wsize=1:scale=6",
                "--lua-desync=fake:blob=tls_google",
                "",
            )
        )
        services, _store = self._profile_services(text)

        self.assertFalse(get_wssize_enabled(services, launch_method=ZAPRET2_MODE))


class TemplateAppendPositionTests(unittest.TestCase):
    def _enable_user_profile(self, text: str) -> tuple[str, str | None]:
        from profile.user_profiles import create_user_profile

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = _PresetStore(text)
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                service = _service(root, store)
                profile_id = create_user_profile(service._app_paths, name="My Site", protocol="tcp", ports="80,443")
                new_key = service.set_profile_enabled(f"template:user:{profile_id}", True)
        return store.text, new_key

    def test_new_profile_goes_before_ru_exclusions_and_keeps_first_profile(self) -> None:
        text = "\n".join(
            (
                "--name=git.zapret.moe",
                "--filter-tcp=443",
                "--hostlist=lists/git-zapret-moe.txt",
                "--lua-desync=fake:blob=tls_google",
                "",
                "--new",
                "",
                "--name=Исключения домены (RU сайты)",
                "--filter-tcp=80,443-65535",
                "--hostlist=lists/netrogat.txt",
                "--lua-desync=pass",
                "",
                "--new",
                "",
                "--name=DeepSeek",
                "--filter-tcp=80,443-65535",
                "--hostlist=lists/deepseek.txt",
                "--lua-desync=pass",
                "",
            )
        )
        saved, new_key = self._enable_user_profile(text)

        names = [profile.name for profile in parse_preset_text(saved, engine="winws2").profiles]
        self.assertEqual(names, ["git.zapret.moe", "My Site", "Исключения домены (RU сайты)", "DeepSeek"])
        self.assertEqual(new_key, "profile:1")

    def test_new_profile_goes_before_catch_all_profile(self) -> None:
        text = "\n".join(
            (
                "--name=YouTube",
                "--filter-tcp=80,443",
                "--hostlist=lists/youtube.txt",
                "--lua-desync=pass",
                "",
                "--new",
                "",
                "--name=Все сайты (айпи)",
                "--filter-tcp=80,443-65535",
                "--ipset-exclude=lists/ipset-ru.txt",
                "--lua-desync=pass",
                "",
            )
        )
        saved, new_key = self._enable_user_profile(text)

        names = [profile.name for profile in parse_preset_text(saved, engine="winws2").profiles]
        self.assertEqual(names, ["YouTube", "My Site", "Все сайты (айпи)"])
        self.assertEqual(new_key, "profile:1")

    def test_template_append_keeps_preset_footer(self) -> None:
        from profile.serializer import append_profile_from_template

        preset = parse_preset_text(
            "--name=A\n--filter-tcp=443\n--hostlist=lists/a.txt\n--lua-desync=pass\n\n--new\n# хвост пресета\n",
            engine="winws2",
        )
        self.assertIn("# хвост пресета", preset.footer_lines)
        template = parse_preset_text(
            "--name=B\n--filter-tcp=443\n--hostlist=lists/b.txt\n",
            engine="winws2",
        ).profiles[0]

        for position in ("top", "bottom", 1):
            with self.subTest(position=position):
                text = serialize_preset(append_profile_from_template(preset, template, position=position))
                self.assertIn("# хвост пресета", text)


class BlockcheckApplyTests(unittest.TestCase):
    def _apply(self, store: _PresetStore, **kwargs):
        from app.feature_facades.profile import ProfileFeature
        from blockcheck.strategy_scan_apply import apply_strategy

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile_feature = ProfileFeature(
                _presets_feature=store,
                _app_paths=AppPaths(user_root=root, local_root=root),
            )
            params = {
                "strategy_args": "--lua-desync=fake:blob=tls_google",
                "strategy_name": "found",
                "scan_target": "www.youtube.com",
                "scan_protocol": "tcp_https",
                "scan_udp_games_scope": "all",
            }
            params.update(kwargs)
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                return apply_strategy(profile_feature=profile_feature, **params)

    def test_port_filter_merge_is_union_of_both_values(self) -> None:
        from blockcheck.strategy_scan_apply import merge_windivert_port_filter

        self.assertEqual(merge_windivert_port_filter("80,443-65535", "443-65535"), "80,443-65535")
        self.assertEqual(merge_windivert_port_filter("443,50000-50100", "443-65535"), "443,50000-50100,443-65535")
        self.assertEqual(merge_windivert_port_filter("1-100,101-200", "50-150"), "1-100,101-200")
        self.assertEqual(merge_windivert_port_filter("~80", "443"), "~80,443")

    def test_voice_apply_merges_into_existing_udp_intercept(self) -> None:
        text = "\n".join(
            (
                "--wf-tcp-out=80,443",
                "--wf-udp-out=443,19294-19344,50000-50100",
                "",
                "--name=YouTube",
                "--filter-tcp=80,443",
                "--hostlist=lists/youtube.txt",
                "--lua-desync=pass",
                "",
            )
        )
        store = _PresetStore(text)

        result = self._apply(store, scan_protocol="stun_voice", scan_target="stun.l.google.com:19302")

        self.assertEqual(result.operation, "created")
        udp_lines = [line for line in store.text.splitlines() if line.startswith("--wf-udp-out=")]
        self.assertEqual(udp_lines, ["--wf-udp-out=443,19294-19344,50000-50100,443-65535"])

    def test_apply_to_multi_branch_profile_writes_one_union_payload(self) -> None:
        # Как выбор готовой стратегии на странице: у profile-а одна стратегия,
        # поверх веток пишется один --payload с объединением их типов.
        text = "\n".join(
            (
                "--name=YouTube",
                "--filter-tcp=80,443",
                "--hostlist-domains=www.youtube.com",
                "--out-range=-d8",
                "--payload=tls_client_hello",
                "--lua-desync=multisplit:pos=1",
                "--payload=http_req",
                "--lua-desync=fake:blob=http_req",
                "",
            )
        )
        store = _PresetStore(text)

        result = self._apply(store)

        self.assertEqual(result.operation, "updated")
        self.assertEqual(
            store.text,
            "\n".join(
                (
                    "--name=YouTube",
                    "--filter-tcp=80,443",
                    "--hostlist-domains=www.youtube.com",
                    "--out-range=-d8",
                    "--payload=tls_client_hello,http_req",
                    "--lua-desync=fake:blob=tls_google",
                    "",
                )
            ),
        )

    def test_apply_adds_scanned_payload_when_profile_payload_misses_it(self) -> None:
        text = "\n".join(
            (
                "--name=YouTube",
                "--filter-tcp=80,443",
                "--hostlist-domains=www.youtube.com",
                "--out-range=-d8",
                "--payload=http_req",
                "--lua-desync=multisplit:pos=1",
                "",
            )
        )
        store = _PresetStore(text)

        result = self._apply(store)

        self.assertEqual(result.operation, "updated")
        self.assertEqual(
            store.text,
            text.replace("--payload=http_req", "--payload=http_req,tls_client_hello").replace(
                "--lua-desync=multisplit:pos=1", "--lua-desync=fake:blob=tls_google"
            ),
        )

    def test_unconfirmed_write_is_an_error_not_success(self) -> None:
        store = _PresetStore("--name=A\n--filter-tcp=443\n--hostlist=lists/a.txt\n--lua-desync=pass\n", persist=False)

        with self.assertRaisesRegex(RuntimeError, "не сохранён"):
            self._apply(store)
        self.assertEqual(store.save_count, 1)


class _PresetLibrary:
    def __init__(self, files_by_method: dict[str, dict[str, tuple[str, str]]]) -> None:
        # file_name -> (storage_scope, text)
        self.files_by_method = files_by_method
        self.saved: list[tuple[str, str]] = []

    def read_selected_preset_source(self, launch_method: str):
        files = self.files_by_method.get(launch_method) or {}
        file_name = next(iter(files), "selected.txt")
        return files.get(file_name, ("user", ""))[1], SimpleNamespace(file_name=file_name, name=file_name)

    def save_selected_preset_source(self, launch_method: str, text: str, **_kwargs) -> None:
        files = self.files_by_method.setdefault(launch_method, {})
        file_name = next(iter(files), "selected.txt")
        self.save_preset_source_by_file_name(launch_method, file_name, text)

    def list_preset_manifests(self, launch_method: str):
        return [
            SimpleNamespace(file_name=file_name, name=file_name, storage_scope=scope, kind=scope)
            for file_name, (scope, _text) in self.files_by_method.get(launch_method, {}).items()
        ]

    def read_preset_source_by_file_name(self, launch_method: str, file_name: str) -> str:
        return self.files_by_method[launch_method][file_name][1]

    def save_preset_source_by_file_name(self, launch_method: str, file_name: str, source_text: str):
        scope, _text = self.files_by_method[launch_method][file_name]
        self.files_by_method[launch_method][file_name] = (scope, source_text)
        self.saved.append((launch_method, file_name))


class UserProfileFanOutTests(unittest.TestCase):
    def _library(self) -> _PresetLibrary:
        own = "\n".join(
            (
                "--name=My Site",
                "--filter-tcp=80,443",
                "--hostlist=lists/my-site.txt",
                "--lua-desync=fake:blob=tls_google",
                "",
            )
        )
        foreign_same_name = "\n".join(
            (
                "--name=My Site",
                "--filter-tcp=443",
                "--hostlist=lists/something-else.txt",
                "--lua-desync=pass",
                "",
            )
        )
        return _PresetLibrary(
            {
                ZAPRET2_MODE: {
                    "mine.txt": ("user", own),
                    "foreign.txt": ("user", foreign_same_name),
                    "Builtin.txt": ("builtin", own),
                },
                ZAPRET1_MODE: {
                    "mine1.txt": ("user", "--comment=My Site\n--filter-tcp=80,443\n--hostlist=lists/my-site.txt\n"),
                },
            }
        )

    def _run(self, action: str) -> tuple[_PresetLibrary, int]:
        from profile.user_profiles import create_user_profile

        library = self._library()
        originals = {
            (method, name): text
            for method, files in library.files_by_method.items()
            for name, (_scope, text) in files.items()
        }
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                service = _service(root, library)
                profile_id = create_user_profile(service._app_paths, name="My Site", protocol="tcp", ports="80,443")
                if action == "update":
                    changed = service.update_user_profile(profile_id, name="New Site", protocol="tcp", ports="443")
                else:
                    changed = service.delete_user_profile(profile_id)
        self.assertEqual(library.files_by_method[ZAPRET2_MODE]["foreign.txt"][1], originals[(ZAPRET2_MODE, "foreign.txt")])
        self.assertEqual(library.files_by_method[ZAPRET2_MODE]["Builtin.txt"][1], originals[(ZAPRET2_MODE, "Builtin.txt")])
        self.assertNotIn((ZAPRET2_MODE, "foreign.txt"), library.saved)
        self.assertNotIn((ZAPRET2_MODE, "Builtin.txt"), library.saved)
        return library, changed

    def test_update_rewrites_only_profiles_created_from_user_profile(self) -> None:
        library, changed = self._run("update")

        self.assertEqual(changed, 2)
        self.assertIn("--name=New Site", library.files_by_method[ZAPRET2_MODE]["mine.txt"][1])
        self.assertIn("--hostlist=lists/new-site.txt", library.files_by_method[ZAPRET2_MODE]["mine.txt"][1])
        self.assertIn("--comment=New Site", library.files_by_method[ZAPRET1_MODE]["mine1.txt"][1])

    def test_delete_removes_only_profiles_created_from_user_profile(self) -> None:
        library, changed = self._run("delete")

        self.assertEqual(changed, 2)
        self.assertNotIn("My Site", library.files_by_method[ZAPRET2_MODE]["mine.txt"][1])
        self.assertNotIn("My Site", library.files_by_method[ZAPRET1_MODE]["mine1.txt"][1])

    def test_concurrent_user_profile_write_is_not_lost(self) -> None:
        from profile.user_profiles import create_user_profile, update_user_profile
        from settings.store import read_settings

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = AppPaths(user_root=root, local_root=root)
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                first_id = create_user_profile(paths, name="First", protocol="tcp", ports="443")
                from profile.user_profiles import service as user_profiles_service

                original_rename = user_profiles_service._rename_user_list_file
                writers: list[threading.Thread] = []

                def _rename_while_other_writer_runs(*args, **kwargs):
                    if not writers:
                        writer = threading.Thread(
                            target=create_user_profile,
                            args=(paths,),
                            kwargs={"name": "Second", "protocol": "udp", "ports": "443"},
                        )
                        writers.append(writer)
                        writer.start()
                        writer.join(timeout=0.5)
                    return original_rename(*args, **kwargs)

                with patch.object(user_profiles_service, "_rename_user_list_file", _rename_while_other_writer_runs):
                    update_user_profile(paths, first_id, name="First renamed", protocol="tcp", ports="443")
                for writer in writers:
                    writer.join(timeout=5)
                profiles = read_settings()["user_profiles"]["profiles"]

        names = sorted(row["name"] for row in profiles.values())
        self.assertEqual(names, ["First renamed", "Second"])


class Winws1UserProfileDefaultTests(unittest.TestCase):
    def test_enabling_winws1_user_profile_does_not_pick_a_strategy(self) -> None:
        from profile.user_profiles import create_user_profile

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalog_dir = root / "system" / "strategy_catalogs" / "winws1"
            catalog_dir.mkdir(parents=True)
            (catalog_dir / "tcp.txt").write_text("[first]\nname = first\n--dpi-desync=fake\n", encoding="utf-8")
            store = _PresetStore("")
            with patch("settings.store.MAIN_DIRECTORY", str(root)):
                service = _service(root, store, ZAPRET1_MODE)
                profile_id = create_user_profile(service._app_paths, name="My Site", protocol="tcp", ports="443")
                service.set_profile_enabled(f"template:user:{profile_id}", True)

        self.assertEqual(
            _profile_lines(store.text, 0, engine="winws1"),
            ["--comment=My Site", "--filter-tcp=443", "--hostlist=lists/my-site.txt"],
        )


if __name__ == "__main__":
    unittest.main()
