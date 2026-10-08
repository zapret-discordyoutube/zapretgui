"""Замедление по имени сайта: один сервер, один файл, разные имена в приветствии."""

from __future__ import annotations

import unittest

from diagnostics import name_speed as ns
from diagnostics import sections


def _run(name: str, kbps: float | None, kind: str = ns.RUN_OK) -> ns.NameRun:
    if kbps is None:
        return ns.NameRun(name, kind)
    return ns.NameRun(name, ns.RUN_OK, int(kbps * 1024), 1.0)


class _Line:
    """Линия: скорость по имени; список — замеры по очереди."""

    def __init__(self, **speeds) -> None:
        self.speeds = {name.replace("_", "."): value for name, value in speeds.items()}
        self.asked: list[str] = []

    def fetch(self, name: str) -> ns.NameRun:
        self.asked.append(name)
        value = self.speeds.get(name, 5000.0)
        if isinstance(value, list):
            value = value.pop(0) if len(value) > 1 else value[0]
        if isinstance(value, str):
            return ns.NameRun(name, value)
        return _run(name, value)


def _verdicts(line: _Line, names) -> tuple[float | None, dict[str, ns.NameVerdict]]:
    control, verdicts = ns.judge(ns.collect(names, line.fetch))
    return control, {item.name: item for item in verdicts}


class NameSpeedTests(unittest.TestCase):
    def test_name_that_is_slow_twice_is_throttled(self) -> None:
        line = _Line(youtube_com=30.0)
        control, verdicts = _verdicts(line, ["youtube.com", "github.com"])

        self.assertEqual(control, 5000.0)
        self.assertEqual(verdicts["youtube.com"].code, ns.SLOW)
        self.assertIn("замедляют", verdicts["youtube.com"].text)
        self.assertEqual(verdicts["github.com"].code, ns.FINE)
        # Медленное имя перепроверено, и контроль в конце повторён.
        self.assertEqual(line.asked.count("youtube.com"), 2)
        self.assertEqual(line.asked[-1], ns.SERVER_HOST)

    def test_single_slow_measurement_is_not_believed(self) -> None:
        line = _Line(youtube_com=[30.0, 4000.0])
        _control, verdicts = _verdicts(line, ["youtube.com"])

        self.assertEqual(verdicts["youtube.com"].code, ns.FINE)

    def test_line_that_sagged_gives_no_verdict(self) -> None:
        # Контроль в начале быстрый, в конце — такой же медленный, как имя: просела сама линия.
        line = _Line(**{"mirror_yandex_ru": [5000.0, 5000.0, 40.0], "youtube_com": 30.0})
        _control, verdicts = _verdicts(line, ["youtube.com"])

        self.assertEqual(verdicts["youtube.com"].code, ns.UNKNOWN)
        self.assertIn("просела", verdicts["youtube.com"].text)

    def test_cold_first_control_does_not_hide_throttling(self) -> None:
        # Первая загрузка идёт медленнее («холодная») — в счёт идёт лучший из двух контролей.
        line = _Line(**{"mirror_yandex_ru": [300.0, 5000.0, 5000.0], "youtube_com": 30.0})
        _control, verdicts = _verdicts(line, ["youtube.com"])

        self.assertEqual(verdicts["youtube.com"].code, ns.SLOW)

    def test_slow_but_not_much_slower_than_control_is_fine(self) -> None:
        # Медленный тариф: с именем сайта не быстрее и не медленнее, чем без него.
        line = _Line(**{"mirror_yandex_ru": 200.0, "youtube_com": 150.0})
        _control, verdicts = _verdicts(line, ["youtube.com"])

        self.assertEqual(verdicts["youtube.com"].code, ns.FINE)
        self.assertEqual(line.asked.count("youtube.com"), 1)

    def test_failed_control_gives_no_verdicts_and_asks_nothing(self) -> None:
        line = _Line(**{"mirror_yandex_ru": ns.RUN_CONNECT})
        control, verdicts = _verdicts(line, ["youtube.com"])

        self.assertEqual((control, verdicts), (None, {}))
        self.assertNotIn("youtube.com", line.asked)

    def test_name_that_does_not_connect_is_unknown_not_slow(self) -> None:
        line = _Line(youtube_com=ns.RUN_CONNECT, discord_com=ns.RUN_REFUSED)
        _control, verdicts = _verdicts(line, ["youtube.com", "discord.com"])

        self.assertEqual(verdicts["youtube.com"].code, ns.UNKNOWN)
        self.assertIn("не прошло", verdicts["youtube.com"].text)
        self.assertIn("не отдал", verdicts["discord.com"].text)

    def test_number_of_names_is_limited_and_repeats_are_dropped(self) -> None:
        line = _Line()
        names = [f"site{index}.example" for index in range(ns.MAX_NAMES + 4)]
        ns.collect([names[0], *names], line.fetch)

        asked = [name for name in line.asked if name != ns.SERVER_HOST]
        self.assertEqual(asked, names[: ns.MAX_NAMES])

    def test_stop_ends_collection(self) -> None:
        line = _Line()
        facts = ns.collect(["a.example", "b.example"], line.fetch, should_stop=lambda: len(line.asked) >= 3)

        self.assertEqual([runs[0].name for runs in facts.names], ["a.example"])

    def test_little_data_over_the_whole_time_is_a_speed_not_a_failure(self) -> None:
        crawl = ns.NameRun("x", ns.RUN_OK, 9_000, ns.SAMPLE_SECONDS)
        early = ns.NameRun("x", ns.RUN_OK, 9_000, 0.1)

        self.assertAlmostEqual(crawl.kbps, 9_000 / 1024 / ns.SAMPLE_SECONDS)
        self.assertIsNone(early.kbps)


