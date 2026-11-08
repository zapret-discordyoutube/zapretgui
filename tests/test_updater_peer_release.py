from __future__ import annotations

"""Выпуск, названный соседями по интернету: проходит те же проверки, что и выпуск с сервера."""

import base64
import hashlib
import unittest
from unittest.mock import patch

from updater.download.contracts import UpdateSignatureError
from updater.download.flow import resolve_artifact
from updater.release import signed
from updater.release.resolver import ReleaseLookup, lookup_latest_release
from updater.share import wiring
from zapret_update import manifest, share_discover, share_internet, share_manifest
from zapret_update.ed25519 import public_key_from_seed, sign_ed25519

SEED = hashlib.sha256(b"test key").digest()
KEYS = {"test-a": public_key_from_seed(SEED)}
VERSION = "99.1.2.3"
NAME = "Zapret2Setup_DEV_99_1_2_3.exe"
NEIGHBOUR = share_discover.Neighbour("93.184.216.34", 45000, tls=True)


def _signed(version=VERSION, *, sha="a" * 64, size=12345, seed=SEED):
    raw = manifest.dumps(
        {
            "schema": 1, "product": "zapret-gui", "channel": "dev", "version": version, "created": 1792000000,
            "key": "test-a", "files": [{"path": "_internal/Zapret.exe", "size": 3, "sha256": "b" * 64}],
            "installer": {"name": f"Zapret2Setup_DEV_{version.replace('.', '_')}.exe", "size": size, "sha256": sha},
        }
    )
    return raw, base64.b64encode(sign_ed25519(seed, raw)) + b"\n"


def _peer_release(raw, signature):
    parsed = manifest.load_signed(raw, signature, trusted=KEYS, product="zapret-gui", channel="dev")
    return share_manifest.PeerRelease(parsed, raw, signature, "93.184.216.34:45000")


