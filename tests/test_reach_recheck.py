"""Сайт называется «забаненным по адресу», только когда это перепроверено.

Одно неудачное соединение бывает из-за устаревшей записи в файле hosts,
потерянного пакета или одного закрытого адреса из нескольких. Раньше любой
такой сбой шёл в группу «Бан по адресу (IP)» — и туда попадали сайты,
которые у пользователя открываются.
"""

import unittest
from unittest.mock import patch

from diagnostics import block_kind as bk
from diagnostics import engine, net_access, reach, report_text, service_view, video_nodes
from diagnostics.tls_probe import KIND_CONNECT, KIND_OK, KIND_TIMEOUT, ProbeResult
from diagnostics.verdict import ReachState, judge_reach
from utils.windows_dns_query import DnsAnswer

HOSTS_IP = "151.101.130.146"
REAL = ("104.244.42.1", "104.244.42.65", "104.244.42.129")


def _ok(ip: str) -> ProbeResult:
    return ProbeResult(ip=ip, kind=KIND_OK, status=200)


def _dead(ip: str) -> ProbeResult:
    """Адрес молчит: на соединение нет никакого ответа."""
    return ProbeResult(ip=ip, kind=KIND_CONNECT, connect_fail="timeout")


class ReachRecheckTests(unittest.TestCase):
    def _reach(self, answers, *, hosts=(), system=REAL, reference=REAL, ipv6=(), quic_ok=False):
        """Прогоняет «открывается ли» с заданным ответом каждого адреса. Возвращает (проба, порядок запросов)."""
        probe = engine._Probe(target=engine.Target("x.com", "сайт", main=True), service="x", host="x.com")
        probe.hosts_ips = tuple(hosts)
        probe.dns = DnsAnswer(ips=tuple(system))
        probe.reference_ips = tuple(reference)
        asked: list[str] = []

        def _get(_run, _host, ip, _path, **_kwargs):
            asked.append(ip)
            answer = answers(ip) if callable(answers) else answers[ip]
            return answer

        run = engine._Run(None, workers=8, deadline=60)
        self.addCleanup(run.close)
        with (
            patch.object(net_access, "get", side_effect=_get),
            # Адреса IPv6 эталон отдаёт только запасной попытке, когда IPv4 не открылся.
            patch.object(net_access, "doh_lookup", return_value=(True, tuple(ipv6))),
            patch.object(reach, "RETRY_PAUSE_S", 0.0),
        ):
            reach.check_reach(run, probe, read_limit=0)
        probe.reach_state = judge_reach(probe.reach)
        if quic_ok:
            probe.quic = engine.quic_probe.QuicVerdict(engine.quic_probe.QUIC_OK, "отвечает")
        return probe, asked

    def test_stale_hosts_address_does_not_make_the_site_blocked(self) -> None:
        """Запись в hosts ведёт на неотвечающий адрес, а настоящий адрес сайта открывается."""
        probe, asked = self._reach(lambda ip: _dead(ip) if ip == HOSTS_IP else _ok(ip), hosts=(HOSTS_IP,))

        self.assertEqual(asked[0], HOSTS_IP)
        self.assertEqual(probe.reach_state, ReachState.OK)
        self.assertTrue(probe.hosts_stale)
        self.assertNotEqual(probe.reach_source, reach.SOURCE_HOSTS)
        self.assertEqual(probe.kind, "")
        self.assertIn("запись в нём устарела", service_view.reach_text(probe))

    def test_hosts_address_alone_never_proves_an_address_ban(self) -> None:
        """Других адресов узнать не удалось: проверена запись в hosts, а не сайт."""
        probe, asked = self._reach(_dead, hosts=(HOSTS_IP,), system=(), reference=())

        self.assertEqual(asked, [HOSTS_IP, HOSTS_IP])
        self.assertEqual(probe.reach_state, ReachState.IP_BLOCK)
        self.assertFalse(probe.address_confirmed)
        self.assertEqual(probe.kind, bk.KIND_NO_CONNECT)

    def test_one_dead_address_of_several_is_not_a_block(self) -> None:
        """Первые адреса молчат, а один из остальных открывается — браузер взял бы его."""
        probe, asked = self._reach(lambda ip: _ok(ip) if ip == REAL[2] else _dead(ip))

        self.assertEqual(set(asked), set(REAL))
        self.assertEqual(probe.reach_state, ReachState.OK)
        self.assertEqual(probe.reach.ip, REAL[2])
        self.assertFalse(probe.hosts_stale)
        self.assertEqual(probe.kind, "")

    def test_every_address_silent_is_a_confirmed_address_ban(self) -> None:
        probe, asked = self._reach(_dead)

        self.assertEqual(set(asked), set(REAL))
        # Три адреса разом и ещё одна попытка на первом после паузы.
        self.assertEqual(len(asked), 4)
        # Запасные адреса уходят одновременно: их порядок в ``asked`` случаен.
        self.assertEqual(sorted(probe.tried), sorted((ip, KIND_CONNECT) for ip in asked))
        self.assertEqual((probe.tried[0][0], probe.tried[-1][0]), (REAL[0], REAL[0]))
        self.assertTrue(probe.address_confirmed)
        self.assertEqual(probe.kind, bk.KIND_IP)
        self.assertIn("не ответил ни один из 3 адресов", service_view.reach_text(probe))

    def test_single_address_is_retried_after_a_pause(self) -> None:
        """Потерянный пакет: первая попытка не прошла, повтор на том же адресе — прошёл."""
        answers = iter([_dead(REAL[0]), _ok(REAL[0])])
        probe, asked = self._reach(lambda _ip: next(answers), system=REAL[:1], reference=REAL[:1])

        self.assertEqual(asked, [REAL[0], REAL[0]])
        self.assertEqual(probe.reach_state, ReachState.OK)

    def test_single_address_silent_twice_is_confirmed(self) -> None:
        probe, asked = self._reach(_dead, system=REAL[:1], reference=REAL[:1])

        self.assertEqual(asked, [REAL[0], REAL[0]])
        self.assertTrue(probe.address_confirmed)
        self.assertEqual(probe.kind, bk.KIND_IP)

    def test_answering_quic_cancels_the_address_ban(self) -> None:
        """QUIC к тому же адресу отвечает: дорога до адреса открыта, браузер сайт откроет."""
        probe, _asked = self._reach(_dead, quic_ok=True)

        self.assertFalse(probe.address_confirmed)
        self.assertEqual(probe.kind, bk.KIND_NO_CONNECT)

    def test_ipv6_is_tried_even_when_the_address_came_from_hosts(self) -> None:
        v6 = "2606:4700::1"
        probe, asked = self._reach(lambda ip: _ok(ip) if ip == v6 else _dead(ip), hosts=(HOSTS_IP,), ipv6=(v6,))

        self.assertEqual(asked[-1], v6)
        self.assertEqual(probe.reach_state, ReachState.OK)
        self.assertTrue(probe.hosts_stale)

    def test_refused_or_system_error_is_not_an_address_ban(self) -> None:
        """Адрес сам отказал или система не дала соединиться (нет сети, сетевой экран): фильтр так не закрывает."""
        for fail in ("refused", "error"):
            with self.subTest(fail=fail):
                probe, _asked = self._reach(lambda ip, fail=fail: ProbeResult(ip=ip, kind=KIND_CONNECT, connect_fail=fail))
                self.assertFalse(probe.address_confirmed)
                self.assertEqual(probe.kind, bk.KIND_NO_CONNECT)

    def test_all_addresses_get_one_more_try_after_a_pause(self) -> None:
        """Адреса пробуются в одну секунду: секундный сбой сети задел бы все. Нужна попытка позже."""
        calls = {"n": 0}

        def answer(ip):
            calls["n"] += 1
            # Первый залп (три адреса) не проходит, попытка после паузы — проходит.
            return _dead(ip) if calls["n"] <= 3 else _ok(ip)

        probe, asked = self._reach(answer)

        self.assertEqual(len(asked), 4)
        self.assertEqual(asked[-1], asked[0])
        self.assertEqual(probe.reach_state, ReachState.OK)

    def test_mixed_failures_are_not_called_an_address_ban(self) -> None:
        """Один адрес не соединился, другой соединился и замолчал: это уже не «молчат все адреса»."""
        probe, _asked = self._reach(
            lambda ip: _dead(ip) if ip == REAL[0] else ProbeResult(ip=ip, kind=KIND_TIMEOUT, stage="tls")
        )

        self.assertFalse(probe.address_confirmed)

    def test_no_more_than_four_addresses_are_tried(self) -> None:
        many = tuple(f"10.0.0.{n}" for n in range(1, 10))
        _probe, asked = self._reach(_dead, system=many, reference=many)

        self.assertEqual(len(set(asked)), reach.REACH_ADDRESSES)

    def test_other_networks_are_tried_before_neighbours(self) -> None:
        """У сайта пять адресов одной сети молчат, а адрес другой сети открывается."""
        same = tuple(f"87.240.132.{n}" for n in range(1, 6))
        other = "93.186.225.194"
        probe, asked = self._reach(
            lambda ip: _ok(ip) if ip == other else _dead(ip), system=same + (other,), reference=same + (other,)
        )

        self.assertIn(other, asked)
        self.assertEqual(probe.reach_state, ReachState.OK)
        self.assertEqual(probe.reach.ip, other)

    def test_deadline_before_the_recheck_means_not_enough_time(self) -> None:
        probe = engine._Probe(target=engine.Target("x.com", "сайт", main=True), service="x", host="x.com")
        probe.dns = DnsAnswer(ips=REAL)
        probe.reference_ips = REAL
        run = engine._Run(None, workers=4, deadline=60)
        self.addCleanup(run.close)

        def _get(_run, _host, ip, _path, **_kwargs):
            # Общий лимит времени истекает, пока идёт первый запрос.
            run.deadline = 0.0
            return _dead(ip)

        with patch.object(net_access, "get", side_effect=_get):
            reach.check_reach(run, probe, read_limit=0)

        self.assertEqual(judge_reach(probe.reach), ReachState.UNKNOWN)
        self.assertEqual(probe.kind, "")

    def test_silent_first_address_does_not_hold_back_the_others(self) -> None:
        # Первый адрес молчит весь свой срок. Остальные должны уйти, не дожидаясь его:
        # иначе каждый закрытый сайт стоил бы проверке лишних секунд.
        import threading
        import time

        first_done = threading.Event()
        order: list[str] = []

        def _answers(ip: str) -> ProbeResult:
            if ip == REAL[0]:
                time.sleep(0.4)
                first_done.set()
            else:
                order.append("after" if first_done.is_set() else "before")
            return _dead(ip)

        with patch.object(reach, "HEAD_START_S", 0.05):
            self._reach(_answers)

        self.assertTrue(order)
        self.assertEqual(set(order[: len(REAL) - 1]), {"before"})

    def test_quick_first_address_is_the_only_one_asked(self) -> None:
        _probe, asked = self._reach(_ok)

        self.assertEqual(asked, [REAL[0]])

    def test_report_tells_which_addresses_were_tried(self) -> None:
        probe, asked = self._reach(_dead)
        report = report_text.target_report(probe)

        # Запасные адреса пробуются одновременно, поэтому их порядок в ``asked`` случаен.
        tried = [item["address"] for item in report["tried"]]
        self.assertEqual((tried[0], tried[-1], sorted(tried)), (asked[0], asked[-1], sorted(asked)))
        self.assertEqual({item["result"] for item in report["tried"]}, {"connect"})
        self.assertTrue(report["address_confirmed"])
        self.assertEqual(report["kind"], "ip")


