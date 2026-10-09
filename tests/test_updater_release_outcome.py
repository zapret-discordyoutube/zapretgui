from __future__ import annotations

"""Сообщение серверу о том, чем кончилось обновление, поставленное самой программой."""

import unittest
from unittest.mock import patch

from updater.release import outcome


class _Store:
    def __init__(self, state: dict) -> None:
        self.state = dict(state)

    def get_auto_install_state(self) -> dict:
        return dict(self.state)

    def set_auto_install_outcome(self, version: str, value: str) -> bool:
        if self.state.get("version") != version:
            return False
        self.state["outcome"] = value
        return True


class UpdateOutcomeTests(unittest.TestCase):
    def _store(self, **state) -> _Store:
        store = _Store(state)
        patcher = patch("settings.store", store)
        patcher.start()
        self.addCleanup(patcher.stop)
        log_patcher = patch.object(outcome, "log")
        log_patcher.start()
        self.addCleanup(log_patcher.stop)
        return store

    def test_new_version_tells_where_it_came_from_and_how_long_it_took(self) -> None:
        self._store(version="21.1.7.119", from_version="21.1.7.118", granted_at=1000.0, outcome="started")

        report = outcome.pending_report(current_version="21.1.7.119", now=1024.6)

        self.assertEqual(report, {"prev": "21.1.7.118", "took": "24"})

    def test_report_is_sent_once(self) -> None:
        store = self._store(version="21.1.7.119", from_version="21.1.7.118", granted_at=1000.0, outcome="started")
        report = outcome.pending_report(current_version="21.1.7.119", now=1024.0)

        outcome.report_delivered(report)

        self.assertEqual(store.state["outcome"], "")
        self.assertEqual(outcome.pending_report(current_version="21.1.7.119", now=1030.0), {})

    def test_nothing_is_reported_while_the_old_version_is_still_installing(self) -> None:
        self._store(version="21.1.7.119", from_version="21.1.7.118", granted_at=1000.0, outcome="started")

        # Установка только началась (или скачивание оборвалось): это ещё не исход.
        self.assertEqual(outcome.pending_report(current_version="21.1.7.118", now=1010.0), {})

    def test_failed_installer_is_reported_by_the_old_version(self) -> None:
        store = self._store(version="21.1.7.119", from_version="21.1.7.118", granted_at=1000.0, outcome="started")

        outcome.note_attempt_failed("21.1.7.119")
        report = outcome.pending_report(current_version="21.1.7.118", now=1100.0)

        self.assertEqual(report, {"fail": "21.1.7.119"})
        outcome.report_delivered(report)
        self.assertEqual(store.state["outcome"], "")

    def test_failure_of_a_version_the_program_did_not_install_itself_is_not_reported(self) -> None:
        store = self._store(version="21.1.7.119", from_version="21.1.7.118", granted_at=1000.0, outcome="")

        # Человек ставил 21.1.7.120 по кнопке: сервер разрешения не давал.
        outcome.note_attempt_failed("21.1.7.120")

        self.assertEqual(store.state["outcome"], "")
        self.assertEqual(outcome.pending_report(current_version="21.1.7.118", now=1100.0), {})

    def test_strange_clock_gives_no_update_time(self) -> None:
        self._store(version="21.1.7.119", from_version="21.1.7.118", granted_at=5000.0, outcome="started")

        # Часы перевели назад: время обновления неизвестно, но «дошла» — правда.
        self.assertEqual(outcome.pending_report(current_version="21.1.7.119", now=1000.0), {"prev": "21.1.7.118"})

    def test_unknown_previous_version_closes_the_question_silently(self) -> None:
        store = self._store(version="21.1.7.119", from_version="", granted_at=1000.0, outcome="started")

        self.assertEqual(outcome.pending_report(current_version="21.1.7.119", now=1024.0), {})
        self.assertEqual(store.state["outcome"], "")

    def test_no_state_means_no_report(self) -> None:
        self._store()

        self.assertEqual(outcome.pending_report(current_version="21.1.7.119"), {})

    def test_permission_time_is_remembered_from_the_first_signal(self) -> None:
        outcome.note_granted("77.0.0.1", now=500.0)
        outcome.note_granted("77.0.0.1", now=900.0)

        self.assertEqual(outcome.granted_at("77.0.0.1"), 500.0)
        self.assertEqual(outcome.granted_at("77.0.0.2"), 0.0)


if __name__ == "__main__":
    unittest.main()
