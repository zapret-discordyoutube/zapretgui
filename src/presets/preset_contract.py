"""Пресет — точка истины.

Всё, что запускает winws/winws2, видно в тексте пресета. Программа не держит
скрытых добавок: ни при сохранении, ни при запуске она не дописывает в пресет
то, чего пользователь не увидит в редакторе.

Ниже перечислены ВСЕ разрешённые преобразования. Любое другое изменение текста
пресета или аргументов запуска — нарушение этого договора. Список проверяют
тесты (tests/test_preset_source_of_truth_contract.py — сохранение,
tests/test_preset_launch_identity.py — запуск,
tests/test_preset_contract_architecture_checks.py — структура) и архитектурные
проверки (app/architecture_checks.py, запускаются в CI), поэтому новое
преобразование сначала добавляется сюда — с понятной причиной, — и только
потом в код.

1. При сохранении (сохранить, дублировать, создать, импортировать)
   ``normalize_preset_source_for_save`` делает только это:

   - обязательный блок ``--lua-init`` (только winws2): полный блок
     ``WINWS2_LUA_INIT_LINES`` в начале преамбулы, см.
     ``profile.winws2_preset_source.ensure_winws2_lua_init_block``;
   - перенос старого ``--debug=@logs/...`` в ``--debug=@user/logs/...``
     (логи живут в user\\logs, по старому пути winws2 не стартует);
   - удаление служебных строк шапки ``# Modified:`` и ``# ActivePreset:``;
   - переводы строк CRLF/CR -> LF, ровно один перевод строки в конце файла и
     без метки BOM в начале (BOM — часть кодировки файла, а не текста; файлы
     пресетов читаются как utf-8-sig).

   Отдельно при переименовании, дублировании, создании и импорте шапка получает
   ``# Preset: <имя>`` (и ``# PresetKind: imported`` для импорта), чтобы имя в
   списке совпадало с именем в файле.

2. При запуске (текст пресета -> @config-файл для winws/winws2):

   - комментарии и пустые строки не передаются;
   - строка «--a --b» делится на отдельные аргументы;
   - каждый аргумент записывается в @config через ``shlex.quote``;
   - winws1: относительные пути к спискам и bin-файлам заменяются абсолютными
     путями к ТЕМ ЖЕ файлам (winws.exe не знает рабочих папок программы);
   - ``WINWS2_DRY_RUN_EXTRA_ARGS`` / ``WINWS1_DRY_RUN_EXTRA_ARGS`` добавляются
     ТОЛЬКО в отдельный процесс проверки (``--dry-run``), который ничего не
     перехватывает и сразу завершается;
   - ``FAST_SWITCH_HANDOFF_EXTRA_ARGS`` (``--wf-dup-check=0``) добавляется при
     быстрой смене пресета: старый и новый winws2 должны недолго работать
     одновременно, иначе новый откажется стартовать из-за дубля фильтра.
     На обработку трафика флаг не влияет.

3. Сгенерированные конфиги, которые не являются пресетами пользователя, и
   поэтому не подчиняются пунктам 1–2 (``GENERATED_CONFIG_EXEMPTIONS``):
   временный пресет пробы blockcheck и рабочий конфиг circular-оркестратора.
   Временный пресет пробы сам объявляет ``--blob=`` для фейков проверяемой
   стратегии (строки берутся из реестра фейков, как в пункте 5), иначе
   стратегия с фейком работала бы в пробе как ``pass``.

4. Импорт ZIP-архива с пресетом и списками (``ARCHIVE_IMPORT_TRANSFORMATIONS``):
   если в папке lists уже есть другой файл с тем же именем, список из архива
   сохраняется под новым именем и ссылка в пресете меняется на него. Результат
   виден в сохранённом тексте пресета.

5. Явные действия пользователя (``EXPLICIT_ACTION_TRANSFORMATIONS``): когда
   пользователь сам выбирает стратегию — готовую стратегию на странице
   profile-а или «Применить» найденную стратегию в blockcheck, — программа,
   кроме самой стратегии, дописывает в преамбулу пресета объявления
   ``--blob=ИМЯ:...`` для фейков этой стратегии, которых в пресете ещё нет
   (``profile.preset_blob_declarations``). Строки берутся из реестра фейков,
   пресет сохраняется обычным путём, и пользователь видит их в тексте.
   Имя, уже объявленное в пресете (любым файлом или hex), не добавляется
   повторно: объявление в пресете главнее реестра, а дубль имени winws2
   отвергает. Встроенные фейки winws2 и фейки из lua-кода блока
   ``--lua-init`` не объявляются. При запуске ничего не подставляется.

6. Разовый перевод пресетов пользователя при запуске программы
   (``ONE_TIME_MIGRATIONS``). Пресеты winws2 из папки пользователя, сохранённые
   старыми версиями, могут не содержать полный обязательный блок ``--lua-init``.
   После запуска программа один раз в фоне пропускает каждый такой файл через
   ту же нормализацию сохранения, что и пункт 1
   (``PresetFileService.migrate_user_presets_to_save_contract``) и записывает
   файл через хранилище пресетов, только если текст изменился. Каждый
   переведённый файл пишется одной строкой в лог. Флаг «уже сделано» не нужен:
   повторный проход ничего не находит. Встроенные пресеты не трогаются,
   привязка пресета к источнику (автосинк) не отвязывается.
   Если меняются аргументы запуска, программа узнаёт об изменении так же, как
   при обычном сохранении: для активного пресета при работающем winws2 это
   один перезапуск — после него работает ровно то, что записано в файле.
   Если аргументы запуска те же (убраны только служебные строки шапки),
   файл пишется без оповещения и без перезапуска.

Модуль намеренно не импортирует ничего, кроме стандартной библиотеки, на уровне
модуля: его константы читает ``app.architecture_checks`` в CI без зависимостей.
"""

