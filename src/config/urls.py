# Все ссылки на сайты проекта (git.zapret.moe, wiki.zapret.moe) пишутся здесь,
# на основных именах. Основные имена режут по TLS, поэтому в браузер и в сеть
# адрес уходит через utils.site_links.resolve / open_in_browser, которые
# подставляют открытое имя (git2, wiki2, …). Своих списков имён в других местах
# не заводить.
# URLs for updates and documentation
#VERSION_URL = "https://nozapret.ru/dev.json"  # URL для проверки обновлений
#EXE_UPDATE_URL = "https://nozapret.ru/main.exe"  # URL для скачивания обновления EXE
DOCS_URL = "https://wiki.zapret.moe/Zapret/home"  # Основная документация
INFO_URL = "https://wiki.zapret.moe/Zapret/home"  # URL с информацией о программе
PRESET_INFO_URL = "https://wiki.zapret.moe/Zapret2/preset"  # URL о пресетах
PROFILE_INFO_URL = "https://wiki.zapret.moe/Zapret2/profile"  # URL о профилях
FILTER_INFO_URL = "https://wiki.zapret.moe/Zapret2/filter"  # URL об условиях профиля (фильтрах)
WINWS_LOG_ANALYZER_INFO_URL = "https://wiki.zapret.moe/Zapret2/log-analyzer"  # URL об анализаторе логов winws2
BLOCKCHECK_INFO_URL = "https://wiki.zapret.moe/Zapret/Blockcheck"  # Пошаговая статья о BlockCheck и его вкладках (кнопка «Как это работает?» на странице)
HOSTS_INFO_URL = "https://wiki.zapret.moe/Zapret/hosts"  # Пошаговая статья о «Редакторе hosts» (ссылка «Как это работает?» на странице)
ANDROID_URL = "https://wiki.zapret.moe/Zapret/android"  # URL инструкции для Android
PREMIUM_GUIDE_URL = "https://wiki.zapret.moe/Zapret/premium"  # Гайд по Zapret Premium: как привязать компьютер к подписке бота
GIT_SITE_URL = "https://git.zapret.moe/"  # Git-сайт проекта: исходный код и выпуски программ
GIT_REPO_URL = f"{GIT_SITE_URL}zapretdiscordyoutube/zapretgui"  # Исходный код Zapret 2 GUI
KVN_REPO_URL = f"{GIT_SITE_URL}zapretkvn/zapret-kvn"  # Исходный код Zapret KVN
ZAPRET_DNS_SITE_URL = "https://dns.zapret.moe/"  # Сайт своего DNS-сервера проекта: состояние и список сервисов
ZAPRET_DNS_SITE_HOST = "dns.zapret.moe"  # Его имя: адрес сервера на плитке «Настройки DNS»
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
    "hosts_summary": HOSTS_INFO_URL,
    "blockcheck_start": f"{BLOCKCHECK_INFO_URL}#шаг-1-запустите-проверку",
    "blockcheck_domains": f"{BLOCKCHECK_INFO_URL}#шаг-1-запустите-проверку",
    "blockcheck_summary": f"{BLOCKCHECK_INFO_URL}#шаг-3-прочитайте-итог",
    "blockcheck_cards": f"{BLOCKCHECK_INFO_URL}#шаг-4-посмотрите-карточки-сайтов",
    "blockcheck_checks": f"{BLOCKCHECK_INFO_URL}#шаг-4-посмотрите-карточки-сайтов",
    "blockcheck_card_detail": f"{BLOCKCHECK_INFO_URL}#шаг-5-откройте-подробности-карточки",
    "blockcheck_history": f"{BLOCKCHECK_INFO_URL}#шаг-6-сравните-до-и-после",
    "blockcheck_past_check": f"{BLOCKCHECK_INFO_URL}#шаг-6-сравните-до-и-после",
    "blockcheck_report": f"{BLOCKCHECK_INFO_URL}#шаг-6-сравните-до-и-после",
    "strategy_scan": f"{BLOCKCHECK_INFO_URL}#вкладка-подбор-стратегии",
    "strategy_scan_result": f"{BLOCKCHECK_INFO_URL}#вкладка-подбор-стратегии",
    "domain_lookup": f"{BLOCKCHECK_INFO_URL}#вкладка-проверка-домена",
    "dns_servers_check": f"{BLOCKCHECK_INFO_URL}#вкладка-dns-серверы",
    "dns_spoofing_check": f"{BLOCKCHECK_INFO_URL}#вкладка-dns-подмена",
    "log_analyzer_source": WINWS_LOG_ANALYZER_INFO_URL,
    "dpi_mode": "https://wiki.zapret.moe/Zapret2/Zapret2#чем-zapret-2-отличается-от-обычного-zapret-winws-nfqws",
    "geo_blocks": HOSTS_INFO_URL,
    "diagnostics": WINWS_LOG_ANALYZER_INFO_URL,
    "finish": "https://wiki.zapret.moe/Zapret/zapret_not_working",
}
BOLVAN_URL = "https://github.com/bol-van/zapret-win-bundle"  # URL автора
SUPPORT_ISSUES_URL = f"{GIT_REPO_URL}/issues"  # Основная ссылка поддержки
# Forgejo (в отличие от GitHub) требует в ?template= полный путь к файлу шаблона
BLOCKCHECK_ISSUES_URL = f"{GIT_REPO_URL}/issues/new?template=.forgejo%2FISSUE_TEMPLATE%2Fservice_not_working.yml"  # Обращение по BlockCheck
PROFILE_REQUEST_FORM_URL = f"{GIT_REPO_URL}/issues/new?template=.forgejo%2FISSUE_TEMPLATE%2Fhostlist_ipset_request.yml"  # Форма заявки на hostlist/ipset
