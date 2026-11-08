from __future__ import annotations

"""Обновление Zapret 2 GUI по файлам: выбор пути, запасной установщик, уборка, ход установки."""

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from updater.download import flow
from updater.download.contracts import CancellationToken, UpdateArtifact, UpdateCancelled
from updater.install import apply_mode, file_update
from updater.install import handoff as install_handoff
from zapret_update import share_client
from zapret_update import apply, manifest, swap


@pytest.fixture(autouse=True)
def _no_neighbour_search():
    """Тесты обновления не шлют в сеть настоящий поисковый запрос соседям."""
    quiet = lambda product, version, **_k: share_client.Search(product, version, enabled=False)  # noqa: E731
    with patch.object(share_client, "start_search", quiet):
        yield


def _manifest(paths: dict[str, str], *, zsize: int = 1) -> manifest.Manifest:
    return manifest.parse({
        "schema": 1, "product": "zapret-gui", "channel": "dev", "version": "1.0.0.2", "created": 1, "key": "k",
        "files": [{"path": p, "size": 1, "sha256": sha * 64, "zsize": zsize} for p, sha in paths.items()],
    })


def _artifact(*, zsize: int = 1) -> UpdateArtifact:
    signed = SimpleNamespace(manifest=_manifest({"_internal/Zapret.exe": "a"}, zsize=zsize), raw=b"{}", signature=b"s")
    return UpdateArtifact("1.0.0.2", "Zapret2Setup.exe", 10, "b" * 64, (), signed=signed)


class _Dpi:
    def __init__(self) -> None:
        self.running, self.stops = True, []

    def is_running(self) -> bool:
        return self.running

    def stop(self, *, reason, update_runtime_state):
        self.stops.append(reason)
        self.running = False


def _install(artifact, **overrides):
    dpi = _Dpi()
    shown = []
    options = dict(
        token=CancellationToken(), dpi=dpi, on_stage=lambda _t: None, on_progress=None, on_downloaded=lambda: None,
        splash="splash", show_splash=shown.append, start_in_tray=True,
        prepare=lambda *a, **k: Path("plan.json"), launch=lambda _p: True,
    )
    options.update(overrides)
    with patch.object(file_update, "is_available", lambda signed: bool(signed.manifest.files[0].zsize)):
        result = flow.install_by_files(artifact, **options)
    return result, dpi, shown


def test_new_version_takes_over_and_the_installer_is_not_needed() -> None:
    result, dpi, shown = _install(_artifact())

    assert result is True
    assert dpi.stops == ["updater_installer_handoff"]
    assert shown == ["splash"]


def test_release_without_a_store_goes_to_the_installer() -> None:
    prepare = Mock()

    result, dpi, _shown = _install(_artifact(zsize=0), prepare=prepare)

    assert result is False
    prepare.assert_not_called()
    assert dpi.stops == []


def test_failed_download_falls_back_to_the_installer() -> None:
    def broken(*_a, **_k):
        raise OSError("нет сети")

    result, dpi, _shown = _install(_artifact(), prepare=broken)

    assert result is False
    assert dpi.running  # DPI не останавливали: установщик пойдёт своим путём


def test_new_version_that_did_not_take_over_falls_back_to_the_installer() -> None:
    result, _dpi, shown = _install(_artifact(), launch=lambda _p: False)

    assert result is False
    assert shown == []


def test_cancel_stops_everything() -> None:
    from zapret_update.fetch import FetchCancelled

    def cancelled(*_a, **_k):
        raise FetchCancelled("стоп")

    with pytest.raises(UpdateCancelled):
        _install(_artifact(), prepare=cancelled)


def test_removal_takes_only_files_the_program_installed(tmp_path) -> None:
    for relative in ("_internal/old.pyd", "_internal/Zapret.exe", "user/settings.sqlite3", "lua/stale.lua", "lua/kept.lua"):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text("x")
    new = _manifest({"_internal/Zapret.exe": "a", "lua/kept.lua": "b"})
    installed = _manifest({"lua/stale.lua": "c", "lua/kept.lua": "b"})

    # Без списка установленной версии убирается только устаревшая поставка в
    # папках, которые установщик ставит заново целиком (_internal).
    assert file_update.removal_list(new, tmp_path, None) == ["_internal/old.pyd"]
    assert file_update.removal_list(new, tmp_path, installed) == ["_internal/old.pyd", "lua/stale.lua"]


