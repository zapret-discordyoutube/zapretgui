"""Фоновый автосинк удалённых пресетов: гейт, очередь, периодика."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


def _startup_host():
    return SimpleNamespace(
        startup_interactive_ready=object(),
        startup_state=SimpleNamespace(interactive_logged=True),
    )


class RemotePresetsSyncInstallTests(unittest.TestCase):
    def _install(self, presets_feature, notify=None):
        from main import post_startup_remote_presets as module

        gate_calls = []
        scheduled = []
        enqueued = []

        def fake_bind_gate(_signal, callback, *, is_ready):
            gate_calls.append((callback, is_ready))

        def fake_schedule_after(delay, callback):
            scheduled.append((delay, callback))

        def fake_enqueue(subsystem, name, task):
            enqueued.append((subsystem, name, task))

        patches = [
            patch.object(module, "bind_startup_gate", side_effect=fake_bind_gate),
            patch.object(module, "schedule_after", side_effect=fake_schedule_after),
            patch.object(module, "enqueue_subsystem_task", side_effect=fake_enqueue),
            patch.object(module, "is_startup_host_alive", return_value=True),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

        module.install_remote_presets_sync(
            _startup_host(),
            presets_feature=presets_feature,
            log_startup_metric=Mock(),
            notify=notify,
            delay_ms=100,
            interval_ms=60_000,
        )
        return module, gate_calls, scheduled, enqueued

    def test_gate_schedules_startup_round_and_periodic_tick(self):
        feature = SimpleNamespace(get_preset_remote_bindings=Mock(return_value={}))
        _module, gate_calls, scheduled, enqueued = self._install(feature)
        self.assertEqual(len(gate_calls), 1)

        gate_calls[0][0]()  # интерактивная готовность
        self.assertEqual(len(scheduled), 2)  # стартовый проход + периодический тик
        delays = sorted(delay for delay, _ in scheduled)
        self.assertEqual(delays, [100, 60_000])

        # снимок: периодический тик сам себя перепланирует и дописывает в scheduled
        for _delay, callback in list(scheduled):
            callback()
        # startup-проход + periodic-тик: по обоим движкам
        self.assertEqual(len(enqueued), 4)
        self.assertTrue(all(subsystem == "presets" for subsystem, _n, _t in enqueued))

    def test_sync_round_checks_due_bindings_only(self):
        bindings = {
            "Due.txt": {"url": "https://e.com/a.txt", "auto": True, "detached": False, "checked_at": ""},
            "Fresh.txt": {"url": "https://e.com/b.txt", "auto": True, "detached": False, "checked_at": "fresh"},
        }
        feature = SimpleNamespace(
            get_preset_remote_bindings=Mock(return_value=bindings),
            get_selected_source_preset_file_name=Mock(return_value=""),
        )
        module, gate_calls, scheduled, enqueued = self._install(feature)
        gate_calls[0][0]()
        # снимок: периодический тик сам себя перепланирует и дописывает в scheduled
        for _delay, callback in list(scheduled):
            callback()

        synced = []
        outcome = SimpleNamespace(status="updated", detail="")
        with (
            patch(
                "presets.remote_sync_workers.sync_remote_preset_by_file_name",
                side_effect=lambda _f, method, file_name, **_k: synced.append((method, file_name)) or outcome,
            ),
            patch(
                "presets.remote_sync.should_auto_check",
                side_effect=lambda binding, **_k: binding.get("checked_at") != "fresh",
            ),
        ):
            for _subsystem, _name, task in enqueued:
                task()

        file_names = {file_name for _method, file_name in synced}
        self.assertEqual(file_names, {"Due.txt"})

    def test_active_preset_update_notifies(self):
        bindings = {"Active.txt": {"url": "https://e.com/a.txt", "auto": True, "detached": False, "checked_at": ""}}
        notify = Mock()
        feature = SimpleNamespace(
            get_preset_remote_bindings=Mock(return_value=bindings),
            get_selected_source_preset_file_name=Mock(return_value="Active.txt"),
        )
        module, gate_calls, scheduled, enqueued = self._install(feature, notify=notify)
        gate_calls[0][0]()
        # снимок: периодический тик сам себя перепланирует и дописывает в scheduled
        for _delay, callback in list(scheduled):
            callback()

        outcome = SimpleNamespace(status="updated", detail="")
        with (
            patch(
                "presets.remote_sync_workers.sync_remote_preset_by_file_name",
                return_value=outcome,
            ),
            patch("presets.remote_sync.should_auto_check", return_value=True),
        ):
            for _subsystem, _name, task in enqueued:
                task()

        self.assertTrue(notify.called)
        payload = notify.call_args[0][0]
        self.assertIn("Active.txt", str(payload))


if __name__ == "__main__":
    unittest.main()
