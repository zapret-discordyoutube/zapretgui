from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from updater.release_contract import (
    ReleaseArtifactMetadata,
    ReleaseMetadataError,
    is_installable_release,
)
from updater.download.downloader import UpdatePipelineError
from updater.download.flow import resolve_artifact
from updater.release.resolver import ReleaseLookup


def _release(version: str = "99.1.2.3") -> dict:
    file_name = f"Zapret2Setup_DEV_{version.replace('.', '_')}.exe"
    return {
        "version": version,
        "update_url": f"https://git.zapret.moe/zapretdiscordyoutube/zapretgui/releases/download/{version}/{file_name}",
        "file_name": file_name,
        "file_size": 12345,
        "sha256": "a" * 64,
        "verify_ssl": True,
        "release_notes": "Исправление обновления",
        "source": "Forgejo API",
    }


class ReleaseArtifactMetadataTests(unittest.TestCase):
    def test_accepts_one_complete_installable_release(self) -> None:
        metadata = ReleaseArtifactMetadata.from_mapping(_release())

        self.assertEqual(metadata.version, "99.1.2.3")
        self.assertEqual(metadata.file_size, 12345)
        self.assertEqual(metadata.sha256, "a" * 64)
        self.assertTrue(is_installable_release(_release()))

    def test_rejects_announcement_without_sha256(self) -> None:
        announcement = _release()
        announcement.pop("sha256")

        with self.assertRaisesRegex(ReleaseMetadataError, "нет SHA-256"):
            ReleaseArtifactMetadata.from_mapping(announcement)
        self.assertFalse(is_installable_release(announcement))

    def test_rejects_telegram_pseudo_url(self) -> None:
        announcement = _release()
        announcement["update_url"] = "telegram://zapretguidev"

        with self.assertRaisesRegex(ReleaseMetadataError, "некорректная ссылка"):
            ReleaseArtifactMetadata.from_mapping(announcement)


class UpdateReleaseResolutionTests(unittest.TestCase):
    def _resolve(self, lookup: ReleaseLookup, **kwargs):
        with (
            patch("updater.download.flow.lookup_latest_release", return_value=lookup),
            patch("updater.download.flow.APP_VERSION", "21.1.5.79"),
            patch("updater.release.forgejo.fetch_latest_release") as forgejo_refetch,
        ):
            try:
                return resolve_artifact(**kwargs), forgejo_refetch
            finally:
                forgejo_refetch.assert_not_called()

    def test_install_uses_one_release_without_refetching_forgejo_integrity(self) -> None:
        release = _release()

        artifact, _ = self._resolve(ReleaseLookup(release), requested_version=release["version"])

        self.assertEqual(artifact.version, release["version"])
        self.assertEqual(artifact.expected_sha256, release["sha256"])
        self.assertEqual(artifact.expected_size, release["file_size"])
        self.assertEqual(artifact.sources[0].url, release["update_url"])

    def test_install_does_not_mix_telegram_version_with_forgejo_hash(self) -> None:
        announcement = _release()
        announcement["update_url"] = "telegram://zapretguidev"
        announcement.pop("sha256")

        with self.assertRaisesRegex(ReleaseMetadataError, "некорректная ссылка"):
            self._resolve(ReleaseLookup(announcement), requested_version=announcement["version"])

    def test_install_reports_lookup_error_instead_of_generic_failure(self) -> None:
        with self.assertRaisesRegex(UpdatePipelineError, "Forgejo: нет ответа"):
            self._resolve(ReleaseLookup(None, "Forgejo: нет ответа. Зеркала: нет ответа"), requested_version="99.1.2.3")

    def test_same_version_is_refused_for_update_but_allowed_for_repair(self) -> None:
        release = _release("21.1.5.79")

        with self.assertRaisesRegex(UpdatePipelineError, "уже не требуется"):
            self._resolve(ReleaseLookup(release))
        artifact, _ = self._resolve(ReleaseLookup(release), allow_same_version=True)
        self.assertEqual(artifact.version, "21.1.5.79")

    def test_release_lookup_has_no_telegram_source(self) -> None:
        from updater.release import resolver

        source = inspect.getsource(resolver)
        self.assertNotIn("telegram", source.lower())

    def test_check_asks_only_its_own_channel(self) -> None:
        """Раньше проверялись оба канала подряд, и предложение ждало лишний запрос."""
        from updater.check.flow import run_update_check

        asked: list[str] = []

        def lookup(channel: str) -> ReleaseLookup:
            asked.append(channel)
            return ReleaseLookup(_release("99.1.2.4"))

        outcome = run_update_check(
            "dev",
            language="ru",
            emit_row=lambda *_args: None,
            dpi=None,
            lookup=lookup,
            probe=lambda **_kwargs: True,
            probe_telegram=lambda **_kwargs: None,
        )

        self.assertEqual(asked, ["dev"])
        self.assertEqual(outcome.release["version"], "99.1.2.4")

    def test_server_status_rows_cannot_offer_an_update_directly(self) -> None:
        from updater.ui.page import ServersPage

        handler_source = inspect.getsource(ServersPage._on_server_status)
        self.assertNotIn("show_update", handler_source)
        self.assertNotIn("_found_version", handler_source)

if __name__ == "__main__":
    unittest.main()
