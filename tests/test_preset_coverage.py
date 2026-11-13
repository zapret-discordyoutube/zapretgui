"""Какой профиль запущенного пресета ловит имя: те же правила, что у winws2 (руководство zapret2)."""

from __future__ import annotations

import dataclasses
import unittest
from pathlib import Path, PureWindowsPath

from diagnostics import preset_coverage as pc
from diagnostics.service_view import target_rows
from diagnostics.verdict import ReachState, DnsState, TargetOutcome, coverage_cure, summarize_service


def _preset(args: list[str], lists: dict[str, str] | None = None) -> pc.RunningPreset:
    files = {Path("/zapret") / name: text for name, text in (lists or {}).items()}
    return pc.parse_launch_args(args, Path("/zapret"), read_lines=lambda path: files.get(path, "").splitlines())


# Как в настоящих пресетах: общие параметры и первый профиль идут одним куском, без «--new» между ними.
YOUTUBE_PRESET = [
    "--wf-tcp-out=80,443",
    "--lua-init=@lua/zapret-lib.lua",
    "--name=Исключения домены (RU сайты)",
    "--filter-tcp=80,443-65535",
    "--hostlist=lists/netrogat.txt",
    "--payload=tls_client_hello",
    # Так профили-исключения устроены в настоящих пресетах: «pass» ничего не делает.
    "--lua-desync=pass",
    "--new",
    "--name=YouTube · быстрый протокол QUIC",
    "--filter-udp=443",
    "--hostlist=lists/youtube.txt",
    "--lua-desync=drop",
    "--new",
    "--name=YouTube · сайт и приложение",
    "--filter-tcp=80,443",
    "--hostlist=lists/youtube.txt",
    "--lua-desync=multisplit:pos=2",
    "--new",
    "--name=Бэкенд Google",
    "--filter-tcp=80,443",
    "--hostlist=lists/google-backend.txt",
    "--lua-desync=multisplit:pos=2",
]
LISTS = {
    "lists/netrogat.txt": "ya.ru\n# комментарий\nvk.com\n",
    "lists/youtube.txt": "youtube.com\nyoutubei.googleapis.com\n",
    "lists/google-backend.txt": "googleapis.com\n",
}


