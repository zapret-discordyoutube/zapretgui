"""Какой профиль запущенного пресета ловит имя: те же правила, что у winws2 (руководство zapret2)."""

from __future__ import annotations

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


class WordsTests(unittest.TestCase):
    def test_row_and_advice_say_what_to_do_in_each_case(self) -> None:
        rows = {row.title: row.text for row in target_rows({"text": "x", "coverage": {"profile": "", "via": "", "strategy": True}})}
        self.assertIn("ни в один профиль", rows["Профиль пресета"])
        rows = {row.title: row.text for row in target_rows({"text": "x", "coverage": {"profile": "YouTube", "via": "список youtube.txt", "strategy": True}})}
        # Строка не обещает, что виновата стратегия: при закрытом адресе дело не в ней.
        self.assertEqual(rows["Профиль пресета"], "имя попадает в профиль «YouTube» (список youtube.txt): Zapret применяет к нему стратегию этого профиля")
        self.assertNotIn("Профиль пресета", {row.title for row in target_rows({"text": "x"})})

        self.assertIn("Добавьте эти имена", coverage_cure(pc.Coverage())[0])
        self.assertIn("нет стратегии", coverage_cure(pc.Coverage("Исключения", "список netrogat.txt", strategy=False))[0])
        # Профиль со стратегией — своего совета нет: его даёт вид блокировки.
        self.assertEqual(coverage_cure(pc.Coverage("YouTube", "список youtube.txt")), ())
        self.assertEqual(coverage_cure(None), ())


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
