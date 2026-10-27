from __future__ import annotations

"""Общее ядро: план, скачивание из хранилища с докачкой и пробное дерево (Python 3.14)."""

import hashlib
import http.server
import random
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("compression.zstd", reason="сжатие zstd есть только в Python 3.14")

from zapret_update import builder, fetch, manifest, plan, stage, store  # noqa: E402


class _Store(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        files: dict[str, bytes],
        *,
        cut_after: int | None = None,
        corrupt: bool = False,
        hang: bool = False,
        hang_after: int | None = None,
    ) -> None:
        self.files, self.cut_after, self.corrupt, self.requests = files, cut_after, corrupt, []
        # hang — принимает запрос и молчит; hang_after — отдаёт столько байт тела и молчит.
        self.hang, self.hang_after, self.released = hang, hang_after, threading.Event()
        super().__init__(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def shutdown(self) -> None:
        self.released.set()
        super().shutdown()

    def url_for(self, name: str) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/{name}"


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        server: _Store = self.server
        name = self.path.lstrip("/")
        server.requests.append((name, self.headers.get("Range")))
        if server.hang:
            server.released.wait(30)
            return
        data = server.files.get(name)
        if data is None:
            self.send_response(404)
            self.end_headers()
            return
        if server.corrupt:
            data = bytes(len(data))
        start = 0
        if self.headers.get("Range"):
            start = int(self.headers["Range"].split("=")[1].rstrip("-"))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            self.send_response(200)
        body = data[start:]
        if server.cut_after is not None:
            body = body[: server.cut_after]
            server.cut_after = None  # обрыв только один раз
        self.send_header("Content-Length", str(len(data) - start))
        self.end_headers()
        try:
            if server.hang_after is not None:
                self.wfile.write(body[: server.hang_after])
                self.wfile.flush()
                server.released.wait(30)
                return
            self.wfile.write(body)
        except OSError:
            pass


def _release(tmp_path: Path):
    """Установленная версия 1 и выпуск 2: один файл изменился, один новый, один ушёл."""
    rng = random.Random(7)
    exe_old = rng.randbytes(600_000)
    exe_new = exe_old[:300_000] + b"fix" * 50 + exe_old[300_000:]
    v1 = {"_internal/Zapret.exe": exe_old, "lua/a.lua": b"print(1)", "lua/old.lua": b"old"}
    v2 = {"_internal/Zapret.exe": exe_new, "lua/a.lua": b"print(1)", "lua/new.lua": b"new"}
    root = tmp_path / "app"
    builds = {}
    for version, files in (("1.0.0.1", v1), ("1.0.0.2", v2)):
        source = tmp_path / f"src{version}"
        for relative, data in files.items():
            (source / relative).parent.mkdir(parents=True, exist_ok=True)
            (source / relative).write_bytes(data)
        previous = [builds["1.0.0.1"]] if builds else []
        built = builder.build_store(
            layout={relative: source / relative for relative in files}, cache_dir=tmp_path / "cache", previous=previous
        )
        builds[version] = manifest.parse({
            "schema": 1, "product": "zapret-gui", "channel": "dev", "version": version, "created": 1, "key": "k",
            "files": built.files, "patches": built.patches,
        })
    for relative, data in v1.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(data)
    stored = {path.name: path.read_bytes() for path in (tmp_path / "cache").glob("*.zst")}
    return root, builds["1.0.0.1"], builds["1.0.0.2"], stored, v2


def test_plan_keeps_same_files_patches_changed_and_removes_gone(tmp_path) -> None:
    root, old, new, _stored, _v2 = _release(tmp_path)

    planned = plan.make_plan(new, root, installed=old)

    assert [entry.path for entry in planned.keep] == ["lua/a.lua"]
    by_path = {want.entry.path: want for want in planned.want}
    assert by_path["_internal/Zapret.exe"].patch is not None
    assert by_path["lua/new.lua"].patch is None
    assert planned.remove == ["lua/old.lua"]
    assert planned.download_size < 20_000  # вместо ~600 КБ файла целиком


def test_plan_without_knowing_the_installed_version_removes_nothing(tmp_path) -> None:
    root, _old, new, _stored, _v2 = _release(tmp_path)

    assert plan.make_plan(new, root).remove == []


def test_version_without_store_cannot_be_planned_by_files(tmp_path) -> None:
    root, _old, new, _stored, _v2 = _release(tmp_path)
    bare = manifest.parse({
        "schema": 1, "product": "zapret-gui", "channel": "dev", "version": "1.0.0.3", "created": 1, "key": "k",
        "files": [{"path": "a", "size": 1, "sha256": "a" * 64}],
    })

    with pytest.raises(plan.PlanError):
        plan.make_plan(bare, root)


def test_fetch_resumes_after_a_cut_and_builds_the_new_tree(tmp_path) -> None:
    root, old, new, stored, v2 = _release(tmp_path)
    server = _Store(stored, cut_after=3)
    planned = plan.make_plan(new, root, installed=old)
    seen = []

    ready = fetch.fetch_plan(
        planned, root=root, work_dir=tmp_path / "work", threads=2,
        sources=[fetch.Source("forgejo", server.url_for)], progress=lambda done, total: seen.append((done, total)),
        sleep=lambda _s: None,
    )
    tree = stage.build_stage(planned, root=root, ready=ready, stage_dir=tmp_path / "stage")

    assert {path.relative_to(tree).as_posix(): path.read_bytes() for path in tree.rglob("*") if path.is_file()} == v2
    # Оборванный кусок продолжен с места обрыва, а не заново.
    assert any(rng and rng != "bytes=0-" for _name, rng in server.requests)
    assert seen[-1] == (planned.download_size, planned.download_size)
    server.shutdown()


def test_broken_source_is_skipped_for_the_next_one(tmp_path) -> None:
    root, old, new, stored, v2 = _release(tmp_path)
    bad, good = _Store(stored, corrupt=True), _Store(stored)
    planned = plan.make_plan(new, root, installed=old)

    ready = fetch.fetch_plan(
        planned, root=root, work_dir=tmp_path / "work",
        sources=[fetch.Source("bad", bad.url_for), fetch.Source("good", good.url_for)], sleep=lambda _s: None,
    )

    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == sha for sha, path in ready.items())
    bad.shutdown(), good.shutdown()