from __future__ import annotations


ENGINE_WINWS1 = "winws1"
ENGINE_WINWS2 = "winws2"

# --- 1. Сохранение ---------------------------------------------------------

SAVE_WINWS2_LUA_INIT_BLOCK = "winws2_lua_init_block"
SAVE_LEGACY_DEBUG_LOG_RELOCATION = "legacy_debug_log_relocation"
SAVE_DROP_SERVICE_HEADER_LINES = "drop_modified_and_active_preset_header_lines"
SAVE_LINE_ENDINGS = "lf_line_endings_and_single_final_newline"
SAVE_PRESET_IDENTITY_HEADERS = "preset_identity_headers_on_rename_duplicate_create_import"

SAVE_TIME_NORMALIZATIONS: dict[str, str] = {
    SAVE_WINWS2_LUA_INIT_BLOCK: "winws2: полный обязательный блок --lua-init в начале преамбулы",
    SAVE_LEGACY_DEBUG_LOG_RELOCATION: "--debug=@logs/... -> --debug=@user/logs/...",
    SAVE_DROP_SERVICE_HEADER_LINES: "удаление строк # Modified: и # ActivePreset:",
    SAVE_LINE_ENDINGS: "CRLF/CR -> LF, ровно один перевод строки в конце, без метки BOM в начале",
    SAVE_PRESET_IDENTITY_HEADERS: "# Preset: / # PresetKind: при переименовании, дублировании, создании и импорте",
}

# Служебные строки шапки, которые сохранение удаляет (в нижнем регистре).
SERVICE_HEADER_PREFIXES: tuple[str, ...] = ("# modified:", "# activepreset:")
LEGACY_DEBUG_LOG_PREFIX = "logs/"
DEBUG_LOG_DIR = "user/logs"

# --- 2. Запуск -------------------------------------------------------------

LAUNCH_DROP_COMMENTS_AND_BLANK_LINES = "drop_comments_and_blank_lines"
LAUNCH_SPLIT_INLINE_OPTIONS = "split_inline_options"
LAUNCH_SHLEX_QUOTE_AT_CONFIG = "shlex_quote_into_at_config"
LAUNCH_WINWS1_PATH_RESOLUTION = "winws1_list_and_bin_path_resolution"
LAUNCH_DRY_RUN_CHECK_FLAGS = "dry_run_check_process_flags"
LAUNCH_FAST_SWITCH_HANDOFF_FLAG = "fast_switch_handoff_flag"