class NoRealNodeTests(unittest.TestCase):
    def test_video_target_without_a_real_node_is_unknown_not_an_error(self) -> None:
        target = engine.Target("redirector.googlevideo.com", "видео", discover_googlevideo=True)
        run = engine._Run(None, workers=2, deadline=60)
        self.addCleanup(run.close)
        asked: list[str] = []
        with (
            patch.object(engine, "_discover_googlevideo", return_value=((), "видеосервер не проверялся")),
            patch.object(engine, "_probe_host", side_effect=lambda *args, **kwargs: asked.append(args[3])),
        ):
            probe = engine._probe_target(run, target, "youtube", full=False)

        self.assertEqual(asked, [])
        self.assertEqual(probe.reach_state, ReachState.UNKNOWN)
        report = report_text.target_report(probe)
        self.assertIn("видеосервер не проверялся", report["note"])
        # Ни «не удалось узнать адрес», ни пустых строк про DNS: адрес не проверялся вовсе.
        self.assertEqual(report["text"], "не проверялся")
        self.assertEqual((report["dns_reason"], report["dns_detail"]), ("", ""))


class VideoServerFallbackTests(unittest.TestCase):
    def _probe(self, hosts, states):
        target = engine.Target("redirector.googlevideo.com", "видео", discover_googlevideo=True)
        asked: list[str] = []

        def _probe_host(_run, target_, service, host, note, **_kwargs):
            asked.append(host)
            probe = engine._Probe(target=target_, service=service, host=host, discovery_note=note)
            probe.reach_state = states[host]
            return probe

        run = engine._Run(None, workers=2, deadline=60)
        self.addCleanup(run.close)
        with (
            patch.object(engine, "_discover_googlevideo", return_value=(tuple(hosts), "адрес видеосервера получен от YouTube")),
            patch.object(engine, "_probe_host", side_effect=_probe_host),
        ):
            return engine._probe_target(run, target, "youtube", full=True), asked

    def test_spare_video_server_is_checked_when_the_first_is_silent(self) -> None:
        probe, asked = self._probe(("rr1.example", "rr2.example", "rr3.example"), {
            "rr1.example": ReachState.IP_BLOCK, "rr2.example": ReachState.OK, "rr3.example": ReachState.OK,
        })

        self.assertEqual(asked, ["rr1.example", "rr2.example"])
        self.assertEqual(probe.host, "rr2.example")
        self.assertIn("проверен запасной", probe.discovery_note)

    def test_first_server_is_reported_when_all_are_silent(self) -> None:
        probe, asked = self._probe(("rr1.example", "rr2.example"), {
            "rr1.example": ReachState.IP_BLOCK, "rr2.example": ReachState.DPI,
        })

        self.assertEqual(asked, ["rr1.example", "rr2.example"])
        self.assertEqual(probe.host, "rr1.example")

    def test_slow_first_node_is_compared_with_a_spare_one(self) -> None:
        from types import SimpleNamespace

        target = engine.Target("redirector.googlevideo.com", "видео", discover_googlevideo=True)
        levels = {"rr1.example": ("warn", "pause"), "rr2.example": ("ok", "ok")}
        asked: list[str] = []

        def _probe_host(_run, target_, service, host, note, **_kwargs):
            asked.append(host)
            probe = engine._Probe(target=target_, service=service, host=host, discovery_note=note)
            probe.reach_state = ReachState.OK
            probe.reach = _ok("203.0.113.9")
            probe.transfer = SimpleNamespace(level=levels[host][0], code=levels[host][1])
            return probe

        run = engine._Run(None, workers=2, deadline=60)
        self.addCleanup(run.close)
        with (
            patch.object(engine, "_discover_googlevideo", return_value=(("rr1.example", "rr2.example"), "адрес видеосервера получен от YouTube")),
            patch.object(engine, "_probe_host", side_effect=_probe_host),
            patch.object(video_nodes, "owner_note", return_value="узел в сети провайдера, AS20485 (TTK)"),
        ):
            probe = engine._probe_target(run, target, "youtube", full=True, volume=True)

        self.assertEqual((probe.host, asked), ("rr1.example", ["rr1.example", "rr2.example"]))
        self.assertIn("узел в сети провайдера, AS20485", probe.discovery_note)
        self.assertIn("запасной видеосервер rr2.example отвечает быстро — дело в первом узле", probe.discovery_note)

    def test_fast_first_node_needs_no_spare_probe(self) -> None:
        from types import SimpleNamespace

        target = engine.Target("redirector.googlevideo.com", "видео", discover_googlevideo=True)
        asked: list[str] = []

        def _probe_host(_run, target_, service, host, note, **_kwargs):
            asked.append(host)
            probe = engine._Probe(target=target_, service=service, host=host, discovery_note=note)
            probe.reach_state = ReachState.OK
            probe.reach = _ok("203.0.113.9")
            probe.transfer = SimpleNamespace(level="ok", code="ok")
            return probe

        run = engine._Run(None, workers=2, deadline=60)
        self.addCleanup(run.close)
        with (
            patch.object(engine, "_discover_googlevideo", return_value=(("rr1.example", "rr2.example"), "")),
            patch.object(engine, "_probe_host", side_effect=_probe_host),
            patch.object(video_nodes, "owner_note", return_value=""),
        ):
            engine._probe_target(run, target, "youtube", full=True, volume=True)

        self.assertEqual(asked, ["rr1.example"])

    def test_working_first_server_needs_no_spare(self) -> None:
        _probe, asked = self._probe(("rr1.example", "rr2.example"), {"rr1.example": ReachState.OK})

        self.assertEqual(asked, ["rr1.example"])