def _interrupted(tmp_path: Path) -> Path:
    app = tmp_path / "app"
    (app / "_internal").mkdir(parents=True)
    work = app / ".update" / "1.0.0.2" / "swap"
    (work / "old" / "_internal").mkdir(parents=True)
    (work / "old" / "_internal" / "Zapret.exe").write_text("old")
    (work / swap.JOURNAL_NAME).write_text(json.dumps({
        "app_dir": str(app),
        "moves": [[str(work / "old" / "_internal" / "Zapret.exe"), str(app / "_internal" / "Zapret.exe")]],
        "created_dirs": [],
    }))
    return app


def test_swap_cut_by_power_loss_is_undone_on_next_start(tmp_path, monkeypatch) -> None:
    app = _interrupted(tmp_path)
    monkeypatch.delenv("ZAPRET_UPDATE_HOLD", raising=False)

    file_update.housekeeping(root=app, now=time.time() + 11 * 60)

    assert (app / "_internal" / "Zapret.exe").read_text() == "old"


def test_fresh_journal_of_a_running_install_is_left_alone(tmp_path, monkeypatch) -> None:
    app = _interrupted(tmp_path)
    monkeypatch.delenv("ZAPRET_UPDATE_HOLD", raising=False)

    file_update.housekeeping(root=app, now=time.time() + 60)

    assert not (app / "_internal" / "Zapret.exe").exists()


def test_old_work_folders_are_removed(tmp_path) -> None:
    app = tmp_path / "app"
    old = app / ".update" / "1.0.0.1"
    old.mkdir(parents=True)
    (old / "parts").mkdir()

    file_update.housekeeping(root=app, now=time.time() + 25 * 3600)

    assert not old.exists()


def test_folder_with_a_rollback_that_could_not_finish_is_never_removed(tmp_path, monkeypatch) -> None:
    app = _interrupted(tmp_path)
    monkeypatch.delenv("ZAPRET_UPDATE_HOLD", raising=False)
    # Файл всё ещё занят: вернуть прежний exe и сейчас не выходит.
    monkeypatch.setattr(swap.FileSwap, "_move", lambda self, s, d: (_ for _ in ()).throw(PermissionError(13, "занят")))

    file_update.housekeeping(root=app, now=time.time() + 25 * 3600)

    # Раньше через сутки папка стиралась вместе с последней копией программы.
    assert (app / ".update" / "1.0.0.2" / "swap" / "old" / "_internal" / "Zapret.exe").read_text() == "old"


def test_driver_path_under_the_program_folder() -> None:
    root = Path("C:/Zapret/Dev") if os.name == "nt" else Path("/opt/zapret/dev")
    inside = str(root / "exe" / "WinDivert64.sys")
    assert apply_mode._image_path_under("\\\\??\\\\" + inside if os.name == "nt" else inside, root)
    assert not apply_mode._image_path_under(str(root.parent / "Stable" / "exe" / "WinDivert64.sys"), root)


def test_install_progress_is_written_for_the_splash_and_the_next_start(tmp_path) -> None:
    plan = apply.ApplyPlan(
        product="Zapret 2 GUI", version="1.0.0.2", from_version="1.0.0.1", app_dir=str(tmp_path),
        tree_dir="t", work_dir=str(tmp_path), app_pid=7, app_exe="_internal/Zapret.exe",
        failure_file=str(tmp_path / "failure.txt"), error_log=str(tmp_path / "error.log"),
        blocker_file=str(tmp_path / "blocker.txt"),
    )
    written = []
    with patch.object(install_handoff, "write_record", side_effect=lambda record: written.append(record)):
        report = apply_mode._reporter(plan)
        for stage in (apply.Stage.PREPARE, apply.Stage.WAIT_APP, apply.Stage.SWAP, apply.Stage.START):
            report(stage, 0.0)
        (tmp_path / "failure.txt").write_text("lock")
        (tmp_path / "blocker.txt").write_text("msmpeng")
        (tmp_path / "error.log").write_text("Новая версия завершилась сразу после запуска (код 3)\n")
        report(apply.Stage.ROLLBACK, 0.0)
        report(apply.Stage.FAILED, 1.0)

    assert [record.state for record in written] == [
        install_handoff.HandoffState.PREPARED,
        install_handoff.HandoffState.LAUNCHED,
        install_handoff.HandoffState.SUCCEEDED,
        install_handoff.HandoffState.FAILED,
    ]
    assert (written[-1].failure, written[-1].version, written[-1].installer_path) == ("lock", "1.0.0.2", "")
    assert written[-1].blocker == "msmpeng"
    # Запись переживает запись на диск и чтение следующим запуском.
    assert install_handoff.UpdateHandoffRecord.from_payload(written[-1].to_payload()).blocker == "msmpeng"
    assert "код 3" in written[-1].error


