"""Одно место выбора открытого имени сайтов проекта (utils.site_links) и то, что кнопки идут через него."""

from __future__ import annotations

import json
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils import site_links  # noqa: E402


def _lists(names: dict[str, str | None]):
    """Подмена общих списков: у каждого семейства ``link()`` отдаёт заданное имя (``None`` — имени нет)."""
    return patch.object(
        site_links, "mirror_list", side_effect=lambda family="git": SimpleNamespace(link=lambda: names[family])
    )


class ResolveTests(unittest.TestCase):
    def test_project_links_get_the_chosen_name_of_their_own_family(self) -> None:
        names = {"git": "git2.zapret.moe", "wiki": "wiki3.zapret.moe"}
        with _lists(names):
            self.assertEqual(
                site_links.resolve("https://git.zapret.moe/zapretdiscordyoutube/zapretgui/issues"),
                "https://git2.zapret.moe/zapretdiscordyoutube/zapretgui/issues",
            )
            self.assertEqual(
                site_links.resolve("https://wiki.zapret.moe/Zapret/home#x"),
                "https://wiki3.zapret.moe/Zapret/home#x",
            )

    def test_other_addresses_are_untouched_and_ask_nothing(self) -> None:
        with patch.object(site_links, "mirror_list", side_effect=AssertionError("имя не нужно")), patch.object(
            site_links, "chosen_host", side_effect=AssertionError("сеть не нужна")
        ):
            for url in (
                "https://t.me/zaprethelp",
                "https://discord.gg/kkcBDG2uws",
                "https://git.zapret.moe.evil.example/x",
                "http://git.zapret.moe/x",
                "https://git.zapret.moe:8443/x",
                "not a url",
                "",
            ):
                self.assertEqual(site_links.resolve(url), url, url)

    def test_one_shared_list_per_family(self) -> None:
        self.assertIs(site_links.mirror_list("git"), site_links.mirror_list("git"))
        self.assertIsNot(site_links.mirror_list("git"), site_links.mirror_list("wiki"))

    def test_updater_uses_the_same_git_list_as_the_buttons(self) -> None:
        from updater.release import forgejo

        self.assertIs(forgejo._MIRRORS, site_links.mirror_list("git"))


class OpenInBrowserTests(unittest.TestCase):
    def test_opens_the_best_name_at_once_without_any_network(self) -> None:
        opened: list[str] = []
        with _lists({"git": "git2.zapret.moe", "wiki": "wiki2.zapret.moe"}), patch.object(
            site_links, "chosen_host", side_effect=AssertionError("кнопка не должна ждать пробу")
        ), patch.object(site_links.webbrowser, "open", side_effect=opened.append):
            site_links.open_in_browser("https://wiki.zapret.moe/Zapret/home")
        self.assertEqual(opened, ["https://wiki2.zapret.moe/Zapret/home"])

    def test_a_race_in_progress_does_not_hold_up_a_click(self) -> None:
        """Гонка в фоне держит блокировку списка; нажатие её не ждёт."""
        import time
        from zapret_update import mirrors

        release = threading.Event()

        def silent(url: str):
            release.wait(10)
            return None

        names = mirrors.MirrorList(silent, family="wiki")
        threading.Thread(target=names.choose, daemon=True).start()
        time.sleep(0.2)
        try:
            with patch.object(site_links, "mirror_list", return_value=names):
                started = time.monotonic()
                url = site_links.resolve("https://wiki.zapret.moe/Zapret/home")
                elapsed = time.monotonic() - started
        finally:
            release.set()
        self.assertLess(elapsed, 0.3)
        # Гонка ещё не дала ни имени, ни списка: ссылка ведёт на входную страницу, а не на имя из прошивки.
        self.assertEqual(url, mirrors.entry_page_url("Zapret/home", "wiki"))

    def test_a_failed_open_never_raises_into_the_window(self) -> None:
        with patch.object(site_links, "resolve", side_effect=RuntimeError("сбой")), patch.object(
            site_links, "log"
        ) as log:
            site_links.open_in_browser("https://git.zapret.moe/")
        self.assertTrue(log.called)


def _document(family: str, names: list[tuple[str, bool]]) -> bytes:
    """Список сервера схемы 1: (имя, закрыто ли)."""
    return json.dumps(
        {"schema": 1, "families": {family: {"names": [{"name": name, "blocked": blocked} for name, blocked in names]}}}
    ).encode()


