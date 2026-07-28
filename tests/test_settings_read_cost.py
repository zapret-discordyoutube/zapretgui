from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from settings import store as settings_store


class SettingsReadCostTests(unittest.TestCase):
    """Чтение настроек не должно копировать весь документ ради одного значения."""

    def setUp(self) -> None:
        self._temp_dir = TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        patcher = patch.object(settings_store, "MAIN_DIRECTORY", self._temp_dir.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        settings_store.reset_settings()

    def test_scalar_getter_does_not_copy_whole_document(self) -> None:
        with patch.object(settings_store, "read_settings") as read_settings:
            settings_store.get_dpi_autostart()

        read_settings.assert_not_called()

    def test_scalar_getter_returns_stored_value(self) -> None:
        settings_store.set_dpi_autostart(False)
        self.assertFalse(settings_store.get_dpi_autostart())

        settings_store.set_dpi_autostart(True)
        self.assertTrue(settings_store.get_dpi_autostart())

    def test_container_value_is_detached_from_cache(self) -> None:
        settings_store.set_hosts_selection({"youtube": "default"})

        selection = settings_store.get_hosts_selection()
        selection["youtube"] = "hacked"
        selection["discord"] = "hacked"

        self.assertEqual(settings_store.get_hosts_selection(), {"youtube": "default"})

    def test_section_copy_is_detached_from_cache(self) -> None:
        program = settings_store.get_program_settings()
        program["dpi_autostart"] = "corrupted"

        self.assertNotEqual(settings_store.get_program_settings().get("dpi_autostart"), "corrupted")

    def test_custom_dns_servers_are_detached_from_cache(self) -> None:
        stored = settings_store.set_custom_dns_servers([{"name": "One", "primary": "1.1.1.1"}])

        servers = settings_store.get_custom_dns_servers()
        servers.append({"name": "Hacked", "primary": "8.8.8.8"})
        if servers and isinstance(servers[0], dict):
            servers[0]["primary"] = "9.9.9.9"

        self.assertEqual(settings_store.get_custom_dns_servers(), stored)

    def test_read_settings_still_returns_detached_document(self) -> None:
        data = settings_store.read_settings()
        data["program"]["dpi_autostart"] = "corrupted"

        self.assertNotEqual(
            settings_store.read_settings()["program"]["dpi_autostart"],
            "corrupted",
        )

    def test_write_is_visible_to_the_next_scalar_read(self) -> None:
        settings_store.set_tray_close_mode("minimize_only")
        self.assertEqual(settings_store.get_tray_close_mode(), "minimize_only")


if __name__ == "__main__":
    unittest.main()
