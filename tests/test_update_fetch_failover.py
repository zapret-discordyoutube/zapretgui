"""Скачивание обновления по файлам: одинаковые файлы и сломанный ответ зеркала.

Без сжатия zstd: само скачивание и распаковка подменены, проверяется только,
что и откуда просит ``fetch_plan``.
"""

from __future__ import annotations

import http.client
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from zapret_update import fetch
from zapret_update.manifest import FileEntry
from zapret_update.plan import UpdatePlan, Want


def _source(label: str) -> fetch.Source:
    return fetch.Source(label, lambda name: f"http://{label}.example/{name}")


def _fake_finish(want, part, ready, root) -> None:
    ready.write_bytes(b"x" * want.entry.size)
    part.unlink(missing_ok=True)


class FetchPlanTests(unittest.TestCase):
    def test_files_with_the_same_content_are_downloaded_once(self) -> None:
        same = "a" * 64
        plan = UpdatePlan(
            version="1.0.0.2",
            want=[
                Want(FileEntry("lua/a/__init__.lua", 0, same, 10)),
                Want(FileEntry("lua/b/__init__.lua", 0, same, 10)),
            ],
        )
        downloads: list[str] = []
        lock = threading.Lock()

        def download(source, name, part, size, **kwargs):
            with lock:
                downloads.append(name)
            part.write_bytes(b"z" * size)
            kwargs["progress"].add(size)

        seen: list[tuple[int, int]] = []
        with TemporaryDirectory() as tmp, (
            patch.object(fetch, "_download_part", side_effect=download)
        ), patch.object(fetch, "_finish", side_effect=_fake_finish), patch.object(fetch, "_ready_ok", return_value=False):
            ready = fetch.fetch_plan(
                plan,
                root=Path(tmp) / "app",
                work_dir=Path(tmp) / "work",
                sources=[_source("one")],
                progress=lambda done, total: seen.append((done, total)),
                sleep=lambda _s: None,
            )

        self.assertEqual(len(downloads), 1)
        self.assertEqual(set(ready), {same})
        # Прогресс считает только то, что правда качается.
        self.assertEqual(seen[-1], (10, 10))

    def test_broken_http_answer_switches_to_the_next_mirror(self) -> None:
        plan = UpdatePlan(version="1.0.0.2", want=[Want(FileEntry("lua/a.lua", 0, "b" * 64, 10))])
        tried: list[str] = []

        def download(source, name, part, size, **_kwargs):
            tried.append(source.label)
            if source.label == "broken":
                raise http.client.BadStatusLine("garbage")
            part.write_bytes(b"z" * size)

        with TemporaryDirectory() as tmp, (
            patch.object(fetch, "_download_part", side_effect=download)
        ), patch.object(fetch, "_finish", side_effect=_fake_finish), patch.object(fetch, "_ready_ok", return_value=False):
            ready = fetch.fetch_plan(
                plan,
                root=Path(tmp) / "app",
                work_dir=Path(tmp) / "work",
                sources=[_source("broken"), _source("good")],
                sleep=lambda _s: None,
            )

        self.assertEqual(tried, ["broken", "good"])
        self.assertEqual(set(ready), {"b" * 64})


if __name__ == "__main__":
    unittest.main()
