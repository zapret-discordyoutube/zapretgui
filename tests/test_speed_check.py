import unittest

from blockcheck.ui.result_cards_model import build_cards
from diagnostics import speed_check as sc
from diagnostics.verdict import Level

MB = 1024 * 1024


def _samples(foreign, domestic):
    """Скорости в КБ/с по порядку серверов; None — замер не получился."""
    result = []
    for server, kbps in list(zip(sc.FOREIGN, foreign)) + list(zip(sc.DOMESTIC, domestic)):
        result.append(sc.SpeedSample(server, kbps, 3 * MB if kbps else 0, 1.0))
    return tuple(result)


class SpeedSummaryTests(unittest.TestCase):
    def test_all_foreign_servers_slow_while_domestic_fast_is_named(self) -> None:
        report = sc.summarize_speed(_samples([60, 80, 70], [6000, 5000]))

        self.assertEqual(report.level, Level.WARN)
        self.assertIn("медленнее российских", report.headline)
        self.assertIn("0.6 Мбит/с против 48 Мбит/с", report.headline)

    def test_one_slow_foreign_server_is_just_that_server(self) -> None:
        report = sc.summarize_speed(_samples([60, 4000, 5000], [6000, 5000]))

        self.assertEqual(report.level, Level.OK)

    def test_single_measured_foreign_server_is_not_enough(self) -> None:
        self.assertEqual(sc.summarize_speed(_samples([60, None, None], [6000, 5000])).level, Level.OK)

    def test_slow_everywhere_is_a_slow_tariff_not_throttling(self) -> None:
        report = sc.summarize_speed(_samples([60, 80, 70], [90, 100]))

        self.assertEqual(report.level, Level.OK)
        self.assertIn("Заметной разницы", report.headline)

    def test_big_ratio_on_a_fast_line_is_not_a_problem(self) -> None:
        """Зарубежные в десять раз медленнее, но сами быстрые: это ничему не мешает."""
        self.assertEqual(sc.summarize_speed(_samples([1500, 1800, 2000], [20000, 18000])).level, Level.OK)

    def test_nothing_to_compare_with_is_unknown(self) -> None:
        no_home = sc.summarize_speed(_samples([60, 80, 70], [None, None]))
        self.assertEqual(no_home.level, Level.UNKNOWN)
        self.assertIn("российских", no_home.headline)
        self.assertIn("зарубежных", sc.summarize_speed(_samples([None, None, None], [6000, 5000])).headline)


class SpeedCollectTests(unittest.TestCase):
    def test_servers_are_measured_in_turn_domestic_and_foreign_mixed(self) -> None:
        asked = []

        def download(server):
            asked.append(server.domestic)
            return 3 * MB, 1.5

        samples = sc.check_speed(download)

        self.assertEqual(len(samples), len(sc.FOREIGN) + len(sc.DOMESTIC))
        self.assertEqual(asked[:4], [True, False, True, False])
        self.assertAlmostEqual(samples[0].kbps, 3 * 1024 / 1.5)

    def test_too_little_data_and_failures_give_no_speed(self) -> None:
        answers = iter([(50_000, 4.0), None, (0, 0.0), (3 * MB, 2.0), (3 * MB, 2.0)])
        samples = sc.check_speed(lambda _server: next(answers))

        self.assertEqual([item.kbps is None for item in samples], [True, True, True, False, False])

    def test_stop_ends_the_measuring(self) -> None:
        samples = sc.check_speed(lambda _server: (3 * MB, 1.0), should_stop=lambda: True)

        self.assertEqual(samples, ())

    def test_speed_is_shown_in_megabits(self) -> None:
        self.assertEqual(sc.speed_text(125.0), "1.0 Мбит/с")
        self.assertEqual(sc.speed_text(6000.0), "48 Мбит/с")
        self.assertEqual(sc.speed_text(None), "замер не получился")


class SpeedCardTests(unittest.TestCase):
    def test_card_lists_servers_by_group(self) -> None:
        [card] = build_cards({
            "speed": {
                "level": "warn",
                "headline": "Зарубежные серверы отдают данные примерно в 80 раз медленнее российских",
                "items": [
                    {"name": "Яндекс", "domestic": True, "state": "ok", "text": "48 Мбит/с"},
                    {"name": "OVH (Франция)", "domestic": False, "state": "warn", "text": "0.6 Мбит/с"},
                    {"name": "Cloudflare", "domestic": False, "state": "unknown", "text": "замер не получился"},
                ],
            }
        })

        self.assertEqual((card.key, card.level, card.status), ("speed", "warn", "Зарубежные медленнее"))
        self.assertEqual([line.name for line in card.lines], ["Яндекс (Россия)", "OVH (Франция)", "Cloudflare"])
        self.assertEqual(card.lines[1].text, "0.6 Мбит/с")


if __name__ == "__main__":
    unittest.main()