def test_apply_mode_reports_to_the_old_version_and_releases_the_hold(tmp_path) -> None:
    plan = apply.ApplyPlan(
        product="Zapret 2 GUI", version="1.0.0.2", from_version="1.0.0.1", app_dir=str(tmp_path),
        tree_dir="t", work_dir=str(tmp_path), app_pid=7, app_exe="_internal/Zapret.exe",
    )
    plan.save(tmp_path / "plan.json")
    plan.hold_marker.write_text("hold")

    with patch.object(apply, "run_apply", return_value=True) as run, patch.object(apply_mode, "_setup_logging"):
        assert apply_mode.run_apply_mode(str(tmp_path / "plan.json")) == 0

    assert (tmp_path / "applier.ready").exists()
    assert not plan.hold_marker.exists()
    assert run.call_args.args[0] == plan


def test_new_version_arms_the_rescue_even_when_the_old_plan_did_not_know_it(tmp_path) -> None:
    plan = apply.ApplyPlan(
        product="Zapret 2 GUI", version="1.0.0.2", from_version="1.0.0.1", app_dir=str(tmp_path),
        tree_dir="t", work_dir=str(tmp_path), app_pid=7, app_exe="_internal/Zapret.exe",
    )
    plan.save(tmp_path / "plan.json")  # план прежней версии: без rescue_dir

    with patch.object(apply, "run_apply", return_value=True) as run, patch.object(apply_mode, "_setup_logging"), patch.object(
        file_update, "rescue_dir", return_value=r"C:\ProgramData\Zapret\update\dev\rescue"
    ):
        assert apply_mode.run_apply_mode(str(tmp_path / "plan.json")) == 0

    assert run.call_args.args[0].rescue_dir.endswith("rescue")


def test_prepare_builds_the_new_version_tree_from_the_store(tmp_path) -> None:
    pytest.importorskip("compression.zstd", reason="сжатие zstd есть только в Python 3.14")
    import http.server
    import threading

    from updater.release.signed import SignedRelease
    from zapret_update import builder, fetch

    root = tmp_path / "Zapret"
    old = {"_internal/Zapret.exe": os.urandom(400_000), "lua/a.lua": b"print(1)", "user/settings.sqlite3": b"mine"}
    for relative, data in old.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(data)
    new_files = {"_internal/Zapret.exe": old["_internal/Zapret.exe"][:200_000] + b"fix" + old["_internal/Zapret.exe"][200_000:], "lua/a.lua": b"print(1)"}
    source = tmp_path / "src"
    for relative, data in new_files.items():
        (source / relative).parent.mkdir(parents=True, exist_ok=True)
        (source / relative).write_bytes(data)
    first = builder.build_store(layout={"_internal/Zapret.exe": root / "_internal/Zapret.exe", "lua/a.lua": root / "lua/a.lua"}, cache_dir=tmp_path / "cache")
    previous = manifest.parse({"schema": 1, "product": "zapret-gui", "channel": "dev", "version": "1.0.0.1", "created": 1, "key": "k", "files": first.files})
    built = builder.build_store(layout={k: source / k for k in new_files}, cache_dir=tmp_path / "cache", previous=[previous])
    listed = manifest.parse({"schema": 1, "product": "zapret-gui", "channel": "dev", "version": "1.0.0.2", "created": 1, "key": "k", "files": built.files, "patches": built.patches,
                             "installer": {"name": "Zapret2Setup.exe", "size": 40, "sha256": "c" * 64}})

    stored = {path.name: path.read_bytes() for path in (tmp_path / "cache").glob("*.zst")}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_GET(self):  # noqa: N802
            data = stored.get(self.path.lstrip("/"))
            self.send_response(200 if data else 404)
            self.send_header("Content-Length", str(len(data or b"")))
            self.end_headers()
            self.wfile.write(data or b"")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    local = [fetch.Source("local", lambda name: f"http://127.0.0.1:{server.server_address[1]}/{name}")]

    with patch.object(file_update, "install_root", return_value=root), patch.object(file_update, "store_sources", return_value=local):
        plan_path = file_update.prepare(
            SignedRelease(listed, b'{"raw": 1}', b"sig"), channel="dev", current_version="1.0.0.1", start_in_tray=True
        )
    server.shutdown()

    plan = apply.ApplyPlan.load(plan_path)
    tree = Path(plan.tree_dir)
    assert (tree / "_internal" / "Zapret.exe").read_bytes() == new_files["_internal/Zapret.exe"]
    assert (tree / "system" / "update" / "installed-manifest.json").read_bytes() == b'{"raw": 1}'
    assert not (tree / "user").exists()
    assert plan.restart_args == ["--tray"] and plan.app_exe == "_internal/Zapret.exe"
    assert "user" in plan.preserve