LAUNCH_TIME_TRANSFORMATIONS: dict[str, str] = {
    LAUNCH_DROP_COMMENTS_AND_BLANK_LINES: "комментарии и пустые строки не передаются",
    LAUNCH_SPLIT_INLINE_OPTIONS: "строка «--a --b» делится на отдельные аргументы",
    LAUNCH_SHLEX_QUOTE_AT_CONFIG: "аргументы пишутся в @config через shlex.quote",
    LAUNCH_WINWS1_PATH_RESOLUTION: "winws1: относительные пути к спискам/bin -> абсолютные пути к тем же файлам",
    LAUNCH_DRY_RUN_CHECK_FLAGS: "флаги проверки только в отдельном процессе --dry-run",
    LAUNCH_FAST_SWITCH_HANDOFF_FLAG: "--wf-dup-check=0 при быстрой смене пресета (старый и новый winws2 недолго работают вместе)",
}

WINWS2_DRY_RUN_EXTRA_ARGS: tuple[str, ...] = ("--wf-dup-check=0", "--dry-run")
WINWS1_DRY_RUN_EXTRA_ARGS: tuple[str, ...] = ("--dry-run",)
FAST_SWITCH_HANDOFF_EXTRA_ARGS: tuple[str, ...] = ("--wf-dup-check=0",)

# Функции раннеров, внутри которых процесс проверки запускается со своим
# @config (имя функции содержит эту метку).
DRY_RUN_FUNCTION_MARKER = "dry_run"

# --- 3. Сгенерированные конфиги --------------------------------------------

GENERATED_CONFIG_EXEMPTIONS: dict[str, str] = {
    "src/blockcheck/strategy_search/environment.py": "временный @config пробы подбора стратегии",
    "src/orchestra/orchestra_runner.py": "рабочий конфиг circular-оркестратора",
}

# --- 4. Импорт ZIP-архива --------------------------------------------------

ARCHIVE_IMPORT_LIST_RENAME = "portable_archive_list_rename_on_name_collision"

ARCHIVE_IMPORT_TRANSFORMATIONS: dict[str, str] = {
    ARCHIVE_IMPORT_LIST_RENAME: "список из ZIP с именем, которое уже занято другим файлом, "
    "сохраняется под новым именем, и ссылка в пресете меняется на него",
}

# --- 5. Явные действия пользователя ---------------------------------------

STRATEGY_CHOICE_BLOB_DECLARATIONS = "strategy_choice_blob_declarations"

EXPLICIT_ACTION_TRANSFORMATIONS: dict[str, str] = {
    STRATEGY_CHOICE_BLOB_DECLARATIONS: "выбор готовой стратегии или применение найденной в blockcheck "
    "дописывает в преамбулу --blob= для фейков стратегии, которых в пресете ещё нет",
}

# --- 6. Разовый перевод при запуске ---------------------------------------

USER_WINWS2_PRESETS_SAVE_FORMAT_MIGRATION = "user_winws2_presets_save_format_migration"

ONE_TIME_MIGRATIONS: dict[str, str] = {
    USER_WINWS2_PRESETS_SAVE_FORMAT_MIGRATION: "после запуска пресеты winws2 из папки пользователя один раз "
    "проходят нормализацию сохранения (полный блок --lua-init) и записываются, только если текст изменился",
}

# Причина изменения пресета, с которой программа узнаёт о разовом переводе.
CONTRACT_MIGRATION_CHANGE_KIND = "contract_migration"

# --- Кто пишет файлы пресетов ----------------------------------------------

# Единственные модули, которые вызывают запись файла пресета
# (PresetFileStore.create_preset / update_preset). Все, кроме самого хранилища,
# обязаны пропустить текст через normalize_preset_source_for_save перед записью.
PRESET_FILE_STORE_MODULE = "src/presets/file_store.py"
PRESET_FILE_WRITE_OWNERS: dict[str, str] = {
    PRESET_FILE_STORE_MODULE: "хранилище: единственная запись файла пресета на диск",
    "src/presets/file_service.py": "сохранение текста пресета и разовый перевод пресетов пользователя",
    "src/presets/preset_file_ops.py": "переименование, дублирование, создание, импорт TXT",
    "src/presets/portable_archive.py": "импорт ZIP-архива со списками",
}
PRESET_FILE_WRITE_METHODS: frozenset[str] = frozenset({"create_preset", "update_preset", "_write_source"})
PRESET_SAVE_NORMALIZER_NAMES: frozenset[str] = frozenset({"normalize_preset_source_for_save", "normalize_source_text"})

