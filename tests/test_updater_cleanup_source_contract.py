from __future__ import annotations

import ast
from pathlib import Path
import unittest


class UpdaterCleanupSourceContractTests(unittest.TestCase):
    def test_updater_cleanup_policy_marks_only_state_changing_workers_blocking(self) -> None:
        source_path = Path(__file__).resolve().parents[1] / "src" / "updater" / "update_page_runtime.py"
        module = ast.parse(source_path.read_text(encoding="utf-8"))
        policies = None
        for node in module.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_UPDATER_CLEANUP_RUNTIME_POLICIES":
                    policies = ast.literal_eval(node.value)
                    break
            if policies is not None:
                break

        self.assertIsNotNone(policies)
        blocking_by_name = {entry[0]: entry[2] for entry in policies}

        for name in (
            "server_worker",
            "version_worker",
            "auto_check_load_worker",
            "update_channel_open_worker",
            "cache_invalidate_worker",
            "server_check_gate_worker",
        ):
            self.assertIs(blocking_by_name[name], False)

        for name in (
            "server_retry_without_dpi_worker",
            "dpi_restart_worker",
            "auto_check_save_worker",
            "update_preflight_worker",
            "update_download_worker",
            "update_installer_worker",
            "update_dpi_stop_worker",
        ):
            self.assertIs(blocking_by_name[name], True)


if __name__ == "__main__":
    unittest.main()