def test_plan_summary_names_patch_kinds() -> None:
    from types import SimpleNamespace

    from updater.install import file_update
    from zapret_update import manifest, plan

    exe = manifest.FileEntry("_internal/Zapret.exe", 80, "a" * 64, 20)
    lua = manifest.FileEntry("lua/a.lua", 5, "b" * 64, 3)
    planned = plan.UpdatePlan(
        version="1.0.0.2",
        keep=[manifest.FileEntry("x", 1, "c" * 64, 1)],
        want=[plan.Want(exe, manifest.PatchEntry("d" * 64, "a" * 64, 2, "bsdiff")), plan.Want(lua)],
    )
    text = file_update.plan_summary(
        planned, SimpleNamespace(version="1.0.0.2", installer=SimpleNamespace(size=40))
    )

    assert text == (
        "Обновление по файлам до v1.0.0.2: скачать 5 байт (файлов 2, заплаток 1 (bsdiff 1), "
        "без изменений 1), установщик целиком — 40 байт"
    )


def test_version_that_failed_by_files_goes_to_the_installer_next_time(tmp_path) -> None:
    from updater.release.signed import SignedRelease

    root = tmp_path / "Zapret"
    work = root / file_update.UPDATE_DIR_NAME / "1.0.0.2"
    work.mkdir(parents=True)
    (work / file_update.FAILURE_FILE_NAME).write_text("lock", encoding="utf-8")
    listed = manifest.parse({"schema": 1, "product": "zapret-gui", "channel": "dev", "version": "1.0.0.2", "created": 1, "key": "k",
                             "files": [{"path": "a", "size": 1, "sha256": "a" * 64, "zsize": 1}]})

    with patch.object(file_update, "install_root", return_value=root), pytest.raises(file_update.FileUpdateUnavailable, match="lock"):
        file_update.prepare(SignedRelease(listed, b"{}", b"sig"), channel="dev", current_version="1.0.0.1", start_in_tray=False)


def test_engine_survives_an_update_that_does_not_touch_exe(tmp_path) -> None:
    from zapret_update import plan as update_plan

    gui = manifest.FileEntry("_internal/Zapret.exe", 80, "a" * 64, 20)
    engine = manifest.FileEntry("exe/winws2.exe", 80, "b" * 64, 20)
    only_gui = update_plan.UpdatePlan(version="1.0.0.2", want=[update_plan.Want(gui)])
    with_engine = update_plan.UpdatePlan(version="1.0.0.2", want=[update_plan.Want(gui), update_plan.Want(engine)])

    assert file_update.engine_untouched(only_gui, [])
    assert not file_update.engine_untouched(with_engine, [])
    # Убранный файл движка — тоже изменение движка.
    assert not file_update.engine_untouched(only_gui, ["EXE/old.dll"])


def test_old_version_keeps_bypass_running_when_the_engine_survives(tmp_path) -> None:
    plan = apply.ApplyPlan(
        product="Zapret 2 GUI", version="1.0.0.2", from_version="1.0.0.1", app_dir=str(tmp_path), tree_dir="t",
        work_dir=str(tmp_path), app_pid=1, app_exe="_internal/Zapret.exe", keep_running=list(file_update.ENGINE_PROCESSES),
    )
    plan_path = tmp_path / "plan.json"
    plan.save(plan_path)
    dpi = Mock()
    dpi.is_running.return_value = True
    artifact = SimpleNamespace(signed=SimpleNamespace(manifest=SimpleNamespace(version="1.0.0.2")))

    with patch.object(file_update, "is_available", return_value=True):
        started = flow.install_by_files(
            artifact, token=CancellationToken(), dpi=dpi, on_stage=lambda _t: None, on_progress=None,
            on_downloaded=lambda: None, splash=None, show_splash=lambda _s: True, start_in_tray=False,
            prepare=lambda *_a, **_k: plan_path, launch=lambda _p: True,
        )

    assert started
    dpi.stop.assert_not_called()


