"""Гео-сайты: сервис сам ограничивает доступ из России, стратегия Zapret его не чинит.

BlockCheck и «Подбор стратегии» для таких сайтов советуют hosts или DNS, а
не подбор стратегии.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from blockcheck import public as blockcheck_public
from blockcheck import strategy_scan_page_plans as plans
from hosts.geo_sites import GeoSites, build_geo_sites

_INDEX = {
    "services": ["Discord", "Claude", "Spotify", "Gemini AI"],
    "has_proxy_by_service": {"Discord": False, "Claude": True, "Spotify": True, "Gemini AI": True},
    "domain_names_by_service": {
        "Discord": ["discord.com", "gateway.discord.gg"],
        "Claude": ["claude.ai", "api.anthropic.com"],
        "Spotify": ["www.spotify.com", "spotifycdn.com"],
        "Gemini AI": ["gemini.google.com"],
    },
}
_GEO_SITES = build_geo_sites(_INDEX)


class GeoSitesTests(unittest.TestCase):
    def test_site_and_its_subdomains_belong_to_the_service(self) -> None:
        self.assertEqual(_GEO_SITES.service_for("claude.ai"), "Claude")
        self.assertEqual(_GEO_SITES.service_for("WWW.Claude.AI."), "Claude")
        self.assertEqual(_GEO_SITES.service_for("status.claude.ai"), "Claude")
        self.assertEqual(_GEO_SITES.service_for("api.anthropic.com"), "Claude")

    def test_www_and_bare_name_are_the_same_site(self) -> None:
        self.assertEqual(_GEO_SITES.service_for("spotify.com"), "Spotify")
        self.assertEqual(_GEO_SITES.service_for("open.spotify.com"), "Spotify")

    def test_parent_of_a_listed_subdomain_is_not_a_geo_site(self) -> None:
        # В каталоге только gemini.google.com и api.anthropic.com.
        self.assertEqual(_GEO_SITES.service_for("google.com"), "")
        self.assertEqual(_GEO_SITES.service_for("www.google.com"), "")
        self.assertEqual(_GEO_SITES.service_for("anthropic.com"), "")

    def test_services_without_dns_profile_are_not_geo_sites(self) -> None:
        # Discord режет провайдер: ему как раз помогает стратегия.
        self.assertEqual(_GEO_SITES.service_for("discord.com"), "")
        self.assertEqual(_GEO_SITES.service_for(""), "")
        self.assertEqual(_GEO_SITES.service_for("ai"), "")

    def test_shipped_catalog_has_no_false_matches(self) -> None:
        catalog = Path(__file__).resolve().parents[2] / "private_zapretgui/resources/system/hosts_catalog.sqlite3"
        if not catalog.is_file():
            self.skipTest("каталог hosts лежит в закрытом репозитории")
        from hosts.catalog_repository import load_catalog
        from hosts.proxy_domains import _build_services_profile_index

        geo_sites = build_geo_sites(_build_services_profile_index(load_catalog(catalog)))

        self.assertEqual(geo_sites.service_for("claude.ai"), "Claude")
        self.assertTrue(geo_sites.service_for("chatgpt.com"))
        for host in (
            "discord.com",
            "www.youtube.com",
            "google.com",
            "www.google.com",
            "microsoft.com",
            "github.com",
            "telegram.org",
            "x.com",
            "www.instagram.com",
            "rutracker.org",
        ):
            self.assertEqual(geo_sites.service_for(host), "", host)


class StrategyScanGeoPlanTests(unittest.TestCase):
    def test_only_site_targets_are_checked(self) -> None:
        def service(protocol: str, target: str, geo_sites=_GEO_SITES) -> str:
            return plans.geo_service_for_target(geo_sites, scan_protocol=protocol, target_input=target)

        self.assertEqual(service("tcp_https", "claude.ai"), "Claude")
        self.assertEqual(service("tcp_https", "https://claude.ai/chats"), "Claude")
        self.assertEqual(service("tcp_https", "discord.com"), "")
        self.assertEqual(service("stun_voice", "claude.ai"), "")
        # Каталог ещё не прочитан.
        self.assertEqual(service("tcp_https", "claude.ai", geo_sites=None), "")

    def test_texts_name_the_service_and_the_real_fix(self) -> None:
        notice = plans.geo_site_notice_text("Claude", language="ru")
        question = plans.geo_site_question_text("Claude", "https://claude.ai/chats", language="ru")

        for text in (notice, question):
            self.assertIn("Claude", text)
            self.assertIn("Редактор", text)
            self.assertIn("DNS", text)
        self.assertIn("claude.ai — это Claude", question)


class _Feature:
    build_selection_state = staticmethod(blockcheck_public.build_selection_state)
    build_protocol_ui_plan = staticmethod(blockcheck_public.build_protocol_ui_plan)
    build_udp_scope_hint_plan = staticmethod(blockcheck_public.build_udp_scope_hint_plan)
    build_idle_interaction_plan = staticmethod(blockcheck_public.build_idle_interaction_plan)
    build_running_interaction_plan = staticmethod(blockcheck_public.build_running_interaction_plan)
    plan_scan_start = staticmethod(blockcheck_public.plan_scan_start)

    @staticmethod
    def count_resumable_strategies(**_kwargs):
        return 0


class StrategyScanPageGeoWarningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _page(self):
        from blockcheck.ui.strategy_scan_page import StrategyScanPage

        self.opened: list[str] = []
        page = StrategyScanPage(
            blockcheck_feature=_Feature(),
            create_strategy_scan_worker=lambda **_kw: None,
            open_hosts_editor=lambda: self.opened.append("hosts"),
            open_dns_settings=lambda: self.opened.append("dns"),
        )
        self.addCleanup(page.deleteLater)
        # Так страница получает каталог от фонового загрузчика.
        page._on_geo_sites_loaded(page._geo_sites_runtime.request_id, _GEO_SITES)
        return page

    def test_notice_follows_the_target(self) -> None:
        page = self._page()
        notice = page._geo_notice

        self.assertTrue(notice.isHidden())  # discord.com по умолчанию

        page._on_pick_quick_domain("claude.ai")
        self.assertFalse(notice.isHidden())
        self.assertIn("Claude сам ограничивает доступ из России", notice.text())
        self.assertIn("Предупреждение", notice.notice.property("screenReaderStateText"))

        page._target_input.setText("discord.com")
        self.assertTrue(notice.isHidden())

    def test_notice_is_only_for_sites(self) -> None:
        page = self._page()
        page._target_input.setText("claude.ai")
        self.assertFalse(page._geo_notice.isHidden())

        page._protocol_combo.setCurrentIndex(page._protocol_combo.findData("stun_voice"))

        self.assertTrue(page._geo_notice.isHidden())

    def test_notice_buttons_open_hosts_and_dns(self) -> None:
        page = self._page()
        page._target_input.setText("claude.ai")

        page._geo_notice.hosts_button.click()
        page._geo_notice.dns_button.click()

        self.assertEqual(self.opened, ["hosts", "dns"])

    def test_no_notice_until_catalog_is_loaded(self) -> None:
        from blockcheck.ui.strategy_scan_page import StrategyScanPage

        page = StrategyScanPage(blockcheck_feature=_Feature(), create_strategy_scan_worker=lambda **_kw: None)
        self.addCleanup(page.deleteLater)
        page._target_input.setText("claude.ai")

        self.assertTrue(page._geo_notice.isHidden())
        self.assertTrue(page._confirm_geo_site_scan())

    def _confirm(self, page, press: str):
        from qfluentwidgets import MessageBox, PushButton

        seen: list[str] = []

        def fake_exec(box):
            seen.append(box.contentLabel.text())
            if press == "scan":
                [button] = [b for b in box.findChildren(PushButton) if b.text() == "Всё равно подобрать"]
                button.click()
            elif press == "hosts":
                box.yesButton.click()
            else:
                box.cancelButton.click()
            from PyQt6.QtTest import QTest

            QTest.qWait(300)
            return box.result()

        with patch.object(MessageBox, "exec", new=fake_exec):
            return page._confirm_geo_site_scan(), seen

    def test_start_asks_before_scanning_a_geo_site(self) -> None:
        page = self._page()
        page._target_input.setText("claude.ai")

        proceed, seen = self._confirm(page, "cancel")
        self.assertFalse(proceed)
        # Окно само переносит длинный текст по строкам.
        self.assertIn("ни одна стратегия Zapret его не откроет", " ".join(seen[0].split()))
        self.assertEqual(self.opened, [])

        self.assertFalse(self._confirm(page, "hosts")[0])
        self.assertEqual(self.opened, ["hosts"])

        self.assertTrue(self._confirm(page, "scan")[0])
        self.assertEqual(self.opened, ["hosts"])

    def test_ordinary_site_starts_without_question(self) -> None:
        page = self._page()

        proceed, seen = self._confirm(page, "cancel")

        self.assertTrue(proceed)
        self.assertEqual(seen, [])

    def test_cancelled_question_does_not_start_the_scan(self) -> None:
        page = self._page()
        page._target_input.setText("claude.ai")

        with (
            patch.object(page, "_confirm_geo_site_scan", return_value=False),
            patch("blockcheck.ui.strategy_scan_page.start_strategy_scan_run") as start,
        ):
            page._on_start()

        start.assert_not_called()


class BlockcheckProblemsGeoTests(unittest.TestCase):
    """Итог BlockCheck: у гео-сайта кнопка ведёт в hosts, а не в подбор стратегии."""

    def _problems(self, host: str, geo_service_for):
        from diagnostics import problems
        from diagnostics.verdict import ADVICE_VIA_ZAPRET, Level, ReachState, ServiceVerdict

        from diagnostics.services import _site

        service = _site("site", host, host)
        probe = SimpleNamespace(host=host, reach_state=ReachState.DPI, judgement=None, cause=None, quic=None)
        verdict = ServiceVerdict(Level.FAIL, f"{host} не открывается", (ADVICE_VIA_ZAPRET[0],))
        found, _working, _spoofed = problems.collect_problems(
            {"site": service},
            {"site": verdict},
            {"site": [probe]},
            voice=None,
            freeze=None,
            zapret_running=True,
            geo_service_for=geo_service_for,
        )
        return found

    def test_geo_site_is_sent_to_hosts_instead_of_strategy_scan(self) -> None:
        from diagnostics.verdict import ADVICE_VIA_ZAPRET

        [problem] = self._problems("claude.ai", _GEO_SITES.service_for)

        self.assertEqual(problem["action"], "hosts")
        self.assertEqual(problem["target"], "claude.ai")
        self.assertIn("Claude сам ограничивает доступ из России", problem["advice"][0])
        self.assertIn("Редактор hosts", problem["advice"][0])
        for advice in ADVICE_VIA_ZAPRET:
            self.assertNotIn(advice, problem["advice"])

    def test_ordinary_site_still_gets_strategy_scan(self) -> None:
        from diagnostics.verdict import ADVICE_VIA_ZAPRET

        for lookup in (_GEO_SITES.service_for, None):
            [problem] = self._problems("discord.com", lookup)
            self.assertEqual(problem["action"], "strategy")
            self.assertEqual(problem["advice"], [ADVICE_VIA_ZAPRET[0]])

    def test_hosts_button_is_shown_in_results(self) -> None:
        QApplication.instance() or QApplication([])
        from blockcheck.ui.check_results import _ProblemRow

        clicked: list[tuple[str, str]] = []
        row = _ProblemRow(
            {"level": "fail", "text": "claude.ai не открывается", "advice": ["совет"], "action": "hosts", "target": "claude.ai"},
            lambda action, target: clicked.append((action, target)),
        )
        self.addCleanup(row.deleteLater)

        self.assertEqual(row.action_button.text(), "Редактор hosts")
        row.action_button.click()
        self.assertEqual(clicked, [("hosts", "claude.ai")])


class BlockcheckWorkerGeoTests(unittest.TestCase):
    def test_worker_passes_catalog_lookup_to_the_check(self) -> None:
        from blockcheck.worker import BlockcheckWorker

        worker = BlockcheckWorker(
            report_path=lambda *_a: "",
            save_report=lambda *_a: "",
            load_geo_sites=lambda: _GEO_SITES,
        )
        with patch("diagnostics.engine.run_blockcheck", return_value={}) as run:
            worker.run()

        self.assertEqual(run.call_args.kwargs["geo_service_for"]("claude.ai"), "Claude")

    def test_feature_facade_wires_the_loader(self) -> None:
        from app.feature_facades.blockcheck import BlockcheckFeature

        feature = BlockcheckFeature(presets_feature=None, profile_feature=None, load_geo_sites=lambda: _GEO_SITES)
        worker = feature.create_geo_sites_worker(7)
        loaded: list = []
        worker.completed.connect(lambda request_id, geo_sites: loaded.append((request_id, geo_sites)))

        worker.run()

        self.assertEqual(loaded, [(7, _GEO_SITES)])
        self.assertIsInstance(loaded[0][1], GeoSites)


if __name__ == "__main__":
    unittest.main()
