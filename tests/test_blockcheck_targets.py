"""Сборка целей BlockCheck для проверки видеотракта YouTube."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from blockcheck.hosts import host_of  # noqa: E402
from blockcheck.targets import (  # noqa: E402
    build_targets_with_user_domains,
    get_default_https_targets,
)


class BlockcheckTargetTests(unittest.TestCase):
    def test_default_targets_do_not_keep_temporary_googlevideo_host(self) -> None:
        targets = get_default_https_targets()
        googlevideo_hosts = [
            host_of(target["value"])
            for target in targets
            if host_of(target["value"]).endswith(".googlevideo.com")
        ]

        self.assertIn("redirector.googlevideo.com", googlevideo_hosts)
        self.assertFalse(any(host.startswith("rr") for host in googlevideo_hosts))

    def test_discovered_googlevideo_host_is_added_only_to_current_run(self) -> None:
        discovered_host = "rr7---sn-local-user.googlevideo.com"

        with patch("blockcheck.targets.load_user_domains", return_value=[]):
            targets = build_targets_with_user_domains(
                googlevideo_host=discovered_host,
            )

        matching = [
            target for target in targets
            if host_of(target["value"]) == discovered_host
        ]
        self.assertEqual(
            matching,
            [{
                "name": "YouTube Video (*.googlevideo.com)",
                "value": f"https://{discovered_host}",
            }],
        )

        default_hosts = {
            host_of(target["value"])
            for target in get_default_https_targets()
        }
        self.assertNotIn(discovered_host, default_hosts)


if __name__ == "__main__":
    unittest.main()