def test_nothing_anywhere_gives_a_network_reason(tmp_path) -> None:
    root, old, new, _stored, _v2 = _release(tmp_path)
    empty = _Store({})
    planned = plan.make_plan(new, root, installed=old)

    with pytest.raises(fetch.FetchError) as caught:
        fetch.fetch_plan(planned, root=root, work_dir=tmp_path / "work", sources=[fetch.Source("e", empty.url_for)], sleep=lambda _s: None)

    assert caught.value.code == "net"
    empty.shutdown()


def test_already_downloaded_files_are_not_fetched_again(tmp_path) -> None:
    root, old, new, stored, _v2 = _release(tmp_path)
    server = _Store(stored)
    planned = plan.make_plan(new, root, installed=old)
    kwargs = dict(root=root, work_dir=tmp_path / "work", sources=[fetch.Source("s", server.url_for)], sleep=lambda _s: None)
    fetch.fetch_plan(planned, **kwargs)
    server.requests.clear()

    fetch.fetch_plan(planned, **kwargs)

    assert server.requests == []
    server.shutdown()


def test_stage_refuses_a_program_file_changed_during_the_update(tmp_path) -> None:
    root, old, new, stored, _v2 = _release(tmp_path)
    server = _Store(stored)
    planned = plan.make_plan(new, root, installed=old)
    ready = fetch.fetch_plan(planned, root=root, work_dir=tmp_path / "w", sources=[fetch.Source("s", server.url_for)], sleep=lambda _s: None)
    (root / "lua" / "a.lua").write_bytes(b"print(2)")  # тот же размер, другое содержимое

    with pytest.raises(stage.StageError):
        stage.build_stage(planned, root=root, ready=ready, stage_dir=tmp_path / "stage")
    server.shutdown()


def _many_new_files(tmp_path: Path, count: int):
    """Выпуск из ``count`` новых файлов: каждый качается отдельным запросом."""
    rng = random.Random(11)
    files = {f"lua/f{index}.lua": rng.randbytes(3_000) for index in range(count)}
    source = tmp_path / "src"
    for relative, data in files.items():
        (source / relative).parent.mkdir(parents=True, exist_ok=True)
        (source / relative).write_bytes(data)
    built = builder.build_store(layout={relative: source / relative for relative in files}, cache_dir=tmp_path / "cache")
    release = manifest.parse({
        "schema": 1, "product": "zapret-gui", "channel": "dev", "version": "1.0.0.2", "created": 1, "key": "k",
        "files": built.files, "patches": built.patches,
    })
    root = tmp_path / "app"
    root.mkdir()
    stored = {path.name: path.read_bytes() for path in (tmp_path / "cache").glob("*.zst")}
    return root, release, stored


def test_a_silent_source_is_dropped_for_all_files_after_one_wait(tmp_path) -> None:
    # Выход 21.1.7.207: перегруженный Forgejo молчал 30+ секунд на каждый файл,
    # и каждый файл ждал его до обрыва. Зависший источник должен стоить одно
    # ожидание на поток, а не по ожиданию на файл.
    root, release, stored = _many_new_files(tmp_path, 16)
    silent, good = _Store(stored, hang=True), _Store(stored)
    planned = plan.make_plan(release, root)
    started = time.monotonic()

    ready = fetch.fetch_plan(
        planned, root=root, work_dir=tmp_path / "work", threads=4, timeout=0.5,
        sources=[fetch.Source("silent", silent.url_for), fetch.Source("good", good.url_for)], sleep=lambda _s: None,
    )

    assert len(ready) == 16
    assert len(silent.requests) <= 4
    assert time.monotonic() - started < 4.0
    silent.shutdown(), good.shutdown()


def test_a_source_that_stops_mid_file_is_resumed_on_the_next_one(tmp_path) -> None:
    root, old, new, stored, v2 = _release(tmp_path)
    stuck, good = _Store(stored, hang_after=5), _Store(stored)
    planned = plan.make_plan(new, root, installed=old)

    ready = fetch.fetch_plan(
        planned, root=root, work_dir=tmp_path / "work", threads=2, timeout=0.5,
        sources=[fetch.Source("stuck", stuck.url_for), fetch.Source("good", good.url_for)], sleep=lambda _s: None,
    )
    tree = stage.build_stage(planned, root=root, ready=ready, stage_dir=tmp_path / "stage")

    assert {path.relative_to(tree).as_posix(): path.read_bytes() for path in tree.rglob("*") if path.is_file()} == v2
    # Следующий источник продолжил с места, где замолчал первый.
    assert any(rng == "bytes=5-" for _name, rng in good.requests)
    stuck.shutdown(), good.shutdown()


def test_default_wait_for_a_silent_source_is_short() -> None:
    assert fetch.STALL_TIMEOUT <= 10.0
