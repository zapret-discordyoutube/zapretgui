from __future__ import annotations

import unittest

from profile.parser import parse_preset_text
from profile.serializer import serialize_preset, with_profile_raw_text
from settings.mode import ENGINE_WINWS2


class ProfileRawEditorTests(unittest.TestCase):
    def test_raw_profile_text_replaces_only_selected_profile(self) -> None:
        preset = parse_preset_text(
            "\n".join(
                (
                    "--filter-tcp=80,443",
                    "--hostlist=lists/youtube.txt",
                    "--lua-desync=pass",
                    "",
                    "--new",
                    "--filter-udp=443",
                    "--hostlist=lists/discord.txt",
                    "--lua-desync=pass",
                    "",
                )
            ),
            engine=ENGINE_WINWS2,
            source_name="test.txt",
        )

        updated = with_profile_raw_text(
            preset,
            0,
            "\n".join(
                (
                    "--name=custom youtube",
                    "--filter-tcp=80,443",
                    "--ipset=lists/ipset-youtube.txt",
                    "--in-range=x",
                    "--out-range=d10",
                    "--lua-desync=fake",
                )
            ),
        )
        text = serialize_preset(updated)

        self.assertIn("--name=custom youtube", text)
        self.assertIn("--ipset=lists/ipset-youtube.txt", text)
        self.assertIn("--hostlist=lists/discord.txt", text)
        self.assertNotIn("--hostlist=lists/youtube.txt", text)

    def test_raw_profile_text_keeps_leading_comments(self) -> None:
        preset = parse_preset_text(
            "--filter-tcp=80\n--lua-desync=pass\n\n--new=YT\n\n# YouTube profile\n--filter-tcp=443\n--lua-desync=pass\n",
            engine=ENGINE_WINWS2,
            source_name="test.txt",
        )

        updated = with_profile_raw_text(
            preset,
            1,
            "# YouTube profile\n--filter-tcp=443\n--lua-desync=fake\n",
        )

        text = serialize_preset(updated)
        # Комментарий над профилем раньше уходил в шапку разобранного текста
        # и молча пропадал; имя из «--new=YT» не заменяется автоименем.
        self.assertIn("--new=YT\n\n# YouTube profile\n--filter-tcp=443\n--lua-desync=fake", text)
        self.assertIn("--filter-tcp=80\n--lua-desync=pass", text)

    def test_raw_profile_text_rejects_multiple_profiles(self) -> None:
        preset = parse_preset_text(
            "--filter-tcp=80,443\n--hostlist=lists/youtube.txt\n",
            engine=ENGINE_WINWS2,
            source_name="test.txt",
        )

        with self.assertRaises(ValueError):
            with_profile_raw_text(
                preset,
                0,
                "--filter-tcp=80,443\n--hostlist=lists/youtube.txt\n--new\n--filter-udp=443\n",
            )


if __name__ == "__main__":
    unittest.main()
