"""Встроенные цели BlockCheck для проверки видеотракта YouTube."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from blockcheck.hosts import host_of  # noqa: E402
from blockcheck.targets import get_default_https_targets  # noqa: E402


class BlockcheckTargetTests(unittest.TestCase):
    def test_googlevideo_has_real_video_cdn_target(self) -> None:
        targets = get_default_https_targets()
        googlevideo_hosts = {
            host_of(target["value"]): target["name"]
            for target in targets
            if host_of(target["value"]).endswith(".googlevideo.com")
        }

        self.assertEqual(
            googlevideo_hosts.get("rr2---sn-axq7sn7z.googlevideo.com"),
            "YouTube Video (*.googlevideo.com)",
        )
        self.assertIn("redirector.googlevideo.com", googlevideo_hosts)


if __name__ == "__main__":
    unittest.main()
