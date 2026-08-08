from __future__ import annotations

import unittest

from updater.forgejo_release import _parse_sha256_sidecar, _trusted_release_asset_url


class ForgejoReleaseTrustTests(unittest.TestCase):
    def test_accepts_exact_release_asset(self) -> None:
        self.assertTrue(
            _trusted_release_asset_url(
                "https://git.zapret.moe/zapretdiscordyoutube/zapretgui/releases/download/21.1.5.42/Zapret2Setup_DEV_21_1_5_42.exe",
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
                    _trusted_release_asset_url(
                        url,
                        tag_name="21.1.5.42",
                        file_name="file.exe",
                    )
                )

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