def test_no_promise_to_keep_bypass_when_it_is_off(tmp_path) -> None:
    plan = apply.ApplyPlan(
        product="Zapret 2 GUI", version="1.0.0.2", from_version="1.0.0.1", app_dir=str(tmp_path), tree_dir="t",
        work_dir=str(tmp_path), app_pid=1, app_exe="_internal/Zapret.exe", keep_running=list(file_update.ENGINE_PROCESSES),
    )
    plan_path = tmp_path / "plan.json"
    plan.save(plan_path)
    dpi = Mock()
    dpi.is_running.return_value = False
    artifact = SimpleNamespace(signed=SimpleNamespace(manifest=SimpleNamespace(version="1.0.0.2")))
    lines = []

    with patch.object(file_update, "is_available", return_value=True), patch.object(
        flow, "log", side_effect=lambda text, *_a, **_k: lines.append(text)
    ):
        flow.install_by_files(
            artifact, token=CancellationToken(), dpi=dpi, on_stage=lambda _t: None, on_progress=None,
            on_downloaded=lambda: None, splash=None, show_splash=lambda _s: True, start_in_tray=False,
            prepare=lambda *_a, **_k: plan_path, launch=lambda _p: True,
        )

    # Обход выключен — журнал не обещает, что «он не останавливается».
    assert not any("Обход не останавливается" in line for line in lines)
    dpi.stop.assert_not_called()


def test_new_version_leaves_the_driver_of_a_surviving_engine_alone(tmp_path) -> None:
    hooks = apply_mode._hooks_class()()
    plan = SimpleNamespace(app_dir=str(tmp_path), keep_running=["exe/winws2.exe"])

    with patch.object(apply_mode, "stop_driver_services") as stop:
        hooks.before_swap(plan)
        stop.assert_not_called()
        hooks.before_swap(SimpleNamespace(app_dir=str(tmp_path), keep_running=[]))
        stop.assert_called_once()


def test_prepare_asks_neighbours_after_the_plan_and_before_the_servers(tmp_path) -> None:
    """Порядок: поиск соседей стартует до плана, файлы берутся у соседей до сервера."""
    from updater.release.signed import SignedRelease
    from zapret_update import fetch, plan as plan_module

    calls: list[str] = []
    listed = manifest.parse({"schema": 1, "product": "zapret-gui", "channel": "dev", "version": "1.0.0.2", "created": 1,
                             "key": "k", "files": [], "installer": {"name": "Z.exe", "size": 40, "sha256": "c" * 64}})
    fake_plan = SimpleNamespace(download_size=0, want=[], keep=[], remove=[])

    def search(product, version, **_k):
        calls.append(f"search {product} {version}")
        return share_client.Search(product, version, enabled=False)

    def fill(planned, **kwargs):
        calls.append("neighbours")
        assert planned is fake_plan and kwargs["work_dir"] == tmp_path / ".update" / "1.0.0.2"

    def servers(*_a, **_k):
        calls.append("servers")
        raise RuntimeError("stop here")

    with (
        patch.object(file_update, "install_root", return_value=tmp_path),
        patch.object(share_client, "start_search", search),
        patch.object(share_client, "fill_from_neighbours", fill),
        patch.object(plan_module, "make_plan", lambda *_a, **_k: calls.append("plan") or fake_plan),
        patch.object(fetch, "fetch_plan", servers),
        patch.object(file_update, "store_sources", return_value=[]),
        patch.object(file_update, "plan_summary", return_value=""),
    ):
        with pytest.raises(RuntimeError, match="stop here"):
            file_update.prepare(SignedRelease(listed, b"{}", b"sig"), channel="dev", current_version="1.0.0.1", start_in_tray=False)

    assert calls == ["search zapret-gui 1.0.0.2", "plan", "neighbours", "servers"]