class ReleaseFromPeersTests(unittest.TestCase):
    def _release(self, raw, signature, *, switch=True):
        with (
            patch("settings.store.get_share_updates_internet", return_value=switch),
            patch.object(share_internet, "find_internet_neighbours", return_value=[NEIGHBOUR]) as find,
            patch.object(share_manifest, "fetch_from_peers", return_value=_peer_release(raw, signature)),
            patch.object(wiring, "registry_bases", return_value=[]),
            patch.object(wiring, "peer_book", return_value=object()),
            patch("config.build_info.APP_VERSION", "21.1.5.79"),
        ):
            return wiring.release_from_peers("dev"), find

    def test_switch_off_means_no_search_at_all(self) -> None:
        with (
            patch("settings.store.get_share_updates_internet", return_value=False),
            patch.object(share_internet, "find_internet_neighbours") as find,
        ):
            self.assertIsNone(wiring.release_from_peers("dev"))
        find.assert_not_called()

    def test_unreadable_switch_means_no_search(self) -> None:
        with patch("settings.store.get_share_updates_internet", side_effect=OSError("db locked")):
            self.assertIsNone(wiring.release_from_peers("dev"))

    def test_neighbours_are_searched_for_any_version(self) -> None:
        _, find = self._release(*_signed())
        self.assertIsNone(find.call_args.args[2])  # версию нового выпуска мы пока не знаем

    def test_release_is_built_only_from_the_verified_list(self) -> None:
        raw, signature = _signed()
        release, _ = self._release(raw, signature)
        self.assertEqual(release["version"], VERSION)
        self.assertEqual(release["file_name"], NAME)
        self.assertEqual((release["file_size"], release["sha256"]), (12345, "a" * 64))
        # Адрес — обычный адрес выпуска на Forgejo, собранный нами, а не присланный соседом.
        self.assertEqual(
            release["update_url"],
            f"https://git.zapret.moe/zapretdiscordyoutube/zapretgui/releases/download/{VERSION}/{NAME}",
        )
        self.assertEqual(release["signed_blob"], (raw, signature))
        self.assertEqual(release["source"], "соседи по интернету")

    def test_such_release_passes_the_normal_artifact_checks_without_any_download(self) -> None:
        raw, signature = _signed()
        release, _ = self._release(raw, signature)

        class NoNetwork:
            def get(self, *_a, **_k):
                raise AssertionError("список уже у нас: скачивать нечего")

            def close(self):
                pass

        real = signed.fetch_signed_release

        def fetch(*args, **options):
            return real(*args, session_factory=NoNetwork, **options)

        with (
            patch.object(signed, "TRUSTED_KEYS", KEYS),
            patch("updater.download.flow.fetch_signed_release", fetch),
            patch("updater.download.flow.lookup_latest_release", return_value=ReleaseLookup(release)),
            patch("updater.download.flow.APP_VERSION", "21.1.5.79"),
            patch("updater.download.flow.CHANNEL", "dev"),
        ):
            artifact = resolve_artifact()
        self.assertEqual((artifact.version, artifact.expected_sha256), (VERSION, "a" * 64))
        self.assertEqual(artifact.signed.raw, raw)

    def test_tampered_list_from_a_neighbour_is_refused_by_the_normal_checks(self) -> None:
        raw, signature = _signed()
        release, _ = self._release(raw, signature)
        release["signed_blob"] = (raw.replace(b"99.1.2.3", b"99.9.9.9"), signature)
        with (
            patch.object(signed, "TRUSTED_KEYS", KEYS),
            patch("updater.download.flow.lookup_latest_release", return_value=ReleaseLookup(release)),
            patch("updater.download.flow.APP_VERSION", "21.1.5.79"),
            patch("updater.download.flow.CHANNEL", "dev"),
        ):
            with self.assertRaises(UpdateSignatureError):
                resolve_artifact()

    def test_neighbours_without_a_newer_release_give_nothing(self) -> None:
        with (
            patch("settings.store.get_share_updates_internet", return_value=True),
            patch.object(share_internet, "find_internet_neighbours", return_value=[NEIGHBOUR]),
            patch.object(share_manifest, "fetch_from_peers", return_value=None),
            patch.object(wiring, "registry_bases", return_value=[]),
            patch.object(wiring, "peer_book", return_value=object()),
        ):
            self.assertIsNone(wiring.release_from_peers("dev"))


class ResolverFallbackTests(unittest.TestCase):
    def _lookup(self, **kwargs):
        def fail(_channel):
            raise RuntimeError("blocked")

        return lookup_latest_release(
            "dev", fetch_forgejo=kwargs.pop("forgejo", fail), fetch_mirrors=fail,
            fallback_delay=0.0, forgejo_deadline=2.0, mirrors_deadline=2.0, **kwargs,
        )

    def test_peers_are_asked_only_when_both_sources_failed(self) -> None:
        asked = []
        lookup = self._lookup(fetch_peers=lambda channel: asked.append(channel) or {"version": "9.9.9.9", "source": "peers"})
        self.assertTrue(lookup.ok and lookup.release["source"] == "peers" and asked == ["dev"])
        asked.clear()
        ok = self._lookup(forgejo=lambda _c: {"version": "1.0.0.1", "source": "Forgejo"}, fetch_peers=lambda c: asked.append(c))
        self.assertEqual(ok.release["source"], "Forgejo")
        self.assertEqual(asked, [])  # пока сервер отвечает, соседей не спрашиваем

    def test_nothing_from_peers_keeps_the_old_plain_error(self) -> None:
        for peers in (lambda c: None, lambda c: (_ for _ in ()).throw(OSError("no peers"))):
            lookup = self._lookup(fetch_peers=peers)
            self.assertFalse(lookup.ok)
            self.assertIn("Не удалось узнать новейшую версию", lookup.error)

    def test_default_hook_is_the_wiring_function(self) -> None:
        with patch.object(wiring, "release_from_peers", return_value={"version": "9.9.9.9", "source": "peers"}) as hook:
            lookup = self._lookup()
        self.assertTrue(lookup.ok)
        hook.assert_called_once_with("dev")


if __name__ == "__main__":
    unittest.main()
