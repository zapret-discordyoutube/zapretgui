from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class BlockcheckFeature:
    presets_feature: Any
    profile_feature: Any
    # Гео-сайты из каталога hosts (сервисы, которые сами закрыты для России):
    # им помогает hosts или DNS, а не стратегия. Читает файл — звать в фоне.
    load_geo_sites: Callable[[], Any] | None = None

    @staticmethod
    def _commands():
        import blockcheck.public as blockcheck_commands

        return blockcheck_commands

    @staticmethod
    def _worker_commands():
        from blockcheck import commands as blockcheck_worker_commands

        return blockcheck_worker_commands

    def create_blockcheck_worker(self, **kwargs):
        from blockcheck.worker import BlockcheckWorker

        return BlockcheckWorker(
            report_path=self.make_blockcheck_report_path,
            save_report=self.save_blockcheck_report,
            load_geo_sites=self.load_geo_sites,
            remember_run=self.remember_blockcheck_run,
            check_dns_servers=self.check_dns_servers,
            **kwargs,
        )

    def create_strategy_scan_worker(self, *, shutdown_sync, **kwargs):
        from blockcheck.strategy_scan_worker import StrategyScanWorker

        return StrategyScanWorker(
            shutdown_sync=shutdown_sync,
            start_run_log=self.start_strategy_scan_run_log,
            append_run_log=self.append_strategy_scan_run_log,
            close_run_log=self.close_strategy_scan_run_log,
            load_fakes_catalog=self.profile_feature.load_fakes_catalog,
            **kwargs,
        )

    def create_strategy_apply_worker(self, request_id: int, **kwargs):
        from blockcheck.strategy_apply_worker import StrategyApplyWorker

        return StrategyApplyWorker(request_id, apply_strategy=self.apply_strategy, **kwargs)

    def create_page_initial_state_worker(self, request_id: int, *, parent=None):
        from blockcheck.workers import BlockcheckInitialStateWorker

        return BlockcheckInitialStateWorker(
            request_id,
            load_page_initial_state=self.load_page_initial_state,
            parent=parent,
        )

    def create_blockcheck_support_prepare_worker(self, request_id: int, **kwargs):
        from blockcheck.workers import BlockcheckSupportPrepareWorker

        return BlockcheckSupportPrepareWorker(
            request_id,
            prepare_support=self.prepare_support,
            **kwargs,
        )

    def create_user_domain_action_worker(self, request_id: int, **kwargs):
        from blockcheck.workers import BlockcheckUserDomainActionWorker

        return BlockcheckUserDomainActionWorker(
            request_id,
            run_user_domain_action=self.run_user_domain_action,
            **kwargs,
        )

    def create_strategy_scan_support_prepare_worker(self, request_id: int, **kwargs):
        from blockcheck.workers import StrategyScanSupportPrepareWorker

        return StrategyScanSupportPrepareWorker(
            request_id,
            prepare_strategy_scan_support=self.prepare_strategy_scan_support,
            **kwargs,
        )

    def create_strategy_scan_quick_targets_worker(self, request_id: int, **kwargs):
        from blockcheck.workers import StrategyScanQuickTargetsWorker

        return StrategyScanQuickTargetsWorker(
            request_id,
            build_quick_target_menu_plan=self.build_quick_target_menu_plan,
            **kwargs,
        )

    def create_geo_sites_worker(self, request_id: int, *, parent=None):
        from blockcheck.workers import GeoSitesWorker

        return GeoSitesWorker(request_id, load_geo_sites=self.load_geo_sites, parent=parent)

    def create_strategy_scan_finalize_worker(self, request_id: int, **kwargs):
        from blockcheck.workers import StrategyScanFinalizeWorker

        return StrategyScanFinalizeWorker(
            request_id,
            finalize_scan_report=self.finalize_scan_report,
            **kwargs,
        )

    def check_dns_servers(self, *args, **kwargs):
        return self._worker_commands().check_dns_servers(*args, **kwargs)

    def remember_blockcheck_run(self, report, log_file):
        return self._worker_commands().remember_blockcheck_run(report, log_file, preset=self._selected_preset_name())

    def _selected_preset_name(self) -> str:
        """Название выбранного пресета — чтобы сравнение «с Zapret и без» называло, что проверялось."""
        try:
            from settings.dpi.launch_method import get_current_launch_method

            return str(self.presets_feature.get_selected_source_preset_display(get_current_launch_method(default=""))[0])
        except Exception:
            return ""

    def load_past_blockcheck_report(self, *args, **kwargs):
        return self._worker_commands().load_past_blockcheck_report(*args, **kwargs)

    def load_page_initial_state(self, *args, **kwargs):
        return self._worker_commands().load_page_initial_state(*args, **kwargs)

    def append_run_log(self, *args, **kwargs) -> None:
        return self._commands().append_run_log(*args, **kwargs)

    def start_run_log(self, *args, **kwargs):
        return self._commands().start_run_log(*args, **kwargs)

    def make_blockcheck_report_path(self, *args, **kwargs) -> str:
        return self._worker_commands().make_blockcheck_report_path(*args, **kwargs)

    def save_blockcheck_report(self, *args, **kwargs) -> str:
        return self._worker_commands().save_blockcheck_report(*args, **kwargs)

    def start_strategy_scan_run_log(self, *args, **kwargs):
        return self._worker_commands().start_strategy_scan_run_log(*args, **kwargs)

    def append_strategy_scan_run_log(self, *args, **kwargs) -> None:
        return self._worker_commands().append_strategy_scan_run_log(*args, **kwargs)

    def close_strategy_scan_run_log(self, *args, **kwargs) -> None:
        return self._worker_commands().close_strategy_scan_run_log(*args, **kwargs)

    def prepare_support(self, *args, **kwargs):
        return self._worker_commands().prepare_support(*args, **kwargs)

    def run_user_domain_action(self, *args, **kwargs):
        return self._worker_commands().run_user_domain_action(*args, **kwargs)

    def prepare_strategy_scan_support(self, *args, **kwargs):
        return self._worker_commands().prepare_strategy_scan_support(*args, **kwargs)

    def apply_strategy(self, **kwargs):
        return self._commands().apply_strategy(
            profile_feature=self.profile_feature,
            **kwargs,
        )

    def build_selection_state(self, *args, **kwargs):
        return self._commands().build_selection_state(*args, **kwargs)

    def build_protocol_ui_plan(self, *args, **kwargs):
        return self._commands().build_protocol_ui_plan(*args, **kwargs)

    def build_udp_scope_hint_plan(self, *args, **kwargs):
        return self._commands().build_udp_scope_hint_plan(*args, **kwargs)

    def build_quick_target_menu_plan(self, *args, **kwargs):
        return self._commands().build_quick_target_menu_plan(*args, **kwargs)

    def plan_scan_start(self, *args, **kwargs):
        return self._commands().plan_scan_start(*args, **kwargs)

    def count_resumable_strategies(self, *args, **kwargs):
        return self._commands().count_resumable_strategies(*args, **kwargs)

    def build_running_interaction_plan(self, *args, **kwargs):
        return self._commands().build_running_interaction_plan(*args, **kwargs)

    def build_idle_interaction_plan(self, *args, **kwargs):
        return self._commands().build_idle_interaction_plan(*args, **kwargs)

    def build_progress_plan(self, *args, **kwargs):
        return self._commands().build_progress_plan(*args, **kwargs)

    def build_result_presentation(self, *args, **kwargs):
        return self._commands().build_result_presentation(*args, **kwargs)

    def finalize_scan_report(self, *args, **kwargs):
        return self._commands().finalize_scan_report(*args, **kwargs)

    def build_finish_notification_plan(self, *args, **kwargs):
        return self._commands().build_finish_notification_plan(*args, **kwargs)

    def build_apply_success_plan(self, *args, **kwargs):
        return self._commands().build_apply_success_plan(*args, **kwargs)

    def build_apply_error_plan(self, *args, **kwargs):
        return self._commands().build_apply_error_plan(*args, **kwargs)

    def build_language_plan(self, *args, **kwargs):
        return self._commands().build_language_plan(*args, **kwargs)

    def build_support_context(self, *args, **kwargs):
        return self._commands().build_support_context(*args, **kwargs)

    def build_support_success_plan(self, *args, **kwargs):
        return self._commands().build_support_success_plan(*args, **kwargs)

    def build_support_error_plan(self, *args, **kwargs):
        return self._commands().build_support_error_plan(*args, **kwargs)
