"""Отметка «в реестре РКН»: локальный список, обновление раз в сутки, поиск без сети."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from blockcheck.ui.result_cards_model import build_cards
from diagnostics import net_access, registry

HOSTS = "\n".join(['"quoted.example', "facebook.com", "www.linkedin.com", "discordapp.com", "FACEBOOK.com", "localhost"])
IPS = "68.171.224.0/19\n203.0.113.7\n# заметка\nне адрес\n2001:db8::/32\n"
DAY = registry.MAX_AGE_S


def _big(extra: str = "") -> bytes:
    """Список правдоподобного размера: маленький файл считается ошибкой скачивания."""
    filler = "\n".join(f"site{number}.example" for number in range(registry._MIN_HOSTS))
    return f"{HOSTS}\n{extra}\n{filler}\n".encode()


class _Server:
    def __init__(self, hosts: bytes | None = None, ips: bytes | None = IPS.encode(), etag: str = "v1") -> None:
        self.hosts, self.ips, self.etag = hosts if hosts is not None else _big(), ips, etag
        self.asked: list[tuple[str, str]] = []

    def __call__(self, url: str, etag: str):
        self.asked.append((url.rsplit("/", 1)[-1], etag))
        body = self.hosts if url == registry.HOSTS_URL else self.ips
        if body is None:
            return None
        if etag and etag == self.etag:
            return registry.Fetched(unchanged=True, etag=etag)
        return registry.Fetched(body=body, etag=self.etag)


class IndexTests(unittest.TestCase):
    def setUp(self) -> None:
        hosts, count = registry.build_hosts(HOSTS.splitlines())
        self.count = count
        self.index = registry.Index(hosts=hosts, networks=tuple(registry.build_networks(IPS.splitlines())), updated=1000.0)

    def test_names_are_cleaned_and_counted_once(self) -> None:
        # Кавычка в начале строки убрана, регистр не важен, «localhost» без точки — не сайт.
        self.assertEqual(self.count, 4)
        self.assertEqual(self.index.find_host("quoted.example"), "quoted.example")

    def test_site_is_found_by_itself_or_by_a_domain_above(self) -> None:
        self.assertEqual(self.index.find_host("facebook.com"), "facebook.com")
        self.assertEqual(self.index.find_host("WWW.Facebook.com."), "facebook.com")
        self.assertEqual(self.index.find_host("cdn.discordapp.com"), "discordapp.com")
        self.assertEqual(self.index.find_host("www.linkedin.com"), "www.linkedin.com")

    def test_listed_subdomain_does_not_mark_the_whole_domain(self) -> None:
        self.assertEqual(self.index.find_host("linkedin.com"), "")
        self.assertEqual(self.index.find_host("x.com"), "")
        # Сама зона («com») в реестре не ищется.
        self.assertEqual(registry.Index(hosts=registry.build_hosts(["a.com"])[0]).find_host("b.com"), "")

    def test_address_is_found_inside_a_blocked_network(self) -> None:
        self.assertEqual(self.index.find_ip("68.171.230.1"), "68.171.224.0/19")
        self.assertEqual(self.index.find_ip("203.0.113.7"), "203.0.113.7")
        self.assertEqual(self.index.find_ip("203.0.113.8"), "")
        self.assertEqual(self.index.find_ip("8.8.8.8"), "")
        self.assertEqual(self.index.find_ip("2001:db8::1"), "")
        self.assertEqual(self.index.find_ip("не адрес"), "")

    def test_empty_list_finds_nothing_and_says_it_is_missing(self) -> None:
        empty = registry.Index()

        self.assertEqual((empty.find_host("facebook.com"), empty.find_ip("68.171.230.1")), ("", ""))
        self.assertEqual(empty.state(), registry.STATE_MISSING)
        self.assertEqual(self.index.state(now=1000.0 + DAY), registry.STATE_FRESH)
        self.assertEqual(self.index.state(now=1000.0 + 8 * DAY), registry.STATE_STALE)


class RefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory())) / "registry"

    def test_first_run_downloads_and_the_list_survives_on_disk(self) -> None:
        index = registry.refresh(self.folder, _Server(), now=1000.0)

        self.assertEqual(index.find_host("www.facebook.com"), "facebook.com")
        self.assertEqual(index.find_ip("68.171.230.1"), "68.171.224.0/19")
        self.assertEqual((index.updated, index.state(now=1000.0)), (1000.0, registry.STATE_FRESH))
        self.assertEqual(registry.load(self.folder).count, index.count)

    def test_nothing_is_downloaded_twice_within_a_day(self) -> None:
        server = _Server()
        registry.refresh(self.folder, server, now=1000.0)
        server.asked.clear()

        registry.refresh(self.folder, server, now=1000.0 + DAY - 1)

        self.assertEqual(server.asked, [])

    def test_next_day_asks_with_the_version_mark_and_keeps_an_unchanged_list(self) -> None:
        server = _Server()
        registry.refresh(self.folder, server, now=1000.0)
        server.asked.clear()

        index = registry.refresh(self.folder, server, now=1000.0 + DAY + 1)

        self.assertEqual(server.asked, [("reestr_hostname.txt", "v1"), ("reestr_ipban4.txt", "v1")])
        # Список тот же: дата скачивания прежняя, сверка — новая.
        self.assertEqual((index.updated, index.checked), (1000.0, 1000.0 + DAY + 1))

    def test_changed_list_replaces_the_old_one(self) -> None:
        registry.refresh(self.folder, _Server(), now=1000.0)

        index = registry.refresh(self.folder, _Server(hosts=_big("newsite.example"), etag="v2"), now=1000.0 + DAY + 1)

        self.assertEqual(index.find_host("newsite.example"), "newsite.example")
        self.assertEqual(index.updated, 1000.0 + DAY + 1)

    def test_failed_download_keeps_the_previous_list(self) -> None:
        registry.refresh(self.folder, _Server(), now=1000.0)
        broken = _Server()
        broken.hosts = None

        index = registry.refresh(self.folder, broken, now=1000.0 + 3 * DAY)

        self.assertEqual(index.find_host("facebook.com"), "facebook.com")

    def test_error_page_instead_of_the_list_is_not_taken_for_the_list(self) -> None:
        registry.refresh(self.folder, _Server(), now=1000.0)

        index = registry.refresh(self.folder, _Server(hosts=b"<html>500</html>\nfoo.example\n", etag="v2"), now=1000.0 + 3 * DAY)

        self.assertEqual(index.find_host("facebook.com"), "facebook.com")
        self.assertEqual(registry.refresh(self.folder / "other", _Server(hosts=b"oops"), now=1.0).state(), registry.STATE_MISSING)

    def test_damaged_files_give_an_empty_list_not_a_crash(self) -> None:
        registry.refresh(self.folder, _Server(), now=1000.0)
        (self.folder / "hosts.idx").write_bytes(b"abc")

        self.assertEqual(registry.load(self.folder).count, 0)
        self.assertEqual(registry.load(self.folder / "nowhere").count, 0)


class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = registry.Index(
            hosts=registry.build_hosts(HOSTS.splitlines())[0],
            networks=tuple(registry.build_networks(IPS.splitlines())),
            updated=1000.0,
        )
        self.services = [
            {
                "key": "fb",
                "label": "Facebook",
                "level": "fail",
                "targets": [{"host": "www.facebook.com", "address": "68.171.230.1", "ok": False, "main": True}],
            },
            {"key": "x", "label": "X", "level": "fail", "targets": [{"host": "x.com", "address": "1.2.3.4", "ok": False, "main": True}]},
            {"key": "vk", "label": "VK", "level": "ok", "targets": [{"host": "vk.com", "address": "5.6.7.8", "ok": True, "main": True}]},
        ]

    def test_report_gets_a_mark_for_every_site(self) -> None:
        registry.annotate(self.services, self.index)

        marks = [service["targets"][0]["registry"] for service in self.services]
        self.assertEqual(marks[0], {"listed": True, "name": "facebook.com", "network": "68.171.224.0/19"})
        self.assertEqual(marks[1], {"listed": False, "name": "", "network": ""})

    def test_without_a_list_nothing_is_claimed(self) -> None:
        registry.annotate(self.services, registry.Index())

        self.assertNotIn("registry", self.services[0]["targets"][0])
        self.assertIn("скачать не удалось", "\n".join(registry.lines(self.services, registry.Index())))
        cards = {card.title: card for card in build_cards({"services": self.services})}
        self.assertNotIn(("в реестре РКН", "info"), cards["Facebook"].chips)

    def test_text_report_names_listed_sites_and_warns_about_blocks_outside_the_registry(self) -> None:
        registry.annotate(self.services, self.index)
        text = "\n".join(registry.lines(self.services, self.index, now=1000.0))

        self.assertIn("📕 www.facebook.com: в реестре есть facebook.com; адрес 68.171.230.1 в списке заблокированных", text)
        self.assertNotIn("x.com:", text)
        self.assertIn("и без записи в реестре", text)
        self.assertIn("давно не обновлялся", "\n".join(registry.lines(self.services, self.index, now=1000.0 + 9 * DAY)))

    def test_cards_show_the_mark_and_do_not_call_an_unlisted_site_unblocked(self) -> None:
        registry.annotate(self.services, self.index)
        cards = {card.title: card for card in build_cards({"services": self.services})}

        def row(title: str) -> str:
            return next(line.text for line in cards[title].sections[0].lines if line.name == "Реестр РКН")

        self.assertIn(("в реестре РКН", "info"), cards["Facebook"].chips)
        self.assertNotIn(("в реестре РКН", "info"), cards["X"].chips)
        self.assertIn("в реестре значится facebook.com", row("Facebook"))
        self.assertEqual(row("X"), "не значится — блокировать могут и без записи в реестре")
        self.assertEqual(row("VK"), "не значится")


class DownloadTests(unittest.TestCase):
    def test_unreachable_server_is_none_not_an_error(self) -> None:
        self.assertIsNone(net_access.download_file("http://127.0.0.1:9/nothing", timeout=0.5))


if __name__ == "__main__":
    unittest.main()
