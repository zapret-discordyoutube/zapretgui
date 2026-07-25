from __future__ import annotations


def init_launch_runtime_api(*, runtime_feature):
    from settings.mode import exe_name_for_launch_method, exe_path_for_launch_method
    from log.log import log
    from winws_runtime.runtime.status_feedback import runtime_status_callback
    from winws_runtime.runtime.runtime_api import PresetLaunchRuntimeApi

    snapshot = runtime_feature.objects.runtime_service.snapshot()
    launch_method = str(getattr(snapshot, "launch_method", "") or "").strip().lower()
    winws_exe = exe_path_for_launch_method(launch_method)
    exe_name = exe_name_for_launch_method(launch_method)
    log(f"Используется {exe_name} для режима {launch_method}", "INFO")

    runtime_api = PresetLaunchRuntimeApi(
        expected_exe_path=winws_exe,
        status_callback=runtime_status_callback(runtime_feature),
    )
    log("Launch runtime API инициализирован", "INFO")
    return runtime_api


def init_launch_runtime(*, runtime_feature, runtime_api, notify) -> None:
    from log.log import log
    from winws_runtime.runtime.launch_runtime import PresetLaunchRuntime

    launch_runtime = PresetLaunchRuntime(
        runtime_feature=runtime_feature,
        runtime_api=runtime_api,
        notify=notify,
    )
    log("Launch runtime инициализирован", "INFO")
    return launch_runtime


def init_process_monitor(*, process_monitor_manager=None, runtime_api=None, runtime_service=None) -> None:
    import time

    from log.log import log

    started_at = time.perf_counter()

    manager = process_monitor_manager
    if manager is None:
        raise RuntimeError("Process monitor manager is required")
    manager.initialize_process_monitor()

    log(f"✅ Process monitor: {(time.perf_counter() - started_at) * 1000:.0f}ms", "DEBUG")


def init_core_startup() -> None:
    import time

    from log.log import log

    started_at = time.perf_counter()

    from lists.file_manager import ensure_required_files_fast

    ensure_required_files_fast()

    log(f"✅ Core startup: {(time.perf_counter() - started_at) * 1000:.0f}ms", "DEBUG")
