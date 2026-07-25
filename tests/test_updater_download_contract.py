from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from updater.update_pipeline import (
    CancellationToken,
    InstallerHandoff,
    ThrottledProgress,
    UpdateArtifact,
    UpdateCancelled,
    UpdateIntegrityError,
    normalize_sha256,
    verify_artifact,
)


class UpdaterDownloadContractTests(unittest.TestCase):
    def test_progress_is_limited_by_time_and_flushes_completion(self) -> None:
        now = [10.0]
        emitted: list[tuple[int, int, int]] = []
        progress = ThrottledProgress(
            lambda percent, done, total: emitted.append((percent, done, total)),
            interval_seconds=0.25,
            clock=lambda: now[0],
        )

        progress.update(1, 100)
        progress.update(2, 100)
        now[0] += 0.24
        progress.update(3, 100)
        now[0] += 0.01
        progress.update(4, 100)
        progress.update(100, 100)

        self.assertEqual(
            emitted,
            [(1, 1, 100), (4, 4, 100), (100, 100, 100)],
        )

    def test_sha256_accepts_github_digest_format(self) -> None:
        digest = "a" * 64
        self.assertEqual(normalize_sha256(f"sha256:{digest}"), digest)
        self.assertEqual(normalize_sha256(digest.upper()), digest)
        self.assertEqual(normalize_sha256("md5:abcd"), "")

    def test_verification_checks_size_and_sha256(self) -> None:
        payload = b"verified installer"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "setup.exe"
            path.write_bytes(payload)
            artifact = UpdateArtifact(
                version="21.1.5.1",
                file_name="setup.exe",
                expected_size=len(payload),
                expected_sha256=hashlib.sha256(payload).hexdigest(),
                sources=(),
            )

            verify_artifact(artifact, str(path), CancellationToken())

            wrong = UpdateArtifact(
                version=artifact.version,
                file_name=artifact.file_name,
                expected_size=artifact.expected_size,
                expected_sha256="0" * 64,
                sources=(),
            )
            with self.assertRaises(UpdateIntegrityError):
                verify_artifact(wrong, str(path), CancellationToken())

    def test_handoff_is_an_explicit_pipeline_value(self) -> None:
        handoff = InstallerHandoff(
            version="21.1.5.1",
            installer_path=r"C:\Zapret\Dev\update\Zapret2Setup.exe",
            arguments=("/AUTOUPDATE",),
        )
        self.assertEqual(handoff.arguments, ("/AUTOUPDATE",))

    def test_cancellation_token_stops_at_checkpoint(self) -> None:
        token = CancellationToken()
        token.cancel()
        with self.assertRaises(UpdateCancelled):
            token.checkpoint()


if __name__ == "__main__":
    unittest.main()