class MatchRulesTests(unittest.TestCase):
    def test_first_matching_profile_wins_and_names_its_list(self) -> None:
        preset = _preset(YOUTUBE_PRESET, LISTS)

        # youtubei.googleapis.com есть и в youtube.txt, и (как поддомен) в google-backend.txt: winws2 берёт первый.
        found = preset.covering("youtubei.googleapis.com", "172.217.112.4")
        self.assertEqual((found.profile, found.via, found.strategy), ("YouTube · сайт и приложение", "список youtube.txt", True))
        self.assertEqual(preset.covering("fonts.googleapis.com").profile, "Бэкенд Google")
        self.assertEqual(preset.covering("WWW.YouTube.com.").profile, "YouTube · сайт и приложение")

    def test_name_outside_every_list_is_not_covered(self) -> None:
        found = _preset(YOUTUBE_PRESET, LISTS).covering("discord.com")

        self.assertFalse(found.covered)
        self.assertEqual(found, pc.Coverage())

    def test_udp_only_profile_does_not_catch_tls_and_quic_goes_to_it(self) -> None:
        preset = _preset(YOUTUBE_PRESET, LISTS)

        self.assertEqual(preset.covering("www.youtube.com", protocol=pc.PROTOCOL_QUIC).profile, "YouTube · быстрый протокол QUIC")
        self.assertEqual(preset.covering("www.youtube.com").profile, "YouTube · сайт и приложение")

    def test_exclusion_profile_without_strategy_is_reported_as_such(self) -> None:
        found = _preset(YOUTUBE_PRESET, LISTS).covering("ya.ru")

        self.assertEqual((found.profile, found.strategy), ("Исключения домены (RU сайты)", False))

    def test_pass_and_debug_are_not_strategies_but_any_real_function_is(self) -> None:
        def strategy(*lines: str) -> bool:
            return _preset(["--name=P", *lines]).covering("x.org").strategy

        self.assertFalse(strategy("--lua-desync=pass"))
        self.assertFalse(strategy("--lua-desync=pktdebug", "--lua-desync=pass"))
        self.assertTrue(strategy("--lua-desync=pass", "--lua-desync=multisplit:pos=2"))
        self.assertTrue(strategy("--lua-desync=drop"))
        self.assertTrue(strategy("--dpi-desync=fake,multisplit"))

    def test_profile_without_any_filter_catches_everything(self) -> None:
        found = _preset(["--name=Всё подряд", "--lua-desync=fake"]).covering("example.org")

        self.assertEqual((found.profile, found.via), ("Всё подряд", "все сайты"))

    def test_empty_list_files_count_as_no_hostlist_filter(self) -> None:
        # «all list files are empty is treated as if no hostlist filter exists».
        found = _preset(["--filter-tcp=443", "--hostlist=lists/empty.txt", "--name=Мои сайты", "--lua-desync=fake"], {"lists/empty.txt": "# пусто\n"}).covering("example.org")

        self.assertEqual(found.profile, "Мои сайты")

    def test_caret_exclamation_and_exclude_lists(self) -> None:
        args = ["--name=A", "--filter-tcp=443", "--hostlist=lists/a.txt", "--hostlist-exclude-domains=bad.example.com", "--lua-desync=fake"]
        preset = _preset(args, {"lists/a.txt": "^exact.org\nexample.com\n!skip.example.com\n"})

        self.assertTrue(preset.covering("exact.org").covered)
        self.assertFalse(preset.covering("www.exact.org").covered)
        self.assertTrue(preset.covering("www.example.com").covered)
        self.assertFalse(preset.covering("skip.example.com").covered)
        self.assertFalse(preset.covering("bad.example.com").covered)

    def test_ports_l7_ipset_template_and_autohostlist(self) -> None:
        self.assertFalse(_preset(["--filter-tcp=80", "--lua-desync=fake"]).covering("x.org").covered)
        self.assertTrue(_preset(["--filter-tcp=1-1000", "--lua-desync=fake"]).covering("x.org").covered)
        self.assertFalse(_preset(["--filter-tcp=~443", "--lua-desync=fake"]).covering("x.org").covered)
        self.assertFalse(_preset(["--filter-tcp=443", "--filter-l7=quic", "--lua-desync=fake"]).covering("x.org").covered)
        ipset = _preset(["--name=IP", "--filter-tcp=443", "--ipset-ip=172.217.0.0/16", "--ipset-exclude-ip=172.217.9.9", "--lua-desync=fake"])
        self.assertEqual(ipset.covering("x.org", "172.217.112.4").via, "адрес в --ipset-ip")
        self.assertFalse(ipset.covering("x.org", "172.217.9.9").covered)
        self.assertFalse(ipset.covering("x.org", "").covered)
        self.assertFalse(_preset(["--template=t", "--lua-desync=fake"]).covering("x.org").covered)
        auto = _preset(["--name=Авто", "--filter-tcp=443", "--hostlist-auto=lists/auto.txt", "--lua-desync=fake"])
        self.assertEqual(auto.covering("new.org").profile, "Авто")