class NameChoiceTests(unittest.TestCase):
    """Какие имена идут на замер: открывшиеся, не контрольные, сначала по одному с сервиса."""

    def test_main_host_of_each_service_first_then_second_hosts(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        from diagnostics.verdict import ReachState

        def probe(host: str, *, main: bool = False, state=ReachState.OK):
            return SimpleNamespace(host=host, reach_state=state, target=SimpleNamespace(main=main))

        services = {
            "yt": SimpleNamespace(label="YouTube", control=False),
            "dc": SimpleNamespace(label="Discord", control=False),
            "ya": SimpleNamespace(label="Яндекс", control=True),
        }
        collected = {
            "yt": [probe("rr1.googlevideo.com"), probe("www.youtube.com", main=True)],
            "dc": [probe("discord.com", main=True), probe("gateway.discord.gg", state=ReachState.DPI)],
            "ya": [probe("ya.ru", main=True)],
        }
        asked: list[str] = []

        def collect(names, fetch, **_kwargs):
            asked.extend(names)
            return ns.NameFacts((ns.NameRun(ns.SERVER_HOST, ns.RUN_CONNECT),))

        run = SimpleNamespace(dns_cancelled=lambda: False, probe_cancel=None)
        with (
            patch.object(sections.net_access, "known_address", return_value="192.0.2.1"),
            patch.object(sections.name_speed, "collect", side_effect=collect),
        ):
            self.assertIsNone(sections._name_speed(run, collected, services, lambda _line: None))

        # Закрытый адрес и контрольный сайт не меряются; видеосервер YouTube — после главных адресов.
        self.assertEqual(asked, ["www.youtube.com", "discord.com", "rr1.googlevideo.com"])


class SpeedProblemsTests(unittest.TestCase):
    SPEED = {
        "names": {
            "items": [
                {"name": "YouTube", "host": "www.youtube.com", "state": "warn", "text": "0.2 Мбит/с против 40 Мбит/с"},
                {"name": "GitHub", "host": "github.com", "state": "ok", "text": "40 Мбит/с"},
            ]
        }
    }

    def test_throttled_site_becomes_a_problem(self) -> None:
        found = sections.speed_problems(self.SPEED, zapret_running=False)

        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["level"], found[0]["target"], found[0]["action"]), ("warn", "www.youtube.com", "start_zapret"))
        self.assertIn("YouTube открывается, но медленно", found[0]["text"])

    def test_with_zapret_running_the_preset_is_named_not_zapret(self) -> None:
        found = sections.speed_problems(self.SPEED, zapret_running=True)

        self.assertEqual(found[0]["action"], "strategy")
        self.assertIn("выбранный пресет", found[0]["advice"][0])

    def test_no_speed_section_no_problems(self) -> None:
        self.assertEqual(sections.speed_problems(None, zapret_running=False), [])
        self.assertEqual(sections.speed_problems({"names": None}, zapret_running=False), [])


if __name__ == "__main__":
    unittest.main()
