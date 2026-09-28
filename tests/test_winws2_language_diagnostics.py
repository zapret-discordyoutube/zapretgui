"""Проверка текста пресета winws2 в редакторе: где ошибка и как исправить."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

BUILTIN_WINWS2 = PROJECT_SRC / "presets" / "builtin" / "winws2"
HEAD = "--wf-tcp-out=80,443\n"


def diagnose(text: str, **kwargs):
    from profile.winws2_language import diagnose_winws2_text

    return diagnose_winws2_text(text, **kwargs)


def apply_fix(text: str, fix) -> str:
    lines = text.split("\n")
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)
    result = text
    for edit in sorted(fix.edits, key=lambda e: (e.start_line, e.start_column), reverse=True):
        start = offsets[edit.start_line] + edit.start_column
        end = offsets[edit.end_line] + edit.end_column
        result = result[:start] + edit.text + result[end:]
    return result


class Winws2DiagnosticsTests(unittest.TestCase):
    def errors(self, text: str, **kwargs):
        return [d for d in diagnose(text, **kwargs) if d.severity == "error"]

    def only(self, text: str, severity: str, needle: str, **kwargs):
        found = [d for d in diagnose(text, **kwargs) if d.severity == severity and needle in d.message]
        self.assertEqual(len(found), 1, [(d.severity, d.message) for d in diagnose(text, **kwargs)])
        return found[0]

    def test_all_builtin_presets_have_no_errors(self) -> None:
        paths = sorted(BUILTIN_WINWS2.glob("*.txt"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(preset=path.name):
                errors = self.errors(path.read_text(encoding="utf-8"))
                self.assertEqual([(d.line + 1, d.message) for d in errors], [])

    def test_unknown_option_is_marked_exactly_with_suggestion(self) -> None:
        text = HEAD + "--filter-tcp=443\n--hostlst=lists/a.txt\n--lua-desync=pass\n"
        problem = self.only(text, "error", "Неизвестная опция")
        self.assertEqual((problem.line, problem.start, problem.end), (2, 0, 9))
        fixed = apply_fix(text, problem.fixes[0])
        self.assertIn("--hostlist=lists/a.txt", fixed)
        self.assertEqual(self.errors(fixed), [])

    def test_option_without_equals_swallows_next_option(self) -> None:
        text = HEAD + "--filter-tcp=443\n--hostlist\n--new\n--filter-udp=443\n--lua-desync=pass\n"
        problem = self.only(text, "error", "заберёт следующий аргумент «--new»")
        self.assertEqual(problem.line, 2)

    def test_optional_value_on_next_line_is_lost(self) -> None:
        text = HEAD + "--filter-tcp=443\n--lua-desync=pass\n--new\nmy_profile\n--filter-udp=443\n--lua-desync=pass\n"
        problem = self.only(text, "error", "Значение потеряется")
        self.assertEqual(problem.line, 4)
        self.assertIn("--new=my_profile", apply_fix(text, problem.fixes[0]))

    def test_value_after_space_inside_one_argument(self) -> None:
        text = HEAD + "--filter-tcp=443\n--hostlist lists/a.txt\n--lua-desync=pass\n"
        problem = self.only(text, "error", "Значение через пробел")
        self.assertIn("--hostlist=lists/a.txt", apply_fix(text, problem.fixes[0]))

    def test_linux_only_option_is_error(self) -> None:
        problem = self.only(HEAD + "--qnum=200\n--filter-tcp=443\n--lua-desync=pass\n", "error", "Linux")
        self.assertEqual(problem.line, 1)

    def test_bad_values_are_marked_on_value(self) -> None:
        text = HEAD + "--filter-tcp=443,70000\n--payload=tls_clinet_hello\n--lua-desync=pass\n--out-range=-z8\n"
        port = self.only(text, "error", "65535")
        self.assertEqual((port.line, port.start, port.end), (1, 13, 22))
        payload = self.only(text, "error", "tls_clinet_hello")
        self.assertIn("tls_client_hello", apply_fix(text, payload.fixes[0]))
        self.only(text, "error", "Неверный диапазон")

    def test_lua_function_from_unloaded_file_offers_lua_init(self) -> None:
        text = "--lua-init=@lua/zapret-lib.lua\n" + HEAD + "--filter-tcp=443\n--lua-desync=rst_flood\n"
        problem = self.only(text, "error", "zapret-rst-flood.lua")
        fixed = apply_fix(text, problem.fixes[0])
        self.assertIn("--lua-init=@lua/zapret-rst-flood.lua", fixed)
        self.assertEqual(self.errors(fixed), [])

    def test_unknown_function_only_when_all_lua_files_are_known(self) -> None:
        text = HEAD + "--filter-tcp=443\n--lua-desync=faek:blob=fake_default_tls\n"
        problem = self.only(text, "error", "«faek»")
        self.assertIn("fake:blob", apply_fix(text, problem.fixes[0]))
        custom = "--lua-init=@user/my.lua\n" + text
        self.assertEqual([d for d in self.errors(custom) if "faek" in d.message], [])

    def test_undeclared_blob_offers_declaration_from_catalog(self) -> None:
        from profile.winws2_language import LanguageContext

        context = LanguageContext(fake_values={"tls_google": "@bin/tls_clienthello_www_google_com.bin"})
        text = "--blob=tls1:@bin/a.bin\n" + HEAD + "--filter-tcp=443\n--lua-desync=fake:blob=tls_google\n"
        problem = self.only(text, "error", "«tls_google» не объявлен", context=context)
        fixed = apply_fix(text, problem.fixes[0])
        self.assertTrue(fixed.startswith("--blob=tls1:@bin/a.bin\n--blob=tls_google:@bin/"))
        self.assertEqual(self.errors(fixed, context=context), [])

    def test_duplicate_and_builtin_blob_names(self) -> None:
        text = "--blob=a:0x00\n--blob=a:0x01\n--blob=fake_default_tls:0x00\n" + HEAD + "--filter-tcp=443\n--lua-desync=pass\n"
        self.only(text, "error", "уже объявлен")
        self.only(text, "error", "встроенный фейк")

    def test_lua_argument_typo_is_warning_with_fix(self) -> None:
        text = HEAD + "--filter-tcp=443\n--lua-desync=fake:blob=fake_default_tls:repeat=2\n"
        problem = self.only(text, "warning", "repeats")
        self.assertIn(":repeats=2", apply_fix(text, problem.fixes[0]))

    def test_launch_rejections_are_errors_in_editor(self) -> None:
        # Всё, что не пропускает проверка перед запуском, редактор показывает ошибкой.
        cases = {
            "нет фильтра перехвата": "--filter-tcp=443\n--lua-desync=pass\n",
            "только выключенные профили": HEAD + "--filter-tcp=443\n--skip\n--lua-desync=pass\n",
            "strategy вне circular": HEAD + "--filter-tcp=443\n--lua-desync=fake:blob=fake_default_tls:strategy=1\n",
            "неверный out-range": HEAD + "--filter-tcp=443\n--out-range=bad\n--lua-desync=pass\n",
            "неверный payload": HEAD + "--filter-tcp=443\n--payload=nope\n--lua-desync=pass\n",
        }
        for title, text in cases.items():
            with self.subTest(case=title):
                self.assertTrue(self.errors(text))

    def test_valid_circular_strategy_tags_are_not_errors(self) -> None:
        text = HEAD + "--filter-tcp=443\n--lua-desync=circular\n--lua-desync=fake:blob=fake_default_tls:strategy=1\n"
        self.assertEqual(self.errors(text), [])

    def test_fragment_mode_uses_preset_context(self) -> None:
        from profile.winws2_language import LanguageContext

        fragment = "--filter-tcp=443\n--lua-desync=fake:blob=tls1\n--ctrack-disable=1\n"
        context = LanguageContext(preset_text="--blob=tls1:@bin/a.bin\n" + HEAD)
        diagnostics = diagnose(fragment, fragment=True, context=context)
        self.assertEqual([d for d in diagnostics if d.severity == "error"], [])
        self.assertTrue(any("общая опция" in d.message for d in diagnostics))

    def test_missing_files_are_reported_from_file_facts(self) -> None:
        from profile.winws2_language import LanguageContext, collect_winws2_file_facts

        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "lists").mkdir()
            (Path(root) / "lists" / "ok.txt").write_text("a.com\n", encoding="utf-8")
            text = HEAD + "--filter-tcp=443\n--hostlist=lists/ok.txt\n--hostlist=lists/missing.txt\n--lua-desync=pass\n"
            facts = collect_winws2_file_facts(text, root)
            self.assertEqual(facts.listings["lists"], ("ok.txt",))
            problem = self.only(text, "error", "Файл не найден", context=LanguageContext(file_facts=facts))
            self.assertEqual(problem.line, 3)

    def test_diagnostics_never_change_text(self) -> None:
        text = HEAD + "--filter-tcp=443\n--lua-desync=pass\n"
        before = str(text)
        diagnose(text)
        self.assertEqual(text, before)


if __name__ == "__main__":
    unittest.main()
