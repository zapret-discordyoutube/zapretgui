"""Страница «Фейки»: навигация, зависимости и заполнение таблицы."""

from __future__ import annotations

from unittest.mock import MagicMock, Mock

from app.page_names import PageName
from ui.widgets.fluent_item_tooltip import FLUENT_ITEM_TOOLTIP_ROLE


def test_fakes_page_is_nested_winws2_page():
    from settings.mode import ORCHESTRA_MODE, ZAPRET1_MODE, ZAPRET2_MODE
    from ui.navigation import schema

    spec = schema.get_page_spec(PageName.FAKES)
    assert spec.breadcrumb_parent == PageName.ZAPRET2_MODE_CONTROL
    assert spec.is_hidden and not spec.is_top_level
    assert schema.get_breadcrumb_chain(PageName.FAKES) == (PageName.ZAPRET2_MODE_CONTROL, PageName.FAKES)
    assert schema.is_page_allowed_for_method(PageName.FAKES, ZAPRET2_MODE)
    assert not schema.is_page_allowed_for_method(PageName.FAKES, ZAPRET1_MODE)
    assert not schema.is_page_allowed_for_method(PageName.FAKES, ORCHESTRA_MODE)
    assert PageName.FAKES in schema.get_hidden_pages_for_method(ZAPRET2_MODE)
    assert PageName.FAKES not in schema.get_hidden_pages_for_method(ZAPRET1_MODE)
    for method in (ZAPRET2_MODE, ZAPRET1_MODE):
        assert PageName.FAKES not in schema.get_sidebar_pages_for_method(method)
    assert PageName.FAKES in schema.PAGE_CLEANUP_ORDER


def test_fakes_page_deps_are_narrow_worker_factories():
    from ui.page_composition import PAGE_DEPS_BUILDERS
    from ui.page_deps.system import build_fakes_page_kwargs

    spec = PAGE_DEPS_BUILDERS[PageName.FAKES]
    assert set(spec.features) == {"fakes", "external_actions"}

    fakes_feature = Mock()
    external_actions = Mock()
    show_page = Mock()
    deps = build_fakes_page_kwargs(
        page_name=PageName.FAKES,
        fakes_feature=fakes_feature,
        external_actions_feature=external_actions,
        show_page=show_page,
    )["deps"]

    assert deps.create_snapshot_worker is fakes_feature.create_snapshot_worker
    assert deps.create_import_worker is fakes_feature.create_import_worker
    assert deps.create_delete_worker is fakes_feature.create_delete_worker
    deps.open_control_page()
    show_page.assert_called_once_with(PageName.ZAPRET2_MODE_CONTROL)
    deps.create_open_folder_worker(7, parent="p")
    external_actions.create_external_action_worker.assert_called_once_with(
        7,
        action_name="open_user_fakes_folder",
        action_fn=fakes_feature.open_user_fakes_folder,
        parent="p",
    )


def test_only_zapret2_control_page_gets_open_fakes():
    from ui.page_deps.presets import build_control_page_kwargs

    def _kwargs(page_name, show_page):
        return build_control_page_kwargs(
            page_name=page_name,
            presets_feature=Mock(),
            profile_feature=Mock(),
            launch_control=Mock(),
            program_settings_feature=Mock(),
            external_actions_feature=Mock(),
            set_status=Mock(),
            request_exit=Mock(),
            open_connection_test=Mock(),
            open_folder=Mock(),
            show_page=show_page,
            start_onboarding_tour=Mock(),
            ui_state_store=Mock(),
        )

    show_page = Mock()
    _kwargs(PageName.ZAPRET2_MODE_CONTROL, show_page)["open_fakes"]()
    show_page.assert_called_once_with(PageName.FAKES, allow_internal=True)
    assert "open_fakes" not in _kwargs(PageName.ZAPRET1_MODE_CONTROL, Mock())


def _snapshot():
    from fakes.user_fakes import FakeNameRules, FakeRow, FakesPageSnapshot

    rows = (
        FakeRow("tls_google", "tls_clienthello_www_google_com.bin", "tls", "www.google.com", "Google", 3, False,
                "--blob=tls_google:@bin/tls_clienthello_www_google_com.bin"),
        FakeRow("quic1", "quic_1.bin", "quic", "", "", 0, False, "--blob=quic1:@bin/quic_1.bin"),
        FakeRow("mine", "mine.bin", "other", "", "мой", None, True, "--blob=mine:@user/fakes/mine.bin"),
    )
    rules = FakeNameRules(shipped=frozenset({"tls_google", "quic1"}), engine=frozenset({"string"}),
                          user=frozenset({"mine"}))
    return FakesPageSnapshot(rows=rows, rules=rules)


