import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from telegram_proxy.ui import proxy_runtime_workflow


class RelayWarningTests(unittest.TestCase):
    def test_new_relay_warning_replaces_previous_one(self) -> None:
        bars = [Mock(name="first"), Mock(name="second")]
        info_bar_cls = SimpleNamespace(warning=Mock(side_effect=bars))
        parent = SimpleNamespace()
        manager = SimpleNamespace(
            is_running=True,
            stats=SimpleNamespace(bytes_received=0, bytes_sent=0),
            host="127.0.0.1",
            port=1353,
        )
        plan = SimpleNamespace(
            status_text="нет связи",
            show_warning=True,
            warning_title="Прокси",
            warning_content="нет связи",
        )

        with patch.object(
            proxy_runtime_workflow.telegram_proxy_page_runtime,
            "build_relay_result_plan",
            return_value=plan,
        ):
            for _ in range(2):
                proxy_runtime_workflow.apply_relay_result(
                    manager=manager,
                    diag={"status": "fail"},
                    status_label=Mock(),
                    info_bar_cls=info_bar_cls,
                    info_bar_position=SimpleNamespace(TOP="top"),
                    parent=parent,
                )

        bars[0].close.assert_called_once_with()
        bars[1].close.assert_not_called()
        self.assertIs(parent._relay_warning_bar, bars[1])


if __name__ == "__main__":
    unittest.main()
