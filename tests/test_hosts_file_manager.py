from __future__ import annotations

import os
import unittest
from unittest.mock import patch


class HostsFileManagerTests(unittest.TestCase):
    def test_hosts_manager_has_no_legacy_bootstrap(self) -> None:
        from hosts import hosts as hosts_module

        manager = hosts_module.HostsManager()
        self.assertFalse(hasattr(manager, "apply_hosts_bootstrap_if_needed"))

    def test_apply_draft_writes_selection_once_and_rereads_snapshot(self) -> None:
        from hosts import commands as hosts_commands

        calls: list[str] = []

        class FakeHostsManager:
            last_status = "Файл hosts обновлён"

            def apply_hosts_bootstrap_if_needed(self) -> None:
                raise AssertionError("legacy bootstrap must not run")

            def apply_service_dns_selections(self, service_dns) -> bool:
                calls.append(f"apply:{service_dns.get('ChatGPT')}")
                return True

            def add_adobe_domains(self) -> bool:
                calls.append("adobe:on")
                return True

            def remove_adobe_domains(self) -> bool:
                calls.append("adobe:off")
                return True

        snapshot = object()
        with (
            patch.object(hosts_commands, "create_hosts_manager", return_value=FakeHostsManager()),
            patch.object(hosts_commands, "save_user_selection", side_effect=lambda sel: calls.append(f"save:{sel}") or True),
            patch.object(hosts_commands, "load_page_snapshot", return_value=snapshot),
            patch.object(hosts_commands, "load_user_selection", return_value={"Discord": "hosts", "Rutor": "hosts"}),
            patch.object(hosts_commands, "check_addresses", side_effect=AssertionError("запись не ждёт проверку адресов")),
        ):
            result = hosts_commands.apply_hosts_draft(
                {"ChatGPT": "xbox_dns", "Discord": "hosts", "Rutor": "off"}, adobe=True
            )

        self.assertTrue(result.success)
        self.assertIs(result.snapshot, snapshot)
        self.assertEqual(
            calls,
            ["apply:xbox_dns", "save:{'ChatGPT': 'xbox_dns', 'Discord': 'hosts', 'Rutor': 'off'}", "adobe:on"],
        )
        # На проверку адресов уходит только то, что эта запись включила: Discord был включён и раньше,
        # Rutor выключен — проверять у них нечего.
        self.assertEqual(result.touched, ("ChatGPT",))

    def test_apply_draft_does_not_save_selection_when_write_fails(self) -> None:
        from hosts import commands as hosts_commands

        class FakeHostsManager:
            last_status = "Файл hosts недоступен для изменения"

            def apply_service_dns_selections(self, service_dns) -> bool:
                return False

            def add_adobe_domains(self) -> bool:
                raise AssertionError("Adobe не трогаем, если основная запись не удалась")

        with (
            patch.object(hosts_commands, "create_hosts_manager", return_value=FakeHostsManager()),
            patch.object(hosts_commands, "save_user_selection") as save,
            patch.object(hosts_commands, "load_page_snapshot", return_value=None),
            patch.object(hosts_commands, "load_user_selection", return_value={}),
        ):
            result = hosts_commands.apply_hosts_draft({"ChatGPT": "xbox_dns"}, adobe=True)

        self.assertFalse(result.success)
        self.assertEqual(result.message, "Файл hosts недоступен для изменения")
        save.assert_not_called()
        # Ничего не записано — проверять нечего.
        self.assertEqual(result.touched, ())

    def test_write_with_no_answering_relay_asks_the_profile_server_and_tries_once_more(self) -> None:
        # Comss 2026-11-01 перевёл 309 имён на новые посредники: старые молчат, а свежие знает сам сервер.
        from hosts import commands as hosts_commands
        from utils.probe_gate import Why

        calls: list[str] = []

        class FakeHostsManager:
            last_status = ""
            stranded_services: list[str] = []

            def apply_service_dns_selections(self, service_dns) -> bool:
                calls.append("apply")
                if len(calls) == 1:
                    self.stranded_services = ["Gemini AI"]
                    self.last_status = "Gemini AI: ни один сервер-посредник профиля не отвечает"
                    return False
                self.stranded_services = []
                self.last_status = "Файл hosts обновлён"
                return True

        def check(selection, *, why, recheck):
            calls.append(f"check:{why.name}:{sorted(recheck)}")

        with (
            patch.object(hosts_commands, "create_hosts_manager", return_value=FakeHostsManager()),
            patch.object(hosts_commands, "save_user_selection", side_effect=lambda sel: calls.append("save")),
            patch.object(hosts_commands, "load_page_snapshot", return_value=None),
            patch.object(hosts_commands, "load_user_selection", return_value={}),
            patch.object(hosts_commands, "check_addresses", side_effect=check),
        ):
            result = hosts_commands.apply_hosts_draft({"Gemini AI": "comss_dns"})

        self.assertTrue(result.success)
        self.assertEqual(calls, ["apply", f"check:{Why.USER.name}:['Gemini AI']", "apply", "save"])
        self.assertEqual(result.message, "Файл hosts обновлён")

    def test_other_write_failures_are_not_retried(self) -> None:
        from hosts import commands as hosts_commands

        class FakeHostsManager:
            last_status = "Файл hosts недоступен для изменения"
            stranded_services: list[str] = []

            def apply_service_dns_selections(self, service_dns) -> bool:
                return False

        with (
            patch.object(hosts_commands, "create_hosts_manager", return_value=FakeHostsManager()),
            patch.object(hosts_commands, "save_user_selection"),
            patch.object(hosts_commands, "load_page_snapshot", return_value=None),
            patch.object(hosts_commands, "load_user_selection", return_value={}),
            patch.object(hosts_commands, "check_addresses", side_effect=AssertionError("повторять нечего")),
        ):
            self.assertFalse(hosts_commands.apply_hosts_draft({"ChatGPT": "comss_dns"}).success)

    def test_check_after_the_write_rewrites_hosts_only_when_it_found_something_better(self) -> None:
        from hosts import commands as hosts_commands
        from hosts.address_engine import EngineReport
        from hosts.state import HostsCommandResult

        asked: list[tuple] = []

        def check(selection, *, why, recheck):
            asked.append((selection, why, sorted(recheck)))
            return report

        with (
            patch.object(hosts_commands, "check_addresses", side_effect=check),
            patch.object(hosts_commands, "refresh_applied_selection", return_value=HostsCommandResult(True, changed=True)) as refresh,
        ):
            report = EngineReport(checked=6, changed=False)
            same = hosts_commands.recheck_applied(("YouTube",))
            refresh.assert_not_called()

            report = EngineReport(checked=6, changed=True, dropped={"YouTube": ("1.1.1.1",)})
            better = hosts_commands.recheck_applied(("YouTube",))
            refresh.assert_called_once()

        # Проверяется сохранённый выбор и только названные сервисы; мёртвый адрес убирается сразу.
        from utils.probe_gate import Why

        self.assertEqual(asked[0], (None, Why.USER, ["YouTube"]))
        self.assertEqual((same.changed, same.message), (False, ""))
        self.assertEqual((better.changed, better.message), (True, "Неработающие адреса убраны: YouTube."))

    def test_apply_domain_rows_skips_write_when_content_is_unchanged(self) -> None:
        from hosts import hosts as hosts_module

        original = "\n".join(
            [
                "# >>> zapretgui:hosts managed begin >>>",
                "# Generated by ZapretGUI. Do not edit this block manually.",
                "2.2.2.2 chatgpt.com",
                "# <<< zapretgui:hosts managed end <<<",
                "",
            ]
        )
        manager = hosts_module.HostsManager()
        manager.is_hosts_file_accessible = lambda: True

        with (
            patch.object(hosts_module, "safe_read_hosts_file", return_value=original),
            patch.object(hosts_module, "safe_write_hosts_file", return_value=True) as write_hosts,
            patch.object(hosts_module, "is_ipv6_available", return_value=True),
        ):
            self.assertTrue(manager.apply_domain_ip_rows([("chatgpt.com", "2.2.2.2")]))

        write_hosts.assert_not_called()
        self.assertEqual(manager.last_status, "Файл hosts уже актуален: 1 запись")

    def test_apply_domain_rows_skips_ipv6_when_unavailable(self) -> None:
        from hosts import hosts as hosts_module

        written: list[str] = []
        manager = hosts_module.HostsManager()
        manager.is_hosts_file_accessible = lambda: True

        with (
            patch.object(hosts_module, "safe_read_hosts_file", return_value=""),
            patch.object(hosts_module, "safe_write_hosts_file", side_effect=lambda text: written.append(text) or True),
            patch.object(hosts_module, "is_ipv6_available", return_value=False, create=True),
        ):
            self.assertTrue(
                manager.apply_domain_ip_rows(
                    [
                        ("instagram.com", "157.240.245.174"),
                        ("instagram.com", "2a03:2880:f330:25:face:b00c:0:4420"),
                    ]
                )
            )

        self.assertEqual(len(written), 1)
        self.assertIn("157.240.245.174 instagram.com", written[0])
        self.assertNotIn("2a03:2880:f330:25:face:b00c:0:4420 instagram.com", written[0])

    def test_apply_domain_rows_does_not_clear_hosts_when_all_rows_are_filtered_ipv6(self) -> None:
        from hosts import hosts as hosts_module

        original = "\n".join(
            [
                "# >>> zapretgui:hosts managed begin >>>",
                "# Generated by ZapretGUI. Do not edit this block manually.",
                "2.2.2.2 old.example",
                "# <<< zapretgui:hosts managed end <<<",
                "",
            ]
        )
        written: list[str] = []
        manager = hosts_module.HostsManager()
        manager.is_hosts_file_accessible = lambda: True

        with (
            patch.object(hosts_module, "safe_read_hosts_file", return_value=original),
            patch.object(hosts_module, "safe_write_hosts_file", side_effect=lambda text: written.append(text) or True),
            patch.object(hosts_module, "is_ipv6_available", return_value=False, create=True),
        ):
            self.assertFalse(
                manager.apply_domain_ip_rows(
                    [("ipv6-only.example", "2a03:2880:f330:25:face:b00c:0:4420")]
                )
            )

        self.assertEqual(written, [])
        self.assertEqual(manager.last_status, "Нет подходящих hosts-записей для применения")

    def test_apply_domain_rows_replaces_only_zapretgui_managed_block(self) -> None:
        from hosts import hosts as hosts_module

        original = "\n".join(
            [
                "# user header",
                "10.0.0.1 manual.example",
                "# >>> zapretgui:hosts managed begin >>>",
                "# Generated by ZapretGUI. Do not edit this block manually.",
                "1.1.1.1 old.example",
                "# <<< zapretgui:hosts managed end <<<",
                "10.0.0.2 another.example",
                "",
            ]
        )
        written: list[str] = []
        manager = hosts_module.HostsManager()
        manager.is_hosts_file_accessible = lambda: True

        with (
            patch.object(hosts_module, "safe_read_hosts_file", return_value=original),
            patch.object(hosts_module, "safe_write_hosts_file", side_effect=lambda text: written.append(text) or True),
            patch.object(hosts_module, "is_ipv6_available", return_value=True),
        ):
            self.assertTrue(manager.apply_domain_ip_rows([("new.example", "2.2.2.2")]))

        self.assertEqual(len(written), 1)
        self.assertIn("10.0.0.1 manual.example", written[0])
        self.assertIn("10.0.0.2 another.example", written[0])
        self.assertIn("# >>> zapretgui:hosts managed begin >>>", written[0])
        self.assertIn("2.2.2.2 new.example", written[0])
        self.assertNotIn("1.1.1.1 old.example", written[0])

    def test_apply_domain_rows_places_managed_block_before_manual_hosts_entries(self) -> None:
        from hosts import hosts as hosts_module

        original = "\n".join(
            [
                "# user header",
                "10.0.0.1 manual.example",
                "10.0.0.2 another.example",
                "",
            ]
        )
        written: list[str] = []
        manager = hosts_module.HostsManager()
        manager.is_hosts_file_accessible = lambda: True

        with (
            patch.object(hosts_module, "safe_read_hosts_file", return_value=original),
            patch.object(hosts_module, "safe_write_hosts_file", side_effect=lambda text: written.append(text) or True),
            patch.object(hosts_module, "is_ipv6_available", return_value=True),
        ):
            self.assertTrue(manager.apply_domain_ip_rows([("chatgpt.com", "2.2.2.2")]))

        self.assertEqual(len(written), 1)
        self.assertIn("# user header", written[0])
        self.assertIn("10.0.0.1 manual.example", written[0])
        self.assertIn("10.0.0.2 another.example", written[0])
        self.assertIn("2.2.2.2 chatgpt.com", written[0])
        self.assertLess(written[0].index("# user header"), written[0].index("# >>> zapretgui:hosts managed begin >>>"))
        self.assertLess(written[0].index("2.2.2.2 chatgpt.com"), written[0].index("10.0.0.1 manual.example"))
        self.assertLess(written[0].index("2.2.2.2 chatgpt.com"), written[0].index("10.0.0.2 another.example"))

    def test_foreign_rows_that_fight_an_enabled_service_are_removed(self) -> None:
        """Решение владельца 2026-10-14: «такие записи надо удалять, даже если они ручные, — если они
        мешают и для нашего ресурса». В конце его hosts стояли две строки IPv6 для www.youtube.com:
        Windows ставила их впереди блока программы, а IPv6 на компьютере не отвечал."""
        from hosts import hosts as hosts_module

        original = "\n".join(
            [
                "# user header",
                "10.0.0.1 chatgpt.com",
                "10.0.0.2 another.example",
                "2001:db8::5 chatgpt.com extra.example  # заметка",
                "2.2.2.2 chatgpt.com",
                "# 10.0.0.9 chatgpt.com",
                "",
            ]
        )
        written: list[str] = []
        manager = hosts_module.HostsManager()
        manager.is_hosts_file_accessible = lambda: True

        with (
            patch.object(hosts_module, "safe_read_hosts_file", return_value=original),
            patch.object(hosts_module, "safe_write_hosts_file", side_effect=lambda text: written.append(text) or True),
            patch.object(hosts_module, "is_ipv6_available", return_value=True),
        ):
            self.assertTrue(manager.apply_domain_ip_rows([("chatgpt.com", "2.2.2.2")]))

        self.assertEqual(len(written), 1)
        mappings = [line for line in written[0].splitlines() if line.strip() and not line.lstrip().startswith("#")]
        self.assertEqual(
            mappings,
            [
                "2.2.2.2 chatgpt.com",  # блок программы
                "10.0.0.2 another.example",  # чужое имя не тронуто
                "2001:db8::5 extra.example # заметка",  # из строки на два имени ушло только мешающее
                "2.2.2.2 chatgpt.com",  # такая же строка, как в блоке, никому не мешает
            ],
        )
        # Закомментированная строка и заголовок пользователя на месте.
        self.assertIn("# 10.0.0.9 chatgpt.com", written[0])
        self.assertIn("# user header", written[0])
        self.assertEqual(
            manager.last_status,
            "Файл hosts обновлён: применено 1 запись. Убраны чужие строки, которые мешали: "
            "10.0.0.1 chatgpt.com, 2001:db8::5 chatgpt.com",
        )

    def test_blocks_of_other_features_are_not_touched_by_the_cleanup(self) -> None:
        from hosts import hosts as hosts_module

        lines = [
            "# >>> zapretgui:russian-state-media-block begin >>>\n",
            "0.0.0.0 chatgpt.com\n",
            "# <<< zapretgui:russian-state-media-block end <<<\n",
            "# MAX BLOCKED BY ZAPRET GUI\n",
            "127.0.0.1 chatgpt.com\n",
            "# END MAX BLOCK\n",
            "# --- Telegram Proxy (auto-managed by Zapret 2 GUI) ---\n",
            "149.154.167.220 chatgpt.com\n",
            "\n",
            "9.9.9.9 chatgpt.com\n",
        ]
        kept, dropped = hosts_module._drop_conflicting_foreign_rows(lines, [("chatgpt.com", "2.2.2.2")])
        self.assertEqual(dropped, ["9.9.9.9 chatgpt.com"])
        self.assertEqual(kept, lines[:-1])
        # Выключенному сервису чужие строки не мешают: без строк блока не убирается ничего.
        self.assertEqual(hosts_module._drop_conflicting_foreign_rows(lines, []), (lines, []))

    def test_startup_refresh_rewrites_hosts_when_a_foreign_row_fights_the_block(self) -> None:
        from hosts import hosts as hosts_module

        block = "# >>> zapretgui:hosts managed begin >>>\n# Generated by ZapretGUI. Do not edit this block manually.\n2.2.2.2 chatgpt.com\n# <<< zapretgui:hosts managed end <<<\n"
        manager = hosts_module.HostsManager()
        applied: list[dict] = []
        manager.apply_service_dns_selections = lambda selection: applied.append(selection) or True
        for tail, expected in (("10.0.0.2 another.example\n", False), ("2001:db8::5 chatgpt.com\n", True)):
            applied.clear()
            with (
                patch.object(hosts_module, "safe_read_hosts_file", return_value=block + tail),
                patch.object(hosts_module, "is_ipv6_available", return_value=True),
                patch.object(hosts_module, "_build_service_selection_rows", return_value=([("chatgpt.com", "2.2.2.2")], True)),
            ):
                changed, reason = manager.refresh_applied_service_selection({"ChatGPT": "p"}, has_saved_selection=True)
            self.assertEqual(changed, expected, reason)
            self.assertEqual(bool(applied), expected)

    def test_apply_domain_rows_does_not_shift_block_down_on_repeated_updates(self) -> None:
        from hosts import hosts as hosts_module

        original = "\n".join(
            [
                "# user header",
                "10.0.0.1 chatgpt.com",
                "10.0.0.2 another.example",
                "",
            ]
        )
        written: list[str] = []
        current_content = {"text": original}
        manager = hosts_module.HostsManager()
        manager.is_hosts_file_accessible = lambda: True

        def write_hosts(text: str) -> bool:
            written.append(text)
            current_content["text"] = text
            return True

        with (
            patch.object(hosts_module, "safe_read_hosts_file", side_effect=lambda: current_content["text"]),
            patch.object(hosts_module, "safe_write_hosts_file", side_effect=write_hosts),
            patch.object(hosts_module, "is_ipv6_available", return_value=True),
        ):
            self.assertTrue(manager.apply_domain_ip_rows([("chatgpt.com", "2.2.2.2")]))
            self.assertTrue(manager.apply_domain_ip_rows([("chatgpt.com", "3.3.3.3")]))

        self.assertEqual(len(written), 2)
        first_lines = written[0].splitlines()
        second_lines = written[1].splitlines()
        first_begin = first_lines.index("# >>> zapretgui:hosts managed begin >>>")
        second_begin = second_lines.index("# >>> zapretgui:hosts managed begin >>>")
        self.assertEqual(second_begin, first_begin)
        self.assertNotIn("\n\n\n# >>> zapretgui:hosts managed begin >>>", written[1])

    def test_apply_domain_rows_keeps_other_domains_from_same_hosts_line(self) -> None:
        from hosts import hosts as hosts_module

        original = "\n".join(
            [
                "# user header",
                "10.0.0.1 chatgpt.com manual.example # keep",
                "10.0.0.3 chatgpt.com",
                "",
            ]
        )
        written: list[str] = []
        manager = hosts_module.HostsManager()
        manager.is_hosts_file_accessible = lambda: True

        with (
            patch.object(hosts_module, "safe_read_hosts_file", return_value=original),
            patch.object(hosts_module, "safe_write_hosts_file", side_effect=lambda text: written.append(text) or True),
            patch.object(hosts_module, "is_ipv6_available", return_value=True),
        ):
            self.assertTrue(manager.apply_domain_ip_rows([("chatgpt.com", "2.2.2.2")]))

        self.assertEqual(len(written), 1)
        self.assertIn("2.2.2.2 chatgpt.com", written[0])
        # Чужое имя из той же строки остаётся вместе с заметкой; уходит только имя включённого сервиса.
        self.assertIn("10.0.0.1 manual.example # keep", written[0])
        self.assertNotIn("10.0.0.1 chatgpt.com", written[0])
        self.assertLess(written[0].index("2.2.2.2 chatgpt.com"), written[0].index("10.0.0.1 manual.example"))

    def test_apply_service_selection_with_unknown_rows_does_not_clear_existing_block(self) -> None:
        from hosts import hosts as hosts_module

        written: list[str] = []
        manager = hosts_module.HostsManager()
        manager.is_hosts_file_accessible = lambda: True

        with (
            patch.object(hosts_module, "get_service_domain_ip_rows", return_value=[]),
            patch.object(hosts_module, "safe_write_hosts_file", side_effect=lambda text: written.append(text) or True),
        ):
            self.assertFalse(manager.apply_service_dns_selections({"Missing": "zapret_dns"}))

        self.assertEqual(written, [])
        self.assertEqual(manager.last_status, "Не найдено записей hosts для выбранных сервисов")

    def test_reading_missing_hosts_file_does_not_create_it(self) -> None:
        import tempfile
        from pathlib import Path

        from hosts import hosts as hosts_module

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "etc" / "hosts"
            with patch.object(hosts_module, "HOSTS_PATH", missing):
                hosts_module.invalidate_hosts_file_cache()
                self.assertEqual(hosts_module.safe_read_hosts_file(), "")
                self.assertTrue(hosts_module.HostsManager().is_hosts_file_accessible())
            self.assertFalse(missing.exists())
            self.assertFalse(missing.parent.exists())

    def test_write_does_not_strip_read_only_protection(self) -> None:
        import stat
        import tempfile
        from pathlib import Path

        from hosts import hosts as hosts_module

        with tempfile.TemporaryDirectory() as tmp:
            hosts_path = Path(tmp) / "hosts"
            hosts_path.write_text("10.0.0.1 manual.example\n", encoding="utf-8")
            os.chmod(hosts_path, stat.S_IREAD)
            try:
                with patch.object(hosts_module, "HOSTS_PATH", hosts_path):
                    hosts_module.invalidate_hosts_file_cache()
                    self.assertFalse(hosts_module.safe_write_hosts_file("changed\n"))
                    self.assertFalse(hosts_module.HostsManager().is_hosts_file_accessible())
                self.assertFalse(os.stat(hosts_path).st_mode & stat.S_IWRITE)
                self.assertEqual(hosts_path.read_text(encoding="utf-8"), "10.0.0.1 manual.example\n")
            finally:
                os.chmod(hosts_path, stat.S_IREAD | stat.S_IWRITE)
                hosts_module.invalidate_hosts_file_cache()

    def test_managed_block_without_end_marker_keeps_user_lines_below(self) -> None:
        from hosts import hosts as hosts_module

        lines = [
            "127.0.0.1 localhost\n",
            "\n",
            f"{hosts_module._MANAGED_HOSTS_BEGIN}\n",
            f"{hosts_module._MANAGED_HOSTS_NOTICE}\n",
            "1.1.1.1 managed.example\n",
            "\n",
            "10.0.0.1 my.lan\n",
        ]

        kept, removed = hosts_module._remove_managed_hosts_block(lines)

        self.assertEqual(removed, 1)
        self.assertIn("10.0.0.1 my.lan\n", kept)
        self.assertIn("127.0.0.1 localhost\n", kept)
        self.assertNotIn("1.1.1.1 managed.example\n", kept)
        self.assertEqual(
            hosts_module._iter_managed_hosts_block_rows(lines),
            [("managed.example", "1.1.1.1")],
        )

    def test_cp1251_hosts_file_is_accessible(self) -> None:
        import tempfile
        from pathlib import Path

        from hosts import hosts as hosts_module

        with tempfile.TemporaryDirectory() as tmp:
            hosts_path = Path(tmp) / "hosts"
            hosts_path.write_bytes("# мой комментарий\n127.0.0.1 localhost\n".encode("cp1251"))
            with patch.object(hosts_module, "HOSTS_PATH", hosts_path):
                hosts_module.invalidate_hosts_file_cache()
                try:
                    self.assertEqual(hosts_module.check_hosts_file_name(), (True, None))
                    self.assertTrue(hosts_module.HostsManager().is_hosts_file_accessible())
                finally:
                    hosts_module.invalidate_hosts_file_cache()

    def test_failed_write_restores_previous_hosts(self) -> None:
        import tempfile
        from pathlib import Path

        from hosts import hosts as hosts_module

        with tempfile.TemporaryDirectory() as tmp:
            hosts_path = Path(tmp) / "hosts"
            hosts_path.write_text("10.0.0.1 manual.example\n", encoding="utf-8")
            with patch.object(hosts_module, "HOSTS_PATH", hosts_path):
                hosts_module.invalidate_hosts_file_cache()
                try:
                    with patch.object(hosts_module.os, "fsync", side_effect=OSError(28, "No space left")):
                        self.assertFalse(hosts_module.safe_write_hosts_file("changed\n"))
                    self.assertEqual(hosts_path.read_text(encoding="utf-8"), "10.0.0.1 manual.example\n")
                    # Строка, которую нельзя закодировать, не трогает файл вовсе.
                    self.assertFalse(hosts_module.safe_write_hosts_file("bad \udcff\n"))
                    self.assertEqual(hosts_path.read_text(encoding="utf-8"), "10.0.0.1 manual.example\n")
                finally:
                    hosts_module.invalidate_hosts_file_cache()

    def test_ipv6_detection_uses_winapi_on_windows(self) -> None:
        from hosts import ipv6_detection

        ipv6_detection.reset_ipv6_detection_cache()
        self.addCleanup(ipv6_detection.reset_ipv6_detection_cache)
        with (
            patch.object(ipv6_detection.os, "name", "nt"),
            patch.object(ipv6_detection, "_is_ipv6_available_winapi", return_value=True) as winapi_probe,
            patch.object(ipv6_detection, "_is_ipv6_available_socket_probe", return_value=False) as socket_probe,
        ):
            self.assertTrue(ipv6_detection.is_ipv6_available())

        winapi_probe.assert_called_once()
        socket_probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
