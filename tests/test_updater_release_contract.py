from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from updater.release_contract import (
    ReleaseArtifactMetadata,
    ReleaseMetadataError,
    is_installable_release,
)
from updater.release.resolver import ReleaseLookup
from updater.update_pipeline import CancellationToken, prepare_update


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
    def test_preflight_uses_one_release_without_refetching_forgejo_integrity(self) -> None:
        release = _release()

        with (
            patch("updater.update_pipeline.lookup_latest_release", return_value=ReleaseLookup(release)),
            patch("updater.update_pipeline.test_connectivity", return_value=True),
            patch("updater.release.forgejo.fetch_latest_release") as forgejo_refetch,
        ):
            result = prepare_update(
                requested_version=release["version"],
                token=CancellationToken(),
            )

        forgejo_refetch.assert_not_called()
        self.assertEqual(result.artifact.version, release["version"])
        self.assertEqual(result.artifact.expected_sha256, release["sha256"])
        self.assertEqual(result.artifact.expected_size, release["file_size"])
        self.assertEqual(result.artifact.sources[0].url, release["update_url"])

    def test_preflight_does_not_mix_telegram_version_with_forgejo_hash(self) -> None:
        announcement = _release()
        announcement["update_url"] = "telegram://zapretguidev"
        announcement.pop("sha256")

        with (
            patch("updater.update_pipeline.lookup_latest_release", return_value=ReleaseLookup(announcement)),
            patch("updater.release.forgejo.fetch_latest_release") as forgejo_refetch,
            self.assertRaisesRegex(ReleaseMetadataError, "некорректная ссылка"),
        ):
            prepare_update(
                requested_version=announcement["version"],
                token=CancellationToken(),
            )

        forgejo_refetch.assert_not_called()

    def test_preflight_reports_lookup_error_instead_of_generic_failure(self) -> None:
        from updater.update_pipeline import UpdatePipelineError

        with (
            patch(
                "updater.update_pipeline.lookup_latest_release",
                return_value=ReleaseLookup(None, "Forgejo: нет ответа. Зеркала: нет ответа"),
            ),
            self.assertRaisesRegex(UpdatePipelineError, "Forgejo: нет ответа"),
        ):
            prepare_update(requested_version="99.1.2.3", token=CancellationToken())

    def test_release_lookup_has_no_telegram_source(self) -> None:
        from updater.release import resolver

        source = inspect.getsource(resolver)
        self.assertNotIn("telegram", source.lower())

    def test_version_worker_asks_only_its_own_channel(self) -> None:
        """Раньше проверялись оба канала подряд, и предложение ждало лишний запрос."""
        from updater.server_status_workers import VersionCheckWorker

        worker = VersionCheckWorker("dev")
        found: list[tuple[str, dict]] = []
        worker.version_found.connect(lambda channel, release: found.append((channel, release)))

        with patch(
            "updater.release.resolver.lookup_latest_release",
            return_value=ReleaseLookup(_release("99.1.2.4")),
        ) as resolve:
            worker.run()

        resolve.assert_called_once_with("dev")
        self.assertEqual([channel for channel, _release_info in found], ["dev"])

    def test_version_worker_reports_lookup_error(self) -> None:
        from updater.server_status_workers import VersionCheckWorker

        worker = VersionCheckWorker("dev")
        found: list[dict] = []
        worker.version_found.connect(lambda _channel, info: found.append(info))

        with patch(
            "updater.release.resolver.lookup_latest_release",
            return_value=ReleaseLookup(None, "Не удалось узнать новейшую версию"),
        ):
            worker.run()

        self.assertEqual(found, [{"error": "Не удалось узнать новейшую версию"}])

    def test_stopped_version_worker_stays_silent(self) -> None:
        """Результат остановленной проверки не должен перебить новую."""
        from updater.server_status_workers import VersionCheckWorker

        worker = VersionCheckWorker("dev")
        events: list[str] = []
        worker.version_found.connect(lambda *_args: events.append("found"))
        worker.complete.connect(lambda: events.append("complete"))

        def stopped_lookup(_channel: str) -> ReleaseLookup:
            worker.stop()
            return ReleaseLookup(_release("99.1.2.4"))

        with patch("updater.release.resolver.lookup_latest_release", side_effect=stopped_lookup):
            worker.run()

        self.assertEqual(events, [])

    def test_server_status_rows_cannot_offer_an_update_directly(self) -> None:
        from updater.update_page_runtime import UpdatePageRuntime

        handler_source = inspect.getsource(UpdatePageRuntime._on_server_checked)
        self.assertFalse(hasattr(UpdatePageRuntime, "_maybe_offer_update_from_server"))
        self.assertNotIn("_offer_current_update", handler_source)
        self.assertNotIn("_set_found_update_state", handler_source)


if __name__ == "__main__":
    unittest.main()