# Где архитектурная проверка ищет запись файлов пресетов и команды запуска winws.
PRESET_CONTRACT_SCOPE: tuple[str, ...] = (
    "src/winws_runtime/",
    "src/presets/",
    "src/profile/",
    "src/blockcheck/",
    "src/orchestra/",
    "src/core/",
)


def _relocate_legacy_debug_log_lines(text: str) -> str:
    if "--debug=" not in text.lower():
        return text
    out: list[str] = []
    for raw in text.split("\n"):
        stripped = raw.strip()
        if stripped.lower().startswith(f"--debug=@{LEGACY_DEBUG_LOG_PREFIX}"):
            value = stripped.split("=", 1)[1].strip().lstrip("@").replace("\\", "/")
            out.append(f"--debug=@{relocate_legacy_debug_log_file(value)}")
            continue
        out.append(raw)
    return "\n".join(out)


def relocate_legacy_debug_log_file(value: str) -> str:
    """Переносит старый путь ``logs/...`` на актуальный ``user/logs/...``.

    Пресеты, созданные до переезда логов в user\\, содержат
    ``--debug=@logs/...``; winws2 не находит такой каталог и отказывается
    стартовать («bad file»).
    """
    path = str(value or "").strip()
    if not path:
        return ""
    if path.lower().startswith(LEGACY_DEBUG_LOG_PREFIX):
        return f"{DEBUG_LOG_DIR}/{path[len(LEGACY_DEBUG_LOG_PREFIX):]}"
    return path


def _drop_service_header_lines(text: str) -> str:
    return "\n".join(
        line
        for line in text.split("\n")
        if not line.strip().lower().startswith(SERVICE_HEADER_PREFIXES)
    )


UTF8_BOM = "\ufeff"


def strip_utf8_bom(text: str) -> str:
    """Убирает метку BOM в начале текста: это кодировка файла, а не пресет."""
    value = str(text or "")
    return value[len(UTF8_BOM):] if value.startswith(UTF8_BOM) else value


def _normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _single_final_newline(text: str) -> str:
    return text.rstrip("\n") + "\n"


def normalize_preset_source_for_save(source_text: str, engine: str) -> str:
    """Единственная нормализация текста пресета перед записью на диск.

    Делает ровно преобразования из ``SAVE_TIME_NORMALIZATIONS`` (кроме шапки
    ``# Preset:``, которую переписывают сами операции переименования/создания).
    """
    text = _normalize_line_endings(strip_utf8_bom(source_text))
    text = _relocate_legacy_debug_log_lines(text)
    text = _drop_service_header_lines(text)
    if str(engine or "").strip().lower() == ENGINE_WINWS2:
        from profile.winws2_preset_source import ensure_winws2_lua_init_block

        text = ensure_winws2_lua_init_block(text)
    return _single_final_newline(text)


__all__ = [
    "ARCHIVE_IMPORT_LIST_RENAME",
    "ARCHIVE_IMPORT_TRANSFORMATIONS",
    "CONTRACT_MIGRATION_CHANGE_KIND",
    "DEBUG_LOG_DIR",
    "DRY_RUN_FUNCTION_MARKER",
    "EXPLICIT_ACTION_TRANSFORMATIONS",
    "FAST_SWITCH_HANDOFF_EXTRA_ARGS",
    "GENERATED_CONFIG_EXEMPTIONS",
    "LAUNCH_TIME_TRANSFORMATIONS",
    "ONE_TIME_MIGRATIONS",
    "PRESET_CONTRACT_SCOPE",
    "PRESET_FILE_STORE_MODULE",
    "PRESET_FILE_WRITE_METHODS",
    "PRESET_FILE_WRITE_OWNERS",
    "PRESET_SAVE_NORMALIZER_NAMES",
    "SAVE_TIME_NORMALIZATIONS",
    "SERVICE_HEADER_PREFIXES",
    "STRATEGY_CHOICE_BLOB_DECLARATIONS",
    "USER_WINWS2_PRESETS_SAVE_FORMAT_MIGRATION",
    "WINWS1_DRY_RUN_EXTRA_ARGS",
    "WINWS2_DRY_RUN_EXTRA_ARGS",
    "normalize_preset_source_for_save",
    "relocate_legacy_debug_log_file",
    "strip_utf8_bom",
]
