from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


class _PresetFeature:
    """Выбранный пресет в памяти: запись идёт через ProfilePresetService."""

    def __init__(self, text: str):
        self.text = text
        self.saved_text = ""
        self.save_count = 0

    def get_selected_source_preset_manifest(self, _mode):
        return SimpleNamespace(file_name="Selected.txt", name="Selected")

    def read_selected_preset_source(self, _mode):
        return self.text, self.get_selected_source_preset_manifest(_mode)

    def save_selected_preset_source(self, _mode, text: str, **_kwargs):
        self.text = text
        self.saved_text = text
        self.save_count += 1


TLS_GOOGLE_BLOB_LINE = "--blob=tls_google:@bin/tls_clienthello_www_google_com.bin"


def _fakes_catalog():
    from fakes.public import FakeEntry, FakesCatalog

    return FakesCatalog(
        entries={
            "tls_google": FakeEntry(
                name="tls_google",
                source_kind="file",
                file_name="tls_clienthello_www_google_com.bin",
                hex_value=None,
                kind="tls",
                sni="www.google.com",
                description="",
                same_bytes_as=None,
            )
        }
    )


def _apply(feature, root: str, *, fakes_catalog_loader=_fakes_catalog, **kwargs):
    """apply_strategy через настоящий ProfileFeature (общий путь записи сервиса)."""
    from app.feature_facades.profile import ProfileFeature
    from blockcheck import strategy_scan_apply
    from core.paths import AppPaths

    profile_feature = ProfileFeature(
        _presets_feature=feature,
        _app_paths=AppPaths(user_root=Path(root), local_root=Path(root)),
        _fakes_catalog_loader=fakes_catalog_loader,
    )
    params = {
        "strategy_args": "--lua-desync=fake:blob=tls_google",
        "strategy_name": "found strategy",
        "scan_target": "www.youtube.com",
        "scan_protocol": "tcp_https",
    }
    params.update(kwargs)
    if "apply_lines" not in params:
        from blockcheck.strategy_search.probe_profile import build_probe_profile

        # Как у подбора: «Применить» получает проверенный профиль целиком.
        params["apply_lines"] = tuple(
            build_probe_profile(
                params["scan_protocol"],
                strategy_args=params["strategy_args"],
                match_domain=params["scan_target"],
            ).apply_lines()
        )
    with patch("settings.store.MAIN_DIRECTORY", str(root)):
        return strategy_scan_apply.apply_strategy(profile_feature=profile_feature, **params)


