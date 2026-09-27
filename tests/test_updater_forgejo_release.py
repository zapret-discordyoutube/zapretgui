from __future__ import annotations

"""Выбор выпуска на Forgejo.

Главные правила: доверяем только точным ссылкам своего домена, выбираем по
номеру версии, а файл суммы качаем только у лучшего кандидата — и если он не
скачался, это ошибка, а не молчаливый откат на предыдущий выпуск.
"""

import json
import unittest
from unittest.mock import patch

from updater.release import forgejo
from updater.release.forgejo import (
    ForgejoReleaseError,
    _parse_sha256_sidecar,
    _trusted_release_asset_url,
)

BASE = "https://git.zapret.moe/zapretdiscordyoutube/zapretgui/releases/download"


def _release(version: str, *, dev: bool = True, sidecar: bool = True, draft: bool = False) -> dict:
    name = f"Zapret2Setup_DEV_{version.replace('.', '_')}.exe" if dev else f"Zapret2Setup_{version.replace('.', '_')}.exe"
    assets = [{"name": name, "size": 1000, "browser_download_url": f"{BASE}/{version}/{name}"}]
    if sidecar:
        assets.append(
            {"name": f"{name}.sha256", "size": 90, "browser_download_url": f"{BASE}/{version}/{name}.sha256"}
        )
    return {
        "tag_name": version,
        "prerelease": dev,
        "draft": draft,
        "body": f"notes {version}",
        "assets": assets,
    }


class _Response:
    def __init__(self, payload=None, content: bytes = b"") -> None:
        self._payload = payload
        self.content = content if content else json.dumps(payload).encode()

    def json(self):
        return self._payload


class _FakeForgejo:
    """Отвечает на запросы по адресу; ``broken`` — адреса, которые падают."""

    def __init__(self, pages: list[list[dict]], *, broken: tuple[str, ...] = ()) -> None:
        self.pages = pages
        self.broken = broken
        self.requested: list[str] = []

    def get(self, _session, url: str, *, accept: str):
        self.requested.append(url)
        if any(part in url for part in self.broken):
            raise ConnectionError("обрыв")
        if "releases?limit=" in url:
            page = int(url.rsplit("page=", 1)[1])
            return _Response(self.pages[page - 1] if page <= len(self.pages) else [])
        name = url.rsplit("/", 1)[1][: -len(".sha256")]
        return _Response(content=f"{'ab' * 32}  {name}\n".encode())


