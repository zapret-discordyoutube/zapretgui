"""Итоговая проверка в настоящем браузере без окна (сам браузер подменён)."""

from __future__ import annotations

import unittest

from blockcheck import browser_check


class _Process:
    def __init__(self, output, text: str, *, finishes: bool = True, exit_code: int = 0) -> None:
        output.write(text.encode())
        # Настоящий браузер пишет в файл мимо буфера Python: проверка видит строки, пока он работает.
        output.flush()
        self.finishes = finishes
        self.exit_code = exit_code
        self.killed = False

    def poll(self):
        return self.exit_code if self.finishes or self.killed else None

    def kill(self):
        self.killed = True

    def wait(self):
        return 0


class BrowserCheckTests(unittest.TestCase):
    def _visit(self, text: str, **kwargs):
        commands: list[list[str]] = []
        made: list[_Process] = []
        finishes = kwargs.pop("finishes", True)
        exit_code = kwargs.pop("exit_code", 0)

        def popen(command, output):
            commands.append(command)
            made.append(_Process(output, text, finishes=finishes, exit_code=exit_code))
            return made[-1]

        visit = browser_check.open_in_browser("discord.com", popen=popen, browser=r"C:\Edge\msedge.exe", **kwargs)
        return visit, commands[0], made[0]

    def test_page_means_the_site_opened(self) -> None:
        visit, command, _process = self._visit("<html><head><title>Discord</title></head></html>")
        self.assertTrue(visit.ok)
        self.assertEqual(visit.browser, "msedge.exe")
        # Без окна, с пустым профилем, без QUIC: проверяется обычное соединение.
        self.assertIn("--headless=new", command)
        self.assertIn("--disable-quic", command)
        self.assertIn("--disable-background-networking", command)
        # Без журнала в stderr браузер не печатает «Page load failed» — ошибка выглядела бы как молчание.
        self.assertIn("--enable-logging=stderr", command)
        self.assertEqual(command[-1], "https://discord.com/")

    def test_browser_error_is_named(self) -> None:
        visit, _command, _process = self._visit("[1:2:ERROR:headless_command_handler.cc:403] Page load failed: net::ERR_CONNECTION_RESET\n")
        self.assertEqual((visit.state, visit.error, visit.text), (browser_check.BROWSER_FAILED, "ERR_CONNECTION_RESET", "соединение сброшено"))

    def test_page_that_never_loads_is_a_timeout(self) -> None:
        visit, _command, _process = self._visit("[1:2:ERROR:x.cc:409] Page load timed out.\n<html><body></body></html>")
        self.assertEqual(visit.state, browser_check.BROWSER_TIMEOUT)

    def test_browser_that_says_nothing_is_no_answer_with_its_exit_code(self) -> None:
        # Пустой вывод — не «сайт не открылся», а сбой самой проверки; код выхода идёт в журнал.
        visit, _command, _process = self._visit("", exit_code=-1073741819)
        self.assertEqual(visit.state, browser_check.BROWSER_NO_ANSWER)
        self.assertFalse(visit.ok)
        self.assertEqual(visit.details, "код выхода 0xC0000005; вывод пуст")
        visit, _command, _process = self._visit("[1:2:ERROR:policy.cc:7] Headless mode is disabled\n", exit_code=1)
        self.assertEqual(visit.state, browser_check.BROWSER_NO_ANSWER)
        self.assertIn("код выхода 1", visit.details)
        self.assertIn("Headless mode is disabled", visit.details)

    def test_network_error_in_another_log_line_is_still_named(self) -> None:
        visit, _command, _process = self._visit("[1:2:ERROR:ssl_client_socket_impl.cc:9] handshake failed; net::ERR_CONNECTION_CLOSED\n")
        self.assertEqual((visit.state, visit.error), (browser_check.BROWSER_FAILED, "ERR_CONNECTION_CLOSED"))

    def test_error_is_taken_while_the_browser_is_still_running(self) -> None:
        # Edge 156: напечатал «Page load failed» и не выходит. Итог — сразу, а браузер гасится.
        visit, _command, process = self._visit(
            "[1:2:ERROR:headless_command_handler.cc:403] Page load failed: net::ERR_NAME_NOT_RESOLVED\n", finishes=False
        )
        self.assertEqual((visit.state, visit.error), (browser_check.BROWSER_FAILED, "ERR_NAME_NOT_RESOLVED"))
        self.assertTrue(process.killed)

    def test_browser_that_never_finishes_keeps_its_last_words(self) -> None:
        ticks = iter(range(0, 1000, 10))
        visit, _command, process = self._visit("[1:2:INFO:x.cc:1] still loading\n", finishes=False, clock=lambda: float(next(ticks)))
        self.assertEqual(visit.state, browser_check.BROWSER_TIMEOUT)
        self.assertIn("still loading", visit.details)
        self.assertTrue(process.killed)

    def test_stop_kills_the_browser(self) -> None:
        visit, _command, process = self._visit("", finishes=False, cancelled=lambda: True)
        self.assertEqual(visit.state, browser_check.BROWSER_TIMEOUT)
        self.assertTrue(process.killed)

    def test_no_browser_is_said_plainly(self) -> None:
        visit = browser_check.open_in_browser("discord.com", browser="")
        self.assertEqual(visit.state, browser_check.BROWSER_MISSING)
        self.assertIn("Edge", visit.text)


if __name__ == "__main__":
    unittest.main()