def test_page_fills_table_and_runs_actions_through_workers():
    # ЕДИНСТВЕННЫЙ тест файла, создающий страницу (см. tests/test_winws_log_analyzer_page.py).
    from PyQt6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])
    from fakes.ui.dialogs import AddUserFakeDialog
    from fakes.ui.page import FakesPage

    deps = MagicMock()
    page = FakesPage(deps=deps)
    try:
        # Сборка страницы ничего не запускает.
        deps.create_snapshot_worker.assert_not_called()
        assert [page._ui.breadcrumb.items[i].text for i in range(len(page._ui.breadcrumb.items))] == [
            "Управление",
            "Фейки",
        ]

        started = {}

        def _fake_start(runtime_name):
            runtime = getattr(page, runtime_name)

            def _start(**kwargs):
                request_id = runtime.next_request_id()
                started[runtime_name] = (request_id, kwargs)
                return request_id, None

            return _start

        for name in ("_load_runtime", "_action_runtime", "_folder_runtime"):
            getattr(page, name).start_qthread_worker = _fake_start(name)
            getattr(page, name).is_running = lambda: False

        page.on_page_activated()
        request_id, kwargs = started["_load_runtime"]
        kwargs["worker_factory"](request_id)
        deps.create_snapshot_worker.assert_called_once_with(request_id, parent=page)
        kwargs["on_loaded"](request_id, _snapshot())

        table = page._ui.table
        assert table.rowCount() == 3
        assert table.item(0, 0).text() == "tls_google"
        # В подсказке ячейки «Файл» — полное значение.
        assert table.item(0, 1).data(FLUENT_ITEM_TOOLTIP_ROLE) == table.item(0, 1).text()
        assert table.item(0, 4).text() == "3 стратегии"
        assert table.item(1, 4).text() == "не используется"
        assert table.item(2, 0).text() == "mine  · свой"
        assert table.item(2, 4).text() == "—"
        assert page._ui.add_btn.isEnabled()

        # Встроенный фейк: строка --blob= есть, удалить нельзя.
        table.selectRow(0)
        assert page._ui.blob_line_edit.text() == "--blob=tls_google:@bin/tls_clienthello_www_google_com.bin"
        assert page._ui.copy_btn.isEnabled()
        assert not page._ui.delete_btn.isEnabled()

        # Поиск оставляет только подходящие строки.
        page._ui.search_edit.setText("свой")
        page._refresh_table()
        assert table.rowCount() == 1
        table.selectRow(0)
        assert page._ui.blob_line_edit.text() == "--blob=mine:@user/fakes/mine.bin"
        assert "blob=<имя фейка>" in page._ui.blob_hint.text()
        assert page._ui.delete_btn.isEnabled()

        # Удаление своего фейка идёт через worker и затем перечитывает данные.
        page._confirm_delete = lambda name: name == "mine"
        page._on_delete_clicked()
        request_id, kwargs = started["_action_runtime"]
        kwargs["worker_factory"](request_id)
        deps.create_delete_worker.assert_called_once_with(request_id, name="mine", parent=page)
        page._show_info = Mock()
        kwargs["on_loaded"](request_id, "mine")
        page._show_info.assert_called_once()
        assert started["_load_runtime"][0] == 2

        # Добавление: файл -> имя -> worker импорта.
        page._choose_fake_file = lambda: "C:/Users/me/Downloads/my site.bin"
        page._ask_fake_name = lambda path: ("my_site", "описание")
        page._on_add_clicked()
        request_id, kwargs = started["_action_runtime"]
        kwargs["worker_factory"](request_id)
        deps.create_import_worker.assert_called_once_with(
            request_id,
            source_path="C:/Users/me/Downloads/my site.bin",
            name="my_site",
            description="описание",
            parent=page,
        )
        page._show_error = Mock()
        kwargs["on_failed"](request_id, "Файл пустой")
        page._show_error.assert_called_once_with("Фейк не добавлен", "Файл пустой")

        # Диалог имени: подсказка по файлу и проверка на лету.
        dialog = AddUserFakeDialog(rules=_snapshot().rules, file_name="my site.bin", parent=page)
        try:
            assert dialog.nameEdit.text() == "my_site"
            assert dialog.yesButton.isEnabled()
            dialog.nameEdit.setText("string")
            assert not dialog.yesButton.isEnabled()
            assert "winws2" in dialog.warningLabel.text()
            dialog.nameEdit.setText("MINE")
            assert not dialog.validate()
            dialog.nameEdit.setText("ok_name")
            assert dialog.validate() and dialog.fake_name() == "ok_name"
        finally:
            dialog.deleteLater()
    finally:
        page.cleanup()
        page.deleteLater()


def test_long_hex_value_is_shown_compactly_in_file_column():
    # 64 байта нулей в ячейке растягивали столбец «Файл» на всю таблицу.
    from fakes.ui.page import compact_file_label

    assert compact_file_label("0x" + "00" * 64) == "0x00000000… (64 байта)"
    assert compact_file_label("0x" + "00" * 16) == "0x00000000… (16 байт)"
    assert compact_file_label("0x" + "0F" * 21) == "0x0F0F0F0F… (21 байт)"
    assert compact_file_label("0x" + "00" * 22) == "0x00000000… (22 байта)"
    # Короткие значения и имена файлов не меняются.
    assert compact_file_label("0x0F0E0E0F") == "0x0F0E0E0F"
    assert compact_file_label("0x00") == "0x00"
    assert compact_file_label("tls_clienthello_www_google_com.bin") == "tls_clienthello_www_google_com.bin"
