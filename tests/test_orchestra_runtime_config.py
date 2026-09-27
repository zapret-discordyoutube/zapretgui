"""Рабочий конфиг оркестратора и файл поставки circular-config.txt.

winws2, запущенный как ``winws2 @file``, игнорирует все остальные аргументы
командной строки (zapret2 nfqws.c, manual.md ``@<config_file>``). Поэтому
``--lua-init`` с выученными стратегиями и ``--debug=1`` обязаны быть внутри
рабочей копии конфига, а файл поставки (он в манифесте целостности) нельзя
переписывать.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from fakes.names import BLOB_REFERENCE_ARG_NAMES, NFQWS2_BUILTIN_BLOBS  # noqa: E402

PUBLIC_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_LUA_DIR = PUBLIC_ROOT.parent / "private_zapretgui" / "dist" / "lua"


SHIPPED_CONFIG = (
    "# WinDivert filters (header)\n"
    "--wf-tcp-out=443\n"
    "\n"
    "# Lua initialization\n"
    "--lua-init=@lua/zapret-lib.lua\n"
    "--lua-init=@lua/strategy-stats.lua\n"
    "--lua-init=@lua/zapret-multishake.lua\n"
    "\n"
    "--blob=tls1:@bin/tls_clienthello_1.bin\n"
    "\n"
    "# Profile 1: TLS (HTTPS 443)\n"
    "--filter-tcp=443\n"
    "--lua-desync=circular_quality:key=tls:nld=3\n"
    "--lua-desync=fake:blob=tls1:strategy=5\n"
    "--lua-desync=send:repeats=2 --lua-desync=multisplit:pos=1:strategy=9\n"
    "\n"
    "--debug=1\n"
)


class BuildOrchestraRuntimeConfigTests(unittest.TestCase):
    def _build(self, text: str = SHIPPED_CONFIG, learned: str | None = "user/lua/learned-strategies.lua") -> list[str]:
        from orchestra.runtime_config import build_orchestra_runtime_config

        return build_orchestra_runtime_config(text, learned_lua_arg=learned).splitlines()

    def test_learned_lua_init_goes_right_after_last_shipped_lua_init(self) -> None:
        lines = self._build()
        learned_index = lines.index("--lua-init=@user/lua/learned-strategies.lua")
        self.assertEqual(lines[learned_index - 1], "--lua-init=@lua/zapret-multishake.lua")
        init_indexes = [i for i, line in enumerate(lines) if line.startswith("--lua-init=")]
        self.assertEqual(init_indexes[-1], learned_index)

    def test_without_learned_data_no_extra_lua_init(self) -> None:
        lines = self._build(learned=None)
        self.assertEqual(
            [line for line in lines if line.startswith("--lua-init=")],
            [
                "--lua-init=@lua/zapret-lib.lua",
                "--lua-init=@lua/strategy-stats.lua",
                "--lua-init=@lua/zapret-multishake.lua",
            ],
        )

    def test_debug_to_stdout_is_set_exactly_once_first(self) -> None:
        lines = self._build()
        self.assertEqual(lines[0], "--debug=1")
        self.assertEqual([line for line in lines if line.startswith("--debug")], ["--debug=1"])

    def test_debug_to_stdout_forced_even_if_source_has_other_debug_target(self) -> None:
        source = SHIPPED_CONFIG.replace("--debug=1", "--debug=@logs/x.log")
        lines = self._build(source)
        self.assertEqual([line for line in lines if line.startswith("--debug")], ["--debug=1"])

    def test_debug_added_when_source_has_none(self) -> None:
        source = SHIPPED_CONFIG.replace("--debug=1\n", "")
        lines = self._build(source)
        self.assertEqual([line for line in lines if line.startswith("--debug")], ["--debug=1"])

    def test_comment_lines_are_dropped(self) -> None:
        lines = self._build()
        self.assertFalse([line for line in lines if line.lstrip().startswith("#")])

    def test_strategy_tags_are_renumbered_from_scratch(self) -> None:
        lines = self._build()
        self.assertIn("--lua-desync=fake:blob=tls1:strategy=1", lines)
        self.assertIn(
            "--lua-desync=send:repeats=2:strategy=2 --lua-desync=multisplit:pos=1:strategy=2",
            lines,
        )
        self.assertNotIn(":strategy=5", "\n".join(lines))

    def test_leading_bom_is_removed(self) -> None:
        lines = self._build("﻿" + SHIPPED_CONFIG)
        self.assertFalse(any("﻿" in line for line in lines))
        self.assertIn("--wf-tcp-out=443", lines)


class OrchestraRunnerLaunchTests(unittest.TestCase):
    def _runner(self, root: Path):
        from orchestra import orchestra_runner as module

        with patch.object(module, "get_orchestra_keep_debug_file", return_value=False), \
                patch.object(module, "get_orchestra_auto_restart_on_discord_fail", return_value=False), \
                patch.object(module, "get_orchestra_discord_fails_for_restart", return_value=3):
            runner = module.OrchestraRunner(zapret_path=str(root))

        exe = Path(runner.winws_exe)
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"")
        lua_dir = Path(runner.lua_path)
        lua_dir.mkdir(parents=True, exist_ok=True)
        for name in (
            "zapret-lib.lua",
            "zapret-antidpi.lua",
            "zapret-auto.lua",
            "silent-drop-detector.lua",
            "strategy-stats.lua",
            "combined-detector.lua",
        ):
            (lua_dir / name).write_text("", encoding="utf-8")

        learned = Path(runner.user_lua_path) / "learned-strategies.lua"

        def _fake_learned() -> str:
            learned.parent.mkdir(parents=True, exist_ok=True)
            learned.write_text("-- learned\n", encoding="utf-8")
            return str(learned)

        runner.load_existing_strategies = MagicMock(return_value={})
        runner._generate_learned_lua = _fake_learned
        runner._generate_whitelist_file = MagicMock(return_value=True)
        runner._cleanup_old_logs = MagicMock(return_value=0)
        runner._create_startup_info = MagicMock(return_value=None)
        runner._read_output = MagicMock()
        return runner

    def _fake_process(self):
        process = MagicMock()
        process.poll.return_value = None
        process.pid = 4242
        process.stdout = io.StringIO("")
        return process

    def test_start_keeps_shipped_config_untouched_and_passes_nothing_after_at_file(self) -> None:
        from orchestra import orchestra_runner as module

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = self._runner(root)
            shipped = Path(runner.config_path)
            shipped.write_bytes(SHIPPED_CONFIG.encode("utf-8"))
            shipped_before = shipped.read_bytes()

            with patch.object(module.subprocess, "Popen", return_value=self._fake_process()) as popen:
                self.assertTrue(runner.start(), runner.last_start_error)

            self.assertEqual(shipped.read_bytes(), shipped_before)

            cmd = popen.call_args.args[0]
            self.assertEqual(cmd, [runner.winws_exe, f"@{runner.runtime_config_path}"])

            runtime_lines = Path(runner.runtime_config_path).read_text(encoding="utf-8").splitlines()
            self.assertIn("--lua-init=@user/lua/learned-strategies.lua", runtime_lines)
            self.assertEqual([line for line in runtime_lines if line.startswith("--debug")], ["--debug=1"])
            self.assertIn("--lua-desync=fake:blob=tls1:strategy=1", runtime_lines)

    def test_start_fails_without_spawn_when_runtime_config_cannot_be_written(self) -> None:
        from orchestra import orchestra_runner as module

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = self._runner(root)
            Path(runner.config_path).write_text(SHIPPED_CONFIG, encoding="utf-8")
            # Каталог на месте рабочего файла: open(..., "w") падает.
            Path(runner.runtime_config_path).mkdir(parents=True)

            with patch.object(module.subprocess, "Popen") as popen:
                self.assertFalse(runner.start())

            popen.assert_not_called()
            self.assertIn("рабочий конфиг", runner.last_start_error)

    def test_learned_lua_arg_is_relative_to_install_root_with_forward_slashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Program Files (x86)" / "Zapret"
            runner = self._runner(root)
            learned = Path(runner.user_lua_path) / "learned-strategies.lua"
            self.assertEqual(runner._learned_lua_config_arg(str(learned)), "user/lua/learned-strategies.lua")

    def test_runner_has_no_dead_blobs_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = self._runner(Path(tmp))
            self.assertFalse(hasattr(runner, "blobs_path"))


class LearnedStrategiesLuaTests(unittest.TestCase):
    """learned-strategies.lua: заблокированные стратегии передаются только данными.

    Пропуск заблокированных стратегий делает сам circular_quality через
    slm_is_blocked(askey, hostname, strategy). Раньше генератор ещё оборачивал
    circular и звал slm_is_blocked(hostname, result) — не тот порядок и не то
    число аргументов, а circular(ctx, desync) возвращает вердикт, а не номер
    стратегии.
    """

    def test_blocked_strategies_are_preloaded_without_circular_filter(self) -> None:
        from types import SimpleNamespace

        from orchestra import orchestra_runner as module

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(module, "get_orchestra_keep_debug_file", return_value=False), \
                    patch.object(module, "get_orchestra_auto_restart_on_discord_fail", return_value=False), \
                    patch.object(module, "get_orchestra_discord_fails_for_restart", return_value=3):
                runner = module.OrchestraRunner(zapret_path=str(Path(tmp)))
            runner.blocked_manager = SimpleNamespace(
                blocked_strategies={"example.com": [3, 5]},
                is_blocked=lambda _host, _strategy: False,
            )
            runner.locked_manager = SimpleNamespace(
                locked_by_askey={askey: {} for askey in module.ASKEY_ALL},
                user_locked_by_askey={askey: {} for askey in module.ASKEY_ALL},
                strategy_history={},
                get_best_strategy_from_history=lambda *_args, **_kwargs: None,
            )

            lua_path = runner._generate_learned_lua()
            self.assertIsNotNone(lua_path)
            text = Path(lua_path).read_text(encoding="utf-8")

        self.assertIn('slm_preload_blocked("tls", "example.com", {3, 5})', text)
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))
        for call in re.findall(r"slm_is_blocked\(([^()]*)\)", code):
            with self.subTest(call=call):
                self.assertEqual(len(call.split(",")), 3, "slm_is_blocked(askey, hostname, strategy)")
        self.assertNotIn("install_blocked_filter", code)


_BLOB_ARG_RE = re.compile(
    r":(?:" + "|".join(sorted(BLOB_REFERENCE_ARG_NAMES)) + r")=([^:\s]+)"
)
_BLOB_DECL_RE = re.compile(r"^--blob=([A-Za-z_][A-Za-z0-9_]*):", re.MULTILINE)
_LUA_GLOBAL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", re.MULTILINE)


@unittest.skipUnless((PRIVATE_LUA_DIR / "circular-config.txt").is_file(), "private_zapretgui не найден")
class ShippedCircularConfigBlobTests(unittest.TestCase):
    def test_every_referenced_blob_is_declared(self) -> None:
        text = (PRIVATE_LUA_DIR / "circular-config.txt").read_text(encoding="utf-8")
        declared = set(_BLOB_DECL_RE.findall(text))
        custom_funcs = PRIVATE_LUA_DIR / "custom_funcs.lua"
        lua_globals = set(_LUA_GLOBAL_RE.findall(custom_funcs.read_text(encoding="utf-8"))) if custom_funcs.is_file() else set()
        known = declared | NFQWS2_BUILTIN_BLOBS | lua_globals

        missing: dict[str, int] = {}
        for line_no, raw in enumerate(text.splitlines(), start=1):
            if "--lua-desync=" not in raw:
                continue
            for name in _BLOB_ARG_RE.findall(raw):
                if name.lower().startswith("0x"):
                    continue
                if name not in known:
                    missing.setdefault(name, line_no)

        self.assertEqual(missing, {}, "блоб не объявлен через --blob (имя: первая строка)")

    def test_hex_blob_declarations_have_whole_bytes(self) -> None:
        text = (PRIVATE_LUA_DIR / "circular-config.txt").read_text(encoding="utf-8")
        for name, hex_value in re.findall(r"^--blob=([A-Za-z0-9_]+):0x([0-9A-Fa-f]*)\s*$", text, re.MULTILINE):
            with self.subTest(blob=name):
                self.assertTrue(hex_value)
                self.assertEqual(len(hex_value) % 2, 0)


if __name__ == "__main__":
    unittest.main()
