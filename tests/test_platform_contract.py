"""One contract for every backend: whatever ``platforms/base.py`` promises, Windows, Linux and the stub each provide,
with call signatures the program can use interchangeably."""

from __future__ import annotations

import dataclasses
import inspect
import typing

import pytest

from platforms import base
from platforms.linux import PLATFORM as LINUX
from platforms.stub import PLATFORM as STUB
from platforms.windows import PLATFORM as WINDOWS

BACKENDS = {"windows": WINDOWS, "linux": LINUX, "stub": STUB}
INTERFACE_FIELDS = [
    field.name
    for field in dataclasses.fields(base.Platform)
    if field.name not in {"name", "capabilities"}
]


def _protocol_for(field_name: str):
    hints = typing.get_type_hints(base.Platform)
    return hints[field_name]


def _public_methods(protocol) -> dict[str, inspect.Signature]:
    methods = {}
    for name, member in vars(protocol).items():
        if name.startswith("_") or not callable(member):
            continue
        methods[name] = inspect.signature(member)
    return methods


def _parameters(signature: inspect.Signature) -> list[inspect.Parameter]:
    return [p for name, p in signature.parameters.items() if name != "self"]


def test_the_platform_names_and_capabilities_are_what_the_program_expects() -> None:
    assert (WINDOWS.name, LINUX.name, STUB.name) == ("windows", "linux", "stub")
    assert base.Cap.NETWORK_RESET in WINDOWS.capabilities and not LINUX.capabilities & {base.Cap.DEFENDER, base.Cap.BFE}
    assert STUB.capabilities == frozenset()
    for backend in BACKENDS.values():
        assert all(isinstance(item, str) for item in backend.capabilities)
        known = {value for name, value in vars(base.Cap).items() if not name.startswith("_")}
        assert backend.capabilities <= known, "a capability that Cap does not define"


@pytest.mark.parametrize("backend_name", BACKENDS)
@pytest.mark.parametrize("field_name", INTERFACE_FIELDS)
def test_every_interface_is_present_and_complete(backend_name, field_name) -> None:
    backend = BACKENDS[backend_name]
    implementation = getattr(backend, field_name)
    assert implementation is not None
    protocol = _protocol_for(field_name)
    for method_name, promised in _public_methods(protocol).items():
        provided = getattr(implementation, method_name, None)
        assert callable(provided), f"{backend_name}.{field_name}.{method_name} is missing"
        wanted = _parameters(promised)
        try:
            actual = _parameters(inspect.signature(provided))
        except (TypeError, ValueError):
            continue  # a builtin or a dynamic delegate: nothing to compare
        required_by_callers = [p for p in wanted if p.default is inspect.Parameter.empty and p.kind is not p.VAR_KEYWORD]
        accepts_anything = any(p.kind is p.VAR_KEYWORD for p in actual) and any(p.kind is p.VAR_POSITIONAL for p in actual)
        if accepts_anything:
            continue
        actual_names = {p.name for p in actual}
        for parameter in required_by_callers:
            assert parameter.name in actual_names or any(p.kind is p.VAR_KEYWORD for p in actual), (
                f"{backend_name}.{field_name}.{method_name} cannot take '{parameter.name}'"
            )
        # nothing the implementation insists on may be missing from what callers are promised to pass
        promised_names = {p.name for p in wanted}
        for parameter in actual:
            if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
                continue
            if parameter.default is inspect.Parameter.empty:
                assert parameter.name in promised_names, (
                    f"{backend_name}.{field_name}.{method_name} demands '{parameter.name}' which callers are not told to pass"
                )


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_no_interface_method_raises_on_a_harmless_question(backend_name) -> None:
    """The questions the program may ask at any time must answer, never raise, on every system."""
    backend = BACKENDS[backend_name]
    assert isinstance(backend.launch.system_support(), base.SystemSupport)
    assert isinstance(backend.launch.source_launch_message(), str)
    assert isinstance(backend.processes.list_processes(), list)
    assert isinstance(backend.processes.command_line(2**22 + 7), str)
    assert isinstance(backend.processes.start_time(2**22 + 7), float)
    assert backend.processes.kill(2**22 + 7, 10) is False
    assert backend.system_settings.url_scheme_handler_registered("zapret-contract-test") is False
    assert isinstance(backend.system_settings.default_browser_id(), str)
    assert isinstance(backend.netprobe.hosts_file_path(), str)
    assert backend.desktop.handle_native_event(object(), None) is False or backend_name == "windows"
    assert isinstance(backend.desktop.uses_qt_file_drop(), bool)
    assert backend.privileges.gui_needs_elevation in (True, False)
    assert isinstance(backend.privileges.is_elevated(), bool)
    assert backend.has("a-capability-nobody-has") is False


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_every_backend_can_build_a_tray_manager_with_the_calls_the_program_makes(backend_name) -> None:
    from unittest.mock import MagicMock

    if backend_name == "windows":
        pytest.skip("the Windows tray needs a notification area")
    manager = BACKENDS[backend_name].tray.create_manager(
        window_port=MagicMock(), icon_path="", app_version="1", tray_feature=MagicMock()
    )
    for name in ("apply_launch_status", "show_notification", "show_window", "hide_to_tray", "hide_icon", "cleanup"):
        assert callable(getattr(manager, name)), name


def test_a_new_interface_must_come_with_all_three_backends() -> None:
    """If somebody adds a field to Platform, this list and the backends must follow."""
    assert sorted(INTERFACE_FIELDS) == sorted(
        [
            "messages", "privileges", "single_instance", "services", "launch", "processes", "netprobe", "autostart",
            "desktop", "tray", "system_settings", "engine", "working_set",
        ]
    )


def test_the_linux_lane_lists_only_files_that_exist_and_covers_every_platform_test() -> None:
    from pathlib import Path

    tests = Path(__file__).resolve().parent
    listed = [
        line.strip() for line in (tests / "linux_lane.txt").read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")
    ]
    for entry in listed:
        assert (tests.parent / entry).is_file(), f"{entry} is in the Linux lane but does not exist"
    assert len(listed) == len(set(listed))
    new_style = {f"tests/{path.name}" for path in tests.glob("test_platforms_*.py")} | {
        f"tests/{path.name}" for path in tests.glob("test_linux_*.py")
    }
    assert new_style <= set(listed), f"missing from the Linux lane: {sorted(new_style - set(listed))}"
