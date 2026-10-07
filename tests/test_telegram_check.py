import unittest
from concurrent.futures import ThreadPoolExecutor

from blockcheck.ui.result_cards_model import build_cards
from diagnostics import telegram_check as tg
from diagnostics.verdict import Level


def _run(connect):
    with ThreadPoolExecutor(max_workers=8) as pool:
        return tg.check_telegram(pool.submit, lambda future: future.result(), connect=connect, pause=lambda _s: None)


class TelegramCheckTests(unittest.TestCase):
    def test_all_centers_answer(self) -> None:
        servers = _run(lambda _address, _port: (True, 42.0))
        report = tg.summarize_telegram(servers)

        self.assertEqual(len(servers), 5)
        self.assertEqual({item.attempts for item in servers}, {1})
        self.assertEqual(report.level, Level.OK)
        self.assertIn("5 из 5", report.headline)

    def test_failed_connection_is_retried_once(self) -> None:
        calls: dict[str, int] = {}

        def connect(address, _port):
            calls[address] = calls.get(address, 0) + 1
            # Первый дата-центр «теряет пакет»: первая попытка не проходит, вторая проходит.
            if address == tg.DATA_CENTERS[0].address and calls[address] == 1:
                return False, None
            return True, 10.0

        servers = _run(connect)

        self.assertEqual(calls[tg.DATA_CENTERS[0].address], 2)
        self.assertEqual(servers[0].attempts, 2)
        self.assertTrue(servers[0].connected)
        self.assertEqual(tg.summarize_telegram(servers).level, Level.OK)

    def test_one_silent_center_is_a_warning_not_a_block(self) -> None:
        silent = tg.DATA_CENTERS[4].address
        report = tg.summarize_telegram(_run(lambda address, _port: (False, None) if address == silent else (True, 5.0)))

        self.assertEqual(report.level, Level.WARN)
        self.assertIn("4 из 5", report.headline)

    def test_all_silent_twice_is_a_failure_with_proxy_advice(self) -> None:
        servers = _run(lambda _address, _port: (False, None))
        report = tg.summarize_telegram(servers)

        self.assertEqual({item.attempts for item in servers}, {2})
        self.assertEqual(report.level, Level.FAIL)
        self.assertIn("каждый проверен дважды", report.headline)
        self.assertIn("Telegram Proxy", report.advice[0])

    def test_cancelled_check_is_unknown(self) -> None:
        report = tg.summarize_telegram(_run(lambda _address, _port: (None, None)))

        self.assertEqual(report.level, Level.UNKNOWN)

    def test_centers_live_in_more_than_one_network(self) -> None:
        networks = {".".join(center.address.split(".")[:2]) for center in tg.DATA_CENTERS}

        self.assertGreaterEqual(len(networks), 2)
        self.assertEqual(len({center.address for center in tg.DATA_CENTERS}), 5)

    def test_card_lists_every_center(self) -> None:
        [card] = build_cards({
            "telegram": {
                "level": "warn",
                "headline": "Часть дата-центров Telegram не принимает соединения: отвечают 1 из 2",
                "advice": ["Включите встроенный Telegram Proxy."],
                "items": [
                    {"name": "DC1 (Майами)", "address": "149.154.175.53", "state": "ok", "text": "соединение за 60 мс"},
                    {"name": "DC5 (Сингапур)", "address": "91.108.56.130", "state": "fail", "text": "не соединился, 2 попытки"},
                ],
            }
        })

        self.assertEqual((card.key, card.level, card.status), ("telegram", "warn", "Отвечают 1 из 2"))
        self.assertEqual([line.state for line in card.lines], ["ok", "fail"])
        self.assertEqual(card.sections[-1].title, "Что делать")


if __name__ == "__main__":
    unittest.main()
