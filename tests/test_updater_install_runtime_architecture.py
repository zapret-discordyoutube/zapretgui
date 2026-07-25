import inspect
import unittest

from updater.update_page_runtime import UpdatePageRuntime
from updater.update_pipeline import UpdatePipeline


class UpdaterInstallRuntimeArchitectureTest(unittest.TestCase):
    def test_update_pipeline_uses_separate_stage_runtimes(self) -> None:
        runtime_source = inspect.getsource(UpdatePageRuntime)
        init_source = inspect.getsource(UpdatePageRuntime.__init__)
        start_source = inspect.getsource(UpdatePageRuntime._start_update_download)
        teardown_source = inspect.getsource(UpdatePageRuntime._teardown_update_runtime)

        for field in (
            "_update_preflight_runtime",
            "_update_download_runtime",
            "_update_installer_runtime",
            "_update_dpi_stop_runtime",
        ):
            self.assertIn(f"{field} = OneShotWorkerRuntime()", init_source)
            self.assertIn(field, teardown_source)

        self.assertIn("create_update_preflight_worker", start_source)
        self.assertIn("start_qobject_worker", start_source)
        self.assertNotIn("UpdateWorker", runtime_source)
        self.assertNotIn("os._exit", runtime_source)
        self.assertNotIn("QThread", runtime_source)
        self.assertIn("download_and_prepare", inspect.getsource(UpdatePipeline))

    def test_installer_success_requests_lifecycle_exit_on_main_thread(self) -> None:
        source = inspect.getsource(UpdatePageRuntime._on_update_installer_launched)
        self.assertIn("QTimer.singleShot", source)
        self.assertIn("request_exit(stop_dpi=False)", source)
        self.assertNotIn("os._exit", source)


if __name__ == "__main__":
    unittest.main()
