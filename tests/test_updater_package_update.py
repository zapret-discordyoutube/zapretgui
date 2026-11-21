"""Updates on a system without self-update: the release lookup picks the package, the program checks it and hands it over."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from platforms.linux.updates import LinuxUpdates
from platforms.stub import make_platform
from platforms.windows.updates import WindowsUpdates
from updater import package_update
from updater.release import forgejo, mirrors

TAG = "21.1.7.300"
DEB = f"zapretgui_{TAG}_amd64.deb"
EXE = "Zapret2Setup.exe"


def asset(name, tag=TAG):
    return {"name": name, "size": 1234, "browser_download_url": f"https://git.zapret.moe/zapretdiscordyoutube/zapretgui/releases/download/{tag}/{name}"}


def release(*names, prerelease=False):
    return {"tag_name": TAG, "draft": False, "prerelease": prerelease, "assets": [asset(name) for name in names], "body": "notes", "name": "r"}


@pytest.fixture()
def as_linux(monkeypatch):
    fake = make_platform(updates=LinuxUpdates())
    monkeypatch.setattr("platforms.current", lambda: fake)
    return fake


@pytest.fixture()
def as_windows(monkeypatch):
    fake = make_platform(updates=WindowsUpdates())
    monkeypatch.setattr("platforms.current", lambda: fake)
    return fake


def test_linux_takes_the_deb_and_its_checksum_file_and_ignores_the_installer(as_linux):
    both = release(EXE, f"{EXE}.sha256", DEB, f"{DEB}.sha256", "SHA256SUMS", f"zapretgui-{TAG}-x86_64.AppImage")
    candidate = forgejo._candidate(both, "stable")
    assert candidate["file_name"] == DEB and candidate["update_url"].endswith(f"/{DEB}")
    assert candidate["sidecar_url"].endswith(f"{DEB}.sha256")
    assert "manifest_url" not in candidate and "signature_url" not in candidate  # the signed list describes the Windows install


def test_linux_candidate_needs_the_checksum_file(as_linux):
    assert forgejo._candidate(release(DEB), "stable") is None
    assert forgejo._candidate(release(EXE, f"{EXE}.sha256"), "stable") is None  # a Windows-only release is not a Linux release


def test_the_channel_of_a_package_comes_from_the_prerelease_flag(as_linux):
    pre = release(DEB, f"{DEB}.sha256", prerelease=True)
    assert forgejo._candidate(pre, "dev") is not None and forgejo._candidate(pre, "stable") is None
    final = release(DEB, f"{DEB}.sha256")
    assert forgejo._candidate(final, "stable") is not None and forgejo._candidate(final, "dev") is None


def test_windows_still_takes_the_installer_and_the_signed_list(as_windows):
    from zapret_update.manifest import MANIFEST_NAME, SIGNATURE_NAME

    names = (EXE, f"{EXE}.sha256", DEB, f"{DEB}.sha256", MANIFEST_NAME, SIGNATURE_NAME)
    candidate = forgejo._candidate(release(*names), "stable")
    assert candidate["file_name"] == EXE and "manifest_url" in candidate and "signature_url" in candidate


SHA = "a" * 64
ENTRY = {"version": TAG, "file_name": EXE, "file_size": 99, "sha256": "b" * 64, "date": "2026-11-15", "linux": {"file_name": DEB, "file_size": 77, "sha256": SHA}}


def test_mirror_entry_gives_linux_its_own_package_under_the_same_version(as_linux):
    result = mirrors.release_from_versions({"stable": ENTRY}, "stable", base_url="https://m:888", verify_ssl=False, source="m")
    assert (result["version"], result["file_name"], result["file_size"], result["sha256"]) == (TAG, DEB, 77, SHA)
    assert result["update_url"] == f"https://m:888/download/{DEB}" and "manifest_url" not in result


def test_a_mirror_without_a_linux_package_is_an_error_not_a_windows_answer(as_linux):
    without = {key: value for key, value in ENTRY.items() if key != "linux"}
    with pytest.raises(mirrors.MirrorReleaseError, match="linux"):
        mirrors.release_from_versions({"stable": without}, "stable", base_url="https://m:888", verify_ssl=False, source="m")


def test_windows_reads_the_channel_entry_itself_and_ignores_the_linux_part(as_windows):
    result = mirrors.release_from_versions({"stable": ENTRY}, "stable", base_url="https://m:888", verify_ssl=False, source="m")
    assert (result["file_name"], result["file_size"]) == (EXE, 99)


# -- the download ----------------------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, body: bytes, chunk=10):
        self.body, self.chunk = body, chunk

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        for start in range(0, len(self.body), self.chunk):
            yield self.body[start : start + self.chunk]


class FakeSession:
    def __init__(self, body):
        self.body, self.closed = body, False

    def get(self, url, **kwargs):
        return FakeResponse(self.body)

    def close(self):
        self.closed = True


def announce(body: bytes, **changes) -> dict:
    info = {
        "version": TAG,
        "update_url": f"https://git.zapret.moe/zapretdiscordyoutube/zapretgui/releases/download/{TAG}/{DEB}",
        "file_name": DEB,
        "file_size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "verify_ssl": True,
    }
    info.update(changes)
    return info


BODY = b"!<arch>\ndebian-binary" * 7


def test_a_matching_package_is_saved_under_its_name_and_reports_progress(tmp_path):
    seen = []
    path = package_update.download_package(announce(BODY), directory=tmp_path, session=FakeSession(BODY), progress=lambda a, b: seen.append((a, b)))
    assert path == tmp_path / DEB and path.read_bytes() == BODY
    assert not (tmp_path / (DEB + ".part")).exists()
    assert seen[-1] == (len(BODY), len(BODY))


@pytest.mark.parametrize(
    ("served", "announced_changes", "text"),
    [
        (BODY[:-1] + b"X", {}, "checksum"),
        (BODY[:-5], {}, "incomplete"),
        (BODY + b"more", {}, "more bytes"),
        (BODY, {"sha256": ""}, "does not describe"),
        (BODY, {"file_size": 2 << 30, "sha256": "c" * 64}, "too large"),
    ],
)
def test_a_package_that_does_not_match_the_release_is_never_kept(tmp_path, served, announced_changes, text):
    info = announce(BODY, **announced_changes)
    with pytest.raises(package_update.PackageUpdateError, match=text):
        package_update.download_package(info, directory=tmp_path, session=FakeSession(served))
    assert list(tmp_path.iterdir()) == []


def test_a_cancelled_download_leaves_nothing(tmp_path):
    with pytest.raises(package_update.PackageUpdateError, match="cancelled"):
        package_update.download_package(announce(BODY), directory=tmp_path, session=FakeSession(BODY), cancelled=lambda: True)
    assert list(tmp_path.iterdir()) == []


def test_download_and_install_hands_the_checked_file_to_the_system_and_removes_it_after(tmp_path, monkeypatch):
    installed = []

    class Updates(LinuxUpdates):
        def install_package(self, path):
            installed.append((path, Path(path).read_bytes()))
            return True, "done"

    monkeypatch.setattr("platforms.current", lambda: make_platform(updates=Updates()))
    assert package_update.download_and_install(announce(BODY), directory=tmp_path, session=FakeSession(BODY)) == (True, "done")
    assert installed == [(tmp_path / DEB, BODY)] and not (tmp_path / DEB).exists()


def test_download_and_install_never_installs_a_bad_package(tmp_path, monkeypatch):
    class Updates(LinuxUpdates):
        def install_package(self, path):
            raise AssertionError("must not be called")

    monkeypatch.setattr("platforms.current", lambda: make_platform(updates=Updates()))
    done, text = package_update.download_and_install(announce(BODY), directory=tmp_path, session=FakeSession(b"wrong bytes of another size"))
    assert done is False and text


def test_the_button_installs_exactly_the_release_that_was_found(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(package_update, "download_and_install", lambda info, **options: seen.append(info["version"]) or (True, "ok"))
    package_update.remember_found_release(announce(BODY))
    assert package_update.install_found_release(TAG) == (True, "ok") and seen == [TAG]
    done, text = package_update.install_found_release("9.9.9.9")
    assert done is False and "not known" in text


# -- the system part ---------------------------------------------------------------------------------------


def test_linux_install_runs_pkexec_apt_on_the_package(tmp_path):
    package = tmp_path / DEB
    package.write_bytes(b"x")
    calls = []
    updates = LinuxUpdates(run=lambda command: calls.append(list(command)) or (0, ""), which=lambda name: "/usr/bin/" + name)
    assert updates.install_package(package) == (True, "The new version is installed. Close and start Zapret again.")
    assert calls == [["pkexec", "apt-get", "install", "-y", str(package)]]


@pytest.mark.parametrize(("code", "needle"), [(126, "password"), (127, "password"), (100, "failed (100)")])
def test_linux_install_failures_always_say_how_to_do_it_by_hand(tmp_path, code, needle):
    package = tmp_path / DEB
    package.write_bytes(b"x")
    done, text = LinuxUpdates(run=lambda command: (code, "E: broken"), which=lambda name: "/usr/bin/pkexec").install_package(package)
    assert done is False and needle in text and f"sudo apt install {package}" in text


def test_linux_install_refuses_other_files_and_a_missing_pkexec(tmp_path):
    other = tmp_path / "x.exe"
    other.write_bytes(b"x")
    assert LinuxUpdates(run=lambda c: (0, ""), which=lambda n: "/x").install_package(other)[0] is False
    package = tmp_path / DEB
    package.write_bytes(b"x")
    done, text = LinuxUpdates(run=lambda c: (0, ""), which=lambda n: None).install_package(package)
    assert done is False and "pkexec is not installed" in text


def test_the_capabilities_choose_the_update_path():
    from platforms import Cap
    from platforms.linux import PLATFORM as linux
    from platforms.windows import PLATFORM as windows

    assert windows.has(Cap.SELF_UPDATE) and not windows.has(Cap.PACKAGE_UPDATE)
    assert linux.has(Cap.PACKAGE_UPDATE) and not linux.has(Cap.SELF_UPDATE)


def test_the_notification_offers_install_whats_new_and_later():
    from main.post_startup_package_update import notification_for

    payload = notification_for(TAG, "https://git.zapret.moe/x/releases/tag/21.1.7.300")
    kinds = [button["kind"] for button in payload["buttons"]]
    assert kinds == ["install_package_update", "open_url", "dismiss"]
    assert payload["buttons"][0]["value"] == TAG and TAG in payload["title"]
    assert [b["kind"] for b in notification_for(TAG, "")["buttons"]] == ["install_package_update", "dismiss"]


def test_post_startup_sends_linux_to_the_package_flow_and_windows_to_the_installer_flow(monkeypatch):
    import main.post_startup as post_startup

    called = []
    monkeypatch.setattr("main.post_startup_package_update.install_package_update_check", lambda *a, **k: called.append("package") or "p")
    monkeypatch.setattr("main.post_startup_update.install_update_check", lambda *a, **k: called.append("windows") or "w")
    from platforms import Cap

    monkeypatch.setattr("platforms.current", lambda: make_platform(capabilities=frozenset({Cap.PACKAGE_UPDATE})))
    assert post_startup.install_update_check() == "p"
    monkeypatch.setattr("platforms.current", lambda: make_platform(capabilities=frozenset({Cap.SELF_UPDATE})))
    assert post_startup.install_update_check() == "w"
    monkeypatch.setattr("platforms.current", lambda: make_platform(capabilities=frozenset()))
    assert post_startup.install_update_check() is None
    assert called == ["package", "windows"]


def test_the_notification_handler_builds_the_install_action_only_when_it_is_wired():
    from ui.window_notification_actions import WindowNotificationActionHandler

    seen = []

    def build(with_install):
        kwargs = dict(
            notify=lambda *_: None, runtime_actions=None, show_page=lambda *_: None, open_url=lambda *_: None,
            request_disable_proxy=lambda *_: None, request_disable_kaspersky_warning=lambda *_: None,
            request_disable_telega_warning=lambda *_: None, request_windivert_autofix=lambda *_: None,
            request_windows_server_wlanapi_install=lambda *_: None, request_launch_conflict_action=lambda *_: None,
        )
        if with_install:
            kwargs["request_install_package_update"] = lambda value, bar: seen.append((value, bar))
        return WindowNotificationActionHandler(**kwargs)

    action = {"kind": "install_package_update", "value": TAG}
    callback = build(True).build_action_callback(action, "bar")
    callback()
    assert seen == [(TAG, "bar")]
    assert build(False).build_action_callback(action, "bar") is None


def test_linux_package_keeps_its_own_version_when_the_channel_moved_on(as_linux):
    # a Windows-only release (new engine) changed the channel version; the Linux package is still the older one
    newer = {**ENTRY, "version": "21.1.7.294", "linux": {**ENTRY["linux"], "version": TAG}}
    result = mirrors.release_from_versions({"stable": newer}, "stable", base_url="https://m:888", verify_ssl=False, source="m")
    assert (result["version"], result["file_name"]) == (TAG, DEB)

