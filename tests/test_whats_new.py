from __future__ import annotations

"""«Что нового»: когда показывать и откуда брать текст."""

import tempfile
import unittest
from unittest.mock import patch

from settings.normalize import normalize_updater
from updater import whats_new


def _state(*, seen: str = "", pending_version: str = "", history=()) -> dict:
    return {"seen_version": seen, "pending": {"version": pending_version, "history": list(history)}}


class WhatsNewStartupTests(unittest.TestCase):
    def _startup(self, state: dict, *, forgejo=None):
        marked: list[str] = []
        fetch = forgejo or (lambda _version: (_ for _ in ()).throw(AssertionError("Forgejo не нужен")))
        with (
            patch.object(whats_new, "_state", return_value=state),
            patch.object(whats_new, "mark_seen", side_effect=marked.append),
            patch.object(whats_new, "_fetch_from_forgejo", side_effect=fetch),
            patch.object(whats_new, "_with_earlier", side_effect=lambda _v, pending: pending),
        ):
            history = whats_new.startup_history("2.0")
        return history, marked

    def test_saved_history_is_shown_after_update_without_network(self) -> None:
        saved = ({"version": "2.0", "notes": "новое"}, {"version": "1.9", "notes": "старое"})

        history, marked = self._startup(_state(seen="1.8", pending_version="2.0", history=saved))

        self.assertEqual([item["version"] for item in history], ["2.0", "1.9"])
        # Отметку «показано» ставит окно, когда его действительно показали.
        self.assertEqual(marked, [])

    def test_already_seen_version_shows_nothing(self) -> None:
        history, marked = self._startup(_state(seen="2.0", pending_version="2.0", history=({"version": "2.0"},)))

        self.assertEqual(history, ())
        self.assertEqual(marked, [])

    def test_fresh_install_shows_nothing_and_remembers_version(self) -> None:
        history, marked = self._startup(_state())

        self.assertEqual(history, ())
        self.assertEqual(marked, ["2.0"])

    def test_manual_update_takes_text_from_forgejo(self) -> None:
        history, _marked = self._startup(
            _state(seen="1.9"),
            forgejo=lambda version: ({"version": version, "notes": "с Forgejo"},),
        )

        self.assertEqual(history[0]["notes"], "с Forgejo")

    def test_forgejo_failure_does_not_show_window_every_launch(self) -> None:
        def broken(_version):
            raise ConnectionError("нет сети")

        with patch.object(whats_new, "log"):
            history, marked = self._startup(_state(seen="1.9"), forgejo=broken)

        self.assertEqual(history, ())
        self.assertEqual(marked, ["2.0"])

    def test_pending_of_other_version_is_ignored(self) -> None:
        history, _marked = self._startup(
            _state(seen="1.9", pending_version="3.0", history=({"version": "3.0"},)),
            forgejo=lambda version: ({"version": version, "notes": "свой"},),
        )

        self.assertEqual(history[0]["version"], "2.0")


class WhatsNewForgejoFallbackTests(unittest.TestCase):
    def test_forgejo_fallback_shows_last_releases_with_this_one_new(self) -> None:
        entries = tuple({"version": f"2.{n}", "notes": str(n)} for n in range(0, 15))
        with patch("updater.release.forgejo.fetch_recent_release_history", return_value=entries):
            history = whats_new._fetch_from_forgejo("2.12")

        self.assertEqual(history[0]["version"], "2.12")
        self.assertTrue(history[0]["is_new"])
        self.assertEqual(len(history), 10)
        self.assertFalse(any(item["is_new"] for item in history[1:]))


