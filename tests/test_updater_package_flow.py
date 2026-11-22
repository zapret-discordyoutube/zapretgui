"""The update dialog on Linux (report 2026-11-22: "Выпуск v21.1.7.303 не прошёл проверку подписи").

The dialog started the Windows installer path, which demands a signed list of the release; that list describes the Windows
installer and does not exist for a Linux package. On a system that installs packages the same button now downloads the
package and gives it to the system, like the button of the notification."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from updater.download import flow
from updater.download.contracts import UpdateNotNeeded, UpdatePipelineError
from updater.download.downloader import CancellationToken

RELEASE = {
    "version": "99.0.0.1", "tag_name": "99.0.0.1", "update_url": "https://example.test/zapretgui_99.0.0.1_amd64.deb",
    "file_name": "zapretgui_99.0.0.1_amd64.deb", "file_size": 10, "sha256": "a" * 64, "release_notes": "", "prerelease": True,
    "name": "Zapret 99.0.0.1", "published_at": "", "source": "test", "verify_ssl": True,
}


def _run(monkeypatch, *, installed=(True, "Новая версия установлена."), release=None, lookup_ok=True):
    stages, progress = [], []
    seen = {}
    monkeypatch.setattr(flow, "_installs_system_packages", lambda: True)
    monkeypatch.setattr(flow, "lookup_latest_release", lambda channel: SimpleNamespace(ok=lookup_ok, release=release or RELEASE, error="нет сети"))

    def fake_download_and_install(rel, **options):
        seen["release"] = rel
        options["progress"](5, 10)
        options["progress"](10, 10)
        return installed

    monkeypatch.setattr("updater.package_update.download_and_install", fake_download_and_install)
    flow.run_update_install("99.0.0.1", token=CancellationToken(), dpi=None, on_stage=stages.append, on_progress=lambda *a: progress.append(a))
    return stages, progress, seen


def test_the_dialog_installs_the_package_without_a_signed_release_list(monkeypatch) -> None:
    stages, progress, seen = _run(monkeypatch)
    assert seen["release"]["file_name"] == "zapretgui_99.0.0.1_amd64.deb"
    assert progress == [(50, 5, 10), (100, 10, 10)]
    assert stages[0] == "Проверка выпуска…" and "система спросит пароль" in " ".join(stages) and stages[-1] == "Новая версия установлена."


def test_a_failed_package_install_is_an_error_with_the_text_for_the_person(monkeypatch) -> None:
    with pytest.raises(UpdatePipelineError, match="Окно пароля закрыто"):
        _run(monkeypatch, installed=(False, "Окно пароля закрыто или пароль не принят."))


def test_a_release_that_is_not_newer_is_not_installed(monkeypatch) -> None:
    with pytest.raises(UpdateNotNeeded):
        _run(monkeypatch, release={**RELEASE, "version": "1.0.0.1"})