if __name__ == "__main__":
    unittest.main()


class RecheckLanesTests(unittest.TestCase):
    """Перепроверка: лёгкие запросы идут шире, полные проверки — по-прежнему узко."""

    def test_light_requests_go_wider_than_full_rechecks(self) -> None:
        import threading
        import time

        services = {f"s{index}": engine.Service(f"s{index}", f"Сайт {index}", (engine.Target(f"s{index}.com", "сайт", main=True),)) for index in range(12)}
        done_probes = []
        for key, service in services.items():
            target = service.targets[0]
            probe = engine._Probe(target=target, service=key, host=target.host)
            probe.reach = ProbeResult(ip="203.0.113.9", kind=KIND_CONNECT, connect_fail="timeout")
            probe.reach_state = ReachState.DPI
            done_probes.append((key, target, probe, None))

        lock = threading.Condition()
        now = {"light": 0, "full": 0}
        peak = {"light": 0, "full": 0}
        # Задача держит место, пока очередь не заполнится до своего предела (или секунду):
        # пик тогда задаёт очередь, а не то, как быстро машина успела запустить потоки.
        fill = {"light": engine.RECHECK_LIGHT_AT_ONCE, "full": engine.RECHECK_AT_ONCE}

        def busy(kind: str) -> None:
            with lock:
                now[kind] += 1
                peak[kind] = max(peak[kind], now[kind])
                lock.notify_all()
                lock.wait_for(lambda: peak[kind] >= fill[kind], timeout=1.0)
            time.sleep(0.01)
            with lock:
                now[kind] -= 1

        full_done_hosts = set()

        def light(_run, host, ip, _path, **_kwargs):
            if host in full_done_hosts:
                # Третья попытка после полной проверки идёт из тяжёлой очереди, а не из лёгкой:
                # в пик лёгких запросов она не входит.
                return _ok(ip)
            busy("light")
            # Половина сайтов на лёгкий запрос открывается — их ждёт полная проверка.
            return _ok(ip) if int(host[1:].split(".")[0]) % 2 else _dead(ip)

        def full(_run, target, key, **_kwargs):
            busy("full")
            probe = engine._Probe(target=target, service=key, host=target.host)
            probe.reach = _ok("203.0.113.9")
            probe.reach_state = ReachState.OK
            full_done_hosts.add(target.host)
            return probe

        run = engine._Run(None, workers=64, deadline=60)
        self.addCleanup(run.close)
        with patch.object(net_access, "get", side_effect=light), patch.object(engine, "_probe_target", side_effect=full):
            result = engine._recheck_failed(run, services, done_probes)

        self.assertLessEqual(peak["full"], engine.RECHECK_AT_ONCE)
        self.assertGreater(peak["light"], engine.RECHECK_AT_ONCE)
        self.assertLessEqual(peak["light"], engine.RECHECK_LIGHT_AT_ONCE)
        opened = [probe.rechecked for _key, _target, probe, _error in result]
        self.assertEqual(opened.count("opened"), 6)
        self.assertEqual(opened.count("same"), 6)