class WhatsNewEarlierTopUpTests(unittest.TestCase):
    """Старые версии сохраняли только пропущенные выпуски — «Ранее» добирается."""

    def test_short_saved_history_gets_earlier_releases(self) -> None:
        saved = ({"version": "2.12", "notes": "сохранено"},)
        remote = tuple({"version": f"2.{n}", "notes": f"forgejo {n}"} for n in range(0, 15))
        with patch("updater.release.forgejo.fetch_recent_release_history", return_value=remote):
            history = whats_new._with_earlier("2.12", saved)

        self.assertEqual(len(history), 10)
        self.assertEqual(history[0]["notes"], "сохранено")
        self.assertTrue(history[0]["is_new"])
        self.assertEqual([item["version"] for item in history[1:3]], ["2.11", "2.10"])
        self.assertFalse(any(item["is_new"] for item in history[1:]))

    def test_about_button_shows_earlier_for_old_saved_text(self) -> None:
        state = _state(seen="2.11", pending_version="2.12", history=({"version": "2.12", "notes": "x"},))
        remote = tuple({"version": f"2.{n}", "notes": ""} for n in range(15))
        with (
            patch.object(whats_new, "_state", return_value=state),
            patch("updater.release.forgejo.fetch_recent_release_history", return_value=remote),
        ):
            self.assertEqual(len(whats_new.load_release_history("2.12")), 10)
            self.assertEqual(len(whats_new.startup_history("2.12")), 10)

    def test_complete_history_and_offline_are_left_as_is(self) -> None:
        complete = ({"version": "2.12", "is_new": True}, {"version": "2.11", "is_new": False})
        with patch("updater.release.forgejo.fetch_recent_release_history") as fetch:
            self.assertEqual(whats_new._with_earlier("2.12", complete), complete)
        fetch.assert_not_called()

        saved = ({"version": "2.12"},)
        with (
            patch("updater.release.forgejo.fetch_recent_release_history", side_effect=ConnectionError("нет сети")),
            patch.object(whats_new, "log"),
        ):
            self.assertEqual(whats_new._with_earlier("2.12", saved), saved)


class WhatsNewAboutButtonTests(unittest.TestCase):
    def test_about_uses_saved_history_first(self) -> None:
        saved = ({"version": "2.0", "notes": "сохранено"},)
        with (
            patch.object(whats_new, "_state", return_value=_state(pending_version="2.0", history=saved)),
            patch.object(whats_new, "_fetch_from_forgejo") as fetch,
            patch.object(whats_new, "_with_earlier", side_effect=lambda _v, pending: pending),
        ):
            history = whats_new.load_release_history("2.0")

        self.assertEqual(history[0]["notes"], "сохранено")
        fetch.assert_not_called()

    def test_about_falls_back_to_forgejo_and_reports_errors(self) -> None:
        with (
            patch.object(whats_new, "_state", return_value=_state()),
            patch.object(whats_new, "_fetch_from_forgejo", side_effect=ConnectionError("нет сети")),
            self.assertRaises(ConnectionError),
        ):
            whats_new.load_release_history("2.0")


class WhatsNewSettingsTests(unittest.TestCase):
    def test_updater_section_keeps_skip_and_whats_new(self) -> None:
        normalized = normalize_updater(
            {
                "skipped_version": " 2.0 ",
                "whats_new": {
                    "seen_version": "1.9",
                    "pending": {
                        "version": "2.0",
                        "history": [
                            {"version": "2.0", "notes": "x" * 30000, "url": "https://u"},
                            {"notes": "без версии — выбрасывается"},
                            "мусор",
                        ],
                    },
                },
            }
        )

        self.assertEqual(normalized["skipped_version"], "2.0")
        self.assertEqual(normalized["whats_new"]["seen_version"], "1.9")
        pending = normalized["whats_new"]["pending"]
        self.assertEqual(pending["version"], "2.0")
        self.assertEqual(len(pending["history"]), 1)
        self.assertEqual(len(pending["history"][0]["notes"]), 20000)
        # Сохранённое до отметки «новое» считается новым.
        self.assertTrue(pending["history"][0]["is_new"])

    def test_missing_section_gets_empty_defaults(self) -> None:
        normalized = normalize_updater({})

        self.assertEqual(normalized["skipped_version"], "")
        self.assertEqual(normalized["whats_new"], {"seen_version": "", "pending": {"version": "", "history": []}})


class WhatsNewStoreTests(unittest.TestCase):
    def test_marks_survive_in_settings_database(self) -> None:
        from settings import store

        with tempfile.TemporaryDirectory() as tmp, patch("settings.store.MAIN_DIRECTORY", tmp):
            store.set_update_skipped_version("2.0")
            store.set_whats_new_pending("2.1", [{"version": "2.1", "notes": "новое"}])
            store.set_whats_new_seen_version("2.0")

            self.assertEqual(store.get_update_skipped_version(), "2.0")
            state = store.get_whats_new_state()
            self.assertEqual(state["seen_version"], "2.0")
            self.assertEqual(state["pending"]["version"], "2.1")
            self.assertEqual(state["pending"]["history"][0]["notes"], "новое")
            # Отметка «показано» не стирает сохранённый текст: он нужен «О программе».
            with patch.object(whats_new, "_with_earlier", side_effect=lambda _v, pending: pending):
                self.assertEqual(whats_new.load_release_history("2.1")[0]["notes"], "новое")


if __name__ == "__main__":
    unittest.main()
