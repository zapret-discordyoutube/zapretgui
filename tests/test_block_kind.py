from __future__ import annotations

import unittest

from diagnostics import block_kind as bk
from diagnostics.verdict import ADVICE_VIA_ZAPRET, DnsState, Level, ReachState, TargetOutcome, summarize_service


class SiteKindTests(unittest.TestCase):
    def test_open_or_unchecked_site_has_no_kind(self) -> None:
        for reach in ("ok", "unknown", "no_address", ""):
            with self.subTest(reach=reach):
                self.assertEqual(bk.site_kind(reach, "by_name"), "")

    def test_kind_follows_what_the_probes_found(self) -> None:
        cases = {
            ("dpi", "by_name"): bk.KIND_SNI,
            ("dpi", "by_address"): bk.KIND_IP,
            # Соединение не установилось, но перепроверки нет: «баном по адресу» это не называется.
            ("ip_block", "address_closed"): bk.KIND_NO_CONNECT,
            ("ip_block", "address_silent"): bk.KIND_NO_CONNECT,
            ("ip_block", ""): bk.KIND_NO_CONNECT,
            ("dpi", ""): bk.KIND_UNCLEAR,
            ("freeze", ""): bk.KIND_CUT,
            ("cert", ""): bk.KIND_CERT,
        }
        for (reach, cause), kind in cases.items():
            with self.subTest(reach=reach, cause=cause):
                self.assertEqual(bk.site_kind(reach, cause), kind)
        # Перепроверка подтвердила: молчат все адреса сайта — тогда это адрес.
        for cause in ("", "address_closed", "address_silent"):
            self.assertEqual(bk.site_kind("ip_block", cause, address_confirmed=True), bk.KIND_IP)
        # Подтверждение ничего не меняет там, где вид и так известен.
        self.assertEqual(bk.site_kind("dpi", "by_name", address_confirmed=True), bk.KIND_SNI)
        self.assertEqual(bk.site_kind("ok", "", address_confirmed=True), "")

    def test_block_page_is_direct_evidence_and_wins(self) -> None:
        for reach in ("dpi", "ip_block", "freeze"):
            with self.subTest(reach=reach):
                self.assertEqual(bk.site_kind(reach, "stub_page"), bk.KIND_STUB)

    def test_every_kind_is_described_and_ordered(self) -> None:
        self.assertEqual(set(bk.KIND_ORDER), set(bk.KINDS))
        for info in bk.KINDS.values():
            self.assertTrue(info.title and info.short and info.reason, info.key)
        self.assertIs(bk.kind_info("нет такого"), bk.KINDS[bk.KIND_OTHER])

    def test_three_main_kinds_are_named_differently(self) -> None:
        """Ради этого всё и делалось: бан по адресу, блокировка по имени и обрыв не сливаются в одну фразу."""
        reasons = {bk.kind_info(kind).reason for kind in (bk.KIND_IP, bk.KIND_SNI, bk.KIND_CUT)}

        self.assertEqual(len(reasons), 3)
        self.assertIn("(IP)", bk.kind_info(bk.KIND_IP).title)
        self.assertIn("(SNI)", bk.kind_info(bk.KIND_SNI).title)
        self.assertIn("16 КБ", bk.kind_info(bk.KIND_CUT).title)


class HeadlineNamesTheKindTests(unittest.TestCase):
    @staticmethod
    def _verdict(reach: ReachState, kind: str = "", *, running: bool = True):
        outcome = TargetOutcome("x.com", "сайт", reach, DnsState.OK, main=True, kind=kind)
        return summarize_service("X", [outcome], zapret_running=running)

    def test_block_by_name(self) -> None:
        verdict = self._verdict(ReachState.DPI, bk.KIND_SNI)

        self.assertEqual(verdict.kind, bk.KIND_SNI)
        self.assertEqual(
            verdict.headline,
            "X не открывается: блокировка по имени сайта (SNI) — Zapret запущен, но эту блокировку не обходит",
        )
        self.assertEqual(verdict.advice, (ADVICE_VIA_ZAPRET[0],))

    def test_block_by_address(self) -> None:
        verdict = self._verdict(ReachState.IP_BLOCK, bk.KIND_IP)

        self.assertEqual((verdict.level, verdict.kind), (Level.FAIL, bk.KIND_IP))
        self.assertEqual(verdict.headline, "X не открывается: сервер заблокирован по адресу (IP)")

    def test_address_closed_for_encryption_gets_honest_advice(self) -> None:
        """Соединение есть, шифрование не проходит ни с каким именем: стратегия может не помочь."""
        verdict = self._verdict(ReachState.DPI, bk.KIND_IP)

        self.assertIn("сервер заблокирован по адресу (IP)", verdict.headline)
        [advice] = verdict.advice
        self.assertIn("Попробуйте подобрать стратегию", advice)
        self.assertIn("Редакторе hosts", advice)
        # Гео-сайтам этот совет заменяют на hosts так же, как обычный «подберите стратегию».
        self.assertIn(advice, ADVICE_VIA_ZAPRET)
        # Zapret выключен — сначала его надо запустить.
        self.assertIn("Запустите Zapret", self._verdict(ReachState.DPI, bk.KIND_IP, running=False).advice[0])

    def test_cut_after_16kb_is_not_called_closed(self) -> None:
        verdict = self._verdict(ReachState.FREEZE)

        self.assertEqual(verdict.kind, bk.KIND_CUT)
        self.assertTrue(verdict.headline.startswith("X грузится не до конца: загрузка обрывается после 16 КБ"))

    def test_without_refined_kind_reach_alone_decides(self) -> None:
        self.assertEqual(self._verdict(ReachState.DPI).kind, bk.KIND_UNCLEAR)
        self.assertIn("соединение блокирует провайдер", self._verdict(ReachState.DPI).headline)
        # Одно несоединение — не «бан по адресу»: причина не ясна, пока нет перепроверки.
        self.assertEqual(self._verdict(ReachState.IP_BLOCK).kind, bk.KIND_NO_CONNECT)
        self.assertIn("причина не ясна", self._verdict(ReachState.IP_BLOCK).headline)
        self.assertNotIn("заблокирован", self._verdict(ReachState.IP_BLOCK).headline)

    def test_open_site_has_no_kind(self) -> None:
        self.assertEqual(self._verdict(ReachState.OK).kind, "")

    def test_kind_of_broken_part_when_site_itself_opens(self) -> None:
        verdict = summarize_service(
            "YouTube",
            [
                TargetOutcome("www.youtube.com", "сайт", ReachState.OK, DnsState.OK, main=True),
                TargetOutcome("rr1.googlevideo.com", "видео", ReachState.FREEZE, DnsState.OK),
            ],
            zapret_running=True,
        )

        self.assertEqual((verdict.level, verdict.kind), (Level.WARN, bk.KIND_CUT))
        self.assertEqual(
            verdict.headline, "YouTube открывается, но не работают видео: загрузка обрывается после 16 КБ"
        )


if __name__ == "__main__":
    unittest.main()
