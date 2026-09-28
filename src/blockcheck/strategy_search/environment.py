"""Настоящее окружение подбора: winws2, WinDivert, сеть, настройки.

Движок (``engine``) работает только через этот класс, а тесты подставляют
вместо него свою версию без сети и процессов.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Sequence

from blockcheck.strategy_search import probes
from blockcheck.strategy_search.engine import STOP_WINWS, ScanFatal
from blockcheck.strategy_search.ordering import Candidate
from blockcheck.strategy_search.winws_session import WinwsSession

logger = logging.getLogger(__name__)

# Временный конфиг пробы лежит в корне программы, рядом с exe.
PROBE_CONFIG_NAME = "blockcheck_probe.txt"
# Прошлые версии подбора оставляли эти файлы; убираем, если найдутся.
_STALE_PROBE_FILES = (PROBE_CONFIG_NAME, "blockcheck_probe_hosts.txt", "blockcheck_probe_games_ipset.txt")

# При активном Kaspersky частые циклы старт/стоп winws2 провоцируют гонку
# драйверов (WinDivert против kl*.sys) в ядре — даём фильтрам время на разрядку.
KASPERSKY_STRATEGY_COOLDOWN_SECONDS = 2.0


def strategy_pause_seconds() -> float:
    """Пауза между стратегиями: без Kaspersky не нужна, с ним — обязательна."""
    try:
        from utils.antivirus_probe import is_kaspersky_present

        if is_kaspersky_present():
            return KASPERSKY_STRATEGY_COOLDOWN_SECONDS
    except Exception as error:
        logger.debug("Kaspersky probe failed, using cooldown: %s", error)
        return KASPERSKY_STRATEGY_COOLDOWN_SECONDS
    return 0.0


class RealEnvironment:
    def __init__(
        self,
        *,
        shutdown_sync: Callable[..., object],
        load_fakes_catalog: Callable[[], object] | None = None,
        log: Callable[[str], None] = lambda _message: None,
    ) -> None:
        from config.runtime_layout import APPLICATION_PATHS
        from diagnostics.tls_probe import ProbeCancel

        self._shutdown_sync = shutdown_sync
        self._load_fakes_catalog = load_fakes_catalog
        self._log = log
        self._root = str(APPLICATION_PATHS.root)
        self._cancel = ProbeCancel()
        self._fakes_catalog = None
        self._fakes_error = ""
        self._fakes_loaded = False
        self._games_paths: dict[str, list[str]] = {}
        self._needs_readiness_check = True
        self._pause: float | None = None
        self._winws2 = ""

    # --- Каталог, история, фейки ------------------------------------------------

    def load_candidates(self, scan_protocol: str) -> list[Candidate]:
        from config.runtime_layout import APPLICATION_PATHS
        from core.paths import AppPaths
        from profile.strategy_catalog import load_strategy_catalogs
        from settings.mode import ENGINE_WINWS2

        catalog_name = {"stun_voice": "voice", "udp_games": "udp"}.get(scan_protocol, "tcp")
        app_paths = AppPaths(user_root=APPLICATION_PATHS.root, local_root=APPLICATION_PATHS.root)
        entries = load_strategy_catalogs(app_paths, ENGINE_WINWS2).get(catalog_name, {})
        return [
            Candidate(strategy_id, getattr(entry, "name", "") or strategy_id, getattr(entry, "args", "") or "")
            for strategy_id, entry in entries.items()
        ]

    def load_history(self, key: str):
        from blockcheck.strategy_search.history import load_target_history

        return load_target_history(key)

    def record_history(self, key: str, *, confirmed: list[str], failed: list[str]) -> None:
        from blockcheck.strategy_search.history import record_results

        record_results(key, confirmed=confirmed, failed=failed, now=time.time())

    def blob_lines(self, strategy_args: str) -> list[str]:
        """Строки ``--blob=`` для фейков стратегии: реестр читается один раз."""
        from profile.preset_blob_declarations import plan_blob_declaration_lines

        if not self._fakes_loaded:
            self._fakes_loaded = True
            if self._load_fakes_catalog is None:
                self._fakes_error = "реестр фейков не подключён"
            else:
                try:
                    self._fakes_catalog = self._load_fakes_catalog()
                except Exception as error:
                    self._fakes_error = str(error) or type(error).__name__
                    self._log(f"Реестр фейков недоступен: {self._fakes_error}")

        def _catalog():
            if self._fakes_catalog is None:
                raise RuntimeError(self._fakes_error or "реестр фейков недоступен")
            return self._fakes_catalog

        lines, report = plan_blob_declaration_lines(str(strategy_args or "").split("\n"), {}, _catalog)
        if report.unknown and not report.catalog_error:
            self._log(f"  фейки без объявления: {', '.join(report.unknown)}")
        return lines

    def games_ipset_paths(self, udp_games_scope: str) -> list[str]:
        from blockcheck.strategy_scan_targeting import resolve_games_ipset_paths

        if udp_games_scope not in self._games_paths:
            self._games_paths[udp_games_scope] = resolve_games_ipset_paths(udp_games_scope)
        return self._games_paths[udp_games_scope]

    # --- Запуск и чистка ----------------------------------------------------------

    def pre_cleanup(self) -> None:
        """Остановить Zapret и почистить WinDivert, чтобы ничего не мешало замерам."""
        from winws_runtime.runtime.scan_guard import mark_external_winws_scan_active

        # Подбор сам запускает и гасит winws2: окно программы не должно
        # считать это падением Zapret и перезапускать его.
        mark_external_winws_scan_active(True)
        self._remove_probe_files()
        result = self._shutdown_sync(reason="blockcheck_pre_scan", include_cleanup=True)
        if getattr(result, "still_running", False):
            raise ScanFatal("Не удалось остановить Zapret перед подбором. Остановите его вручную и повторите.", STOP_WINWS)
        self._needs_readiness_check = True

    def post_cleanup(self) -> None:
        from winws_runtime.runtime.scan_guard import mark_external_winws_scan_active

        try:
            self._shutdown_sync(reason="blockcheck_post_scan", include_cleanup=True)
        except Exception:
            logger.exception("post-scan shutdown failed")
        finally:
            self._remove_probe_files()
            mark_external_winws_scan_active(False)

    def _remove_probe_files(self) -> None:
        for name in _STALE_PROBE_FILES:
            path = os.path.join(self._root, name)
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass

    def _winws2_path(self) -> str:
        if not self._winws2:
            from settings.mode import EXE_NAME_WINWS2, ZAPRET2_MODE, exe_path_for_launch_method

            path = exe_path_for_launch_method(ZAPRET2_MODE)
            if not os.path.exists(path):
                raise ScanFatal(f"Не найден {EXE_NAME_WINWS2}: {path}. Переустановите программу.", STOP_WINWS)
            self._winws2 = path
        return self._winws2

    def start_session(self, config_text: str) -> WinwsSession:
        from winws_runtime.runtime.scan_guard import mark_external_winws_scan_active

        # Флаг «идёт подбор» живёт ограниченное время; полный подбор бывает
        # дольше, поэтому каждая стратегия продлевает его.
        mark_external_winws_scan_active(True)
        exe = self._winws2_path()
        if self._needs_readiness_check:
            self._ensure_windivert_ready()
            self._needs_readiness_check = False
        # Конфиг со своим содержимым — правка пресета здесь невозможна: это
        # временный @config пробы (preset_contract.GENERATED_CONFIG_EXEMPTIONS).
        path = os.path.join(self._root, PROBE_CONFIG_NAME)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(config_text)
        return _ManagedSession(WinwsSession([exe, f"@{path}"], cwd=self._root), self)

    def _on_stop_failed(self) -> None:
        self._log("  winws2 не завершился — принудительная остановка")
        try:
            self._shutdown_sync(reason="blockcheck_kill_failed", include_cleanup=True)
        except Exception:
            logger.debug("forced shutdown after failed stop", exc_info=True)
        self._needs_readiness_check = True

    def recover_after_crash(self) -> None:
        from winws_runtime.runtime.system_ops import standard_windivert_cleanup_runtime

        try:
            standard_windivert_cleanup_runtime(sleep_seconds=max(0.8, self.strategy_pause_seconds()))
        except Exception:
            logger.debug("WinDivert cleanup after crash failed", exc_info=True)
        self._needs_readiness_check = True

    def _ensure_windivert_ready(self) -> None:
        """WinDivert готов к запуску? Ошибка 1058 (служба отключена) — стоп подбора."""
        from winws_runtime.health.windivert_diagnostics import describe_windivert_readiness_failure
        from winws_runtime.runtime.system_ops import (
            aggressive_windivert_cleanup_runtime,
            wait_for_windivert_spawn_ready_runtime,
        )

        try:
            probe = wait_for_windivert_spawn_ready_runtime(max_wait_seconds=3.0, poll_interval=0.25)
        except Exception:
            logger.debug("WinDivert readiness probe failed", exc_info=True)
            return
        if getattr(probe, "ready", False):
            return
        self._log(f"  WinDivert не готов (ошибка {getattr(probe, 'error_code', None)}), чищу...")
        try:
            aggressive_windivert_cleanup_runtime()
            probe = wait_for_windivert_spawn_ready_runtime(max_wait_seconds=5.0, poll_interval=0.25)
        except Exception:
            logger.debug("WinDivert recovery failed", exc_info=True)
        if getattr(probe, "ready", False):
            self._log("  WinDivert готов после чистки")
            return
        description = describe_windivert_readiness_failure(probe)
        if int(getattr(probe, "error_code", 0) or 0) == 1058:
            raise ScanFatal(description, STOP_WINWS)
        self._log(f"  {description}")

    def strategy_pause_seconds(self) -> float:
        if self._pause is None:
            self._pause = strategy_pause_seconds()
        return self._pause

    # --- Сеть -------------------------------------------------------------------------

    def control_alive(self) -> bool:
        return probes.control_alive(cancel=self._cancel)

    def resolve(self, host: str, port: int, *, udp: bool) -> tuple[list[str], str]:
        return probes.resolve_target_addresses(host, port, udp=udp)

    def probe_https(self, host: str, addresses: Sequence[str]):
        return probes.probe_https_addresses(host, addresses, cancel=self._cancel)

    def probe_udp(self, specs):
        return probes.probe_udp_pool(specs)

    def tcp_port_open(self, address: str, port: int) -> bool:
        return probes.tcp_port_open(address, port)

    def cancel_probes(self) -> None:
        self._cancel.cancel()

    # --- Время ---------------------------------------------------------------------------

    def monotonic(self) -> float:
        return time.monotonic()

    def wall_time(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, float(seconds)))


class _ManagedSession:
    """Сессия, которая при неудачной остановке зовёт полную остановку программы."""

    def __init__(self, session: WinwsSession, env: RealEnvironment) -> None:
        self._session = session
        self._env = env

    def start(self):
        return self._session.start()

    def alive(self) -> bool:
        return self._session.alive()

    def output_tail(self, lines: int = 6) -> str:
        return self._session.output_tail(lines)

    def stop(self) -> bool:
        stopped = self._session.stop()
        if not stopped:
            self._env._on_stop_failed()
        return stopped