class RealPresetTests(unittest.TestCase):
    def test_builtin_preset_through_the_real_at_file_keeps_profile_names(self) -> None:
        import shlex
        import tempfile

        from winws_runtime.runners.preset_runner_support import launch_args_from_preset_text

        builtin = Path(__file__).resolve().parents[1] / "src/presets/builtin/winws2/Default + YouTube no QUIC + wssize.txt"
        args = launch_args_from_preset_text(builtin.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            # Файл @… пишется так же, как в zapret2_runner: по аргументу в строке, в кавычках shell.
            at_file = Path(tmp) / "at.txt"
            at_file.write_text("\n".join(shlex.quote(arg) for arg in args) + "\n", encoding="utf-8")
            lists = {
                "lists/youtube.txt": "youtube.com\nyoutubei.googleapis.com\n",
                "lists/googlevideo.txt": "googlevideo.com\n",
                "lists/netrogat.txt": "ya.ru\n",
            }
            # Остальные списки пресета у человека не пусты (пустой список ловил бы всё): здесь — заглушка.
            texts = {Path("/zapret") / name: text for name, text in lists.items()}
            preset = pc.parse_launch_args(
                pc.read_at_config(at_file),
                Path("/zapret"),
                read_lines=lambda path: texts.get(path, "placeholder.invalid").splitlines(),
            )

        self.assertEqual(preset.covering("youtubei.googleapis.com", "172.217.112.4").profile, "YouTube · сайт и приложение")
        self.assertEqual(preset.covering("rr4---sn-x.googlevideo.com").profile, "YouTube · видео (googlevideo.com)")
        # Исключения в настоящем пресете пропускают трафик через «pass»: это не стратегия.
        excluded = preset.covering("ya.ru", "77.88.55.242")
        self.assertEqual((excluded.profile, excluded.strategy), ("Исключения домены (RU сайты)", False))


class RunningProcessTests(unittest.TestCase):
    def test_at_file_is_taken_from_the_winws2_command_line(self) -> None:
        line = r'"C:\Zapret\Dev\exe\winws2.exe" @C:\Zapret\Dev\user\tmp\winws2_at_config\winws2_at_0123.txt'
        found = pc._at_config_of(line)

        self.assertEqual(PureWindowsPath(str(found)).name, "winws2_at_0123.txt")
        self.assertIsNone(pc._at_config_of(r'"C:\Zapret\Dev\exe\winws2.exe" --version'))

    def test_at_file_lines_are_shell_quoted_arguments(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "at.txt"
            path.write_text("--name='YouTube · сайт и приложение'\n--filter-tcp=80,443\n\n", encoding="utf-8")

            self.assertEqual(pc.read_at_config(path), ["--name=YouTube · сайт и приложение", "--filter-tcp=80,443"])


class PresetAndStrategyNameTests(unittest.TestCase):
    """Строка называет пресет и стратегию — отчёт не заставляет гадать, что именно не сработало."""

    BUILTIN = Path(__file__).resolve().parents[1] / "src/presets/builtin/winws2/Default + YouTube no QUIC + wssize.txt"
    SRC = Path(__file__).resolve().parents[1] / "src"

    def test_preset_is_found_by_the_at_file_name_the_runner_writes(self) -> None:
        import tempfile
        from types import SimpleNamespace

        from winws_runtime.runners.zapret2_runner import Winws2StrategyRunner

        text = self.BUILTIN.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            mine = base / "presets" / "winws2_builtin" / "Мой пресет.txt"
            other = base / "presets" / "winws2" / "Другой.txt"
            stale = base / "presets" / "replaced" / "winws2" / "Мой пресет.txt"
            for path in (mine, other, stale):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            # Файл @… пишет сам zapret2_runner: если он поменяет имя файла, тест это заметит.
            runner = SimpleNamespace(work_dir=str(base))
            runner._build_winws2_at_config_text = lambda prepared: Winws2StrategyRunner._build_winws2_at_config_text(runner, prepared)
            runner._winws2_at_config_dir = lambda: Winws2StrategyRunner._winws2_at_config_dir(runner)
            config = Path(Winws2StrategyRunner._write_winws2_at_config(runner, str(mine), text))

            self.assertEqual(pc.preset_name_of(config, config.resolve().parents[3]), "Мой пресет")
            config.rename(config.with_name("winws2_at_00000000000000000000.txt"))
            self.assertEqual(pc.preset_name_of(config.with_name("winws2_at_00000000000000000000.txt"), base), "")

    def _running(self, args: list[str]) -> pc.RunningPreset:
        lists = {"youtube.txt": ["youtube.com", "youtubei.googleapis.com"], "googlevideo.txt": ["googlevideo.com"], "netrogat.txt": ["ya.ru"]}
        # Остальные списки у человека не пусты (пустой ловил бы всё): здесь — заглушка.
        running = pc.parse_launch_args(args, Path("/zapret"), read_lines=lambda path: lists.get(path.name, ["placeholder.invalid"]))
        return pc.with_strategy_names(dataclasses.replace(running, preset="Default + YouTube no QUIC + wssize"), "\n".join(args), self.SRC)

    def test_strategy_names_come_from_the_ready_strategy_lists(self) -> None:
        from winws_runtime.runners.preset_runner_support import launch_args_from_preset_text

        args = launch_args_from_preset_text(self.BUILTIN.read_text(encoding="utf-8"))
        running = self._running(args)

        site = running.covering("youtubei.googleapis.com", "172.217.112.4")
        self.assertEqual((site.profile, site.strategy_name, site.preset), ("YouTube · сайт и приложение", "MultiSplit Pattern 7", "Default + YouTube no QUIC + wssize"))
        video = running.covering("rr4---sn-x.googlevideo.com")
        self.assertEqual(video.strategy_name, "MultiDisorder Complex + WSSize 1:1 · из googlevideo.com")
        quic = running.covering("rr4---sn-x.googlevideo.com", protocol=pc.PROTOCOL_QUIC)
        self.assertEqual(quic.strategy_name, "Drop · выключить QUIC")
        # Профиль-исключение без стратегии: названия нет, есть только «нет стратегии».
        self.assertEqual(running.covering("ya.ru", "77.88.55.242").strategy_name, "")

        own = ["--lua-desync=fake:blob=my_own_unlisted_blob:repeats=99" if arg.startswith("--lua-desync=multisplit:pos=2,midsld-2") else arg for arg in args]
        custom = self._running(own).covering("youtubei.googleapis.com", "172.217.112.4")
        self.assertEqual((custom.strategy_name, custom.custom_strategy), ("", True))
        # Своя стратегия показывается как есть: видно, что именно работает.
        self.assertEqual(custom.strategy_text, "--lua-desync=fake:blob=my_own_unlisted_blob:repeats=99")


class Winws1PresetTests(unittest.TestCase):
    """Zapret 1: название профиля в ``--comment``, полные пути в ``@…`` — а названия стратегий те же, что на странице профилей."""

    BUILTIN = Path(__file__).resolve().parents[1] / "src/presets/builtin/winws1/alt1.txt"
    CATALOGS = Path(__file__).resolve().parents[1] / "src/system/strategy_catalogs"

    def _launch(self, base: Path, preset: Path) -> Path:
        from types import SimpleNamespace

        from winws_runtime.runners.runner_base import StrategyRunnerBase
        from winws_runtime.runners.zapret1_runner import Winws1StrategyRunner

        # Файл @… пишет сам zapret1_runner (с полными путями): если он поменяет запись, тест это заметит.
        runner = SimpleNamespace(work_dir=str(base), lists_dir=str(base / "lists"), bin_dir=str(base / "bin"))
        runner._resolve_file_paths = lambda args: StrategyRunnerBase._resolve_file_paths(runner, args)
        runner._build_winws1_at_config_text = lambda text: Winws1StrategyRunner._build_winws1_at_config_text(runner, text)
        runner._winws1_at_config_dir = lambda: Winws1StrategyRunner._winws1_at_config_dir(runner)
        return Path(Winws1StrategyRunner._write_winws1_at_config(runner, str(preset), preset.read_text(encoding="utf-8")))

    def test_named_and_own_strategies_of_a_running_zapret1_preset(self) -> None:
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            preset = base / "presets" / "winws1_builtin" / "alt1.txt"
            preset.parent.mkdir(parents=True)
            preset.write_text(self.BUILTIN.read_text(encoding="utf-8"), encoding="utf-8")
            shutil.copytree(self.CATALOGS / "winws1", base / "system" / "strategy_catalogs" / "winws1")
            (base / "lists").mkdir()
            for name in ("discord.txt", "ipset-discord.txt", "list-general.txt", "ipset-cloudflare.txt"):
                (base / "lists" / name).write_text("placeholder.invalid\n", encoding="utf-8")
            (base / "lists" / "discord.txt").write_text("discord.com\n", encoding="utf-8")
            (base / "lists" / "ipset-discord.txt").write_text("162.159.130.0/24\n", encoding="utf-8")
            config = self._launch(base, preset)
            self.assertIn(str(base / "lists" / "discord.txt"), config.read_text(encoding="utf-8"))

            running = pc.running_from_at_config(config, "winws.exe")
            self.assertEqual(running.preset, "alt1")
            voice = running.covering("discord.media", "162.159.130.234", protocol=pc.PROTOCOL_QUIC, port=50001)
            self.assertEqual((voice.profile, voice.strategy_name), ("Discord · UDP (обычно не нужно)", "General ALT2 1.6.1"))
            own = running.covering("discord.com", protocol=pc.PROTOCOL_QUIC)
            self.assertTrue(own.custom_strategy)
            self.assertEqual(
                own.strategy_text,
                "--dpi-desync=fake --dpi-desync-repeats=6 --dpi-desync-fake-quic=bin/quic_initial_www_google_com.bin",
            )
            # Полный путь (так его пишет программа) — только именем файла.
            self.assertEqual(pc.strategy_text(["--dpi-desync-fake-tls=C:\\Zapret\\Dev\\bin\\tls.bin", "--x=/opt/z/bin/a.bin"]), "--dpi-desync-fake-tls=tls.bin --x=a.bin")

            # Пресет поменяли после запуска: @… уже не из него — названий нет, гадать нельзя.
            preset.write_text(preset.read_text(encoding="utf-8").replace("--dpi-desync-repeats=6", "--dpi-desync-repeats=7"), encoding="utf-8")
            stale = pc.running_from_at_config(config, "winws.exe").covering("discord.media", "162.159.130.234", protocol=pc.PROTOCOL_QUIC, port=50001)
            self.assertEqual((stale.profile, stale.strategy_name, stale.custom_strategy), ("Discord · UDP (обычно не нужно)", "", False))


class OrchestraTests(unittest.TestCase):
    """Оркестратор: строка говорит, какую стратегию он закрепил за именем, а не «своя стратегия»."""

    SOURCE = (
        "--wf-tcp-out=443\n"
        "--lua-init=@lua/zapret-lib.lua\n"
        "# Profile 1: TLS (HTTPS 443)\n"
        "--filter-tcp=443\n"
        "--hostlist-exclude=user/lua/whitelist.txt\n"
        "--lua-desync=circular_quality:key=tls:fails=1:nld=3\n"
        "--lua-desync=fake:blob=tls_google:repeats=2\n"
        "--lua-desync=wssize:wsize=1:scale=1 --lua-desync=multidisorder:pos=1:seqovl=1\n"
        "--new\n"
        "# Profile 3: QUIC\n"
        "--filter-udp=443\n"
        "--filter-l7=quic\n"
        "--payload=quic_initial\n"
        "--lua-desync=circular_quality:key=quic:fails=3\n"
        "--lua-desync=drop\n"
    )

    def test_locked_strategy_and_its_lines_are_named(self) -> None:
        import tempfile

        from orchestra.runtime_config import build_orchestra_runtime_config

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            # Файл, с которым оркестратор запускает winws2, — тем же сборщиком, что у orchestra_runner.
            config = base / "user" / "lua" / pc.ORCHESTRA_AT_CONFIG_NAME
            config.parent.mkdir(parents=True)
            config.write_text(build_orchestra_runtime_config(self.SOURCE), encoding="utf-8")
            (config.parent / "whitelist.txt").write_text("ya.ru\n", encoding="utf-8")
            asked: list = []

            def locks(keys):
                asked.append(list(keys))
                return {"tls": {"googleapis.com": 1, "youtubei.googleapis.com": 2}, "quic": {}}

            running = pc.running_from_at_config(config, "winws2.exe", locks=locks)

        self.assertEqual(asked, [["tls", "quic"]])
        site = running.covering("youtubei.googleapis.com", "172.217.112.4")
        self.assertEqual((site.orchestra, site.profile, site.locked_strategy), (True, "TLS", 2))
        self.assertEqual(site.strategy_text, "--lua-desync=wssize:wsize=1:scale=1 --lua-desync=multidisorder:pos=1:seqovl=1")
        self.assertEqual((site.custom_strategy, site.strategy_name, site.preset), (False, "", ""))
        # Запись под родительским именем (nld) тоже находится; не закреплено — номер 0.
        self.assertEqual(running.covering("www.googleapis.com").locked_strategy, 1)
        self.assertEqual(running.covering("example.org").locked_strategy, 0)
        self.assertEqual(running.covering("rr1.googlevideo.com", protocol=pc.PROTOCOL_QUIC).profile, "QUIC")
        # Имя из белого списка оркестратор не трогает.
        white = running.covering("ya.ru", "77.88.55.242")
        self.assertEqual((white.covered, white.orchestra), (False, True))

    def test_lock_lookup_needs_at_least_two_labels(self) -> None:
        self.assertEqual(pc.locked_number({"b.example.com": 5}, "a.b.example.com"), 5)
        self.assertEqual(pc.locked_number({"com": 5}, "example.com"), 0)
        self.assertEqual(pc.locked_number({"example.com": "x"}, "example.com"), 0)


class WordsTests(unittest.TestCase):
    def test_row_and_advice_say_what_to_do_in_each_case(self) -> None:
        rows = {row.title: row.text for row in target_rows({"text": "x", "coverage": {"profile": "", "via": "", "strategy": True}})}
        self.assertIn("ни в один профиль", rows["Профиль пресета"])
        rows = {row.title: row.text for row in target_rows({"text": "x", "coverage": {"profile": "YouTube", "via": "список youtube.txt", "strategy": True}})}
        # Строка не обещает, что виновата стратегия: при закрытом адресе дело не в ней.
        self.assertEqual(rows["Профиль пресета"], "имя попадает в профиль «YouTube» (список youtube.txt): Zapret применяет к нему стратегию этого профиля")
        self.assertNotIn("Профиль пресета", {row.title for row in target_rows({"text": "x"})})
        # Пресет и стратегия названы — отчёт с другой сети читается без вопросов «какой пресет, какая стратегия».
        full = {"profile": "YouTube", "via": "список youtube.txt", "strategy": True, "strategy_name": "MultiSplit Pattern 7", "preset": "Default"}
        rows = {row.title: row.text for row in target_rows({"text": "x", "coverage": full})}
        self.assertEqual(
            rows["Профиль пресета"],
            "имя попадает в профиль «YouTube» пресета «Default» (список youtube.txt): "
            "Zapret применяет к нему стратегию этого профиля — «MultiSplit Pattern 7»",
        )
        rows = {row.title: row.text for row in target_rows({"text": "x", "coverage": {**full, "strategy_name": "", "custom_strategy": True}})}
        self.assertTrue(rows["Профиль пресета"].endswith("— своя, среди готовых стратегий её нет"))
        own = {**full, "strategy_name": "", "custom_strategy": True, "strategy_text": "--dpi-desync=fake"}
        rows = {row.title: row.text for row in target_rows({"text": "x", "coverage": own})}
        self.assertTrue(rows["Профиль пресета"].endswith("— своя, среди готовых стратегий её нет: --dpi-desync=fake"))
        rows = {row.title: row.text for row in target_rows({"text": "x", "coverage": {"profile": "", "preset": "Default"}})}
        self.assertIn("ни в один профиль запущенного пресета «Default»", rows["Профиль пресета"])
        self.assertIn("профиль пресета «Default»", coverage_cure(pc.Coverage(preset="Default"))[0])

        self.assertIn("Добавьте эти имена", coverage_cure(pc.Coverage())[0])
        self.assertIn("нет стратегии", coverage_cure(pc.Coverage("Исключения", "список netrogat.txt", strategy=False))[0])
        # Профиль со стратегией — своего совета нет: его даёт вид блокировки.
        self.assertEqual(coverage_cure(pc.Coverage("YouTube", "список youtube.txt")), ())
        self.assertEqual(coverage_cure(None), ())

    def test_orchestra_rows_and_advice(self) -> None:
        locked = {"profile": "TLS", "via": "все сайты", "strategy": True, "orchestra": True, "locked_strategy": 265, "strategy_text": "--lua-desync=drop"}
        rows = {row.title: row.text for row in target_rows({"text": "x", "coverage": locked})}
        self.assertEqual(
            rows["Профиль пресета"],
            "имя попадает в профиль «TLS» оркестратора (все сайты): оркестратор закрепил за этим именем стратегию №265 (--lua-desync=drop)",
        )
        rows = {row.title: row.text for row in target_rows({"text": "x", "coverage": {**locked, "locked_strategy": 0}})}
        self.assertIn("пока нет — оркестратор ещё перебирает стратегии", rows["Профиль пресета"])
        self.assertNotIn("пресета", rows["Профиль пресета"].split(":")[0].replace("Профиль пресета", ""))
        rows = {row.title: row.text for row in target_rows({"text": "x", "coverage": {"profile": "", "orchestra": True}})}
        self.assertIn("ни в один его профиль", rows["Профиль пресета"])
        self.assertIn("Белый список", coverage_cure(pc.Coverage(orchestra=True))[0])
        self.assertEqual(coverage_cure(pc.Coverage("TLS", "все сайты", orchestra=True, locked_strategy=3)), ())

    def test_row_button_leads_where_it_is_fixed(self) -> None:
        def action(coverage):
            [row] = [row for row in target_rows({"text": "x", "host": "youtubei.googleapis.com", "coverage": coverage}) if row.title == "Профиль пресета"]
            return row.action, row.target

        self.assertEqual(action({"profile": "YouTube", "strategy": True}), ("strategy", "youtubei.googleapis.com"))
        self.assertEqual(action({"profile": "", "strategy": True}), ("profiles", "youtubei.googleapis.com"))
        self.assertEqual(action({"profile": "Исключения", "strategy": False}), ("profiles", "youtubei.googleapis.com"))
        self.assertEqual(action({"profile": "TLS", "orchestra": True}), ("orchestra_settings", "youtubei.googleapis.com"))
        # Кнопка только на экране: текст отчёта её не печатает.
        self.assertTrue(all(not row.action for row in target_rows({"text": "x"})))


class AdviceByBlockKindTests(unittest.TestCase):
    """Совет по профилю не доказан пробами: он заменяет только «подберите стратегию»."""

    @staticmethod
    def _advice(reach: ReachState, coverage: pc.Coverage, kind: str = "") -> tuple[str, ...]:
        from types import SimpleNamespace

        from diagnostics import engine
        from diagnostics.services import Service, Target

        target = Target("rutracker.org", "сайт", main=True)
        probe = SimpleNamespace(
            host="rutracker.org", target=target, reach_state=reach, judgement=None, kind=kind,
            hosts_stale=False, unstable="", dns=SimpleNamespace(ips=("1.2.3.4",)), hosts_ips=(),
            video_node=None, coverage=coverage,
        )
        service = Service("rutracker", "RuTracker", (target,))
        return engine._service_verdict(service, [probe], zapret_running=True).advice

    def test_name_block_outside_every_profile_replaces_pick_a_strategy(self) -> None:
        advice = self._advice(ReachState.DPI, pc.Coverage(), kind="sni")

        self.assertEqual(advice, coverage_cure(pc.Coverage()))

    def test_closed_address_keeps_its_advice_and_gets_the_profile_hint_after_it(self) -> None:
        not_covered = self._advice(ReachState.IP_BLOCK, pc.Coverage(), kind="noconnect")
        covered = self._advice(ReachState.IP_BLOCK, pc.Coverage("Cloudflare TCP", "адрес в ipset-cloudflare.txt"), kind="noconnect")

        self.assertEqual(not_covered[1:], coverage_cure(pc.Coverage()))
        self.assertNotIn("профиль", not_covered[0])
        # Адрес ловит профиль со стратегией: «подберите другую стратегию» здесь был бы неправдой.
        self.assertEqual(len(covered), 1)
        self.assertNotIn("Подберите другую стратегию", covered[0])

    def test_advice_of_the_broken_part_names_the_profile(self) -> None:
        cure = coverage_cure(pc.Coverage("Исключения домены (RU сайты)", "список netrogat.txt", strategy=False))
        verdict = summarize_service(
            "YouTube",
            [
                TargetOutcome("www.youtube.com", "сайт", ReachState.OK, DnsState.OK, main=True),
                TargetOutcome("youtubei.googleapis.com", "служба плеера", ReachState.DPI, DnsState.OK, kind="sni", cure=cure),
            ],
            zapret_running=True,
        )

        self.assertEqual(verdict.advice, cure)
        self.assertIn("«Исключения домены (RU сайты)»", verdict.advice[0])

if __name__ == "__main__":
    unittest.main()