class EntryPageFallbackTests(unittest.TestCase):
    """Нет открывающегося имени — ссылка идёт на входную страницу, а не на имя из прошивки (git2, wiki2)."""

    def _resolve(self, names, url="https://wiki.zapret.moe/Zapret/home"):
        with patch.object(site_links, "mirror_list", return_value=names):
            return site_links.resolve(url)

    def _loaded(self, family, listed):
        from zapret_update import mirrors

        body = _document(family, listed)
        names = mirrors.MirrorList(lambda url: body, family=family)
        names.choose()
        return names

    def test_no_list_yet_goes_to_the_entry_page(self) -> None:
        from zapret_update import mirrors

        names = mirrors.MirrorList(lambda url: None, family="wiki")
        self.assertIsNone(names.link())
        url = self._resolve(names)
        self.assertEqual(url, mirrors.entry_page_url("Zapret/home", "wiki"))
        self.assertTrue(url.startswith(mirrors.ENTRY_PAGE + "?s=wiki&p="))
        self.assertNotIn("wiki2.zapret.moe", url)

    def test_all_names_flagged_blocked_goes_to_the_entry_page(self) -> None:
        from zapret_update import mirrors

        names = self._loaded("wiki", [("wiki.zapret.moe", True), ("wiki2.zapret.moe", True)])
        names._good = None
        self.assertIsNone(names.link())
        self.assertTrue(self._resolve(names).startswith(mirrors.ENTRY_PAGE))

    def test_first_open_name_of_the_server_list_is_handed_out(self) -> None:
        names = self._loaded("wiki", [("wiki.zapret.moe", True), ("wiki2.zapret.moe", True), ("wiki3.zapret.moe", False)])
        names._good = None
        self.assertEqual(names.link(), "wiki3.zapret.moe")
        self.assertEqual(self._resolve(names), "https://wiki3.zapret.moe/Zapret/home")

    def test_a_name_that_answered_from_this_computer_wins(self) -> None:
        from zapret_update import mirrors

        names = mirrors.MirrorList(lambda url: None, family="wiki")
        names.remember("wiki4.zapret.moe")
        self.assertTrue(names.proven())
        self.assertEqual(self._resolve(names), "https://wiki4.zapret.moe/Zapret/home")

    def test_entry_page_carries_family_path_and_query(self) -> None:
        from zapret_update import mirrors

        self.assertEqual(
            mirrors.entry_page_for("https://git.zapret.moe/zapretkvn/zapret-kvn/issues/new?x=1"),
            mirrors.ENTRY_PAGE + "?s=git&p=zapretkvn/zapret-kvn/issues/new%3Fx%3D1",
        )
        self.assertEqual(mirrors.entry_page_for("https://t.me/x"), "https://t.me/x")


class ProbedNameTests(unittest.TestCase):
    """Запросы самой программы идут под именем, до которого дошла гонка."""

    def test_resolve_probed_uses_the_race_winner(self) -> None:
        with patch.object(site_links, "chosen_host", return_value="git3.zapret.moe"):
            self.assertEqual(
                site_links.resolve_probed("https://git.zapret.moe/api/zapret/wait"),
                "https://git3.zapret.moe/api/zapret/wait",
            )
        with patch.object(site_links, "chosen_host", side_effect=AssertionError("сеть не нужна")):
            self.assertEqual(site_links.resolve_probed("https://t.me/x"), "https://t.me/x")


class ButtonsGoThroughTheModuleTests(unittest.TestCase):
    """«Справка», «Исходный код», «Документация», форма заявки: адрес уходит в браузер под открытым именем."""

    def _opened_through(self, call):
        with _lists({"git": "git2.zapret.moe", "wiki": "wiki2.zapret.moe"}), patch("webbrowser.open") as browser:
            call()
        return browser.call_args.args[0]

    def test_about_buttons(self) -> None:
        import about.commands as commands

        self.assertTrue(
            self._opened_through(commands.open_docs_home).startswith("https://wiki2.zapret.moe/"),
        )
        self.assertTrue(
            self._opened_through(commands.open_support_discussions).startswith("https://git2.zapret.moe/"),
        )
        self.assertTrue(
            self._opened_through(lambda: commands.open_github("https://git.zapret.moe/zapretkvn/zapret-kvn")).startswith(
                "https://git2.zapret.moe/zapretkvn/"
            )
        )
        self.assertEqual(
            self._opened_through(lambda: commands.open_link("https://mastodon.social/@zapret")),
            "https://mastodon.social/@zapret",
        )

    def test_external_open_url_command_used_by_workers(self) -> None:
        from config.urls import GIT_SITE_URL
        from app import external_commands

        self.assertEqual(
            self._opened_through(lambda: external_commands.open_url(GIT_SITE_URL)),
            "https://git2.zapret.moe/",
        )

    def test_all_project_links_are_written_once_in_config_urls(self) -> None:
        from config import urls

        self.assertTrue(urls.SUPPORT_ISSUES_URL.startswith(urls.GIT_REPO_URL))
        self.assertTrue(urls.BLOCKCHECK_ISSUES_URL.startswith(urls.GIT_REPO_URL))
        self.assertTrue(urls.PROFILE_REQUEST_FORM_URL.startswith(urls.GIT_REPO_URL))
        self.assertEqual(urls.GIT_REPO_URL, "https://git.zapret.moe/zapretdiscordyoutube/zapretgui")
        self.assertEqual(urls.KVN_REPO_URL, "https://git.zapret.moe/zapretkvn/zapret-kvn")

    def test_file_store_source_follows_the_chosen_name(self) -> None:
        from updater.install import file_update

        with patch.object(site_links, "chosen_host", return_value="git3.zapret.moe"):
            source = file_update.store_sources()[0]
            url = source.url_for("a" * 64)
        self.assertTrue(url.startswith("https://git3.zapret.moe/api/packages/"), url)


class WarmUpTests(unittest.TestCase):
    """Имена выбираются заранее, в фоне: к нажатию кнопки выбор уже готов."""

    def test_warm_up_chooses_both_families_without_blocking(self) -> None:
        release = threading.Event()
        asked: list[str] = []
        both = threading.Event()

        def slow_choice(family="git"):
            asked.append(family)
            if len(asked) >= 2:
                both.set()
            release.wait(5)
            return f"{family}2.zapret.moe"

        import time

        with patch.object(site_links, "chosen_host", side_effect=slow_choice):
            started = time.monotonic()
            site_links.warm_up()
            self.assertLess(time.monotonic() - started, 0.5, "запуск ждал выбор имён")
            self.assertTrue(both.wait(5))
            release.set()
        self.assertEqual(sorted(asked), ["git", "wiki"])

    def test_startup_runs_the_warm_up(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src" / "main" / "post_startup_update.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("site_links.warm_up()", source)


if __name__ == "__main__":
    unittest.main()
