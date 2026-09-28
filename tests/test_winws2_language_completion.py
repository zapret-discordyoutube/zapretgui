"""Подсказки при наборе, описания при наведении и быстрые действия winws2."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

HEAD = "--wf-tcp-out=80,443\n--filter-tcp=443\n"


def complete(text: str, line: int, column: int, **kwargs):
    from profile.winws2_language import complete_winws2

    return complete_winws2(text, line, column, **kwargs)


class Winws2CompletionTests(unittest.TestCase):
    def labels(self, result) -> list[str]:
        self.assertIsNotNone(result)
        return [item.label for item in result.items]

    def test_option_names_are_filtered_by_typed_prefix(self) -> None:
        text = HEAD + "--pay"
        result = complete(text, 2, 5)
        self.assertEqual(self.labels(result)[:2], ["--payload", "--payload-disable"])
        self.assertEqual((result.start, result.end), (0, 5))
        payload = result.items[0]
        self.assertEqual(payload.insert_text, "--payload=")
        self.assertTrue(payload.reopen)

    def test_windows_only_options_are_offered_and_linux_ones_are_not(self) -> None:
        labels = self.labels(complete("--", 0, 2, explicit=True))
        self.assertIn("--wf-tcp-out", labels)
        self.assertNotIn("--qnum", labels)
        self.assertNotIn("--filter-mark", labels)

    def test_payload_list_completes_the_current_item(self) -> None:
        text = HEAD + "--payload=http_req,tls_cl"
        result = complete(text, 2, len("--payload=http_req,tls_cl"))
        self.assertEqual(self.labels(result)[0], "tls_client_hello")
        self.assertEqual(result.start, len("--payload=http_req,"))

    def test_lua_function_then_arguments_then_values(self) -> None:
        functions = complete(HEAD + "--lua-desync=fak", 2, len("--lua-desync=fak"))
        self.assertEqual(self.labels(functions)[0], "fake")
        args = complete(HEAD + "--lua-desync=fake:bl", 2, len("--lua-desync=fake:bl"))
        self.assertIn("blob", self.labels(args))
        self.assertEqual(args.items[0].insert_text, "blob=")
        text = "--blob=tls_vk:@bin/tls_vk.bin\n" + HEAD + "--lua-desync=fake:blob="
        values = complete(text, 3, len("--lua-desync=fake:blob="))
        labels = self.labels(values)
        self.assertIn("tls_vk", labels)
        self.assertIn("fake_default_tls", labels)
        dirs = complete(HEAD + "--lua-desync=fake:dir=", 2, len("--lua-desync=fake:dir="))
        self.assertEqual(self.labels(dirs), ["out", "in", "any"])

    def test_function_from_unloaded_file_is_marked(self) -> None:
        result = complete(HEAD + "--lua-desync=rst_f", 2, len("--lua-desync=rst_f"))
        self.assertIn("--lua-init=@lua/zapret-rst-flood.lua", result.items[0].detail)

    def test_blob_name_completion_inserts_catalog_value(self) -> None:
        from profile.winws2_language import LanguageContext

        context = LanguageContext(fake_values={"tls_google": "@bin/tls_google.bin"})
        result = complete("--blob=tls_g", 0, len("--blob=tls_g"), context=context)
        self.assertEqual(result.items[0].insert_text, "tls_google:@bin/tls_google.bin")

    def test_no_popup_in_comments_or_plain_text(self) -> None:
        self.assertIsNone(complete("# --pay", 0, 7))
        self.assertIsNone(complete("--name=Мой профиль", 0, 10))

    def test_equals_is_not_doubled_when_already_present(self) -> None:
        text = HEAD + "--lua-desync=fake:bl=x"
        result = complete(text, 2, len("--lua-desync=fake:bl"))
        self.assertEqual(result.items[0].insert_text, "blob")


class Winws2HoverAndActionsTests(unittest.TestCase):
    def test_describe_option_function_and_argument(self) -> None:
        from profile.winws2_language import describe_winws2_at

        text = "--lua-desync=fake:blob=fake_default_tls:ip_ttl=4\n"
        self.assertIn("--lua-desync", describe_winws2_at(text, 0, 3))
        self.assertIn("fake —", describe_winws2_at(text, 0, 14))
        self.assertIn("встроенный", describe_winws2_at(text, 0, 26))
        self.assertIn("обман DPI", describe_winws2_at(text, 0, len("--lua-desync=fake:blob=fake_default_tls:ip_")))

    def test_quick_action_declares_all_missing_fakes_at_once(self) -> None:
        from profile.winws2_language import LanguageContext, quick_actions_winws2

        context = LanguageContext(fake_values={"a1": "@bin/a1.bin", "b2": "0x00"})
        text = "--blob=x:0x00\n" + HEAD + "--lua-desync=fake:blob=a1\n--lua-desync=fake:blob=b2\n"
        actions = quick_actions_winws2(text, 3, 0, context=context)
        declare = [action for action in actions if "фейки" in action.title]
        self.assertEqual(len(declare), 1)
        edit = declare[0].edits[0]
        self.assertEqual(edit.text, "\n--blob=a1:@bin/a1.bin\n--blob=b2:0x00")
        self.assertEqual(edit.start_line, 0)


if __name__ == "__main__":
    unittest.main()
