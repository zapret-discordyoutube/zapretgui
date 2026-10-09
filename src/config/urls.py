# URLs for updates and documentation
#VERSION_URL = "https://nozapret.ru/dev.json"  # URL для проверки обновлений
#EXE_UPDATE_URL = "https://nozapret.ru/main.exe"  # URL для скачивания обновления EXE
DOCS_URL = "https://wiki.zapret.moe/Zapret/home"  # Основная документация
INFO_URL = "https://wiki.zapret.moe/Zapret/home"  # URL с информацией о программе
PRESET_INFO_URL = "https://wiki.zapret.moe/Zapret2/preset"  # URL о пресетах
PROFILE_INFO_URL = "https://wiki.zapret.moe/Zapret2/profile"  # URL о профилях
FILTER_INFO_URL = "https://wiki.zapret.moe/Zapret2/filter"  # URL об условиях профиля (фильтрах)
WINWS_LOG_ANALYZER_INFO_URL = "https://wiki.zapret.moe/Zapret2/log-analyzer"  # URL об анализаторе логов winws2
BLOCKCHECK_INFO_URL = "https://wiki.zapret.moe/Zapret2/find-game-strategy"  # URL о подборе стратегии (страница BlockCheck)
ANDROID_URL = "https://wiki.zapret.moe/Zapret/android"  # URL инструкции для Android
# Статьи вики к шагам обучающего тура (ключ — шаг из ui.onboarding.steps).
# Шаги без подходящей статьи здесь не указаны — у них нет кнопки «Подробнее в вики».
ONBOARDING_WIKI_URLS = {
    "welcome": DOCS_URL,
    "how_it_works": "https://wiki.zapret.moe/Zapret/about#обход-dpi-система-тспу",
    "building_blocks": "https://wiki.zapret.moe/Zapret2/profile#пресет-и-профиль--в-чём-разница",
    "preset": PRESET_INFO_URL,
    "presets_list": PRESET_INFO_URL,
    "preset_file": PRESET_INFO_URL,
    "preset_header": PRESET_INFO_URL,
    "preset_lua_init": "https://wiki.zapret.moe/Zapret2/структура-проекта",
    "preset_interception": "https://wiki.zapret.moe/Zapret2/wf",
    "preset_blobs": "https://wiki.zapret.moe/Zapret2/blob",
    "preset_profile": PROFILE_INFO_URL,
    "preset_profile_match": FILTER_INFO_URL,
    "preset_profile_packets": "https://wiki.zapret.moe/Zapret2/последовательность-аргументов",
    "preset_profile_strategy": "https://wiki.zapret.moe/Zapret2/desync",
    "preset_profile_new": "https://wiki.zapret.moe/Zapret2/profile-independence",
    "profiles_list": PROFILE_INFO_URL,
    "profile_group": PROFILE_INFO_URL,
    "profile_row": "https://wiki.zapret.moe/Zapret2/verify-strategy",
    "profiles_toolbar": "https://wiki.zapret.moe/Zapret2/add-profile",
    "profile_order": "https://wiki.zapret.moe/Zapret2/profile-independence",
    "list_type": "https://wiki.zapret.moe/Zapret2/filter#списки-ip-и-доменов",
    "ranges": "https://wiki.zapret.moe/Zapret2/out-range",
    "profile_tabs": FILTER_INFO_URL,
    "strategy_choice": "https://wiki.zapret.moe/Zapret2/desync",
    "technique_fake": "https://wiki.zapret.moe/Zapret2/desync/fake",
    "technique_multisplit": "https://wiki.zapret.moe/Zapret2/desync/multisplit",
    "technique_multidisorder": "https://wiki.zapret.moe/Zapret2/desync/multidisorder",
    "technique_fakedsplit": "https://wiki.zapret.moe/Zapret2/desync/fakedsplit",
    "technique_hostfakesplit": "https://wiki.zapret.moe/Zapret2/desync/hostfakesplit",
    "technique_tcpseg": "https://wiki.zapret.moe/Zapret2/desync/tcpseg",
    "technique_oob": "https://wiki.zapret.moe/Zapret2/desync/oob",
    "technique_syndata": "https://wiki.zapret.moe/Zapret2/desync/syndata",
    "list_entries": "https://wiki.zapret.moe/Zapret2/find-site-domains",
    "fakes": "https://wiki.zapret.moe/Zapret2/blob",
    "fakes_blob": "https://wiki.zapret.moe/Zapret2/blob",
    "hosts_summary": "https://wiki.zapret.moe/Zapret/hosts",
    "strategy_scan": BLOCKCHECK_INFO_URL,
    "log_analyzer_source": WINWS_LOG_ANALYZER_INFO_URL,
    "dpi_mode": "https://wiki.zapret.moe/Zapret2/Zapret2#чем-zapret-2-отличается-от-обычного-zapret-winws-nfqws",
    "geo_blocks": "https://wiki.zapret.moe/Zapret/hosts",
    "diagnostics": WINWS_LOG_ANALYZER_INFO_URL,
    "finish": "https://wiki.zapret.moe/Zapret/zapret_not_working",
}
BOLVAN_URL = "https://github.com/bol-van/zapret-win-bundle"  # URL автора
SUPPORT_ISSUES_URL = "https://git.zapret.moe/zapretdiscordyoutube/zapretgui/issues"  # Основная ссылка поддержки
# Forgejo (в отличие от GitHub) требует в ?template= полный путь к файлу шаблона
BLOCKCHECK_ISSUES_URL = "https://git.zapret.moe/zapretdiscordyoutube/zapretgui/issues/new?template=.forgejo%2FISSUE_TEMPLATE%2Fservice_not_working.yml"  # Обращение по BlockCheck
PROFILE_REQUEST_FORM_URL = "https://git.zapret.moe/zapretdiscordyoutube/zapretgui/issues/new?template=.forgejo%2FISSUE_TEMPLATE%2Fhostlist_ipset_request.yml"  # Форма заявки на hostlist/ipset
