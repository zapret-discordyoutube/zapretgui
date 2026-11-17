"""BlockCheck проверяет по отдельной строке на каждый профиль Discord из пресетов."""

import unittest

from diagnostics.services import SERVICES


class DiscordServiceTargetsTests(unittest.TestCase):
    def test_every_discord_profile_has_its_own_row(self) -> None:
        hosts = {target.host: target.purpose for target in SERVICES["discord"].targets}

        # Профили пресета: Discord · сайт и приложение, обновления, картинки, голос и видео, vencord.dev
        # (списки lists/discord*.txt), плюс чат (gateway) и файлы (cdn).
        for host in (
            "discord.com",
            "gateway.discord.gg",
            "cdn.discordapp.com",
            "updates.discord.com",
            "media.discordapp.net",
            "images-ext-1.discordapp.net",
            "discord.media",
            "vencord.dev",
        ):
            with self.subTest(host=host):
                self.assertIn(host, hosts)
        self.assertEqual(len(hosts), len(SERVICES["discord"].targets), "адреса не должны повторяться")

    def test_only_the_site_is_the_main_address(self) -> None:
        main = [target.host for target in SERVICES["discord"].targets if target.main]

        self.assertEqual(main, ["discord.com"])


if __name__ == "__main__":
    unittest.main()
