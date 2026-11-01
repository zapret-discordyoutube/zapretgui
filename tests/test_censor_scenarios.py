"""Каталог сценариев цензора: как ведёт себя ТСПУ → какой вывод обязана дать проверка сайта.

По образцу сценарных тестов OONI (``webconnectivityqa`` поверх ``netemx``): цензор
задаётся правилами, а не готовыми фактами. Модель отвечает на самых нижних
вызовах — HTTPS-запрос к адресу, приветствие TLS с выбранным именем, порт 80, —
а пробы уточнения причины (``block_cause.collect``) и все выводы работают
настоящим кодом. Новый способ блокировки, который встретился у пользователя,
добавляется строкой в ``SCENARIOS``.
"""

from __future__ import annotations

import unittest
from contextlib import ExitStack
from dataclasses import dataclass, field
from unittest.mock import patch

from test_diagnostics_verdict import DISCORD_REAL, _Net

from diagnostics import block_cause, engine
from diagnostics.block_cause import HELLO_CONNECT, HELLO_OK, HELLO_RESET, HELLO_TIMEOUT, HelloResult, HttpFacts
from diagnostics.tls_probe import KIND_CONNECT, KIND_OK, KIND_RESET, KIND_TIMEOUT, STAGE_CONNECT, STAGE_TLS, ProbeResult

HOST = "discord.com"
ORIGINAL_COLLECT = block_cause.collect


@dataclass
class Censor:
    """Что делает фильтр на дороге. Пустые правила — фильтра нет."""

    # Адреса, пакеты к которым молча пропадают: соединение не устанавливается.
    drop_ips: set[str] = field(default_factory=set)
    # Имена, приветствие с которыми сбрасывают (RST).
    reset_names: set[str] = field(default_factory=set)
    # Имена, приветствие с которыми молча глушат.
    silent_names: set[str] = field(default_factory=set)
    # Белый список имён: всё, кроме них, сбрасывается. None — списка нет.
    allowed_only: set[str] | None = None
    # Сколько байт ответа проходит до заморозки. None — не замораживают.
    freeze_after: int | None = None
    # Адреса, с которых режут любой TLS по ответу сервера — но только в пределах окна приёма клиента.
    reply_cut_ips: set[str] = field(default_factory=set)

    def _name_blocked(self, name: str | None) -> str:
        """Как поступают с приветствием: "" — пропускают, иначе вид отказа."""
        if self.allowed_only is not None and name not in self.allowed_only:
            return "reset"
        if name in self.reset_names:
            return "reset"
        if name in self.silent_names:
            return "silent"
        return ""

    def https(self, host: str, ip: str) -> ProbeResult:
        if ip in self.drop_ips:
            return ProbeResult(ip=ip, kind=KIND_CONNECT, stage=STAGE_CONNECT, connect_fail="timeout")
        if ip in self.reply_cut_ips:
            return ProbeResult(ip=ip, kind=KIND_TIMEOUT, stage=STAGE_TLS)
        blocked = self._name_blocked(host)
        if blocked == "reset":
            return ProbeResult(ip=ip, kind=KIND_RESET, stage=STAGE_TLS)
        if blocked == "silent":
            return ProbeResult(ip=ip, kind=KIND_TIMEOUT, stage=STAGE_TLS)
        if self.freeze_after is not None:
            return ProbeResult(ip=ip, kind=KIND_OK, status=200, body_cut=True, body_size=self.freeze_after)
        return ProbeResult(ip=ip, kind=KIND_OK, status=200, tls_version="TLSv1.3")

    def hello(self, ip: str, name: str | None, **_kwargs) -> HelloResult:
        if ip in self.drop_ips:
            return HelloResult(HELLO_CONNECT)
        if ip in self.reply_cut_ips:
            return HelloResult(HELLO_TIMEOUT)
        blocked = self._name_blocked(name)
        if blocked == "reset":
            return HelloResult(HELLO_RESET)
        if blocked == "silent":
            return HelloResult(HELLO_TIMEOUT)
        return HelloResult(HELLO_OK, ms=20.0)

    def window_hello(self, ip: str, name: str | None, **_kwargs) -> HelloResult:
        """Окно приёма в 1 байт: фильтр по ответу сервера не видит подписи TLS и пропускает."""
        if ip in self.reply_cut_ips:
            # Окно в 1 КБ фильтр читает и режет, в 1 байт — не видит подписи TLS.
            return HelloResult(HELLO_TIMEOUT) if _kwargs.get("window", 1) > 2 else HelloResult(HELLO_OK, ms=20.0)
        return self.hello(ip, name)

    def http(self, _host: str, ip: str, **_kwargs) -> HttpFacts:
        return HttpFacts() if ip in self.drop_ips else HttpFacts(status=301, location=f"https://{HOST}/")


@dataclass(frozen=True)
class Scenario:
    name: str
    censor: Censor
    # Ожидаемый вид блокировки сайта ("" — сайт открывается).
    kind: str
    level: str


SCENARIOS = (
    Scenario("фильтра нет", Censor(), "", "ok"),
    Scenario("сброс по имени сайта", Censor(reset_names={HOST}), "sni", "fail"),
    Scenario("молчание по имени сайта", Censor(silent_names={HOST}), "sni", "fail"),
    Scenario("белый список имён", Censor(allowed_only={"vk.com", "ya.ru", "max.ru"}), "sni", "fail"),
    Scenario("адреса сайта глушат целиком", Censor(drop_ips=set(DISCORD_REAL)), "ip", "fail"),
    Scenario("заморозка после 20 КБ", Censor(freeze_after=20_000), "cut16", "fail"),
    Scenario("TLS с адресов режут по ответу сервера", Censor(reply_cut_ips=set(DISCORD_REAL)), "tls_reply", "fail"),
)


def run_site_check(censor: Censor) -> dict:
    net = _Net(https=censor.https)
    with ExitStack() as stack:
        for item in net.patches():
            stack.enter_context(item)
        # Уточнение причины — настоящим кодом поверх модели цензора.
        stack.enter_context(patch.object(block_cause, "collect", ORIGINAL_COLLECT))
        stack.enter_context(patch.object(block_cause, "tls_hello", side_effect=censor.hello))
        stack.enter_context(patch.object(block_cause, "window_hello", side_effect=censor.window_hello))
        stack.enter_context(patch.object(block_cause, "http_probe", side_effect=censor.http))
        stack.enter_context(patch("diagnostics.net_access.ping_ok", return_value=None))
        return engine.check_site(HOST)


class CensorScenarioTests(unittest.TestCase):
    def test_each_censor_behaviour_gets_its_own_verdict(self) -> None:
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario.name):
                site = run_site_check(scenario.censor)
                self.assertIsNotNone(site)
                self.assertEqual(str(site.get("level") or ""), scenario.level)
                self.assertEqual(str(site.get("kind") or ""), scenario.kind)


if __name__ == "__main__":
    unittest.main()