class StrategyScanApplyTests(unittest.TestCase):
    def test_apply_creates_profile_when_selected_preset_has_no_matching_profile(self) -> None:
        from profile.parser import parse_preset_text
        from settings.mode import ENGINE_WINWS2

        feature = _PresetFeature(
            "\n".join(
                [
                    "--new",
                    "--name=Discord",
                    "--filter-tcp=443",
                    "--hostlist-domains=discord.com",
                    "--out-range=-d8",
                    "--lua-desync=pass",
                    "",
                ]
            )
        )

        with TemporaryDirectory() as temp_dir:
            result = _apply(feature, temp_dir)

        self.assertEqual(result.operation, "created")
        self.assertIn("--hostlist-domains=www.youtube.com", feature.saved_text)
        self.assertIn("--lua-desync=fake:blob=tls_google", feature.saved_text)
        # Явное применение объявляет фейк стратегии в пресете ровно один раз.
        self.assertEqual(feature.saved_text.count("--blob=tls_google:"), 1)
        preset = parse_preset_text(feature.saved_text, engine=ENGINE_WINWS2, source_name="Selected.txt")
        self.assertIn(TLS_GOOGLE_BLOB_LINE, preset.preamble_lines)
        self.assertEqual(result.blob_warnings, ())
        self.assertEqual(len(preset.profiles), 2)
        self.assertIn("www.youtube.com", preset.profiles[0].match_signature)

        with TemporaryDirectory() as temp_dir:
            _apply(feature, temp_dir)
        self.assertEqual(feature.saved_text.count("--blob=tls_google:"), 1)

    def test_apply_keeps_preset_declaration_of_same_fake_name(self) -> None:
        text = "\n".join(
            [
                "--blob=tls_google:@bin/my_google.bin",
                "",
                "--new",
                "--name=Discord",
                "--filter-tcp=443",
                "--hostlist-domains=discord.com",
                "--lua-desync=pass",
                "",
            ]
        )
        feature = _PresetFeature(text)

        with TemporaryDirectory() as temp_dir:
            result = _apply(feature, temp_dir)

        self.assertEqual(result.operation, "created")
        blob_lines = [line for line in feature.saved_text.splitlines() if line.startswith("--blob=")]
        self.assertEqual(blob_lines, ["--blob=tls_google:@bin/my_google.bin"])
        self.assertEqual(result.blob_warnings, ())

    def test_apply_without_fakes_registry_writes_strategy_and_warns(self) -> None:
        def _missing_registry():
            raise RuntimeError("файл не найден")

        feature = _PresetFeature("--new\n--name=Discord\n--filter-tcp=443\n--hostlist-domains=discord.com\n--lua-desync=pass\n")

        with TemporaryDirectory() as temp_dir:
            result = _apply(feature, temp_dir, fakes_catalog_loader=_missing_registry)

        self.assertIn("--lua-desync=fake:blob=tls_google", feature.saved_text)
        self.assertNotIn("--blob=", feature.saved_text)
        self.assertEqual(len(result.blob_warnings), 1)
        self.assertIn("tls_google", result.blob_warnings[0])

    def test_apply_updates_existing_matching_profile(self) -> None:
        from profile.parser import parse_preset_text
        from settings.mode import ENGINE_WINWS2

        feature = _PresetFeature(
            "\n".join(
                [
                    "--new",
                    "--filter-tcp=443",
                    "--hostlist-domains=www.youtube.com",
                    "--out-range=-d8",
                    "--lua-desync=pass",
                    "",
                ]
            )
        )

        with TemporaryDirectory() as temp_dir:
            result = _apply(feature, temp_dir)

        self.assertEqual(result.operation, "updated")
        preset = parse_preset_text(feature.saved_text, engine=ENGINE_WINWS2, source_name="Selected.txt")
        self.assertEqual(len(preset.profiles), 1)
        self.assertIn("--lua-desync=fake:blob=tls_google", feature.saved_text)
        self.assertNotIn("--lua-desync=pass", feature.saved_text)

    def test_apply_updates_existing_hostlist_profile_for_target_domain(self) -> None:
        from blockcheck import strategy_scan_apply
        from profile.parser import parse_preset_text
        from settings.mode import ENGINE_WINWS2

        with TemporaryDirectory() as temp_dir:
            lists_dir = Path(temp_dir) / "lists"
            lists_dir.mkdir()
            (lists_dir / "youtube.txt").write_text("youtube.com\n", encoding="utf-8")

            from config.runtime_layout import ApplicationPaths

            feature = _PresetFeature(
                "\n".join(
                    [
                        "--new",
                        "--name=youtube.com (интерфейс)",
                        "--filter-tcp=80,443",
                        "--hostlist=lists/youtube.txt",
                        "--out-range=-d8",
                        "--payload=tls_client_hello",
                        "--lua-desync=hostfakesplit:host=ozon.ru:tcp_ts=-1000:tcp_md5:repeats=4",
                        "",
                    ]
                )
            )

            with patch.object(
                strategy_scan_apply,
                "APPLICATION_PATHS",
                ApplicationPaths.from_root(temp_dir),
            ):
                result = _apply(feature, temp_dir)

        self.assertEqual(result.operation, "updated")
        preset = parse_preset_text(feature.saved_text, engine=ENGINE_WINWS2, source_name="Selected.txt")
        self.assertEqual(len(preset.profiles), 1)
        self.assertIn("--name=youtube.com (интерфейс)", feature.saved_text)
        self.assertIn("--filter-tcp=80,443", feature.saved_text)
        self.assertIn("--hostlist=lists/youtube.txt", feature.saved_text)
        self.assertIn("--out-range=-d8", feature.saved_text)
        self.assertIn("--payload=tls_client_hello", feature.saved_text)
        self.assertIn("--lua-desync=fake:blob=tls_google", feature.saved_text)
        self.assertNotIn("--hostlist-domains=www.youtube.com", feature.saved_text)
        self.assertNotIn("hostfakesplit:host=ozon.ru", feature.saved_text)


    def test_apply_to_composite_profile_writes_one_union_payload_and_resolves(self) -> None:
        from core.paths import AppPaths
        from profile.derived_cache import basic_strategy_entries, resolve_strategy
        from profile.parser import parse_preset_text
        from profile.strategy_catalog import load_strategy_catalogs
        from settings.mode import ENGINE_WINWS2

        feature = _PresetFeature(
            "\n".join(
                [
                    "--new",
                    "--name=youtube.com (интерфейс)",
                    "--filter-tcp=443",
                    "--hostlist-domains=www.youtube.com",
                    "--out-range=-d8",
                    "--payload=tls_client_hello",
                    "--lua-desync=multisplit:pos=1",
                    "--payload=all",
                    "--lua-desync=fake:blob=fake_default_http",
                    "",
                ]
            )
        )

        with TemporaryDirectory() as temp_dir:
            catalogs_dir = Path(temp_dir) / "system" / "strategy_catalogs" / "winws2"
            catalogs_dir.mkdir(parents=True)
            (catalogs_dir / "tcp.txt").write_text(
                "[found]\nname = Found\n--lua-desync=fake:blob=tls_google\n",
                encoding="utf-8",
            )
            result = _apply(feature, temp_dir)
            catalogs = load_strategy_catalogs(AppPaths(user_root=Path(temp_dir), local_root=Path(temp_dir)), ENGINE_WINWS2)

        self.assertEqual(result.operation, "updated")
        profile_lines = feature.saved_text.split("--name=youtube.com (интерфейс)", 1)[1].splitlines()[1:]
        self.assertEqual(
            [line for line in profile_lines if line.strip()],
            [
                "--filter-tcp=443",
                "--hostlist-domains=www.youtube.com",
                "--out-range=-d8",
                "--payload=all",
                "--lua-desync=fake:blob=tls_google",
            ],
        )
        self.assertIn(TLS_GOOGLE_BLOB_LINE, feature.saved_text)
        preset = parse_preset_text(feature.saved_text, engine=ENGINE_WINWS2, source_name="Selected.txt")
        profile = preset.profiles[0]
        self.assertEqual(resolve_strategy(profile, basic_strategy_entries(profile, catalogs)), ("found", "Found"))

    def test_apply_composite_found_strategy_replaces_whole_strategy(self) -> None:
        feature = _PresetFeature(
            "\n".join(
                [
                    "--new",
                    "--filter-tcp=443",
                    "--hostlist-domains=www.youtube.com",
                    "--out-range=-d10",
                    "--payload=tls_client_hello",
                    "--lua-desync=pass",
                    "",
                ]
            )
        )
        composite = "\n".join(
            (
                "--payload=tls_client_hello",
                "--lua-desync=fake:blob=tls_google",
                "--payload=http_req",
                "--lua-desync=multisplit:pos=2",
            )
        )

        with TemporaryDirectory() as temp_dir:
            result = _apply(feature, temp_dir, strategy_args=composite)

        self.assertEqual(result.operation, "updated")
        self.assertIn("--out-range=-d10\n" + composite + "\n", feature.saved_text)
        self.assertEqual(feature.saved_text.count("--payload="), 2)
        self.assertNotIn("--out-range=-d8", feature.saved_text)
        self.assertIn(TLS_GOOGLE_BLOB_LINE, feature.saved_text)


if __name__ == "__main__":
    unittest.main()
