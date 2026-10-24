"""Имена git-сервера проекта: выбор открытого и приведение адресов (zapret_update.mirrors)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zapret_update import mirrors  # noqa: E402


def setUpModule() -> None:
    # Гонка в тестах: имена подключаются почти сразу, срок короткий.
    global _patches
    from unittest.mock import patch

    _patches = [patch.object(mirrors, "RACE_STAGGER_S", 0.01), patch.object(mirrors, "RACE_DEADLINE_S", 1.5)]
    for patcher in _patches:
        patcher.start()


def tearDownModule() -> None:
    for patcher in _patches:
        patcher.stop()


def _document(*names, preferred="git2.zapret.moe"):
    return {
        "schema": 1,
        "families": {
            "git": {
                "preferred": preferred,
                "names": [
                    {"name": name, "blocked": blocked}
                    for name, blocked in names
                ],
            },
            "wiki": {"preferred": "wiki.zapret.moe", "names": [{"name": "wiki.zapret.moe"}]},
        },
    }


class HostAndUrlTests(unittest.TestCase):
    def test_only_project_git_names_are_git_hosts(self) -> None:
        for good in ("git.zapret.moe", "git2.zapret.moe", "GIT12.ZAPRET.MOE"):
            self.assertTrue(mirrors.is_git_host(good), good)
        for bad in ("", None, "evil.example", "git.zapret.moe.evil.example", "xgit2.zapret.moe",
                    "wiki.zapret.moe", "git.example.zapret.moe", "gitx.zapret.moe"):
            self.assertFalse(mirrors.is_git_host(bad), bad)

    def test_canonical_url_maps_any_mirror_back_to_the_main_name(self) -> None:
        path = "/zapretkvn/zapret-kvn/releases/download/v1/a.zip?x=1"
        self.assertEqual(mirrors.canonical_url(f"https://git3.zapret.moe{path}"), f"https://git.zapret.moe{path}")
        self.assertEqual(mirrors.canonical_url(f"https://git.zapret.moe{path}"), f"https://git.zapret.moe{path}")

    def test_foreign_or_unusual_urls_are_never_rewritten(self) -> None:
        for url in (
            "https://evil.example/a",
            "http://git2.zapret.moe/a",
            "https://git2.zapret.moe:8443/a",
            "https://user@git2.zapret.moe/a",
            "https://git2.zapret.moe.evil.example/a",
            "ftp://git2.zapret.moe/a",
            "не адрес",
        ):
            self.assertEqual(mirrors.canonical_url(url), url, url)
            self.assertEqual(mirrors.with_host(url, "git3.zapret.moe"), url, url)

    def test_with_host_accepts_only_git_names(self) -> None:
        url = "https://git.zapret.moe/api/v1/version"
        self.assertEqual(mirrors.with_host(url, "git4.zapret.moe"), "https://git4.zapret.moe/api/v1/version")
        self.assertEqual(mirrors.with_host(url, "evil.example"), url)
        self.assertEqual(mirrors.with_host(url, ""), url)


class ReaderHostTests(unittest.TestCase):
    """Имя для ссылки в посте о выпуске: preferred сервера, а не то, что открылось у сборщика."""

    def test_takes_preferred_from_the_first_answering_name(self) -> None:
        asked = []
        body = json.dumps(_document(("git.zapret.moe", True), ("git3.zapret.moe", False), preferred="git3.zapret.moe"))

        def fetch(url):
            asked.append(url)
            if url.startswith("https://git2."):
                raise OSError("reset")
            return body.encode()

        self.assertEqual(mirrors.reader_host(fetch), "git3.zapret.moe")
        self.assertEqual(asked[:2], ["https://git2.zapret.moe/_sites/mirrors.json", "https://git3.zapret.moe/_sites/mirrors.json"])

    def test_without_any_answer_never_falls_back_to_the_blocked_main_name(self) -> None:
        self.assertEqual(mirrors.reader_host(lambda url: None), "git2.zapret.moe")
        self.assertEqual(mirrors.reader_host(lambda url: b"{", family="wiki"), "wiki2.zapret.moe")


class OrderedHostsTests(unittest.TestCase):
    def test_preferred_first_blocked_last_unknown_and_foreign_dropped(self) -> None:
        document = _document(
            ("git.zapret.moe", True),
            ("git2.zapret.moe", False),
            ("git3.zapret.moe", False),
            ("git9.zapret.moe", None),
            ("git2.zapret.moe", False),
            ("evil.example", False),
            preferred="git3.zapret.moe",
        )
        self.assertEqual(
            mirrors.ordered_hosts(document),
            ["git3.zapret.moe", "git2.zapret.moe", "git9.zapret.moe", "git.zapret.moe"],
        )

    def test_unreadable_documents_give_nothing(self) -> None:
        for broken in (None, [], {}, {"families": {}}, {"families": {"git": {"names": 5}}}, "text"):
            self.assertEqual(mirrors.ordered_hosts(broken), [], repr(broken))


class MirrorListTests(unittest.TestCase):
    def _list(self, answers: dict, clock=None):
        asked: list[str] = []

        def fetch(url: str):
            asked.append(url)
            value = answers.get(url)
            if isinstance(value, Exception):
                raise value
            return value

        kwargs = {} if clock is None else {"clock": clock}
        return mirrors.MirrorList(fetch, **kwargs), asked

    def test_list_comes_from_the_first_name_that_answers(self) -> None:
        body = json.dumps(_document(("git2.zapret.moe", False), ("git.zapret.moe", True))).encode()
        mirror_list, asked = self._list({"https://git3.zapret.moe/_sites/mirrors.json": body})

        self.assertEqual(mirror_list.hosts(), ["git2.zapret.moe", "git.zapret.moe"])
        # Все имена спрашиваются параллельно: первым — лучшее, победителем стало то, что ответило.
        self.assertEqual(asked[0], "https://git2.zapret.moe/_sites/mirrors.json")
        self.assertIn("https://git3.zapret.moe/_sites/mirrors.json", asked)
        self.assertEqual(mirror_list.choose(), "git3.zapret.moe")

    def test_list_is_cached_and_asked_again_after_the_time_is_up(self) -> None:
        now = [0.0]
        body = json.dumps(_document(("git2.zapret.moe", False))).encode()
        mirror_list, asked = self._list({"https://git2.zapret.moe/_sites/mirrors.json": body}, clock=lambda: now[0])

        mirror_list.hosts()
        mirror_list.hosts()
        self.assertEqual(len(asked), 1)

        now[0] = mirrors.LIST_TTL_S + 1
        mirror_list.hosts()
        self.assertEqual(len(asked), 2)

    def test_when_nothing_answers_the_bootstrap_list_is_used_for_a_short_time(self) -> None:
        now = [0.0]
        mirror_list, asked = self._list({}, clock=lambda: now[0])

        self.assertEqual(mirror_list.hosts(), list(mirrors.BOOTSTRAP_HOSTS))
        self.assertEqual(mirrors.BOOTSTRAP_HOSTS[-1], mirrors.CANONICAL_HOST)
        first = len(asked)
        mirror_list.hosts()
        self.assertEqual(len(asked), first)  # повторно сразу не спрашивает

        now[0] = mirrors.FALLBACK_TTL_S + 1
        mirror_list.hosts()
        self.assertGreater(len(asked), first)

    def test_broken_answers_and_errors_are_skipped(self) -> None:
        good = json.dumps(_document(("git4.zapret.moe", False))).encode()
        mirror_list, _asked = self._list(
            {
                "https://git2.zapret.moe/_sites/mirrors.json": OSError("нет связи"),
                "https://git3.zapret.moe/_sites/mirrors.json": "не JSON".encode(),
                "https://git4.zapret.moe/_sites/mirrors.json": good,
            }
        )

        self.assertEqual(mirror_list.hosts(), ["git4.zapret.moe"])

    def test_remembered_name_goes_first_and_foreign_names_are_ignored(self) -> None:
        body = json.dumps(_document(("git2.zapret.moe", False), ("git3.zapret.moe", False))).encode()
        mirror_list, _asked = self._list({"https://git2.zapret.moe/_sites/mirrors.json": body})

        mirror_list.remember("git3.zapret.moe")
        self.assertEqual(mirror_list.hosts(), ["git3.zapret.moe", "git2.zapret.moe"])

        mirror_list.remember("evil.example")
        self.assertEqual(mirror_list.hosts()[0], "git3.zapret.moe")

        mirror_list.forget()
        self.assertEqual(mirror_list.hosts()[0], "git2.zapret.moe")


class MirrorReportTests(unittest.TestCase):
    """Почему зеркала закрыты на этом компьютере, видно в журнале программы."""

    def test_report_names_the_source_of_the_list_and_every_failed_name(self) -> None:
        said: list[str] = []
        good = json.dumps(_document(("git4.zapret.moe", False))).encode()

        def fetch(url: str):
            if "git2." in url:
                raise OSError("timed out")
            if "git3." in url:
                return None
            return good if "git4." in url else None

        mirror_list = mirrors.MirrorList(fetch, report=said.append)
        mirror_list.hosts()

        self.assertEqual(len(said), 1)
        self.assertIn("git4.zapret.moe ответило", said[0])
        self.assertIn("git2.zapret.moe: OSError", said[0])
        self.assertIn("git3.zapret.moe: ValueError", said[0])

    def test_report_lists_all_causes_when_nothing_answers(self) -> None:
        said: list[str] = []

        def fetch(url: str):
            raise ConnectionResetError("reset by peer")

        mirrors.MirrorList(fetch, report=said.append).hosts()

        self.assertEqual(len(said), 1)
        self.assertIn("запасной", said[0])
        for host in mirrors.BOOTSTRAP_HOSTS:
            self.assertIn(host, said[0])
        self.assertIn("ConnectionResetError", said[0])

    def test_failures_of_a_name_are_reported_and_a_broken_report_does_not_break_the_list(self) -> None:
        said: list[str] = []
        mirror_list = mirrors.MirrorList(lambda _url: None, report=said.append)
        mirror_list.note_failure("git2.zapret.moe", TimeoutError("долго"))
        self.assertIn("git2.zapret.moe: TimeoutError", said[0])

        def broken(_message: str) -> None:
            raise RuntimeError("журнал сломан")

        self.assertEqual(mirrors.MirrorList(lambda _url: None, report=broken).hosts(), list(mirrors.BOOTSTRAP_HOSTS))


if __name__ == "__main__":
    unittest.main()


def _wiki_document():
    document = _document(("git2.zapret.moe", False))
    document["families"]["wiki"] = {
        "preferred": "wiki2.zapret.moe",
        "names": [
            {"name": "wiki.zapret.moe", "blocked": True},
            {"name": "wiki2.zapret.moe", "blocked": False},
            {"name": "wiki3.zapret.moe", "blocked": False},
        ],
    }
    return document


class WikiFamilyTests(unittest.TestCase):
    """Вики устроена как git: основное имя режут, зеркала открываются; семейства не смешиваются."""

    def test_family_of_names(self) -> None:
        self.assertEqual(mirrors.family_of("wiki2.zapret.moe"), "wiki")
        self.assertEqual(mirrors.family_of("GIT.zapret.moe"), "git")
        self.assertEqual(mirrors.family_of("evil.example"), "")
        self.assertEqual(mirrors.family_of("wiki.zapret.moe.evil.example"), "")
        self.assertEqual(mirrors.family_of(None), "")
        self.assertTrue(mirrors.is_site_host("wiki3.zapret.moe"))
        self.assertFalse(mirrors.is_git_host("wiki3.zapret.moe"))

    def test_with_host_changes_only_inside_one_family(self) -> None:
        wiki = "https://wiki.zapret.moe/Zapret/home"
        self.assertEqual(mirrors.with_host(wiki, "wiki2.zapret.moe"), "https://wiki2.zapret.moe/Zapret/home")
        self.assertEqual(mirrors.with_host(wiki, "git2.zapret.moe"), wiki)
        git = "https://git.zapret.moe/zapretdiscordyoutube/zapretgui"
        self.assertEqual(mirrors.with_host(git, "wiki2.zapret.moe"), git)

    def test_canonical_url_returns_each_family_to_its_own_main_name(self) -> None:
        self.assertEqual(mirrors.canonical_url("https://wiki3.zapret.moe/a"), "https://wiki.zapret.moe/a")
        self.assertEqual(mirrors.canonical_url("https://git3.zapret.moe/a"), "https://git.zapret.moe/a")
        self.assertEqual(mirrors.canonical_url("https://example.com/a"), "https://example.com/a")

    def test_ordered_hosts_per_family(self) -> None:
        document = _wiki_document()
        self.assertEqual(mirrors.ordered_hosts(document, "wiki"), ["wiki2.zapret.moe", "wiki3.zapret.moe", "wiki.zapret.moe"])
        self.assertEqual(mirrors.ordered_hosts(document), ["git2.zapret.moe"])

    def test_wiki_list_falls_back_to_wiki_bootstrap_and_can_be_downloaded_from_git_names(self) -> None:
        asked: list[str] = []

        def fetch(url: str):
            asked.append(url)
            return None

        offline = mirrors.MirrorList(fetch, family="wiki")
        self.assertEqual(offline.hosts(), list(mirrors.WIKI_BOOTSTRAP_HOSTS))
        self.assertEqual(mirrors.WIKI_BOOTSTRAP_HOSTS[-1], "wiki.zapret.moe")

        body = json.dumps(_wiki_document()).encode()
        mirror_list = mirrors.MirrorList(
            lambda url: body if "git2." in url else (_ for _ in ()).throw(OSError("закрыто")), family="wiki"
        )
        self.assertEqual(mirror_list.hosts()[0], "wiki2.zapret.moe")

    def test_remember_ignores_another_family(self) -> None:
        mirror_list = mirrors.MirrorList(lambda url: json.dumps(_wiki_document()).encode(), family="wiki")
        mirror_list.remember("git3.zapret.moe")
        self.assertEqual(mirror_list.hosts()[0], "wiki2.zapret.moe")


class ChooseTests(unittest.TestCase):
    """choose(): первое ответившее имя запоминается, дальше выбор не повторяется."""

    def _list(self, open_hosts: set[str], family: str = "git"):
        asked: list[str] = []
        body = json.dumps(_wiki_document() if family == "wiki" else _document(
            ("git.zapret.moe", True), ("git2.zapret.moe", False), ("git3.zapret.moe", False)
        )).encode()

        def fetch(url: str):
            asked.append(url)
            host = url.split("/")[2]
            if host in open_hosts:
                return body
            raise OSError("TLS закрыт")

        return mirrors.MirrorList(fetch, family=family), asked

    def test_first_answering_name_is_chosen_and_kept(self) -> None:
        mirror_list, asked = self._list({"git3.zapret.moe"})
        self.assertEqual(mirror_list.choose(), "git3.zapret.moe")
        after_first = len(asked)
        self.assertEqual(mirror_list.choose(), "git3.zapret.moe")
        self.assertEqual(len(asked), after_first)

    def test_blocked_main_name_is_not_chosen_when_a_mirror_answers(self) -> None:
        mirror_list, _asked = self._list({"git.zapret.moe", "git3.zapret.moe"})
        self.assertEqual(mirror_list.choose(), "git3.zapret.moe")

    def test_nothing_answers_gives_best_guess_and_does_not_race_again_for_a_while(self) -> None:
        now = [0.0]
        asked: list[str] = []

        def fetch(url: str):
            asked.append(url)
            raise OSError("TLS закрыт")

        mirror_list = mirrors.MirrorList(fetch, clock=lambda: now[0])
        self.assertEqual(mirror_list.choose(), "git2.zapret.moe")
        before = len(asked)
        self.assertGreater(before, 0)

        # Кнопка не должна ждать при каждом нажатии: неудача помнится на FALLBACK_TTL_S.
        self.assertEqual(mirror_list.choose(), "git2.zapret.moe")
        self.assertEqual(len(asked), before)

        now[0] = mirrors.FALLBACK_TTL_S + 1
        mirror_list.choose()
        self.assertGreater(len(asked), before)

    def test_wiki_family(self) -> None:
        mirror_list, _asked = self._list({"wiki3.zapret.moe"}, family="wiki")
        self.assertEqual(mirror_list.choose(), "wiki3.zapret.moe")

    def test_forget_makes_the_next_choice_start_over(self) -> None:
        mirror_list, _asked = self._list({"git2.zapret.moe", "git3.zapret.moe"})
        self.assertEqual(mirror_list.choose(), "git2.zapret.moe")
        mirror_list.forget()
        self.assertEqual(mirror_list.choose(), "git2.zapret.moe")


class RaceSpeedTests(unittest.TestCase):
    """Выбор имени — гонка с жёстким сроком: молчащие имена не задерживают кнопку."""

    def _body(self) -> bytes:
        return json.dumps(
            _document(("git.zapret.moe", True), ("git2.zapret.moe", False), ("git3.zapret.moe", False))
        ).encode()

    def test_a_silent_first_name_does_not_hold_up_the_one_that_answers(self) -> None:
        import time

        body = self._body()

        def fetch(url: str):
            host = url.split("/")[2]
            if host == "git2.zapret.moe":
                time.sleep(30)  # молчит: такое имя при переборе стоило бы всего срока
            return body if host == "git3.zapret.moe" else (_ for _ in ()).throw(OSError("закрыто"))

        mirror_list = mirrors.MirrorList(fetch)
        started = time.monotonic()
        self.assertEqual(mirror_list.choose(), "git3.zapret.moe")
        self.assertLess(time.monotonic() - started, 1.0)

    def test_total_silence_is_bounded_by_the_deadline(self) -> None:
        import time
        from unittest.mock import patch

        def fetch(url: str):
            time.sleep(30)

        mirror_list = mirrors.MirrorList(fetch)
        with patch.object(mirrors, "RACE_DEADLINE_S", 0.4):
            started = time.monotonic()
            guess = mirror_list.choose()
            elapsed = time.monotonic() - started
        self.assertEqual(guess, "git2.zapret.moe")
        self.assertLess(elapsed, 1.5)
        # и повторное нажатие уже не ждёт
        started = time.monotonic()
        mirror_list.choose()
        self.assertLess(time.monotonic() - started, 0.1)

    def test_concurrent_callers_share_one_race(self) -> None:
        import threading
        import time

        body = self._body()
        calls: list[str] = []

        def fetch(url: str):
            calls.append(url)
            time.sleep(0.2)
            return body

        mirror_list = mirrors.MirrorList(fetch)
        results: list[str] = []
        threads = [threading.Thread(target=lambda: results.append(mirror_list.choose())) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)

        self.assertEqual(len(results), 6)
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(calls.count("https://git2.zapret.moe/_sites/mirrors.json"), 1)

    def test_good_name_expires_and_the_choice_is_made_again(self) -> None:
        now = [0.0]
        body = self._body()
        asked: list[str] = []

        def fetch(url: str):
            asked.append(url)
            return body

        mirror_list = mirrors.MirrorList(fetch, clock=lambda: now[0])
        mirror_list.choose()
        count = len(asked)
        mirror_list.choose()
        self.assertEqual(len(asked), count)
        now[0] = mirrors.GOOD_TTL_S + 1
        mirror_list.choose()
        self.assertGreater(len(asked), count)

    def test_a_name_from_a_real_exchange_beats_the_race_winner(self) -> None:
        mirror_list = mirrors.MirrorList(lambda url: self._body())
        mirror_list.remember("git3.zapret.moe")
        mirror_list.hosts()
        self.assertEqual(mirror_list.choose(), "git3.zapret.moe")

    def test_garbage_answers_are_not_a_working_name(self) -> None:
        def fetch(url: str):
            host = url.split("/")[2]
            return b"<html>captive portal</html>" if host == "git2.zapret.moe" else self._body()

        mirror_list = mirrors.MirrorList(fetch)
        self.assertEqual(mirror_list.choose(), "git3.zapret.moe")


class BestNameTests(unittest.TestCase):
    """best(): имя для браузера — мгновенно, без сети и без ожидания гонки."""

    def _body(self) -> bytes:
        return json.dumps(
            _document(("git.zapret.moe", True), ("git2.zapret.moe", False), ("git3.zapret.moe", False))
        ).encode()

    def test_without_any_data_it_is_the_first_mirror_and_asks_nothing(self) -> None:
        asked: list[str] = []
        mirror_list = mirrors.MirrorList(lambda url: asked.append(url))
        self.assertEqual(mirror_list.best(), "git2.zapret.moe")
        self.assertEqual(asked, [])
        self.assertEqual(mirrors.MirrorList(lambda url: None, family="wiki").best(), "wiki2.zapret.moe")

    def test_proven_name_wins_then_server_order(self) -> None:
        now = [0.0]
        body = json.dumps(
            _document(("git2.zapret.moe", False), ("git3.zapret.moe", False), preferred="git3.zapret.moe")
        ).encode()
        # Из программы отвечает только git2 (победитель гонки), а сервер считает лучшим git3.
        mirror_list = mirrors.MirrorList(lambda url: body if "git2." in url else None, clock=lambda: now[0])
        mirror_list.hosts()
        self.assertEqual(mirror_list.best(), "git2.zapret.moe")
        # Когда доказательство устарело, остаётся порядок самого сервера.
        now[0] = mirrors.GOOD_TTL_S + 1
        self.assertEqual(mirror_list.best(), "git3.zapret.moe")

    def test_blocked_main_name_is_last_in_the_server_order(self) -> None:
        mirror_list = mirrors.MirrorList(lambda url: self._body() if "git3." in url else None)
        mirror_list.hosts()
        mirror_list.forget()
        self.assertNotEqual(mirror_list.best(), "git.zapret.moe")

    def test_it_does_not_wait_for_a_race_that_holds_the_lock(self) -> None:
        import threading
        import time

        release = threading.Event()
        mirror_list = mirrors.MirrorList(lambda url: release.wait(10) and None)
        threading.Thread(target=mirror_list.choose, daemon=True).start()
        time.sleep(0.2)
        started = time.monotonic()
        name = mirror_list.best()
        elapsed = time.monotonic() - started
        release.set()
        self.assertEqual(name, "git2.zapret.moe")
        self.assertLess(elapsed, 0.2)
