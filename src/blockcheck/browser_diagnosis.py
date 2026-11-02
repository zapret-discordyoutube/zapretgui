"""Браузер не открыл сайт, хотя проба программы открыла: найти, почему, и доказать это.

Проба программы и браузер идут к сайту по-разному. По журналу сети неудачного
открытия (``browser_netlog``) видно, чем именно браузер отличался: прятал имя
сайта (ECH), шёл на другой адрес (часто IPv6), шёл через прокси, ждал части
страницы с других имён.

Догадка не годится в совет человеку, поэтому каждое отличие проверяется делом:
сайт открывается браузером ещё раз, и меняется только одно — ECH выключен,
адрес тот, что проверила проба, прокси нет. Повтор тоже пишет журнал сети:
страница не дождалась, а соединение с сайтом прошло (шифрование договорено,
ответ пришёл), хотя в первый раз не проходило, — изменение тоже доказано.
Повтор, который ничего не сказал и журнал оставил пустым, делается ещё раз;
промолчал снова — вывода нет, и говорится именно это, а не «не помогло». Открылся — эта
причина доказана, и совет о ней. Ни одно изменение по отдельности не помогло —
пробуются все разом. Не помогло и это — так и говорится: браузер не открыл сайт
даже так.

Ключи браузера проверены на Edge 156 по его же журналу сети (win10, 2026-11-01):

* ``--host-resolver-rules=MAP сайт адрес`` ведёт браузер на заданный адрес, минуя
  DNS; ключ ECH браузер берёт из записи HTTPS в DNS, поэтому так он идёт без ECH.
  ECH отдельно выключается картой на тот же адрес, что браузер выбрал сам;
* ``--no-proxy-server`` отменяет прокси, даже заданный ключом;
* ``--disable-features=EncryptedClientHello`` и ``=DnsOverHttps`` в Edge 156 ничего
  не меняют (в журнале тот же ECH и тот же шифрованный DNS) — ими не пользуемся. Здесь нет запуска браузера: открытие передаётся
снаружи (``visit``), поэтому вывод проверяется без браузера.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

__all__ = [
    "DIAG_BROWSER_CUT",
    "DIAG_FIXED",
    "DIAG_PARTS",
    "DIAG_UNCLEAR",
    "FIX_ADDRESS",
    "FIX_ECH",
    "FIX_PROXY",
    "Diagnosis",
    "browser_args",
    "diagnose",
    "suspects",
]

# Что можно изменить в следующем открытии.
FIX_ECH = "ech"
FIX_ADDRESS = "address"
FIX_PROXY = "proxy"
# Порядок проверки: чаще всего мешает ECH, затем другой адрес, прокси.
FIX_ORDER = (FIX_ECH, FIX_ADDRESS, FIX_PROXY)

# Итоги.
DIAG_FIXED = "fixed"
DIAG_PARTS = "parts"
DIAG_BROWSER_CUT = "browser_cut"
# Браузер на повторы не отвечал (ни страницы, ни ошибки, пустой журнал): причину найти не вышло.
DIAG_UNCLEAR = "unclear"

# Итоги одного повтора.
_OPENED = "opened"
_FAILED = "failed"
_SILENT = "silent"



@dataclass(frozen=True, slots=True)
class Diagnosis:
    code: str
    # Изменения, с которыми сайт открылся (DIAG_FIXED).
    fixes: tuple[str, ...] = ()
    # Что пробовали и чем кончилось: (изменения, итог открытия) — для журнала.
    tried: tuple[tuple[tuple[str, ...], str], ...] = ()
    # Факты для текста: куда шёл браузер сам, через какой прокси, какие имена страница не дождалась.
    address: str = ""
    probe_address: str = ""
    proxy: str = ""
    hosts: tuple[str, ...] = ()
    # Браузер спрашивал адрес сайта у своего шифрованного DNS (по журналу сети).
    secure_dns: bool = False
    # Итог последнего открытия — что сказать, если причину не нашли.
    reason: str = ""

    @property
    def tried_fixes(self) -> tuple[str, ...]:
        """Все изменения, которые пробовали, — по порядку, без повторов."""
        return tuple(fix for fix in FIX_ORDER if any(fix in fixes for fixes, _state in self.tried))


def browser_args(fixes, *, host: str, probe_address: str, own_address: str = "") -> tuple[str, ...]:
    """Ключи браузера для набора изменений.

    Адрес задаётся картой мимо DNS — тогда и ECH выключен (ключ ECH приходит из DNS). Поэтому «только без ECH» —
    карта на тот же адрес, что браузер выбрал сам, а «адрес пробы» уже включает «без ECH».
    """
    args: list[str] = []
    target = probe_address if FIX_ADDRESS in fixes else own_address if FIX_ECH in fixes else ""
    if target:
        mapped = f"[{target}]" if ":" in target else target
        args.append(f"--host-resolver-rules=MAP {host} {mapped}")
    if FIX_PROXY in fixes:
        args.append("--no-proxy-server")
    return tuple(args)


def suspects(net, host: str, probe_addresses) -> tuple[str, ...]:
    """Чем браузер отличался от пробы — по журналу сети. Нет журнала — подозреваем всё.

    Пустой журнал — тоже «нет журнала»: зависший браузер гасится, и журнал остаётся недописанным.
    """
    connections = net.for_host(host) if net is not None else ()
    if not connections and (net is None or host not in net.dns_errors):
        # Без журнала ECH отдельно не выключить (нужен адрес, выбранный браузером): его покрывает адрес пробы.
        return (FIX_ADDRESS, FIX_PROXY)
    probe = set(probe_addresses)
    found = []
    if any(connection.ech for connection in connections):
        found.append(FIX_ECH)
    if probe and (host in net.dns_errors or any(connection.address not in probe for connection in connections)):
        found.append(FIX_ADDRESS)
    if host in net.proxies:
        found.append(FIX_PROXY)
    return tuple(found)


def diagnose(
    host: str,
    first,
    *,
    probe_addresses,
    visit: Callable[[tuple[str, ...]], object],
    cancelled: Callable[[], bool] = lambda: False,
) -> Diagnosis:
    """Почему браузер не открыл сайт. ``first`` — неудачное открытие с журналом сети;
    ``visit(args)`` — открыть сайт ещё раз с этими ключами (тоже с журналом сети).
    """
    net = getattr(first, "net", None)
    probe_address = next(iter(probe_addresses), "")
    connections = net.for_host(host) if net is not None else ()
    own = next((connection.address for connection in connections if connection.address not in set(probe_addresses)), "")
    if not own and connections:
        own = connections[-1].address
    proxy = net.proxies.get(host, "") if net is not None else ""
    secure_dns = bool(net is not None and host in net.secure_dns)
    facts = {"address": own, "probe_address": probe_address, "proxy": proxy, "secure_dns": secure_dns, "reason": getattr(first, "text", "")}

    # До сайта браузер достучался, а страница ждала другие имена — меняя путь к сайту, не помочь.
    others = net.failed_other_hosts(host) if net is not None else ()
    if others and any(connection.ok and connection.received for connection in connections):
        return Diagnosis(DIAG_PARTS, hosts=others, **facts)

    tried: list[tuple[tuple[str, ...], str]] = []
    ech_address = next((connection.address for connection in connections if connection.ech), "")
    # В первый раз соединение с сайтом прошло — тогда «прошло и сейчас» ничего не доказывает.
    first_connected = any(connection.ok and connection.received for connection in connections)

    def outcome(result) -> str:
        if result.ok:
            return _OPENED
        result_net = getattr(result, "net", None)
        mine = result_net.for_host(host) if result_net is not None else ()
        if not first_connected and any(connection.ok and connection.received for connection in mine):
            return _OPENED
        if mine or getattr(result, "state", "") != "no_answer":
            return _FAILED
        return _SILENT

    def attempt(fixes: tuple[str, ...]) -> str:
        if (FIX_ADDRESS in fixes and not probe_address) or (fixes == (FIX_ECH,) and not ech_address):
            return ""
        args = browser_args(fixes, host=host, probe_address=probe_address, own_address=ech_address)
        result = None
        for _repeat in range(2):
            if cancelled():
                return ""
            result = visit(args)
            found = outcome(result)
            if found != _SILENT:
                break
        tried.append((fixes, found))
        if found == _FAILED and getattr(result, "text", "") and not facts["reason"]:
            facts["reason"] = result.text
        return found

    candidates = suspects(net, host, probe_addresses)
    for fix in candidates:
        if attempt((fix,)) == _OPENED:
            return Diagnosis(DIAG_FIXED, (fix,), tuple(tried), **facts)
    # По отдельности не помогло — всё разом: так браузер идёт к сайту почти как проба.
    together = tuple(fix for fix in FIX_ORDER if fix != FIX_ADDRESS or probe_address)
    if together and (len(candidates) != 1 or together != candidates) and attempt(together) == _OPENED:
        return Diagnosis(DIAG_FIXED, together, tuple(tried), **facts)
    if any(found == _SILENT for _fixes, found in tried) or not tried:
        return Diagnosis(DIAG_UNCLEAR, (), tuple(tried), **facts)
    return Diagnosis(DIAG_BROWSER_CUT, (), tuple(tried), **facts)
