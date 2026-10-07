"""Обрыв на 16–20 КБ называется обрывом, только если повторился на том же сервере."""

from __future__ import annotations

import unittest

from diagnostics import freeze_check
from diagnostics.freeze_check import FreezeState
from diagnostics.tls_probe import ProbeResult

CUT = ProbeResult(ip="1.1.1.1", kind="ok", body_size=16_500, body_cut=True)
FULL = ProbeResult(ip="1.1.1.1", kind="ok", body_size=70_000)
TARGET = [{"id": "X-01", "provider": "X", "url": "https://x.example/file.bin"}]


def _check(answers):
    asked = []

    def _download(host, path):
        asked.append((host, path))
        return answers[min(len(asked), len(answers)) - 1]

    server = freeze_check._check_provider("X", TARGET, _download, lambda: True)
    return server, asked


class FreezeRecheckTests(unittest.TestCase):
    def test_cut_twice_in_a_row_is_a_freeze(self) -> None:
        server, asked = _check([CUT, CUT])

        self.assertEqual((server.state, len(asked)), (FreezeState.FREEZE, 2))
        self.assertIn("дважды подряд", server.text)

    def test_cut_that_does_not_repeat_is_not_a_freeze(self) -> None:
        server, asked = _check([CUT, FULL])

        self.assertEqual((server.state, len(asked)), (FreezeState.OK, 2))
        self.assertIn("первый обрыв был случайным сбоем", server.text)

    def test_cut_without_a_clear_repeat_stays_unknown(self) -> None:
        server, _asked = _check([CUT, None])

        self.assertEqual(server.state, FreezeState.UNKNOWN)
        self.assertIn("не подтвердился", server.text)

    def test_clean_download_is_not_repeated(self) -> None:
        server, asked = _check([FULL])

        self.assertEqual((server.state, len(asked)), (FreezeState.OK, 1))


if __name__ == "__main__":
    unittest.main()


class UnknownZapretStateTests(unittest.TestCase):
    """«Не удалось узнать, запущен ли Zapret» — не «не запущен»: запускать его не советуем."""

    def test_start_is_advised_only_when_zapret_is_known_to_be_off(self) -> None:
        from diagnostics import problems
        from diagnostics.verdict import ReachState, _ADVICE_START, _advice_for

        self.assertEqual(problems._zapret_action(False), "start_zapret")
        self.assertEqual(problems._zapret_action(None), "strategy")
        self.assertEqual(problems._zapret_action(True), "strategy")
        self.assertEqual(_advice_for([ReachState.DPI], zapret_running=False), (_ADVICE_START,))
        self.assertNotIn(_ADVICE_START, _advice_for([ReachState.DPI], zapret_running=None))

    def test_closed_address_is_never_told_to_start_zapret(self) -> None:
        from diagnostics.block_kind import KIND_IP
        from diagnostics.verdict import ReachState, _ADVICE_START, _advice_for

        self.assertNotIn(_ADVICE_START, _advice_for([ReachState.DPI], zapret_running=False, kind=KIND_IP))

    def test_freeze_advice_follows_the_same_rule(self) -> None:
        from diagnostics.freeze_check import FreezeServer, summarize_freeze

        frozen = tuple(FreezeServer(f"S{i}", FreezeState.FREEZE, "обрыв") for i in range(4))
        self.assertIn("Запустите Zapret", summarize_freeze(frozen, zapret_running=False).advice[0])
        self.assertNotIn("Запустите Zapret", summarize_freeze(frozen, zapret_running=None).advice[0])
