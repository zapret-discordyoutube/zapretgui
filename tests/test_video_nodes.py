"""Видеосерверы YouTube этой сети: откуда список, чей узел и чем запасной отличается от первого."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from diagnostics import video_nodes as vn
from utils.ip_owner import IpOwner

PLAYER = ("rr1---sn-aaa.googlevideo.com", "rr2---sn-bbb.googlevideo.com")
MAPPED = "rr1---sn-ccc.googlevideo.com"


class CombineTests(unittest.TestCase):
    def test_player_nodes_come_first_then_the_redirector_node(self) -> None:
        nodes = vn.combine(PLAYER, MAPPED)

        self.assertEqual(nodes.hosts, (*PLAYER, MAPPED))
        self.assertEqual(nodes.sources, (vn.SOURCE_PLAYER, vn.SOURCE_PLAYER, vn.SOURCE_REDIRECTOR))
        self.assertIn("получен от YouTube", nodes.note)
        self.assertIn("добавлен запасным", nodes.note)

    def test_redirector_node_that_repeats_the_player_is_not_doubled(self) -> None:
        nodes = vn.combine(PLAYER, PLAYER[1])

        self.assertEqual(nodes.hosts, PLAYER)
        self.assertIn("совпал с ответом redirector", nodes.note)

    def test_without_the_page_the_redirector_node_is_used_and_the_reason_is_named(self) -> None:
        nodes = vn.combine((), MAPPED, page_failure="страница YouTube не открылась")

        self.assertEqual((nodes.hosts, nodes.sources), ((MAPPED,), (vn.SOURCE_REDIRECTOR,)))
        self.assertIn("страница YouTube не открылась", nodes.note)
        self.assertIn("узел вашей сети", nodes.note)

    def test_without_a_real_node_nothing_is_probed_and_the_general_address_is_not_used(self) -> None:
        nodes = vn.combine((), "", page_failure="страница YouTube грузилась слишком медленно")

        # Общий адрес googlevideo.com — не видеосервер, прямой запрос к нему всегда кончается ошибкой.
        self.assertEqual((nodes.hosts, nodes.sources), ((), ()))
        self.assertIn("слишком медленно", nodes.note)
        self.assertIn("видеосервер не проверялся", nodes.note)

    def test_list_is_cut_to_the_limit_but_keeps_the_redirector_node(self) -> None:
        many = tuple(f"rr{n}---sn-x{n}.googlevideo.com" for n in range(1, 6))

        self.assertEqual(len(vn.combine(many, MAPPED, limit=3).hosts), 4)
        self.assertEqual(vn.combine(many, MAPPED, limit=3).hosts[:3], many[:3])

    def test_source_of_a_host(self) -> None:
        nodes = vn.combine(PLAYER, MAPPED)

        self.assertEqual((nodes.source_of(PLAYER[0]), nodes.source_of(MAPPED), nodes.source_of("x")), (vn.SOURCE_PLAYER, vn.SOURCE_REDIRECTOR, ""))


class DiscoverTests(unittest.TestCase):
    def test_both_sources_are_asked_and_merged(self) -> None:
        nodes = vn.discover(object(), player=lambda _run: (PLAYER, ""), mapped=lambda _run: MAPPED)

        self.assertEqual(nodes.hosts, (*PLAYER, MAPPED))

    def test_blocked_youtube_still_gives_the_network_node(self) -> None:
        nodes = vn.discover(object(), player=lambda _run: ((), "страница YouTube не открылась"), mapped=lambda _run: MAPPED)

        self.assertEqual(nodes.hosts, (MAPPED,))


class PlayerHostsTests(unittest.TestCase):
    """Страница ролика: адреса пробуются сразу, первый нашедший узлы побеждает, сбой одного адреса ничего не доказывает."""

    def _run(self):
        return SimpleNamespace(dns_cancelled=lambda: False, probe_cancel=None)

    def test_a_working_address_wins_while_others_fail(self) -> None:
        calls: list[tuple[str, str]] = []

        def fetch(_run, _host, ip, path, **kwargs):
            calls.append((ip, path))
            if ip == "2.2.2.2" and path == vn._WATCH_PAGES[0]:
                kwargs["body_done"](b'..."serverAbrStreamingUrl":"https://rr1---sn-aaa.googlevideo.com/videoplayback?x=1"...')
            return SimpleNamespace(ok=(ip == "2.2.2.2"), kind="timeout", status=None)

        with (
            patch.object(vn.net_access, "browser_ipv4", return_value=("1.1.1.1", "2.2.2.2")),
            patch.object(vn.net_access, "fetch", side_effect=fetch),
            patch("blockcheck.googlevideo_discovery.extract_googlevideo_hosts", return_value=("rr1---sn-aaa.googlevideo.com",)),
            patch("blockcheck.googlevideo_discovery.extract_probe_url", return_value=("", "")),
        ):
            hosts, failure, _probe = vn._player_hosts(self._run())

        self.assertEqual(hosts, ("rr1---sn-aaa.googlevideo.com",))
        self.assertEqual(failure, "")
        self.assertIn(("2.2.2.2", vn._WATCH_PAGES[0]), calls)

    def test_attempts_run_at_the_same_time_not_one_after_another(self) -> None:
        import time

        def slow_fetch(*_args, **_kwargs):
            time.sleep(0.3)
            return SimpleNamespace(ok=False, kind="timeout", status=None)

        started = time.monotonic()
        with (
            patch.object(vn.net_access, "browser_ipv4", return_value=("1.1.1.1", "2.2.2.2", "3.3.3.3")),
            patch.object(vn.net_access, "fetch", side_effect=slow_fetch),
        ):
            vn._player_hosts(self._run())

        # Четыре попытки по 0,3 с подряд дали бы 1,2 с.
        self.assertLess(time.monotonic() - started, 0.9)

    def test_failure_names_the_real_reason_and_the_address(self) -> None:
        reset = SimpleNamespace(ok=False, kind="reset", status=None)
        with (
            patch.object(vn.net_access, "browser_ipv4", return_value=("1.1.1.1",)),
            patch.object(vn.net_access, "fetch", return_value=reset),
        ):
            hosts, failure, _probe = vn._player_hosts(self._run())

        self.assertEqual(hosts, ())
        self.assertIn("соединение сброшено", failure)
        self.assertIn("1.1.1.1", failure)

    def test_cancelled_run_is_not_reported_as_a_closed_page(self) -> None:
        run = SimpleNamespace(dns_cancelled=lambda: True, probe_cancel=None)
        with (
            patch.object(vn.net_access, "browser_ipv4", return_value=("1.1.1.1",)),
            patch.object(vn.net_access, "fetch", return_value=SimpleNamespace(ok=False, kind="cancelled", status=None)),
        ):
            _hosts, failure, _probe = vn._player_hosts(run)

        self.assertIn("проверка остановлена", failure)

    def test_redirector_and_page_are_asked_at_the_same_time(self) -> None:
        import time

        def slow(_run):
            time.sleep(0.3)
            return MAPPED

        def slow_player(_run):
            time.sleep(0.3)
            return PLAYER, "", ("", "")

        started = time.monotonic()
        nodes = vn.discover(object(), player=slow_player, mapped=slow)

        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(nodes.hosts, (*PLAYER, MAPPED))


class MappedHostTests(unittest.TestCase):
    """Узел вашей сети от редиректора: адрес берётся как у браузера, а не только системным DNS."""

    def _run(self):
        return SimpleNamespace(dns_cancelled=lambda: False)

    def test_https_redirector_is_asked_first_by_the_browsers_address(self) -> None:
        from unittest.mock import patch

        answer = SimpleNamespace(ok=True, body=b"177.1.187.38 => tzfraa-cd (177.1.187.0/24)")
        with (
            patch.object(vn.net_access, "browser_ipv4", return_value=("192.178.170.113",)),
            patch.object(vn.net_access, "fetch", return_value=answer) as fetch,
        ):
            host = vn._mapped_host(self._run())

        self.assertTrue(host.endswith(".googlevideo.com"))
        self.assertEqual(fetch.call_args.args[1:4], ("redirector.gvt1.com", "192.178.170.113", "/report_mapping?di=no"))
        # Ответ приходит сжатым: тело нужно распаковать.
        self.assertTrue(fetch.call_args.kwargs["decode_body"])

    def test_plain_http_redirector_is_the_second_try(self) -> None:
        from unittest.mock import patch

        with (
            patch.object(vn.net_access, "browser_ipv4", return_value=("203.0.113.9",)),
            patch.object(vn.net_access, "fetch", return_value=SimpleNamespace(ok=False, body=b"")),
            patch("blockcheck.googlevideo_discovery.fetch_mapped_host_at", return_value="rr1---sn-x.googlevideo.com") as plain,
        ):
            host = vn._mapped_host(self._run())

        self.assertEqual(host, "rr1---sn-x.googlevideo.com")
        self.assertEqual(plain.call_args.args[:2], ("203.0.113.9", "redirector.googlevideo.com"))

    def test_nothing_answers_gives_no_node(self) -> None:
        from unittest.mock import patch

        with (
            patch.object(vn.net_access, "browser_ipv4", return_value=()),
            patch.object(vn.net_access, "fetch"),
        ):
            self.assertEqual(vn._mapped_host(self._run()), "")


class GoodputTests(unittest.TestCase):
    """Объём и скорость по настоящему адресу замера, который плеер YouTube получил для вас."""

    def _probe(self, volume=None):
        return SimpleNamespace(volume=volume, transfer=None, unstable="", reach_state="ok", discovery_note="")

    def _run(self, goodput):
        return SimpleNamespace(video_goodput=goodput, dns_cancelled=lambda: False, probe_cancel=None)

    def test_page_without_the_probe_url_changes_nothing(self) -> None:
        probe = self._probe()
        vn.goodput_volume(self._run(("", "")), probe)

        self.assertIsNone(probe.volume)

    def test_unknown_volume_is_replaced_by_the_goodput_series(self) -> None:
        from unittest.mock import patch

        from diagnostics import volume_probe

        samples = SimpleNamespace(samples=())
        facts = SimpleNamespace(first=samples)
        verdict = volume_probe.VolumeVerdict(volume_probe.VOLUME_OK, "по одному соединению получено 48 КБ без обрыва")
        probe = self._probe(volume_probe.VolumeVerdict(volume_probe.VOLUME_UNKNOWN, "набрать только 1 КБ"))
        with (
            patch.object(vn.net_access, "browser_ipv4", return_value=("74.125.1.1",)),
            patch.object(vn.volume_probe, "collect", return_value=facts) as collect,
            patch.object(vn.volume_probe, "judge", return_value=verdict),
            patch.object(vn.transfer_probe, "judge", return_value=None),
            patch.object(vn.transfer_probe, "shaky_reason", return_value=""),
        ):
            vn.goodput_volume(self._run(("r3---sn-tn.googlevideo.com", "/videogoodput?x=1")), probe)

        self.assertEqual(collect.call_args.args[:3], ("r3---sn-tn.googlevideo.com", "74.125.1.1", "/videogoodput?x=1"))
        self.assertIs(probe.volume, verdict)
        self.assertIn("замера скорости самого YouTube", probe.discovery_note)

    def test_a_known_volume_verdict_is_kept(self) -> None:
        from diagnostics import volume_probe

        known = volume_probe.VolumeVerdict(volume_probe.VOLUME_OK, "уже есть")
        probe = self._probe(known)
        vn.goodput_volume(self._run(("r3---sn-tn.googlevideo.com", "/videogoodput?x=1")), probe)

        self.assertIs(probe.volume, known)


class OwnerNoteTests(unittest.TestCase):
    def _note(self, owner):
        with patch("utils.ip_owner.lookup_ip_owner", return_value=owner):
            return vn.owner_note(SimpleNamespace(probe_cancel=None), "188.43.1.1")

    def test_node_in_a_providers_network_is_called_a_cache_inside_it(self) -> None:
        note = self._note(IpOwner(asn="20485", prefix="188.43.0.0/16", country="RU", owner="TRANSTELECOM"))

        self.assertIn("узел в сети провайдера, AS20485 (TRANSTELECOM)", note)
        self.assertIn("кэш Google внутри сети", note)

    def test_node_in_googles_own_network_is_named_so(self) -> None:
        self.assertIn("узел в сети Google, AS15169", self._note(IpOwner(asn="15169", prefix="", country="US", owner="GOOGLE")))

    def test_unknown_owner_gives_no_note(self) -> None:
        self.assertEqual(self._note(None), "")
        self.assertEqual(self._note(IpOwner(asn="", prefix="", country="", owner="")), "")


class SpareNoteTests(unittest.TestCase):
    @staticmethod
    def _probe(level: str, code: str = "pause", host: str = "rr2.example"):
        return SimpleNamespace(host=host, transfer=SimpleNamespace(level=level, code=code))

    def test_slow_first_and_fast_spare_points_at_the_node(self) -> None:
        note = vn.spare_note(self._probe("warn"), self._probe("ok", "ok"))

        self.assertIn("дело в первом узле", note)

    def test_both_slow_points_away_from_one_node(self) -> None:
        self.assertIn("дело не в одном узле", vn.spare_note(self._probe("warn"), self._probe("warn")))

    def test_fast_first_or_unknown_spare_says_nothing(self) -> None:
        self.assertEqual(vn.spare_note(self._probe("ok", "ok"), self._probe("ok", "ok")), "")
        self.assertEqual(vn.spare_note(self._probe("warn"), self._probe("unknown", "unknown")), "")


if __name__ == "__main__":
    unittest.main()