class GooglevideoDiscoveryTests(unittest.TestCase):
    def _discover(self, chunks: list[bytes], *, budget: float):
        def fetch(_run, _host, ip, _path, *, body_done, **_kwargs):
            body = b""
            for chunk in chunks:
                body += chunk
                if body_done(body):
                    break
            return _ok(ip)

        run = engine._Run(None, workers=4, deadline=60)
        self.addCleanup(run.close)
        with (
            patch.object(net_access, "browser_ipv4", return_value=("142.250.1.1",)),
            patch.object(net_access, "fetch", side_effect=fetch),
            patch.object(video_nodes, "DISCOVERY_READ_BUDGET_S", budget),
            patch.object(video_nodes, "_mapped_host", return_value=""),
        ):
            return engine._discover_googlevideo(run)

    def test_slow_page_is_not_read_without_end(self) -> None:
        hosts, note = self._discover([b"<html>" + b"x" * 100] * 50, budget=0.0)

        self.assertEqual(hosts, ())
        self.assertIn("слишком медленно", note)

    def test_video_server_found_in_time_is_used(self) -> None:
        page = b'"url":"https://rr3---sn-abc.googlevideo.com/videoplayback?x"'

        hosts, note = self._discover([b"<html>", page], budget=60.0)

        self.assertEqual(hosts, ("rr3---sn-abc.googlevideo.com",))
        self.assertIn("получен от YouTube", note)
