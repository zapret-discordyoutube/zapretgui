"""Одно место выбора открытого имени сайтов проекта (utils.site_links) и то, что кнопки идут через него."""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils import site_links  # noqa: E402


class ResolveTests(unittest.TestCase):
    def test_project_links_get_the_chosen_name_of_their_own_family(self) -> None:
        names = {"git": "git2.zapret.moe", "wiki": "wiki3.zapret.moe"}
        with patch.object(site_links, "chosen_host", side_effect=lambda family="git": names[family]):
            self.assertEqual(
                site_links.resolve("https://git.zapret.moe/zapretdiscordyoutube/zapretgui/issues"),
                "https://git2.zapret.moe/zapretdiscordyoutube/zapretgui/issues",
            )
            self.assertEqual(
                site_links.resolve("https://wiki.zapret.moe/Zapret/home#x"),
                "https://wiki3.zapret.moe/Zapret/home#x",
            )

    def test_other_addresses_are_untouched_and_ask_nothing(self) -> None:
        with patch.object(site_links, "chosen_host", side_effect=AssertionError("сеть не нужна")):
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
    def test_does_not_wait_for_the_network_and_opens_the_resolved_address(self) -> None:
        release = threading.Event()
        opened: list[str] = []
        done = threading.Event()

        def slow_choice(family="git"):
            release.wait(5)
            return "wiki2.zapret.moe"

        def fake_open(url):
            opened.append(url)
            done.set()

        with patch.object(site_links, "chosen_host", side_effect=slow_choice), patch.object(
            site_links.webbrowser, "open", side_effect=fake_open
        ):
            site_links.open_in_browser("https://wiki.zapret.moe/Zapret/home")
            self.assertEqual(opened, [], "окно ждало выбор имени")
            release.set()
            self.assertTrue(done.wait(5))

        self.assertEqual(opened, ["https://wiki2.zapret.moe/Zapret/home"])

    def test_a_failed_choice_never_raises_into_the_window(self) -> None:
        done = threading.Event()
        with patch.object(site_links, "resolve", side_effect=RuntimeError("нет сети")), patch.object(
            site_links, "log", side_effect=lambda *a, **k: done.set()
        ):
            site_links.open_in_browser("https://git.zapret.moe/")
            self.assertTrue(done.wait(5))


class ButtonsGoThroughTheModuleTests(unittest.TestCase):
    """«Справка», «Исходный код», «Документация», форма заявки: адрес уходит в браузер под открытым именем."""

    def _opened_through(self, call):
        with patch.object(site_links, "chosen_host", side_effect=lambda family="git": f"{family}2.zapret.moe"), patch(
            "webbrowser.open"
        ) as browser:
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