class ForgejoReleaseSelectionTests(unittest.TestCase):
    def _fetch(self, fake: _FakeForgejo, channel: str = "dev") -> dict:
        with patch.object(forgejo, "_get", side_effect=fake.get):
            return forgejo.fetch_latest_release(channel)

    def test_highest_version_wins_and_only_its_checksum_is_downloaded(self) -> None:
        fake = _FakeForgejo([[_release("21.1.5.78"), _release("21.1.5.80"), _release("21.1.5.79")]])

        release = self._fetch(fake)

        self.assertEqual(release["version"], "21.1.5.80")
        self.assertEqual(release["sha256"], "ab" * 32)
        self.assertEqual(release["source"], "Forgejo")
        checksum_requests = [url for url in fake.requested if url.endswith(".sha256")]
        self.assertEqual(len(checksum_requests), 1)
        self.assertIn("21.1.5.80", checksum_requests[0])

    def test_missing_checksum_of_newest_release_is_an_error_not_a_downgrade(self) -> None:
        """Раньше молча предлагался предыдущий выпуск, то есть «обновлений нет»."""
        fake = _FakeForgejo(
            [[_release("21.1.5.80"), _release("21.1.5.79")]],
            broken=("21.1.5.80/Zapret2Setup_DEV_21_1_5_80.exe.sha256",),
        )

        with self.assertRaisesRegex(ForgejoReleaseError, "21.1.5.80"):
            self._fetch(fake)

    def test_release_still_being_uploaded_is_not_a_candidate(self) -> None:
        fake = _FakeForgejo([[_release("21.1.5.80", sidecar=False), _release("21.1.5.79")]])

        self.assertEqual(self._fetch(fake)["version"], "21.1.5.79")

    def test_channels_and_drafts_are_kept_apart(self) -> None:
        fake = _FakeForgejo(
            [[_release("21.1.5.80"), _release("21.1.1.9", dev=False, draft=True), _release("21.1.1.8", dev=False)]]
        )

        self.assertEqual(self._fetch(fake, "stable")["version"], "21.1.1.8")

    def test_pages_are_listed_until_the_channel_appears(self) -> None:
        dev_page = [_release(f"21.1.5.{index}") for index in range(100, 150)]
        fake = _FakeForgejo([dev_page, [_release("21.1.1.8", dev=False)]])

        self.assertEqual(self._fetch(fake, "stable")["version"], "21.1.1.8")

    def test_every_call_goes_to_network(self) -> None:
        """Кэша нет: ручная проверка всегда видит только что вышедший выпуск."""
        fake = _FakeForgejo([[_release("21.1.5.79")]])
        self._fetch(fake)
        fake.pages = [[_release("21.1.5.80"), _release("21.1.5.79")]]

        self.assertEqual(self._fetch(fake)["version"], "21.1.5.80")

    def test_unreachable_list_is_an_error(self) -> None:
        fake = _FakeForgejo([[]], broken=("releases?limit=",))

        with self.assertRaisesRegex(ForgejoReleaseError, "список выпусков"):
            self._fetch(fake)

    def test_probe_reports_current_failure_not_old_success(self) -> None:
        """Строка Forgejo в таблице не показывает «online», когда он недоступен."""
        with patch.object(forgejo, "_get", side_effect=ConnectionError("обрыв")):
            with self.assertRaises(ConnectionError):
                forgejo.probe_forgejo()


class ForgejoReleaseTrustTests(unittest.TestCase):
    def test_accepts_exact_release_asset(self) -> None:
        self.assertTrue(
            _trusted_release_asset_url(
                f"{BASE}/21.1.5.42/Zapret2Setup_DEV_21_1_5_42.exe",
                tag_name="21.1.5.42",
                file_name="Zapret2Setup_DEV_21_1_5_42.exe",
            )
        )

    def test_rejects_host_port_userinfo_query_fragment_and_wrong_path(self) -> None:
        base = "/zapretdiscordyoutube/zapretgui/releases/download/21.1.5.42/file.exe"
        invalid = (
            f"https://evil.example{base}",
            f"https://git.zapret.moe:8443{base}",
            f"https://user@git.zapret.moe{base}",
            f"https://git.zapret.moe{base}?download=1",
            f"https://git.zapret.moe{base}#fragment",
            "https://git.zapret.moe/other/repo/releases/download/21.1.5.42/file.exe",
            "https://git.zapret.moe/zapretdiscordyoutube/zapretgui/releases/download/other/file.exe",
        )
        for url in invalid:
            with self.subTest(url=url):
                self.assertFalse(
                    _trusted_release_asset_url(url, tag_name="21.1.5.42", file_name="file.exe")
                )

    def test_release_with_foreign_link_is_not_a_candidate(self) -> None:
        release = _release("21.1.5.80")
        release["assets"][0]["browser_download_url"] = "https://evil.example/Zapret2Setup_DEV_21_1_5_80.exe"

        self.assertIsNone(forgejo._candidate(release, "dev"))

    def test_sidecar_requires_exact_hash_and_file_name(self) -> None:
        digest = "a" * 64
        self.assertEqual(
            _parse_sha256_sidecar(f"{digest}  file.exe\n", expected_name="file.exe"),
            digest,
        )
        invalid = (
            f"{digest} file.exe\n",
            f"{digest}  other.exe\n",
            f"{digest}  file.exe\n{digest}  file.exe\n",
            f"sha256:{digest}  file.exe\n",
        )
        for content in invalid:
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    _parse_sha256_sidecar(content, expected_name="file.exe")


if __name__ == "__main__":
    unittest.main()
