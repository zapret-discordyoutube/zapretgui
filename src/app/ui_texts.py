"""Centralized application text catalog and sidebar search index.

This module contains user-facing strings for navigation/search and the
declarative index used by the left sidebar global search.
"""

from settings.mode import ENGINE_WINWS1, ENGINE_WINWS2, EXE_NAME_WINWS1, EXE_NAME_WINWS2
from app.page_names import PageName


DEFAULT_UI_LANGUAGE = "ru"
SUPPORTED_UI_LANGUAGES = ("ru", "en")
LANGUAGE_OPTIONS = (
    ("ru", "Русский"),
    ("en", "English"),
)


TEXTS: dict[str, dict[str, str]] = {
    "sidebar.search.placeholder": {
        "ru": "Найти в разделах и страницах",
        "en": "Find in sections and pages",
    },
    "nav.header.settings": {
        "ru": "Настройки Запрета",
        "en": "Zapret Settings",
    },
    "nav.header.system": {
        "ru": "Инструменты",
        "en": "Tools",
    },
    "nav.header.diagnostics": {
        "ru": "Диагностика",
        "en": "Diagnostics",
    },
    "nav.header.appearance": {
        "ru": "Оформление",
        "en": "Appearance",
    },
    "nav.page.zapret2_mode_control": {
        "ru": "Управление Zapret 2",
        "en": "Zapret 2 Control",
    },
    "nav.page.zapret1_mode_control": {
        "ru": "Управление Zapret 1",
        "en": "Zapret 1 Control",
    },
    "nav.page.orchestra": {
        "ru": "Оркестратор",
        "en": "Orchestrator",
    },
    "nav.page.orchestra_settings": {
        "ru": "Настройки оркестратора",
        "en": "Orchestrator Settings",
    },
    "nav.page.dpi_settings": {
        "ru": "Сменить режим DPI",
        "en": "DPI Mode",
    },
    "nav.page.autostart": {
        "ru": "Автозапуск",
        "en": "Autostart",
    },
    "nav.page.network": {
        "ru": "Настройка DNS",
        "en": "DNS Settings",
    },
    "nav.page.hosts": {
        "ru": "Редактор hosts",
        "en": "Hosts Editor",
    },
    "nav.page.blockcheck": {
        "ru": "BlockCheck",
        "en": "BlockCheck",
    },
    "nav.page.winws_log_analyzer": {
        "ru": "Анализ лога winws2",
        "en": "winws2 Log Analysis",
    },
    "page.winws_log_analyzer.title": {
        "ru": "Анализ лога winws2",
        "en": "winws2 Log Analysis",
    },
    "page.winws_log_analyzer.subtitle": {
        "ru": "Разбор debug-лога: соединения, протоколы, профили и вердикты",
        "en": "Debug log breakdown: connections, protocols, profiles and verdicts",
    },
    "nav.page.fakes": {
        "ru": "Фейки",
        "en": "Fakes",
    },
    "page.fakes.title": {
        "ru": "Фейки",
        "en": "Fakes",
    },
    "page.fakes.subtitle": {
        "ru": "Фейки winws2: пакеты, которые стратегия отправляет вместо настоящих. Здесь можно посмотреть встроенные и добавить свои",
        "en": "winws2 fakes: packets a strategy sends instead of real ones. Browse built-in fakes and add your own",
    },
    "page.fakes.breadcrumb.control": {
        "ru": "Управление",
        "en": "Control",
    },
    "page.winws2_control.button.fakes": {
        "ru": "Фейки",
        "en": "Fakes",
    },
    "page.winws2_control.button.fakes.desc": {
        "ru": "Встроенные фейки winws2 и свои .bin-файлы для стратегий",
        "en": "Built-in winws2 fakes and your own .bin files for strategies",
    },
    "page.winws2_control.button.fakes.accessible_name": {
        "ru": "Открыть страницу фейков",
        "en": "Open fakes page",
    },
    "nav.page.appearance": {
        "ru": "Оформление",
        "en": "Appearance",
    },
    "nav.page.premium": {
        "ru": "Донат",
        "en": "Donation",
    },
    "nav.page.logs": {
        "ru": "Логи",
        "en": "Logs",
    },
    "nav.page.about": {
        "ru": "О программе",
        "en": "About",
    },
    "nav.page.zapret2_mode": {
        "ru": "Профили пресета",
        "en": "Preset profiles",
    },
    "nav.page.zapret2_user_presets": {
        "ru": "Мои пресеты",
        "en": "My Presets",
    },
    "nav.page.zapret1_mode": {
        "ru": "Профили пресета",
        "en": "Preset profiles",
    },
    "nav.page.zapret1_user_presets": {
        "ru": "Мои пресеты",
        "en": "My presets",
    },
    "common.toggle.on_off": {
        "ru": "Вкл/Выкл",
        "en": "On/Off",
    },
    "common.ok.got_it": {
        "ru": "Понятно",
        "en": "Got it",
    },
    "common.error.title": {
        "ru": "Ошибка",
        "en": "Error",
    },
    "common.preset_drop.title": {
        "ru": "Отпустите файл для импорта",
        "en": "Drop the file to import",
    },
    "common.preset_drop.single": {
        "ru": "{file_name}",
        "en": "{file_name}",
    },
    "common.preset_drop.multiple": {
        "ru": "Количество TXT-файлов: {count}",
        "en": "TXT files: {count}",
    },
    "common.preset_drop.accepted": {
        "ru": "Файл принят — импортирую…",
        "en": "File accepted — importing…",
    },
    "common.preset_drop.any_txt": {
        "ru": "TXT-файл с пресетом",
        "en": "TXT preset file",
    },
    "page.control.status": {
        "ru": "Статус работы",
        "en": "Service Status",
    },
    "page.control.status.checking": {
        "ru": "Проверка...",
        "en": "Checking...",
    },
    "page.control.status.detecting": {
        "ru": "Определение состояния процесса",
        "en": "Detecting process state",
    },
    "page.control.section.launch_behavior": {
        "ru": "Запуск и поведение",
        "en": "Startup and behavior",
    },
    "page.control.section.windows_blocks": {
        "ru": "Windows и блокировки",
        "en": "Windows and blocking",
    },
    "page.control.section.advanced_bypass": {
        "ru": "Тонкая настройка обхода",
        "en": "Fine-tuning the bypass",
    },
    "page.control.section.quick_actions": {
        "ru": "Быстрые действия",
        "en": "Quick actions",
    },
    "page.control.summary.preset.caption": {
        "ru": "Текущий пресет",
        "en": "Current preset",
    },
    "page.control.summary.profiles.caption": {
        "ru": "Профили",
        "en": "Profiles",
    },
    "page.control.summary.profiles.enabled_template": {
        "ru": "{count} включено",
        "en": "{count} enabled",
    },
    "page.control.summary.profiles.unavailable": {
        "ru": "Проверяем...",
        "en": "Checking...",
    },
    "page.control.summary.mode.caption": {
        "ru": "Текущий режим",
        "en": "Current mode",
    },
    "page.control.summary.premium.checking": {
        "ru": "Проверка...",
        "en": "Checking...",
    },
    "page.control.summary.premium.checking_details": {
        "ru": "Узнаём статус подписки",
        "en": "Checking subscription status",
    },
    "page.control.summary.premium.free_details": {
        "ru": "Базовые функции",
        "en": "Basic features",
    },
    "page.control.summary.premium.active_details": {
        "ru": "Активен",
        "en": "Active",
    },
    "page.control.status.running": {
        "ru": "Zapret работает",
        "en": "Zapret is running",
    },
    "page.control.status.stopped": {
        "ru": "Zapret остановлен",
        "en": "Zapret stopped",
    },
    "page.control.status.uptime.seconds": {
        "ru": "{seconds} с",
        "en": "{seconds} s",
    },
    "page.control.status.uptime.minutes": {
        "ru": "{minutes} мин",
        "en": "{minutes} min",
    },
    "page.control.status.uptime.hours": {
        "ru": "{hours} ч {minutes} мин",
        "en": "{hours} h {minutes} min",
    },
    "page.control.status.uptime.days": {
        "ru": "{days} д {hours} ч",
        "en": "{days} d {hours} h",
    },
    "page.control.status.uptime.accessible": {
        "ru": "Обход работает: {uptime}",
        "en": "Bypass has been running for {uptime}",
    },
    "page.control.status.bypass_active": {
        "ru": "Обход блокировок активен · нажмите на кнопку, чтобы остановить",
        "en": "Bypass is active · click the button to stop",
    },
    "page.control.status.press_start": {
        "ru": "Нажмите на кнопку, чтобы запустить",
        "en": "Click the button to start",
    },
    "page.control.last_message.title": {
        "ru": "Последнее сообщение",
        "en": "Latest message",
    },
    "page.control.last_message.empty": {
        "ru": "Пока нет новых сообщений",
        "en": "No new messages yet",
    },
    "page.control.strategy.not_selected": {
        "ru": "Не выбрана",
        "en": "Not selected",
    },
    "page.control.strategy.select_hint": {
        "ru": "Выберите стратегию в разделе «Стратегии»",
        "en": "Select a strategy in the Strategies section",
    },
    "page.control.strategy.active": {
        "ru": "Активная стратегия",
        "en": "Active strategy",
    },
    "page.control.setting.autostart.title": {
        "ru": "Автозапуск DPI после старта программы",
        "en": "Auto-start DPI after app launch",
    },
    "page.control.setting.autostart.desc": {
        "ru": "После запуска ZapretGUI автоматически запускать текущий DPI-режим",
        "en": "Automatically start the current DPI mode after ZapretGUI launches",
    },
    "page.control.setting.gui_autostart.title": {
        "ru": "Автозапуск ZapretGUI",
        "en": "ZapretGUI autostart",
    },
    "page.control.setting.gui_autostart.desc": {
        "ru": "Запускать программу в трее при входе в Windows",
        "en": "Start the app in the tray when signing in to Windows",
    },
    "page.control.setting.tray_close_mode.title": {
        "ru": "Поведение окна и трея",
        "en": "Window and tray behavior",
    },
    "page.control.setting.tray_close_mode.desc": {
        "ru": "Выберите, когда ZapretGUI будет скрывать окно в системный трей",
        "en": "Choose when ZapretGUI hides the window to the system tray",
    },
    "page.control.setting.defender.title": {
        "ru": "Отключить Windows Defender",
        "en": "Disable Windows Defender",
    },
    "page.control.setting.defender.desc": {
        "ru": "Требуются права администратора",
        "en": "Administrator rights required",
    },
    "page.control.setting.max_block.title": {
        "ru": "Блокировать установку MAX",
        "en": "Block MAX installation",
    },
    "page.control.setting.max_block.desc": {
        "ru": "Блокирует запуск/установку MAX и домены в hosts",
        "en": "Blocks MAX launch/installation and hosts domains",
    },
    "page.control.setting.state_media_block.title": {
        "ru": "Блокировать государственные СМИ РФ",
        "en": "Block Russian state media",
    },
    "page.control.setting.state_media_block.desc": {
        "ru": "Добавляет базовый список государственных новостных сайтов в hosts",
        "en": "Adds a basic list of state news sites to hosts",
    },
    "page.control.button.connection_test": {
        "ru": "Тест соединения",
        "en": "Connection Test",
    },
    "page.control.button.open_folder": {
        "ru": "Открыть папку",
        "en": "Open Folder",
    },
    "page.winws2_control.title": {
        "ru": "Управление Zapret 2",
        "en": "Zapret 2 Control",
    },
    "page.winws2_control.preset_switch": {
        "ru": "Сменить пресет обхода блокировок",
        "en": "Switch Bypass Preset",
    },
    "page.winws2_control.profile_tuning": {
        "ru": "Настройка пресета",
        "en": "Preset setup",
    },
    "page.winws1_control.title": {
        "ru": "Управление Zapret 1",
        "en": "Zapret 1 Control",
    },
    "page.winws1_control.presets": {
        "ru": "Пресеты и настройка пресета",
        "en": "Presets and preset setup",
    },
    "page.winws1_control.status.checking": {
        "ru": "Проверка...",
        "en": "Checking...",
    },
    "page.winws1_control.status.detecting": {
        "ru": "Определение состояния процесса",
        "en": "Detecting process state",
    },
    "page.winws1_control.status.running": {
        "ru": "Zapret 1 работает",
        "en": "Zapret 1 is running",
    },
    "page.winws1_control.status.stopped": {
        "ru": "Zapret 1 остановлен",
        "en": "Zapret 1 stopped",
    },
    "page.winws1_control.status.bypass_active": {
        "ru": "Обход блокировок активен",
        "en": "Bypass is active",
    },
    "page.winws1_control.status.press_start": {
        "ru": "Нажмите «Запустить» для активации",
        "en": "Press Start to activate",
    },
    "page.winws1_control.preset.not_selected": {
        "ru": "Не выбран",
        "en": "Not selected",
    },
    "page.winws1_control.preset.current": {
        "ru": "Текущий активный пресет",
        "en": "Current active preset",
    },
    "page.winws1_control.button.my_presets": {
        "ru": "Мои пресеты",
        "en": "My Presets",
    },
    "page.winws1_control.profiles.title": {
        "ru": "Настройка пресета",
        "en": "Preset setup",
    },
    "page.winws1_control.profiles.desc": {
        "ru": "Открыть профили выбранного пресета и выбрать готовые стратегии",
        "en": "Open profiles from the selected preset and choose ready strategies",
    },
    "page.winws1_control.button.open": {
        "ru": "Открыть",
        "en": "Open",
    },
    "page.winws1_control.setting.autostart.title": {
        "ru": "Автозапуск DPI после старта программы",
        "en": "Auto-start DPI after app launch",
    },
    "page.winws1_control.setting.autostart.desc": {
        "ru": "После запуска ZapretGUI автоматически запускать текущий DPI-режим",
        "en": "Automatically start the current DPI mode after ZapretGUI launches",
    },
    "page.winws1_control.advanced.warning": {
        "ru": "Эти параметры лучше менять, только если уверены в результате",
        "en": "Better change these only if you are sure of the result",
    },
    "page.winws1_control.advanced.discord_restart.title": {
        "ru": "Перезапуск Discord",
        "en": "Restart Discord",
    },
    "page.winws1_control.advanced.discord_restart.desc": {
        "ru": "Автоперезапуск при смене стратегии",
        "en": "Auto-restart on strategy change",
    },
    "page.winws1_control.advanced.wssize.title": {
        "ru": "Включить --wssize",
        "en": "Enable --wssize",
    },
    "page.winws1_control.advanced.wssize.desc": {
        "ru": "Добавляет параметр размера окна TCP",
        "en": "Adds TCP window size parameter",
    },
    "page.winws1_control.advanced.debug_log.title": {
        "ru": "Включить лог-файл (--debug)",
        "en": "Enable log file (--debug)",
    },
    "page.winws1_control.advanced.debug_log.desc": {
        "ru": "Записывает логи winws в папку logs",
        "en": "Writes winws logs to the logs folder",
    },
    "page.winws1_control.button.connection_test": {
        "ru": "Тест соединения",
        "en": "Connection test",
    },
    "page.winws1_control.button.connection_test.desc": {
        "ru": "Проверить доступность сети и состояние обхода",
        "en": "Check network reachability and bypass state",
    },
    "page.winws1_control.button.open_folder": {
        "ru": "Открыть папку",
        "en": "Open folder",
    },
    "page.winws1_control.button.open_folder.desc": {
        "ru": "Перейти в папку программы и служебных файлов",
        "en": "Open the app folder and service files",
    },
    "page.winws1_control.button.documentation": {
        "ru": "Документация",
        "en": "Documentation",
    },
    "page.winws1_control.button.documentation.desc": {
        "ru": "Открыть справку и описание возможностей",
        "en": "Open help and feature documentation",
    },
    "page.orchestra.title": {
        "ru": "Оркестратор",
        "en": "Orchestrator",
    },
    "page.orchestra.training_status": {
        "ru": "Статус обучения",
        "en": "Training Status",
    },
    "page.orchestra.log": {
        "ru": "Лог обучения",
        "en": "Training Log",
    },
    "page.dpi_settings.title": {
        "ru": "Настройки DPI",
        "en": "DPI Settings",
    },
    "page.dpi_settings.launch_method": {
        "ru": "Метод запуска стратегий",
        "en": "Strategy Launch Method",
    },
    "page.autostart.title": {
        "ru": "Автозапуск",
        "en": "Autostart",
    },
    "page.autostart.mode": {
        "ru": "Режим",
        "en": "Mode",
    },
    "page.network.title": {
        "ru": "Настройка DNS",
        "en": "DNS Settings",
    },
    "page.network.dns": {
        "ru": "DNS",
        "en": "DNS",
    },
    "page.network.adapters": {
        "ru": "Сетевые адаптеры",
        "en": "Network Adapters",
    },
    "page.network.tools": {
        "ru": "Утилиты",
        "en": "Utilities",
    },
    "page.hosts.title": {
        "ru": "Редактор hosts",
        "en": "Hosts Editor",
    },
    "page.hosts.services": {
        "ru": "Сервисы",
        "en": "Services",
    },
    "page.blockcheck.scope": {
        "ru": "Что проверить:",
        "en": "What to check:",
    },
    "page.blockcheck.scope_main": {
        "ru": "Только Discord и YouTube (быстро)",
        "en": "Discord and YouTube only (quick)",
    },
    "page.blockcheck.scope_full": {
        "ru": "Полная проверка (около минуты)",
        "en": "Full check (about a minute)",
    },
    "page.blockcheck.scope_all": {
        "ru": "Только сайты (быстро)",
        "en": "Sites only (quick)",
    },
    "page.blockcheck.report": {
        "ru": "Отчёт",
        "en": "Report",
    },
    "page.blockcheck.title": {
        "ru": "BlockCheck",
        "en": "BlockCheck",
    },
    "page.blockcheck.monitoring": {
        "ru": "Живой мониторинг сети",
        "en": "Live Network Monitoring",
    },
    "page.blockcheck.subtitle": {
        "ru": "Какие сайты открываются, почему не открываются остальные и что с этим делать",
        "en": "Which sites open, why others don't and what to do about it",
    },
    "page.blockcheck.tab.blockcheck": {
        "ru": "BlockCheck",
        "en": "BlockCheck",
    },
    "page.blockcheck.tab.strategy_scan": {
        "ru": "Подбор стратегии",
        "en": "Strategy Selection",
    },
    "page.blockcheck.tab.dns_spoofing": {
        "ru": "DNS подмена",
        "en": "DNS Spoofing",
    },
    "page.blockcheck.tab.domain_lookup": {
        "ru": "Проверка домена",
        "en": "Domain Lookup",
    },
    "page.blockcheck.tab.dns_servers": {
        "ru": "DNS-серверы",
        "en": "DNS Servers",
    },
    "page.server_check.title": {
        "ru": "DNS-серверы",
        "en": "DNS Servers",
    },
    "page.server_check.subtitle": {
        "ru": "Каждый адрес каждого сервера: пинг, обычный и шифрованный DNS, перехват по дороге",
        "en": "Every address of every server: ping, plain and encrypted DNS, interception on the way",
    },
    "page.server_check.intro": {
        "ru": "Проверка покажет, какие DNS-серверы и какими способами доступны в вашей сети: обычный DNS (порт 53) и шифрованный — DoT (порт 853) и DoH (порт 443). Провайдер может закрыть любой из них отдельно и для отдельного адреса. Заодно видно, не подменяются ли ответы по дороге.",
        "en": "The check shows which DNS servers are reachable in your network and how: plain DNS (port 53) and encrypted DoT (port 853) and DoH (port 443). A provider can block any of them separately and for a single address. It also shows whether answers are replaced on the way.",
    },
    "page.server_check.idle.title": {
        "ru": "Медоед обзвонит DNS-серверы",
        "en": "The honey badger will call the DNS servers",
    },
    "page.server_check.pending.title": {
        "ru": "Опрашиваем серверы",
        "en": "Asking the servers",
    },
    "page.server_check.button.start": {
        "ru": "Проверить серверы",
        "en": "Check servers",
    },
    "page.server_check.button.start.name": {
        "ru": "Проверить DNS-серверы",
        "en": "Check DNS servers",
    },
    "page.server_check.button.start.description": {
        "ru": "Спросить каждый адрес каждого DNS-сервера всеми способами, по три раза. Занимает около десяти секунд.",
        "en": "Ask every address of every DNS server in every way, three times each. Takes about ten seconds.",
    },
    "page.server_check.button.stop": {
        "ru": "Остановить",
        "en": "Stop",
    },
    "page.server_check.button.stop.name": {
        "ru": "Остановить проверку DNS-серверов",
        "en": "Stop the DNS server check",
    },
    "page.server_check.button.stop.description": {
        "ru": "Прервать проверку; останется то, что уже успели узнать.",
        "en": "Interrupt the check; what was already found stays.",
    },
    "page.server_check.button.report": {
        "ru": "Отчёт",
        "en": "Report",
    },
    "page.server_check.button.report.name": {
        "ru": "Открыть отчёт проверки DNS-серверов",
        "en": "Open the DNS server check report",
    },
    "page.server_check.button.report.description": {
        "ru": "Открыть полный текст проверки — его можно скопировать и отправить.",
        "en": "Open the full text of the check — it can be copied and sent.",
    },
    "page.server_check.section.servers": {
        "ru": "Серверы",
        "en": "Servers",
    },
    "page.server_check.status.failed": {
        "ru": "Проверка не удалась",
        "en": "The check failed",
    },
    "page.server_check.report.title": {
        "ru": "Отчёт проверки DNS-серверов",
        "en": "DNS server check report",
    },
    "page.server_check.report.empty": {
        "ru": "Проверка ещё не запускалась.",
        "en": "The check has not been run yet.",
    },
    "page.server_check.report.description": {
        "ru": "Полный текст проверки: таблица по адресам, итог и подробности.",
        "en": "Full text of the check: table by address, summary and details.",
    },
    "page.domain_lookup.title": {
        "ru": "Проверка домена",
        "en": "Domain Lookup",
    },
    "page.domain_lookup.subtitle": {
        "ru": "Пинг, адреса с разных DNS-серверов и домены на том же адресе",
        "en": "Ping, addresses from different DNS servers and domains on the same address",
    },
    "page.domain_lookup.placeholder": {
        "ru": "Домен или IP-адрес: example.com, 1.2.3.4",
        "en": "Domain or IP address: example.com, 1.2.3.4",
    },
    "page.domain_lookup.input.name": {
        "ru": "Домен или IP-адрес для проверки",
        "en": "Domain or IP address to check",
    },
    "page.domain_lookup.input.description": {
        "ru": "Введите домен, адрес или ссылку и нажмите «Проверить».",
        "en": "Enter a domain, address or link and press Check.",
    },
    "page.domain_lookup.button.start": {
        "ru": "Проверить",
        "en": "Check",
    },
    "page.domain_lookup.button.start.name": {
        "ru": "Проверить домен",
        "en": "Check domain",
    },
    "page.domain_lookup.button.start.description": {
        "ru": "Пропинговать адрес, спросить его у разных DNS-серверов и найти домены на том же адресе.",
        "en": "Ping the address, ask different DNS servers for it and find domains on the same address.",
    },
    "page.domain_lookup.button.stop": {
        "ru": "Остановить",
        "en": "Stop",
    },
    "page.domain_lookup.button.stop.name": {
        "ru": "Остановить проверку домена",
        "en": "Stop domain check",
    },
    "page.domain_lookup.button.stop.description": {
        "ru": "Прервать проверку; останется то, что уже успели узнать.",
        "en": "Interrupt the check; what was already found stays.",
    },
    "page.domain_lookup.button.report": {
        "ru": "Отчёт",
        "en": "Report",
    },
    "page.domain_lookup.button.report.name": {
        "ru": "Открыть отчёт проверки домена",
        "en": "Open domain check report",
    },
    "page.domain_lookup.button.report.description": {
        "ru": "Открыть полный текст проверки — его можно скопировать.",
        "en": "Open the full text of the check — it can be copied.",
    },
    "page.domain_lookup.external": {
        "ru": "Искать соседей по адресу через внешние сервисы (адрес будет отправлен на их сайты)",
        "en": "Look up neighbours via external services (the address is sent to their sites)",
    },
    "page.domain_lookup.external.description": {
        "ru": "Список доменов на том же адресе дают сторонние сервисы. Без галочки наружу ничего не уходит: остаются обратное имя, сертификат и владелец сети.",
        "en": "The list of domains on the same address comes from third-party services. Without this option nothing is sent out: reverse name, certificate and network owner remain.",
    },
    "page.domain_lookup.section.ping": {
        "ru": "Пинг и сеть",
        "en": "Ping and network",
    },
    "page.domain_lookup.section.path": {
        "ru": "Путь до сервера",
        "en": "Path to the server",
    },
    "page.domain_lookup.path.name": {
        "ru": "Узлы по дороге до сервера",
        "en": "Hops on the way to the server",
    },
    "page.domain_lookup.path.description": {
        "ru": "Номер узла, его адрес и время ответа. Отметка показывает, за каким узлом стоит фильтр.",
        "en": "Hop number, its address and response time. The mark shows after which hop the filter stands.",
    },
    "page.domain_lookup.section.dns": {
        "ru": "Адреса с разных DNS-серверов",
        "en": "Addresses from different DNS servers",
    },
    "page.domain_lookup.section.neighbors": {
        "ru": "Кто ещё на этом адресе",
        "en": "Who else is on this address",
    },
    "page.domain_lookup.column.server": {
        "ru": "DNS-сервер",
        "en": "DNS server",
    },
    "page.domain_lookup.column.address": {
        "ru": "Адрес сервера",
        "en": "Server address",
    },
    "page.domain_lookup.column.answer": {
        "ru": "Ответ",
        "en": "Answer",
    },
    "page.domain_lookup.column.time": {
        "ru": "Время",
        "en": "Time",
    },
    "page.domain_lookup.table.name": {
        "ru": "Ответы DNS-серверов",
        "en": "DNS server answers",
    },
    "page.domain_lookup.table.description": {
        "ru": "Для каждого сервера: какие адреса он назвал и за сколько.",
        "en": "For each server: which addresses it returned and how fast.",
    },
    "page.domain_lookup.neighbors.name": {
        "ru": "Домены на том же адресе",
        "en": "Domains on the same address",
    },
    "page.domain_lookup.neighbors.description": {
        "ru": "Списки доменов по источникам.",
        "en": "Domain lists grouped by source.",
    },
    "page.domain_lookup.status.ready": {
        "ru": "Введите домен или IP-адрес и нажмите «Проверить».",
        "en": "Enter a domain or IP address and press Check.",
    },
    "page.domain_lookup.status.empty": {
        "ru": "Введите домен или IP-адрес.",
        "en": "Enter a domain or IP address.",
    },
    "page.domain_lookup.status.failed": {
        "ru": "Проверка не удалась",
        "en": "Check failed",
    },
    "page.domain_lookup.report.title": {
        "ru": "Отчёт проверки домена",
        "en": "Domain check report",
    },
    "page.domain_lookup.report.empty": {
        "ru": "Проверка ещё не запускалась.",
        "en": "The check has not been run yet.",
    },
    "page.domain_lookup.report.description": {
        "ru": "Полный текст проверки: пинг, ответы DNS-серверов и домены на адресе.",
        "en": "Full text of the check: ping, DNS server answers and domains on the address.",
    },
    "page.blockcheck.start": {
        "ru": "Проверить",
        "en": "Check",
    },
    "page.blockcheck.stop": {
        "ru": "Остановить",
        "en": "Stop",
    },
    "page.blockcheck.ready": {
        "ru": "Сайты, хостинги, DNS, звонки и сам компьютер — около минуты",
        "en": "Sites, hostings, DNS, calls and this computer — about a minute",
    },
    "page.blockcheck.running": {
        "ru": "Проверяем… ход виден ниже",
        "en": "Checking… progress is shown below",
    },
    "page.blockcheck.stopping": {
        "ru": "Останавливаем…",
        "en": "Stopping…",
    },
    "page.blockcheck.done": {
        "ru": "Готово",
        "en": "Done",
    },
    "page.blockcheck.error": {
        "ru": "Ошибка выполнения",
        "en": "Execution error",
    },
    "page.blockcheck.custom_domains": {
        "ru": "Проверить ещё и свои домены:",
        "en": "Also check your own domains:",
    },
    "page.blockcheck.domain_placeholder": {
        "ru": "example.com",
        "en": "example.com",
    },
    "page.blockcheck.add_domain": {
        "ru": "Добавить",
        "en": "Add",
    },
    "page.blockcheck.domain_exists_title": {
        "ru": "Домен уже добавлен",
        "en": "Domain already added",
    },
    "page.appearance.title": {
        "ru": "Оформление",
        "en": "Appearance",
    },
    "page.appearance.display_mode": {
        "ru": "Режим отображения",
        "en": "Display Mode",
    },
    "page.appearance.background": {
        "ru": "Фон окна",
        "en": "Window Background",
    },
    "page.premium.title": {
        "ru": "Premium",
        "en": "Premium",
    },
    "page.premium.subscription_status": {
        "ru": "Статус подписки",
        "en": "Subscription Status",
    },
    "page.logs.title": {
        "ru": "Логи",
        "en": "Logs",
    },
    "page.logs.controls": {
        "ru": "Управление логами",
        "en": "Log Controls",
    },
    "page.about.title": {
        "ru": "О программе",
        "en": "About",
    },
    "page.about.version": {
        "ru": "Версия",
        "en": "Version",
    },
    "page.about.support": {
        "ru": "Каналы поддержки",
        "en": "Support Channels",
    },
    "page.about.subtitle": {
        "ru": "Версия, подписка и информация",
        "en": "Version, subscription and information",
    },
    "page.about.tab.about": {
        "ru": "О ПРОГРАММЕ",
        "en": "ABOUT",
    },
    "page.about.tab.support": {
        "ru": "ПОДДЕРЖКА",
        "en": "SUPPORT",
    },
    "page.about.tab.help": {
        "ru": "СПРАВКА",
        "en": "HELP",
    },
    "page.about.section.version": {
        "ru": "Версия",
        "en": "Version",
    },
    "page.about.section.device": {
        "ru": "Устройство",
        "en": "Device",
    },
    "page.about.section.subscription": {
        "ru": "Подписка",
        "en": "Subscription",
    },
    "page.about.version.value_template": {
        "ru": "Версия {version}",
        "en": "Version {version}",
    },
    "page.about.button.update_settings": {
        "ru": "Настройка обновлений",
        "en": "Update Settings",
    },
    "page.about.button.whats_new": {
        "ru": "Что нового",
        "en": "What's new",
    },
    "page.about.action.whats_new.accessible_name": {
        "ru": "Что нового в этой версии",
        "en": "What's new in this version",
    },
    "page.about.action.whats_new.description": {
        "ru": "Открывает окно со списком изменений установленной версии.",
        "en": "Opens the list of changes in the installed version.",
    },
    "page.about.button.open": {
        "ru": "Открыть",
        "en": "Open",
    },
    "page.about.subscription.free": {
        "ru": "Free версия",
        "en": "Free version",
    },
    "page.about.subscription.checking": {
        "ru": "Проверка подписки...",
        "en": "Checking subscription...",
    },
    "page.about.subscription.premium_active": {
        "ru": "Premium активен",
        "en": "Premium active",
    },
    "page.about.subscription.premium_days": {
        "ru": "Premium (осталось {days} {unit})",
        "en": "Premium ({days} {unit} left)",
    },
    "page.about.subscription.desc": {
        "ru": "Подписка Zapret Premium открывает доступ к дополнительным темам, приоритетной поддержке и VPN-сервису.",
        "en": "Zapret Premium gives access to extra themes, priority support, and VPN service.",
    },
    "page.about.button.premium_vpn": {
        "ru": "Premium и VPN",
        "en": "Premium and VPN",
    },
    "page.about.button.zapret_kvn": {
        "ru": "Zapret KVN",
        "en": "Zapret KVN",
    },
    "page.about.action.zapret_kvn.accessible_name": {
        "ru": "Открыть вкладку Zapret KVN",
        "en": "Open the Zapret KVN tab",
    },
    "page.about.action.zapret_kvn.description": {
        "ru": "Показывает вкладку Zapret KVN: канал, подписка и исходный код.",
        "en": "Shows the Zapret KVN tab: channel, subscription and source code.",
    },
    "page.about.support.telegram.desc": {
        "ru": "Быстрые вопросы и общение с сообществом",
        "en": "Quick questions and community chat",
    },
    "page.about.support.discord.title": {
        "ru": "Discord",
        "en": "Discord",
    },
    "page.about.support.discord.desc": {
        "ru": "Обсуждение и живое общение",
        "en": "Discussion and live chat",
    },
    "page.about.help.section.links": {
        "ru": "Ссылки",
        "en": "Links",
    },
    "page.about.help.group.learn": {
        "ru": "Научиться",
        "en": "Learn",
    },
    "page.about.help.group.ask": {
        "ru": "Спросить",
        "en": "Ask",
    },
    "page.about.help.group.follow": {
        "ru": "Следить за новостями",
        "en": "Follow the news",
    },
    "page.about.help.learn.youtube.title": {
        "ru": "Видеокурс на YouTube",
        "en": "Video course on YouTube",
    },
    "page.about.help.learn.youtube.desc": {
        "ru": "Все видео курса по Zapret 2 одним списком",
        "en": "All Zapret 2 course videos in one playlist",
    },
    "page.about.help.learn.youtube.accessible_name": {
        "ru": "Открыть видеокурс на YouTube",
        "en": "Open the video course on YouTube",
    },
    "page.about.help.ask.folder.title": {
        "ru": "Папка со всеми чатами",
        "en": "Folder with all chats",
    },
    "page.about.help.ask.folder.desc": {
        "ru": "Все наши чаты в Telegram одной папкой — добавьте её целиком",
        "en": "All our Telegram chats in one folder — add it at once",
    },
    "page.about.help.ask.folder.accessible_name": {
        "ru": "Открыть папку со всеми чатами в Telegram",
        "en": "Open the folder with all Telegram chats",
    },
    "page.about.help.ask.telegram.title": {
        "ru": "Telegram-чат",
        "en": "Telegram chat",
    },
    "page.about.help.ask.telegram.accessible_name": {
        "ru": "Открыть Telegram-чат",
        "en": "Open the Telegram chat",
    },
    "page.about.help.ask.issues.title": {
        "ru": "Сообщить о проблеме",
        "en": "Report a problem",
    },
    "page.about.help.ask.issues.desc": {
        "ru": "Forgejo Issues: ошибки, пожелания и обмен конфигами",
        "en": "Forgejo Issues: bugs, requests and config sharing",
    },
    "page.about.help.ask.issues.accessible_name": {
        "ru": "Открыть Forgejo Issues",
        "en": "Open Forgejo Issues",
    },
    "page.about.help.news.links.title": {
        "ru": "Канал со всеми ссылками",
        "en": "Channel with all links",
    },
    "page.about.help.news.links.desc": {
        "ru": "Все наши каналы, чаты и сайты в одном месте",
        "en": "All our channels, chats and sites in one place",
    },
    "page.about.help.news.links.accessible_name": {
        "ru": "Открыть канал со всеми ссылками",
        "en": "Open the channel with all links",
    },
    "page.about.help.news.source.title": {
        "ru": "Исходный код",
        "en": "Source code",
    },
    "page.about.help.news.source.desc": {
        "ru": "Репозиторий программы в Forgejo",
        "en": "The program repository on Forgejo",
    },
    "page.about.help.news.source.accessible_name": {
        "ru": "Открыть исходный код в Forgejo",
        "en": "Open the source code on Forgejo",
    },
    "page.about.help_link.title": {
        "ru": "Нужна помощь?",
        "en": "Need help?",
    },
    "page.about.help_link.desc": {
        "ru": "Вики, видеокурс, чаты и новости собраны на вкладке «Справка»",
        "en": "Wiki, video course, chats and news are on the Help tab",
    },
    "page.about.help_link.button": {
        "ru": "Открыть справку",
        "en": "Open help",
    },
    "page.about.help_link.accessible_name": {
        "ru": "Открыть вкладку «Справка»",
        "en": "Open the Help tab",
    },
    "page.about.help.button.open": {
        "ru": "Открыть",
        "en": "Open",
    },
    "page.about.help.docs.forum.title": {
        "ru": "Вики-сайт",
        "en": "Wiki site",
    },
    "page.about.help.docs.forum.desc": {
        "ru": "Документация и инструкции",
        "en": "Documentation and guides",
    },
    "page.about.help.docs.android.title": {
        "ru": "На Android (Magisk Zapret, ByeByeDPI и др.)",
        "en": "On Android (Magisk Zapret, ByeByeDPI, etc.)",
    },
    "page.about.help.docs.android.desc": {
        "ru": "Открыть инструкцию на сайте",
        "en": "Open website guide",
    },
    "page.about.help.news.telegram.title": {
        "ru": "Telegram канал",
        "en": "Telegram channel",
    },
    "page.about.help.news.telegram.desc": {
        "ru": "Новости и обновления",
        "en": "News and updates",
    },
    "page.about.help.news.mastodon.title": {
        "ru": "Mastodon профиль",
        "en": "Mastodon profile",
    },
    "page.about.help.news.mastodon.desc": {
        "ru": "Новости в Fediverse",
        "en": "Fediverse updates",
    },
    "page.about.help.news.bastyon.title": {
        "ru": "Bastyon профиль",
        "en": "Bastyon profile",
    },
    "page.about.help.news.bastyon.desc": {
        "ru": "Новости в Bastyon",
        "en": "Bastyon updates",
    },
    "page.about.help.motto.title": {
        "ru": "keep thinking, keep searching, keep learning....",
        "en": "keep thinking, keep searching, keep learning....",
    },
    "page.about.help.motto.subtitle": {
        "ru": "Продолжай думать, продолжай искать, продолжай учиться....",
        "en": "Keep thinking, keep searching, keep learning....",
    },
    "page.about.help.motto.cta": {
        "ru": "Zapret2 - думай свободно, ищи смелее, учись всегда.",
        "en": "Zapret2 - think freely, search boldly, learn always.",
    },
    "page.appearance.subtitle": {
        "ru": "Настройка внешнего вида приложения",
        "en": "Application appearance settings",
    },
    "page.appearance.section.display_mode": {
        "ru": "Режим отображения",
        "en": "Display Mode",
    },
    "page.appearance.section.background": {
        "ru": "Фон окна",
        "en": "Window Background",
    },
    "page.appearance.section.holiday": {
        "ru": "Новогоднее оформление",
        "en": "Holiday Effects",
    },
    "page.appearance.section.accent": {
        "ru": "Акцентный цвет",
        "en": "Accent Color",
    },
    "page.appearance.section.performance": {
        "ru": "Производительность",
        "en": "Performance",
    },
    "page.autostart.subtitle": {
        "ru": "Настройка автоматического запуска Zapret",
        "en": "Configure automatic Zapret startup",
    },
    "page.autostart.section.status": {
        "ru": "Статус",
        "en": "Status",
    },
    "page.autostart.section.mode": {
        "ru": "Режим",
        "en": "Mode",
    },
    "page.autostart.section.select_type": {
        "ru": "Автозапуск программы",
        "en": "Application autostart",
    },
    "page.autostart.section.info": {
        "ru": "Информация",
        "en": "Information",
    },
    "page.autostart.status.disabled.title": {
        "ru": "Автозапуск отключён",
        "en": "Autostart disabled",
    },
    "page.autostart.status.disabled.desc": {
        "ru": "Zapret не запускается автоматически",
        "en": "Zapret does not start automatically",
    },
    "page.autostart.status.enabled.title": {
        "ru": "Автозапуск включён",
        "en": "Autostart enabled",
    },
    "page.autostart.status.enabled.desc.base": {
        "ru": "Zapret запускается автоматически при входе в Windows и открывается в трее",
        "en": "Zapret starts automatically on Windows logon and opens in the tray",
    },
    "page.autostart.button.disable": {
        "ru": "Отключить",
        "en": "Disable",
    },
    "page.autostart.mode.current_label": {
        "ru": "Текущий режим:",
        "en": "Current mode:",
    },
    "page.autostart.mode.loading": {
        "ru": "Загрузка...",
        "en": "Loading...",
    },
    "page.autostart.mode.strategy_label": {
        "ru": "Стратегия:",
        "en": "Strategy:",
    },
    "page.autostart.mode.zapret2_mode": {
        "ru": "Профили (Zapret 2)",
        "en": "Zapret 2 mode",
    },
    "page.autostart.mode.orchestra_learning": {
        "ru": "Оркестр (автообучение)",
        "en": "Orchestrator (auto-learning)",
    },
    "page.autostart.mode.unknown": {
        "ru": "Неизвестно",
        "en": "Unknown",
    },
    "page.autostart.strategy.not_selected": {
        "ru": "Не выбрана",
        "en": "Not selected",
    },
    "page.autostart.option.gui.title": {
        "ru": "Автозапуск программы Zapret",
        "en": "Autostart Zapret application",
    },
    "page.autostart.option.gui.desc": {
        "ru": "Запускает главное окно программы при входе в Windows. Приложение стартует в трее и уже оттуда применяет текущие настройки.",
        "en": "Starts the main application window on Windows logon. The app launches in the tray and applies the current settings from there.",
    },
    "page.autostart.tip.recommendation": {
        "ru": "Используется один тип автозапуска: ярлык ZapretGUI в папке автозагрузки Windows.",
        "en": "Only one autostart type is used: a ZapretGUI shortcut in the Windows Startup folder.",
    },
    "page.custom_domains.title": {
        "ru": "Кастомные (мои) домены (hostlist) для работы с Zapret",
        "en": "Custom Domains (hostlist) for Zapret",
    },
    "page.custom_domains.subtitle": {
        "ru": "Управление доменами (other.txt). Субдомены учитываются автоматически. Строчка rkn.ru учитывает и сайт fuckyou.rkn.ru и сайт ass.rkn.ru. Чтобы исключить субдомены напишите домен с символов ^ в начале, то есть например так ^rkn.ru",
        "en": "Manage domains (other.txt). Subdomains are handled automatically. Prefix a domain with ^ to match only the exact domain.",
    },
    "page.custom_domains.description": {
        "ru": "Здесь редактируется пользовательский список доменов `lists/user/other.txt`. Системная база лежит в `lists/base/other.txt`, а итоговый `lists/other.txt` собирается автоматически. URL автоматически преобразуются в домены. Изменения сохраняются автоматически. Поддерживается Ctrl+Z.",
        "en": "Edit the custom domains list `lists/user/other.txt`. The system base lives in `lists/base/other.txt`, and the final `lists/other.txt` is rebuilt automatically. URLs are converted to domains automatically. Changes are saved automatically. Ctrl+Z is supported.",
    },
    "page.custom_domains.card.add": {
        "ru": "Добавить домен",
        "en": "Add Domain",
    },
    "page.custom_domains.card.actions": {
        "ru": "Действия",
        "en": "Actions",
    },
    "page.custom_domains.card.editor": {
        "ru": "lists/user/other.txt (редактор)",
        "en": "lists/user/other.txt (editor)",
    },
    "page.custom_domains.input.placeholder": {
        "ru": "Введите домен или URL (например: example.com или https://site.com/page)",
        "en": "Enter a domain or URL (for example: example.com or https://site.com/page)",
    },
    "page.custom_domains.button.add": {
        "ru": "Добавить",
        "en": "Add",
    },
    "page.custom_domains.button.open_file": {
        "ru": "Открыть файл",
        "en": "Open File",
    },
    "page.custom_domains.button.reset_file": {
        "ru": "Сбросить файл",
        "en": "Reset File",
    },
    "page.custom_domains.button.clear_all": {
        "ru": "Очистить всё",
        "en": "Clear All",
    },
    "page.custom_domains.confirm.reset_file": {
        "ru": "Подтвердить сброс",
        "en": "Confirm reset",
    },
    "page.custom_domains.confirm.clear_all": {
        "ru": "Подтвердить очистку",
        "en": "Confirm clear",
    },
    "page.custom_domains.tooltip.open_file": {
        "ru": "Сохраняет изменения и открывает `lists/user/other.txt` в проводнике",
        "en": "Saves changes and opens `lists/user/other.txt` in Explorer",
    },
    "page.custom_domains.tooltip.reset_file": {
        "ru": "Очищает `lists/user/other.txt` и пересобирает `lists/other.txt` из системной базы",
        "en": "Clears `lists/user/other.txt` and rebuilds `lists/other.txt` from the system base",
    },
    "page.custom_domains.tooltip.clear_all": {
        "ru": "Удаляет только пользовательские домены. Системная база из `lists/base/other.txt` останется",
        "en": "Removes only custom domains. The system base from `lists/base/other.txt` remains",
    },
    "page.custom_domains.editor.placeholder": {
        "ru": "Домены по одному на строку:\nexample.com\nsubdomain.site.org\n\nКомментарии начинаются с #",
        "en": "One domain per line:\nexample.com\nsubdomain.site.org\n\nComments start with #",
    },
    "page.custom_domains.hint.autosave": {
        "ru": "Изменения сохраняются автоматически через 500мс",
        "en": "Changes are saved automatically after 500 ms",
    },
    "page.custom_domains.status.error": {
        "ru": "❌ Ошибка: {error}",
        "en": "❌ Error: {error}",
    },
    "page.custom_domains.status.suffix.saved": {
        "ru": " • ✅ Сохранено",
        "en": " • ✅ Saved",
    },
    "page.custom_domains.status.suffix.reset": {
        "ru": " • ✅ Сброшено",
        "en": " • ✅ Reset",
    },
    "page.custom_domains.status.stats": {
        "ru": "📊 Доменов: {total} (база: {base}, пользовательские: {user})",
        "en": "📊 Domains: {total} (base: {base}, custom: {user})",
    },
    "page.custom_domains.infobar.error": {
        "ru": "Ошибка",
        "en": "Error",
    },
    "page.custom_domains.infobar.info": {
        "ru": "Информация",
        "en": "Information",
    },
    "page.custom_domains.infobar.invalid_domain": {
        "ru": "Не удалось распознать домен:\n{value}\n\nВведите корректный домен (например: example.com)",
        "en": "Could not recognize domain:\n{value}\n\nEnter a valid domain (for example: example.com)",
    },
    "page.custom_domains.infobar.duplicate": {
        "ru": "Домен уже добавлен:\n{domain}",
        "en": "Domain already added:\n{domain}",
    },
    "page.custom_domains.infobar.reset_failed": {
        "ru": "Не удалось сбросить my hostlist",
        "en": "Failed to reset my hostlist",
    },
    "page.custom_domains.infobar.reset_failed_error": {
        "ru": "Не удалось сбросить:\n{error}",
        "en": "Failed to reset:\n{error}",
    },
    "page.custom_domains.infobar.open_failed": {
        "ru": "Не удалось открыть:\n{error}",
        "en": "Failed to open:\n{error}",
    },
    "page.custom_ipset.title": {
        "ru": "Кастомные (мои) IP и подсети для ipset-all",
        "en": "Custom IPs and Subnets for ipset-all",
    },
    "page.custom_ipset.subtitle": {
        "ru": "Здесь Вы можете редактировать пользовательский список IP/подсетей `lists/user/ipset-all.txt`. Пишите только IP/CIDR, изменения сохраняются автоматически.",
        "en": "Edit the custom IP/subnet list `lists/user/ipset-all.txt`. Use IP/CIDR format only; changes are saved automatically.",
    },
    "page.custom_ipset.description": {
        "ru": "Добавляйте свои IP/подсети в `lists/user/ipset-all.txt`.\n• Одиночный IP: 1.2.3.4\n• Подсеть: 10.0.0.0/8\nДиапазоны (a-b) не поддерживаются.\nСистемная база хранится в `lists/base/ipset-all.txt`, а итоговый `lists/ipset-all.txt` собирается автоматически.",
        "en": "Add your own IPs/subnets to `lists/user/ipset-all.txt`.\n• Single IP: 1.2.3.4\n• Subnet: 10.0.0.0/8\nRanges (a-b) are not supported.\nThe system base is stored in `lists/base/ipset-all.txt`, and the final `lists/ipset-all.txt` is rebuilt automatically.",
    },
    "page.custom_ipset.section.add": {
        "ru": "Добавить IP/подсеть",
        "en": "Add IP/subnet",
    },
    "page.custom_ipset.input.placeholder": {
        "ru": "Например: 1.2.3.4 или 10.0.0.0/8",
        "en": "For example: 1.2.3.4 or 10.0.0.0/8",
    },
    "page.custom_ipset.button.add": {
        "ru": "Добавить",
        "en": "Add",
    },
    "page.custom_ipset.section.actions": {
        "ru": "Действия",
        "en": "Actions",
    },
    "page.custom_ipset.button.open_file": {
        "ru": "Открыть файл",
        "en": "Open file",
    },
    "page.custom_ipset.button.clear_all": {
        "ru": "Очистить всё",
        "en": "Clear all",
    },
    "page.custom_ipset.section.editor": {
        "ru": "lists/user/ipset-all.txt (редактор)",
        "en": "lists/user/ipset-all.txt (editor)",
    },
    "page.custom_ipset.editor.placeholder": {
        "ru": "IP/подсети по одному на строку:\n192.168.0.1\n10.0.0.0/8\n\nКомментарии начинаются с #",
        "en": "IPs/subnets one per line:\n192.168.0.1\n10.0.0.0/8\n\nComments start with #",
    },
    "page.custom_ipset.hint.autosave": {
        "ru": "💡 Изменения сохраняются автоматически через 500мс",
        "en": "💡 Changes are saved automatically after 500 ms",
    },
    "page.custom_ipset.status.error_load": {
        "ru": "❌ Ошибка загрузки: {error}",
        "en": "❌ Load error: {error}",
    },
    "page.custom_ipset.status.summary": {
        "ru": "📊 Записей: {total} (база: {base}, пользовательские: {user})",
        "en": "📊 Entries: {total} (base: {base}, custom: {user})",
    },
    "page.custom_ipset.status.saved_suffix": {
        "ru": " • ✅ Сохранено",
        "en": " • ✅ Saved",
    },
    "page.custom_ipset.validation.invalid_prefix": {
        "ru": "❌ Неверный формат:\n",
        "en": "❌ Invalid format:\n",
    },
    "page.custom_ipset.validation.line": {
        "ru": "Строка {line}: {value}",
        "en": "Line {line}: {value}",
    },
    "page.custom_ipset.validation.more_suffix": {
        "ru": "\n... и ещё {count}",
        "en": "\n... and {count} more",
    },
    "page.custom_ipset.error.parse_entry": {
        "ru": "Не удалось распознать IP или подсеть.\nПримеры:\n- 1.2.3.4\n- 10.0.0.0/8\nДиапазоны a-b не поддерживаются.",
        "en": "Could not recognize IP or subnet.\nExamples:\n- 1.2.3.4\n- 10.0.0.0/8\nRanges a-b are not supported.",
    },
    "page.custom_ipset.infobar.info_title": {
        "ru": "Информация",
        "en": "Information",
    },
    "page.custom_ipset.info.entry_exists": {
        "ru": "Запись уже есть:\n{entry}",
        "en": "Entry already exists:\n{entry}",
    },
    "page.custom_ipset.dialog.clear.title": {
        "ru": "Очистить всё",
        "en": "Clear all",
    },
    "page.custom_ipset.dialog.clear.body": {
        "ru": "Удалить все записи?",
        "en": "Delete all entries?",
    },
    "page.custom_ipset.error.open_file": {
        "ru": "Не удалось открыть:\n{error}",
        "en": "Failed to open:\n{error}",
    },
    "page.dns_check.title": {
        "ru": "Проверка DNS подмены",
        "en": "DNS Spoofing Check",
    },
    "page.dns_check.subtitle": {
        "ru": "Проверка резолвинга доменов YouTube и Discord через различные DNS серверы",
        "en": "Check resolution of YouTube and Discord domains via different DNS servers",
    },
    "page.dns_check.button.start": {
        "ru": "Начать проверку",
        "en": "Start check",
    },
    "page.dns_check.button.log": {
        "ru": "Подробный лог",
        "en": "Detailed log",
    },
    "page.dns_check.button.save": {
        "ru": "Сохранить результаты",
        "en": "Save results",
    },
    "page.dns_check.status.ready": {
        "ru": "Сравниваем ответ DNS с эталоном и видим, подменяет ли провайдер адреса",
        "en": "Compares the DNS answer with a reference to see whether the ISP spoofs addresses",
    },
    "page.dpi_settings.subtitle": {
        "ru": "Параметры обхода блокировок",
        "en": "Bypass configuration",
    },
    "page.dpi_settings.card.launch_method": {
        "ru": "Метод запуска стратегий (режим работы программы)",
        "en": "Strategy launch method (application mode)",
    },
    "page.dpi_settings.launch_method.desc": {
        "ru": "Выберите способ запуска обхода блокировок",
        "en": "Choose how to run bypass strategies",
    },
    "page.dpi_settings.section.zapret2": {
        "ru": f"Zapret 2 ({EXE_NAME_WINWS2})",
        "en": f"Zapret 2 ({EXE_NAME_WINWS2})",
    },
    "page.dpi_settings.section.zapret1": {
        "ru": f"Zapret 1 ({EXE_NAME_WINWS1})",
        "en": f"Zapret 1 ({EXE_NAME_WINWS1})",
    },
    "page.dpi_settings.option.recommended": {
        "ru": "рекомендуется",
        "en": "recommended",
    },
    "page.dpi_settings.method.zapret2_mode.title": {
        "ru": "Zapret 2",
        "en": "Zapret 2",
    },
    "page.dpi_settings.method.zapret2_mode.desc": {
        "ru": f"Режим Zapret 2 на движке {ENGINE_WINWS2} ({EXE_NAME_WINWS2}) + готовые пресеты для быстрого запуска. Поддерживает Lua-код для своих стратегий.",
        "en": f"Zapret 2 mode on {ENGINE_WINWS2} ({EXE_NAME_WINWS2}) with ready presets for quick launch. Supports custom Lua code for your own strategies.",
    },
    "page.dpi_settings.method.orchestra.title": {
        "ru": "Оркестратор v0.9.6 (Beta)",
        "en": "Orchestrator v0.9.6 (Beta)",
    },
    "page.dpi_settings.method.orchestra.desc": {
        "ru": "Автоматическое обучение. Система сама подбирает лучшие стратегии для каждого домена. Запоминает результаты между запусками.",
        "en": "Automatic learning. The system picks the best strategy per domain and remembers results between launches.",
    },
    "page.dpi_settings.method.zapret1_mode.title": {
        "ru": "Zapret 1",
        "en": "Zapret 1",
    },
    "page.dpi_settings.method.zapret1_mode.desc": {
        "ru": f"Режим Zapret 1 на движке {ENGINE_WINWS1} ({EXE_NAME_WINWS1}) + готовые пресеты для быстрого запуска. Не использует Lua-код и блобы.",
        "en": f"Zapret 1 mode on {ENGINE_WINWS1} ({EXE_NAME_WINWS1}) with ready presets for quick launch. Does not use Lua code or blobs.",
    },
    "page.dpi_settings.discord_restart.title": {
        "ru": "Перезапуск Discord",
        "en": "Restart Discord",
    },
    "page.dpi_settings.discord_restart.desc": {
        "ru": "Автоперезапуск при смене стратегии",
        "en": "Auto-restart on strategy change",
    },
    "page.dpi_settings.section.orchestra_settings": {
        "ru": "Настройки оркестратора",
        "en": "Orchestrator settings",
    },
    "page.dpi_settings.orchestra.strict_detection.title": {
        "ru": "Строгий режим детекции",
        "en": "Strict detection mode",
    },
    "page.dpi_settings.orchestra.strict_detection.desc": {
        "ru": "HTTP 200 + проверка блок-страниц",
        "en": "HTTP 200 + block-page checks",
    },
    "page.dpi_settings.orchestra.debug_file.title": {
        "ru": "Сохранять debug файл",
        "en": "Save debug file",
    },
    "page.dpi_settings.orchestra.debug_file.desc": {
        "ru": "Сырой debug файл для отладки",
        "en": "Raw debug file for troubleshooting",
    },
    "page.dpi_settings.orchestra.auto_restart_discord.title": {
        "ru": "Авторестарт Discord при FAIL",
        "en": "Auto-restart Discord on FAIL",
    },
    "page.dpi_settings.orchestra.auto_restart_discord.desc": {
        "ru": "Перезапуск Discord при неудачном обходе",
        "en": "Restart Discord on failed bypass",
    },
    "page.dpi_settings.orchestra.discord_fails.title": {
        "ru": "Фейлов для рестарта Discord",
        "en": "Fails before Discord restart",
    },
    "page.dpi_settings.orchestra.discord_fails.desc": {
        "ru": "Сколько FAIL подряд для перезапуска Discord",
        "en": "How many consecutive FAILs trigger Discord restart",
    },
    "page.dpi_settings.orchestra.lock_successes.title": {
        "ru": "Успехов для LOCK",
        "en": "Successes for LOCK",
    },
    "page.dpi_settings.orchestra.lock_successes.desc": {
        "ru": "Количество успешных обходов для закрепления стратегии",
        "en": "Number of successful bypasses to lock strategy",
    },
    "page.dpi_settings.orchestra.unlock_fails.title": {
        "ru": "Ошибок для AUTO-UNLOCK",
        "en": "Fails for AUTO-UNLOCK",
    },
    "page.dpi_settings.orchestra.unlock_fails.desc": {
        "ru": "Количество ошибок для автоматической разблокировки стратегии",
        "en": "Number of errors to auto-unlock strategy",
    },
    "page.dpi_settings.card.advanced": {
        "ru": "Дополнительные настройки",
        "en": "ADVANCED SETTINGS",
    },
    "page.dpi_settings.advanced.warning": {
        "ru": "Эти параметры лучше менять, только если уверены в результате",
        "en": "Better change these only if you are sure of the result",
    },
    "page.dpi_settings.advanced.wssize.title": {
        "ru": "Включить --wssize",
        "en": "Enable --wssize",
    },
    "page.dpi_settings.advanced.wssize.desc": {
        "ru": "Добавляет параметр размера окна TCP",
        "en": "Adds TCP window size parameter",
    },
    "page.dpi_settings.advanced.debug_log.title": {
        "ru": "Включить лог-файл (--debug)",
        "en": "Enable log file (--debug)",
    },
    "page.dpi_settings.advanced.debug_log.desc": {
        "ru": "Записывает логи winws в папку logs",
        "en": "Writes winws logs to the logs folder",
    },
    "page.hosts.subtitle": {
        "ru": "Щёлкните по плитке — адреса сервиса сразу запишутся в системный файл hosts.",
        "en": "Click a tile — the service addresses are written to the system hosts file right away.",
    },
    "page.hosts.summary.writing": {
        "ru": "Записываю…",
        "en": "Writing…",
    },
    "page.hosts.summary.written": {
        "ru": "Записано — перезапустите браузер, чтобы изменения заработали",
        "en": "Written — restart the browser for the changes to take effect",
    },
    "page.hosts.button.file": {
        "ru": "Файл hosts",
        "en": "Hosts file",
    },
    "page.hosts.button.file.description": {
        "ru": "Весь файл hosts с раскраской строк по владельцам.",
        "en": "The whole hosts file with lines colored by owner.",
    },
    "page.hosts.dns_all.button": {
        "ru": "DNS для всех",
        "en": "DNS for all",
    },
    "page.hosts.adobe.title": {
        "ru": "Блокировать активацию Adobe",
        "en": "Block Adobe activation",
    },
    "page.hosts.group.blocks": {
        "ru": "Блокировки",
        "en": "Blocking",
    },
    "page.hosts_file.notepad": {
        "ru": "Открыть в Блокноте",
        "en": "Open in Notepad",
    },
    "page.hosts_file.notepad_failed": {
        "ru": "Не удалось открыть Блокнот",
        "en": "Could not open Notepad",
    },
    "page.hosts.group.direct.hint": {
        "ru": "адрес прописывается как есть",
        "en": "the address is written as is",
    },
    "page.hosts.group.ai.hint": {
        "ru": "сами закрыты для России, нужен DNS-профиль",
        "en": "blocked for Russia by themselves, a DNS profile is needed",
    },
    "page.hosts.group.other.hint": {
        "ru": "через DNS-профиль",
        "en": "via a DNS profile",
    },
    "page.hosts.group.counter": {
        "ru": "{on} из {total}",
        "en": "{on} of {total}",
    },
    "page.hosts.summary.services": {
        "ru": "сервисов включено в hosts",
        "en": "services enabled in hosts",
    },
    "page.hosts.summary.lines": {
        "ru": "строк от ZapretGUI в файле: {lines}",
        "en": "ZapretGUI lines in the file: {lines}",
    },
    "page.hosts.adobe.note": {
        "ru": "Закрывает серверы проверки лицензии Adobe",
        "en": "Blocks Adobe license check servers",
    },
    "page.hosts.loading": {
        "ru": "Загрузка…",
        "en": "Loading…",
    },
    "page.hosts.summary.off": {
        "ru": "Сейчас ZapretGUI ничего не прописывает в hosts",
        "en": "ZapretGUI writes nothing to hosts right now",
    },
    "page.hosts.button.all_off": {
        "ru": "Выключить все",
        "en": "Turn all off",
    },
    "page.hosts.button.restore_access": {
        "ru": "Снять защиту и восстановить права",
        "en": "Remove protection and restore access",
    },
    "page.hosts.notice.read_only": {
        "ru": "Файл hosts защищён от записи (стоит «только чтение»). Программа сама защиту не снимает — нажмите кнопку справа, если хотите менять файл.",
        "en": "The hosts file is write-protected (read-only). The app never removes the protection by itself — press the button on the right if you want to change the file.",
    },
    "page.hosts.notice.no_access": {
        "ru": "Нет доступа к файлу hosts. Часто его блокирует антивирус. Кнопка справа вернёт стандартные права Windows.",
        "en": "No access to the hosts file. Antivirus software often locks it. The button on the right restores the standard Windows permissions.",
    },
    "page.hosts.search.placeholder": {
        "ru": "Найти сервис",
        "en": "Find a service",
    },
    "page.hosts_file.title": {
        "ru": "Файл hosts",
        "en": "Hosts file",
    },
    "page.hosts_file.search": {
        "ru": "Поиск по файлу hosts",
        "en": "Search the hosts file",
    },
    "page.hosts_file.save": {
        "ru": "Сохранить",
        "en": "Save",
    },
    "page.hosts_file.revert": {
        "ru": "Отменить правки",
        "en": "Discard edits",
    },
    "page.hosts_file.hint": {
        "ru": "Строки раскрашены по владельцу. Блок ZapretGUI можно править, но при следующем переключении сервиса на странице Hosts он перепишется. Ctrl+F — поиск.",
        "en": "Lines are colored by owner. You can edit the ZapretGUI block, but the next service switch on the Hosts page rewrites it. Ctrl+F — search.",
    },
    "page.hosts_file.editor": {
        "ru": "Текст файла hosts",
        "en": "Hosts file text",
    },
    "page.hosts_file.editor.description": {
        "ru": "Весь файл hosts. Ctrl+F — поиск, Ctrl+H — замена. Изменения записываются кнопкой «Сохранить».",
        "en": "The whole hosts file. Ctrl+F — search, Ctrl+H — replace. Changes are written with the “Save” button.",
    },
    "page.hosts_file.load_failed": {
        "ru": "Не удалось прочитать hosts",
        "en": "Could not read hosts",
    },
    "page.hosts_file.saved": {
        "ru": "Сохранено",
        "en": "Saved",
    },
    "page.hosts_file.saved.content": {
        "ru": "Перезапустите браузер, чтобы изменения заработали.",
        "en": "Restart the browser for the changes to take effect.",
    },
    "page.hosts_file.saved.managed": {
        "ru": "Вы поменяли блок ZapretGUI вручную: при следующем переключении сервиса на странице Hosts он перепишется.",
        "en": "You changed the ZapretGUI block by hand: the next service switch on the Hosts page will rewrite it.",
    },
    "page.hosts_file.save_failed": {
        "ru": "Не удалось сохранить hosts",
        "en": "Could not save hosts",
    },
    "page.hosts_file.notice.no_access": {
        "ru": "Нет доступа к файлу hosts. Часто его блокирует антивирус. Восстановить права можно кнопкой на странице Hosts.",
        "en": "No access to the hosts file. Antivirus software often locks it. Permissions can be restored with the button on the Hosts page.",
    },
    "page.hosts_file.notice.read_only": {
        "ru": "Файл защищён от записи (стоит «только чтение»). Сохранить не получится, пока защита стоит — снять её можно кнопкой на странице Hosts.",
        "en": "The file is write-protected (read-only). Saving is impossible while protection is on — remove it with the button on the Hosts page.",
    },
    "page.hosts_file.owner.zapretgui": {
        "ru": "ZapretGUI",
        "en": "ZapretGUI",
    },
    "page.hosts_file.owner.telegram": {
        "ru": "Telegram Proxy",
        "en": "Telegram Proxy",
    },
    "page.hosts_file.owner.max": {
        "ru": "Блокировка MAX",
        "en": "MAX blocking",
    },
    "page.hosts_file.owner.state_media": {
        "ru": "Блокировка госСМИ",
        "en": "State media blocking",
    },
    "page.hosts_file.owner.adobe": {
        "ru": "Adobe",
        "en": "Adobe",
    },
    "page.hosts_file.owner.user": {
        "ru": "Ваши строки",
        "en": "Your lines",
    },
    "page.hosts.dns_all.hint": {
        "ru": "Ставит выбранный профиль всем сервисам с DNS-профилем. Потом любой можно поменять отдельно.",
        "en": "Sets the chosen profile for every DNS service. You can still change any of them separately.",
    },
    "page.hosts.dns_all.skipped.title": {
        "ru": "Не у всех сервисов есть этот профиль",
        "en": "Not every service has this profile",
    },
    "page.hosts.dns_all.skipped.content": {
        "ru": "Оставлены как были: {names}",
        "en": "Left unchanged: {names}",
    },
    "page.hosts.profile.off": {
        "ru": "Выкл.",
        "en": "Off",
    },
    "page.hosts.group.direct": {
        "ru": "Напрямую",
        "en": "Direct",
    },
    "page.hosts.group.ai": {
        "ru": "ИИ-сервисы",
        "en": "AI services",
    },
    "page.hosts.group.other": {
        "ru": "Остальные сервисы",
        "en": "Other services",
    },
    "page.hosts.hint.ipv6": {
        "ru": "Нужен IPv6 — сейчас его нет",
        "en": "Needs IPv6 — not available right now",
    },
    "page.hosts.empty": {
        "ru": "Ничего не найдено",
        "en": "Nothing found",
    },
    "page.hosts.state.on": {
        "ru": "включён",
        "en": "on",
    },
    "page.hosts.state.off": {
        "ru": "выключен",
        "en": "off",
    },
    "page.hosts.state.changed": {
        "ru": "записывается",
        "en": "being written",
    },
    "page.hosts.apply_failed.title": {
        "ru": "Не удалось записать hosts",
        "en": "Could not write hosts",
    },
    "page.hosts.error.read.title": {
        "ru": "Не удалось прочитать hosts",
        "en": "Could not read hosts",
    },
    "page.hosts.permissions.restored.title": {
        "ru": "Права восстановлены",
        "en": "Access restored",
    },
    "page.hosts.permissions.restored.content": {
        "ru": "Теперь сервисы снова можно включать.",
        "en": "Services can be switched on again.",
    },
    "page.hosts.permissions.failed.title": {
        "ru": "Не удалось восстановить права",
        "en": "Could not restore access",
    },
    "page.logs.subtitle": {
        "ru": "Просмотр логов приложения в реальном времени",
        "en": "Real-time application logs",
    },
    "page.logs.tab.logs": {
        "ru": "ЛОГИ",
        "en": "LOGS",
    },
    "page.logs.tab.send": {
        "ru": "ПОДДЕРЖКА",
        "en": "SUPPORT",
    },
    "page.logs.tab.manage": {
        "ru": "УПРАВЛЕНИЕ",
        "en": "MANAGE",
    },
    "page.logs.card.controls": {
        "ru": "Управление логами",
        "en": "Log Controls",
    },
    "page.logs.card.content": {
        "ru": "Содержимое",
        "en": "Content",
    },
    "page.logs.tooltip.refresh": {
        "ru": "Обновить список файлов",
        "en": "Refresh file list",
    },
    "page.logs.button.copy": {
        "ru": "Копировать",
        "en": "Copy",
    },
    "page.logs.button.clear": {
        "ru": "Очистить",
        "en": "Clear",
    },
    "page.logs.button.folder": {
        "ru": "Папка",
        "en": "Folder",
    },
    "page.logs.errors.title": {
        "ru": "Ошибки и предупреждения",
        "en": "Errors and Warnings",
    },
    "page.logs.errors.count": {
        "ru": "Ошибок: {count}",
        "en": "Errors: {count}",
    },
    "page.logs.stats.loading": {
        "ru": "📊 Загрузка...",
        "en": "📊 Loading...",
    },
    "page.logs.stats.template": {
        "ru": "📊 Логи: {logs} (макс {max_logs}) | 🔧 Debug: {debug} (макс {max_debug}) | 💾 Размер: {size:.2f} MB",
        "en": "📊 Logs: {logs} (max {max_logs}) | 🔧 Debug: {debug} (max {max_debug}) | 💾 Size: {size:.2f} MB",
    },
    "page.logs.send.card.title": {
        "ru": "Поддержка через Forgejo Issues",
        "en": "Support via Forgejo Issues",
    },
    "page.logs.send.orchestra.active": {
        "ru": "В режиме оркестратора проверьте основной лог и файл orchestra_*.log",
        "en": "In orchestrator mode, check both the main log and the orchestra_*.log file",
    },
    "page.logs.send.desc": {
        "ru": "Нажмите кнопку, чтобы собрать ZIP из свежих логов, скопировать шаблон обращения и открыть Forgejo Issues.",
        "en": "Press the button to build a ZIP with fresh logs, copy a report template, and open Forgejo Issues.",
    },
    "page.logs.send.info": {
        "ru": "Будет создан архив в папке logs/support_bundles. Шаблон обращения автоматически попадёт в буфер обмена.",
        "en": "An archive will be created in logs/support_bundles. The report template will be copied to the clipboard automatically.",
    },
    "page.logs.send.button.send": {
        "ru": "Подготовить обращение",
        "en": "Prepare report",
    },
    "page.network.subtitle": {
        "ru": "Выберите DNS-сервер — он сразу встанет на отмеченные сетевые адаптеры. Кнопка «Замерить скорость» покажет, какой сервер отвечает быстрее.",
        "en": "Pick a DNS server — it is applied right away to the checked network adapters. “Measure speed” shows which server answers fastest.",
    },
    "page.network.dns.auto": {
        "ru": "Автоматически (DHCP)",
        "en": "Automatic (DHCP)",
    },
    "page.network.button.flush_dns_cache": {
        "ru": "Сбросить кэш DNS",
        "en": "Flush DNS cache",
    },
    "page.network.auto_tile.title": {
        "ru": "Автоматически",
        "en": "Automatic",
    },
    "page.network.auto_tile.note": {
        "ru": "DNS от роутера",
        "en": "DNS from the router",
    },
    "page.network.auto_tile.tooltip": {
        "ru": "DNS снова будет получаться автоматически от роутера или провайдера (DHCP). Помогает, если после ручной настройки интернет работает нестабильно.",
        "en": "DNS will again be received automatically from your router or ISP (DHCP). Helps when the internet is unstable after manual setup.",
    },
    "page.network.error.title": {
        "ru": "Ошибка",
        "en": "Error",
    },
    "page.network.error.flush_cache_failed": {
        "ru": "Не удалось очистить кэш: {error}",
        "en": "Failed to flush cache: {error}",
    },
    "page.network.now.eyebrow": {
        "ru": "Сейчас на отмеченных адаптерах",
        "en": "Now on the checked adapters",
    },
    "page.network.now.loading": {
        "ru": "Загружаю настройки сети…",
        "en": "Loading network settings…",
    },
    "page.network.now.applying": {
        "ru": "Применяю…",
        "en": "Applying…",
    },
    "page.network.now.auto.detail": {
        "ru": "DNS выдаёт роутер или провайдер.",
        "en": "DNS is provided by your router or ISP.",
    },
    "page.network.now.custom.title": {
        "ru": "Свой DNS",
        "en": "Custom DNS",
    },
    "page.network.now.mixed.title": {
        "ru": "На адаптерах разные DNS",
        "en": "Adapters use different DNS",
    },
    "page.network.now.mixed.detail": {
        "ru": "Выберите сервер — он встанет на все отмеченные адаптеры.",
        "en": "Pick a server — it will be applied to all checked adapters.",
    },
    "page.network.now.no_adapters.title": {
        "ru": "Адаптеры не отмечены",
        "en": "No adapters checked",
    },
    "page.network.now.no_adapters.detail": {
        "ru": "Отметьте адаптер ниже — выбранный DNS встанет на него.",
        "en": "Check an adapter below — the chosen DNS will be applied to it.",
    },
    "page.network.adapter.internet": {
        "ru": "интернет",
        "en": "internet",
    },
    "page.network.adapter.disconnected": {
        "ru": "не подключён",
        "en": "disconnected",
    },
    "page.network.adapters.caption": {
        "ru": "Применять к:",
        "en": "Apply to:",
    },
    "page.network.adapters.empty": {
        "ru": "Сетевые адаптеры не найдены",
        "en": "No network adapters found",
    },
    "page.network.button.measure": {
        "ru": "Замерить скорость",
        "en": "Measure speed",
    },
    "page.network.button.measure.running": {
        "ru": "Замеряю…",
        "en": "Measuring…",
    },
    "page.network.filter.name": {
        "ru": "Группа DNS-серверов",
        "en": "DNS server group",
    },
    "page.network.filter.all": {
        "ru": "Все",
        "en": "All",
    },
    "page.network.filter.custom": {
        "ru": "Свои",
        "en": "Custom",
    },
    "page.network.group.popular": {
        "ru": "Популярные",
        "en": "Popular",
    },
    "page.network.group.secure": {
        "ru": "Безопасные",
        "en": "Secure",
    },
    "page.network.group.ai": {
        "ru": "Для ИИ",
        "en": "For AI",
    },
    "page.network.group.encrypted": {
        "ru": "Шифрованные",
        "en": "Encrypted",
    },
    "page.network.group.encrypted.note": {
        "ru": "запросы шифрует встроенный dnscrypt-proxy — их не подменить и не закрыть по имени",
        "en": "queries are encrypted by the built-in dnscrypt-proxy — they cannot be replaced or blocked by name",
    },
    "page.network.tile.local_proxy": {
        "ru": "Программа запустит на компьютере службу dnscrypt-proxy и пропишет адаптеру адрес 127.0.0.1. Если шифрованные серверы не ответят, DNS не изменится.",
        "en": "The program starts the dnscrypt-proxy service on this computer and sets the adapter DNS to 127.0.0.1. If the encrypted servers do not answer, DNS is left unchanged.",
    },
    "page.network.group.ai.note": {
        "ru": "серверы сообщества — доверия к ним меньше",
        "en": "community servers — trust them less",
    },
    "page.network.group.lesser_known": {
        "ru": "Малоизвестные",
        "en": "Lesser-known",
    },
    "page.network.group.lesser_known.note": {
        "ru": "реже попадают под блокировки",
        "en": "less likely to be blocked",
    },
    "page.network.status.blocked": {
        "ru": "блокируется",
        "en": "blocked",
    },
    "page.network.status.at_risk": {
        "ru": "под угрозой",
        "en": "at risk",
    },
    "page.network.status.blocked.tooltip": {
        "ru": "В России блокируется: обычные запросы к этому серверу не доходят или подменяются по дороге.",
        "en": "Blocked in Russia: plain queries to this server do not arrive or are replaced on the way.",
    },
    "page.network.status.at_risk.tooltip": {
        "ru": "Под угрозой блокировки в России: пока работает, но может перестать отвечать.",
        "en": "At risk of being blocked in Russia: works for now, but may stop answering.",
    },
    "page.network.status.blocked.chosen.title": {
        "ru": "{name} в России блокируется",
        "en": "{name} is blocked in Russia",
    },
    "page.network.status.blocked.chosen.content": {
        "ru": "DNS поставлен, как вы выбрали. Если сайты перестанут открываться, выберите другой сервер. Что отвечает на вашей линии, покажет BlockCheck → «DNS-серверы».",
        "en": "The DNS is set as you chose. If sites stop opening, pick another server. BlockCheck → “DNS servers” shows what answers on your line.",
    },
    "page.network.tile.dnssec": {
        "ru": "DNSSEC: сервер проверяет подписи ответов и не отдаёт подделанные.",
        "en": "DNSSEC: the server verifies answer signatures and drops forged ones.",
    },
    "page.network.adapters.all": {
        "ru": "Все",
        "en": "All",
    },
    "page.network.adapters.all.tooltip": {
        "ru": "Отметить все сетевые интерфейсы: выбранный DNS встанет на каждый из них. Повторное нажатие возвращает исходные отметки.",
        "en": "Check every network interface: the chosen DNS is applied to each of them. Press again to restore the initial checks.",
    },
    "page.network.group.custom": {
        "ru": "Свои DNS",
        "en": "Custom DNS",
    },
    "page.network.grid.name": {
        "ru": "DNS-серверы",
        "en": "DNS servers",
    },
    "page.network.grid.description": {
        "ru": "Стрелки — выбор плитки, Enter или пробел — применить DNS.",
        "en": "Arrows move between tiles, Enter or Space applies the DNS.",
    },
    "page.network.tile.selected": {
        "ru": "выбран",
        "en": "selected",
    },
    "page.network.tile.not_selected": {
        "ru": "не выбран",
        "en": "not selected",
    },
    "page.network.tile.applying": {
        "ru": "применяю…",
        "en": "applying…",
    },
    "page.network.tile.fastest": {
        "ru": "быстрее всех",
        "en": "fastest",
    },
    "page.network.tile.custom_note": {
        "ru": "Свой сервер",
        "en": "Your server",
    },
    "page.network.tile.custom_hint": {
        "ru": "свой DNS, меню правки — клавиша меню",
        "en": "custom DNS, edit menu — Menu key",
    },
    "page.network.tile.custom_menu": {
        "ru": "Правая кнопка мыши — изменить или удалить",
        "en": "Right-click to edit or delete",
    },
    "page.network.add_tile.title": {
        "ru": "Свой DNS",
        "en": "Custom DNS",
    },
    "page.network.add_tile.note": {
        "ru": "Адрес DoH или IP-адреса",
        "en": "DoH address or IP addresses",
    },
    "page.network.custom.button.description": {
        "ru": "Открывает страницу добавления своего DNS-сервера: по адресу DoH или по IP-адресам.",
        "en": "Opens the page for adding your own DNS server: by DoH address or by IP addresses.",
    },
    "page.network.custom_server.title": {
        "ru": "Свой DNS",
        "en": "Custom DNS",
    },
    "page.network.custom_server.new": {
        "ru": "Новый DNS",
        "en": "New DNS",
    },
    "page.network.custom_server.doh": {
        "ru": "Адрес DoH",
        "en": "DoH address",
    },
    "page.network.custom_server.doh.hint": {
        "ru": "Шифрованный DNS, как в браузере. Вставьте адрес — IP-адреса сервера программа найдёт и проверит сама.",
        "en": "Encrypted DNS, like in a browser. Paste the address — the app finds and verifies the server's IP addresses itself.",
    },
    "page.network.custom_server.addresses": {
        "ru": "IP-адреса",
        "en": "IP addresses",
    },
    "page.network.custom_server.addresses.hint": {
        "ru": "Через пробел, первым — основной; IPv4 и IPv6 вместе. С адресом DoH поле можно оставить пустым.",
        "en": "Space-separated, primary first; IPv4 and IPv6 together. With a DoH address this field may stay empty.",
    },
    "page.network.custom_server.name": {
        "ru": "Название",
        "en": "Name",
    },
    "page.network.custom_server.name.hint": {
        "ru": "Подпись на плитке. Можно не писать — подставится имя сервера.",
        "en": "The tile caption. Optional — the server name is used by default.",
    },
    "page.network.custom_server.name.placeholder": {
        "ru": "Например, Мой DNS",
        "en": "For example, My DNS",
    },
    "page.network.custom_server.cancel": {
        "ru": "Отмена",
        "en": "Cancel",
    },
    "page.network.custom_server.cancel.name": {
        "ru": "Отмена: вернуться к настройке DNS",
        "en": "Cancel: back to DNS settings",
    },
    "page.network.custom_server.cancel.description": {
        "ru": "Закрывает страницу без сохранения.",
        "en": "Closes the page without saving.",
    },
    "page.network.custom_server.add": {
        "ru": "Добавить",
        "en": "Add",
    },
    "page.network.custom_server.save": {
        "ru": "Сохранить",
        "en": "Save",
    },
    "page.network.custom_server.checking": {
        "ru": "Проверяю сервер…",
        "en": "Checking the server…",
    },
    "page.network.custom_server.saving": {
        "ru": "Сохраняю…",
        "en": "Saving…",
    },
    "page.network.custom_server.save.description": {
        "ru": "Сохраняет сервер и возвращает к списку DNS. Адреса сервера DoH перед этим проверяются.",
        "en": "Saves the server and returns to the DNS list. DoH server addresses are verified first.",
    },
    "page.network.custom_server.error": {
        "ru": "Ошибка: {text}",
        "en": "Error: {text}",
    },
    "page.network.custom_server.added": {
        "ru": "Сервер «{name}» добавлен",
        "en": "Server “{name}” added",
    },
    "page.network.custom_server.saved": {
        "ru": "Сервер «{name}» сохранён",
        "en": "Server “{name}” saved",
    },
    "page.network.custom_server.saved.content": {
        "ru": "Адреса: {addresses}. Чтобы включить сервер, нажмите его плитку.",
        "en": "Addresses: {addresses}. Click the server tile to start using it.",
    },
    "page.network.custom_server.save_failed": {
        "ru": "Не удалось сохранить сервер: {error}",
        "en": "Could not save the server: {error}",
    },
    "page.network.custom.menu.edit": {
        "ru": "Редактировать",
        "en": "Edit",
    },
    "page.network.custom.menu.duplicate": {
        "ru": "Создать копию",
        "en": "Duplicate",
    },
    "page.network.custom.menu.copy": {
        "ru": "Копировать DNS в буфер обмена",
        "en": "Copy DNS to clipboard",
    },
    "page.network.custom.menu.delete": {
        "ru": "Удалить",
        "en": "Delete",
    },
    "page.network.custom.copied.title": {
        "ru": "DNS скопирован",
        "en": "DNS copied",
    },
    "page.network.custom.copied.content": {
        "ru": "Адреса DNS в буфере обмена.",
        "en": "DNS addresses are in the clipboard.",
    },
    "page.network.latency.measuring": {
        "ru": "замер…",
        "en": "measuring…",
    },
    "page.network.latency.timeout": {
        "ru": "нет ответа",
        "en": "no reply",
    },
    "page.network.latency.ms": {
        "ru": "{ms} мс",
        "en": "{ms} ms",
    },
    "page.network.latency.best": {
        "ru": "Быстрее всех: {name} — {ms} мс",
        "en": "Fastest: {name} — {ms} ms",
    },
    "page.network.latency.none": {
        "ru": "Ни один сервер не ответил",
        "en": "No server replied",
    },
    "page.network.latency.failed": {
        "ru": "Замер не удался",
        "en": "Measurement failed",
    },
    "page.network.latency.intercepted": {
        "ru": "Похоже, DNS-запросы перехватываются по пути (провайдером или роутером): ответил даже адрес, где DNS-сервера нет. Цифры показывают перехватчик, а не выбранные серверы.",
        "en": "DNS queries seem to be intercepted on the way (by your ISP or router): even an address with no DNS server replied. The numbers show the interceptor, not the chosen servers.",
    },
    "page.network.info.wait": {
        "ru": "Секунду — загружаю список адаптеров",
        "en": "One moment — loading the adapter list",
    },
    "page.network.info.no_adapters.title": {
        "ru": "Нет отмеченных адаптеров",
        "en": "No adapters checked",
    },
    "page.network.info.no_adapters.content": {
        "ru": "Отметьте хотя бы один адаптер в панели сверху.",
        "en": "Check at least one adapter in the panel above.",
    },
    "page.network.info.flush_done": {
        "ru": "Кэш DNS очищен",
        "en": "DNS cache flushed",
    },
    "page.network.error.apply.title": {
        "ru": "DNS не применён",
        "en": "DNS was not applied",
    },
    "page.network.error.apply.partial.title": {
        "ru": "DNS встал не везде",
        "en": "DNS was not applied everywhere",
    },
    "page.network.error.apply.partial.content": {
        "ru": "Не удалось изменить DNS на адаптерах: {failed} из {total}.",
        "en": "Could not change DNS on {failed} of {total} adapters.",
    },
    "page.network.isp_dns.infobar.title": {
        "ru": "DNS от провайдера",
        "en": "ISP DNS detected",
    },
    "page.network.isp_dns.infobar.content": {
        "ru": "У вас установлен DNS от провайдера (получен автоматически через DHCP). Провайдерский DNS может подменять ответы и мешать обходу блокировок.\n\nМожно вручную применить публичный DNS Quad9 или выбрать другой DNS из списка ниже.",
        "en": "Your DNS is set automatically from your ISP (via DHCP). ISP DNS may poison responses and interfere with DPI bypass.\n\nYou can manually apply public Quad9 DNS or choose another DNS from the list below.",
    },
    "page.network.isp_dns.infobar.action": {
        "ru": "Применить Quad9",
        "en": "Apply Quad9",
    },
    "page.network.isp_dns.infobar.dismiss": {
        "ru": "Нет, спасибо",
        "en": "No, thanks",
    },
    "page.orchestra.subtitle": {
        "ru": "Автоматическое обучение стратегий DPI bypass. Система находит лучшую стратегию для каждого домена (TCP: TLS/HTTP, UDP: QUIC/Discord Voice/STUN).\nЧтобы начать обучение зайдите на сайт и через несколько секунд обновите вкладку. Продолжайте это пока стратегия не будет помечена как LOCKED",
        "en": "Automatic DPI bypass strategy learning. The system finds the best strategy per domain (TCP: TLS/HTTP, UDP: QUIC/Discord Voice/STUN).\nOpen the site and refresh after a few seconds until strategy becomes LOCKED.",
    },
    "page.orchestra.status.not_started": {
        "ru": "Не запущен",
        "en": "Not started",
    },
    "page.orchestra.status.modes": {
        "ru": "• IDLE - ожидание соединений\n• LEARNING - перебирает стратегии\n• RUNNING - работает на лучших стратегиях\n• UNLOCKED - переобучение (RST блокировка)",
        "en": "• IDLE - waiting for connections\n• LEARNING - iterating strategies\n• RUNNING - using best strategies\n• UNLOCKED - relearning (RST block)",
    },
    "page.orchestra.log.placeholder": {
        "ru": "Логи обучения будут отображаться здесь...",
        "en": "Training logs will be shown here...",
    },
    "page.orchestra.filter.label": {
        "ru": "Фильтр:",
        "en": "Filter:",
    },
    "page.orchestra.filter.domain.placeholder": {
        "ru": "Домен (например: youtube.com)",
        "en": "Domain (for example: youtube.com)",
    },
    "page.orchestra.filter.protocol.all": {
        "ru": "Все",
        "en": "All",
    },
    "page.orchestra.filter.protocol.tls": {
        "ru": "TLS",
        "en": "TLS",
    },
    "page.orchestra.filter.protocol.http": {
        "ru": "HTTP",
        "en": "HTTP",
    },
    "page.orchestra.filter.protocol.udp": {
        "ru": "UDP",
        "en": "UDP",
    },
    "page.orchestra.filter.protocol.success": {
        "ru": "SUCCESS",
        "en": "SUCCESS",
    },
    "page.orchestra.filter.protocol.fail": {
        "ru": "FAIL",
        "en": "FAIL",
    },
    "page.orchestra.filter.clear.tooltip": {
        "ru": "Сбросить фильтр",
        "en": "Reset filter",
    },
    "page.orchestra.button.clear_log": {
        "ru": "Очистить лог",
        "en": "Clear log",
    },
    "page.orchestra.button.clear_learning": {
        "ru": "Сбросить обучение",
        "en": "Reset learning",
    },
    "page.orchestra.button.clear_learning.pending": {
        "ru": "Это всё сотрёт!",
        "en": "This will erase all!",
    },
    "page.orchestra.button.clear_learning.done": {
        "ru": "✓ Сброшено",
        "en": "✓ Reset",
    },
    "page.orchestra.log_history.title": {
        "ru": "История логов (макс. {max_logs})",
        "en": "Log history (max {max_logs})",
    },
    "page.orchestra.log_history.desc": {
        "ru": "Каждый запуск оркестратора создаёт новый лог с уникальным ID. Старые логи автоматически удаляются.",
        "en": "Each orchestrator launch creates a new log with a unique ID. Old logs are removed automatically.",
    },
    "page.orchestra.button.view_log": {
        "ru": "Просмотреть",
        "en": "View",
    },
    "page.orchestra.button.delete_log": {
        "ru": "Удалить",
        "en": "Delete",
    },
    "page.orchestra.button.clear_all_logs": {
        "ru": "Очистить все",
        "en": "Clear all",
    },
    "page.orchestra.status.running": {
        "ru": "✅ RUNNING - работает на лучших стратегиях",
        "en": "✅ RUNNING - using best strategies",
    },
    "page.orchestra.status.learning": {
        "ru": "🔄 LEARNING - перебирает стратегии",
        "en": "🔄 LEARNING - iterating strategies",
    },
    "page.orchestra.status.unlocked": {
        "ru": "🔓 UNLOCKED - переобучение (RST блокировка)",
        "en": "🔓 UNLOCKED - relearning (RST block)",
    },
    "page.orchestra.status.idle": {
        "ru": "⏸ IDLE - ожидание соединений",
        "en": "⏸ IDLE - waiting for connections",
    },
    "page.orchestra.log.learned_cleared": {
        "ru": "[INFO] Данные обучения сброшены",
        "en": "[INFO] Learning data reset",
    },
    "page.orchestra.log_history.current_suffix": {
        "ru": " (текущий)",
        "en": " (current)",
    },
    "page.orchestra.log_history.none": {
        "ru": "  Нет сохранённых логов",
        "en": "  No saved logs",
    },
    "page.orchestra.log_history.loaded": {
        "ru": "\n[INFO] === Загружен лог: {log_id} ===",
        "en": "\n[INFO] === Loaded log: {log_id} ===",
    },
    "page.orchestra.log_history.read_failed": {
        "ru": "[ERROR] Не удалось прочитать лог: {log_id}",
        "en": "[ERROR] Failed to read log: {log_id}",
    },
    "page.orchestra.log_history.deleted": {
        "ru": "[INFO] Удалён лог: {log_id}",
        "en": "[INFO] Deleted log: {log_id}",
    },
    "page.orchestra.log_history.delete_failed": {
        "ru": "[WARNING] Не удалось удалить лог (возможно, активный)",
        "en": "[WARNING] Failed to delete log (possibly active)",
    },
    "page.orchestra.log_history.deleted_count": {
        "ru": "[INFO] Удалено {count} лог-файлов",
        "en": "[INFO] Deleted {count} log files",
    },
    "page.orchestra.log_history.nothing_to_delete": {
        "ru": "[INFO] Нет логов для удаления",
        "en": "[INFO] No logs to delete",
    },
    "page.orchestra.context.copy_line": {
        "ru": "📋 Копировать строку",
        "en": "📋 Copy line",
    },
    "page.orchestra.context.lock_strategy": {
        "ru": "🔒 Залочить стратегию #{strategy} для {domain}",
        "en": "🔒 Lock strategy #{strategy} for {domain}",
    },
    "page.orchestra.context.unblock_strategy": {
        "ru": "✅ Разблокировать стратегию #{strategy} для {domain}",
        "en": "✅ Unblock strategy #{strategy} for {domain}",
    },
    "page.orchestra.context.block_strategy": {
        "ru": "🚫 Заблокировать стратегию #{strategy} для {domain}",
        "en": "🚫 Block strategy #{strategy} for {domain}",
    },
    "page.orchestra.context.add_whitelist": {
        "ru": "⬚ Добавить {domain} в белый список",
        "en": "⬚ Add {domain} to whitelist",
    },
    "page.orchestra.log.clipboard_copied": {
        "ru": "[INFO] Строка скопирована в буфер обмена",
        "en": "[INFO] Line copied to clipboard",
    },
    "page.orchestra.log.lock_unknown_strategy": {
        "ru": "[WARNING] Невозможно залочить: стратегия неизвестна",
        "en": "[WARNING] Cannot lock: strategy is unknown",
    },
    "page.orchestra.log.strategy_locked": {
        "ru": "[INFO] [USER] 🔒 Залочена стратегия #{strategy} для {domain} [{protocol}]",
        "en": "[INFO] [USER] 🔒 Locked strategy #{strategy} for {domain} [{protocol}]",
    },
    "page.orchestra.log.apply_user_lock": {
        "ru": "[INFO] Применяю user lock (перезапуск)...",
        "en": "[INFO] Applying user lock (restart)...",
    },
    "page.orchestra.log.user_lock_applied": {
        "ru": "[INFO] ✓ User lock применён",
        "en": "[INFO] ✓ User lock applied",
    },
    "page.orchestra.log.restart_failed": {
        "ru": "[ERROR] Не удалось перезапустить оркестратор",
        "en": "[ERROR] Failed to restart orchestrator",
    },
    "page.orchestra.log.not_running_user_lock_saved": {
        "ru": "[WARNING] Оркестратор не запущен, user lock сохранён в settings.sqlite3",
        "en": "[WARNING] Orchestrator is not running, user lock is saved in settings.sqlite3",
    },
    "page.orchestra.log.not_initialized": {
        "ru": "[ERROR] Оркестратор не инициализирован",
        "en": "[ERROR] Orchestrator is not initialized",
    },
    "page.orchestra.log.error": {
        "ru": "[ERROR] Ошибка: {error}",
        "en": "[ERROR] Error: {error}",
    },
    "page.orchestra.log.block_unknown_strategy": {
        "ru": "[WARNING] Невозможно заблокировать: стратегия неизвестна",
        "en": "[WARNING] Cannot block: strategy is unknown",
    },
    "page.orchestra.log.strategy_blocked": {
        "ru": "[INFO] 🚫 Заблокирована стратегия #{strategy} для {domain} [{protocol}]",
        "en": "[INFO] 🚫 Blocked strategy #{strategy} for {domain} [{protocol}]",
    },
    "page.orchestra.log.restart_for_block": {
        "ru": "[INFO] Перезапуск оркестратора для применения блокировки...",
        "en": "[INFO] Restarting orchestrator to apply block...",
    },
    "page.orchestra.log.strategy_unblocked": {
        "ru": "[INFO] ✅ Разблокирована стратегия #{strategy} для {domain} [{protocol}]",
        "en": "[INFO] ✅ Unblocked strategy #{strategy} for {domain} [{protocol}]",
    },
    "page.orchestra.log.restart_for_unblock": {
        "ru": "[INFO] Перезапуск оркестратора для применения разблокировки...",
        "en": "[INFO] Restarting orchestrator to apply unblock...",
    },
    "page.orchestra.log.whitelist_added": {
        "ru": "[INFO] ✅ Добавлен в белый список: {domain}",
        "en": "[INFO] ✅ Added to whitelist: {domain}",
    },
    "page.orchestra.log.whitelist_add_failed": {
        "ru": "[WARNING] Не удалось добавить: {domain}",
        "en": "[WARNING] Failed to add: {domain}",
    },
    "page.orchestra.blocked.title": {
        "ru": "Заблокированные стратегии",
        "en": "Blocked Strategies",
    },
    "page.orchestra.blocked.subtitle": {
        "ru": "Системные блокировки (стратегия 1 для заблокированных РКН сайтов) + пользовательский чёрный список. Оркестратор не будет их использовать.",
        "en": "System blocks (strategy=1 for blocked sites) plus custom blacklist. Orchestrator will not use them.",
    },
    "page.orchestra.blocked.card.add": {
        "ru": "Заблокировать стратегию вручную",
        "en": "Block strategy manually",
    },
    "page.orchestra.blocked.input.domain.placeholder": {
        "ru": "example.com",
        "en": "example.com",
    },
    "page.orchestra.blocked.button.block.tooltip": {
        "ru": "Заблокировать стратегию",
        "en": "Block strategy",
    },
    "page.orchestra.blocked.card.list": {
        "ru": "Чёрный список",
        "en": "Blacklist",
    },
    "page.orchestra.blocked.search.placeholder": {
        "ru": "Поиск по доменам...",
        "en": "Search by domain...",
    },
    "page.orchestra.blocked.button.refresh.tooltip": {
        "ru": "Обновить",
        "en": "Refresh",
    },
    "page.orchestra.blocked.button.clear_user": {
        "ru": "Очистить пользовательские",
        "en": "Clear custom",
    },
    "page.orchestra.blocked.button.clear_user.tooltip": {
        "ru": "Удалить все пользовательские блокировки (системные останутся)",
        "en": "Delete all custom blocks (system blocks remain)",
    },
    "page.orchestra.blocked.hint": {
        "ru": "Измените номер стратегии и она автоматически сохранится • Системные блокировки неизменяемы",
        "en": "Change strategy number and it saves automatically • System blocks are read-only",
    },
    "page.orchestra.blocked.section.user": {
        "ru": "Пользовательские ({count})",
        "en": "Custom ({count})",
    },
    "page.orchestra.blocked.section.system": {
        "ru": "Системные ({count}) - заблокированные РКН сайты",
        "en": "System ({count}) - blocked sites",
    },
    "page.orchestra.blocked.row.add.tooltip": {
        "ru": "Добавить ещё одну заблокированную стратегию для этого домена",
        "en": "Add one more blocked strategy for this domain",
    },
    "page.orchestra.blocked.row.unblock.tooltip": {
        "ru": "Разблокировать",
        "en": "Unblock",
    },
    "page.orchestra.blocked.row.system.tooltip": {
        "ru": "Системная блокировка (нельзя изменить)",
        "en": "System block (cannot be changed)",
    },
    "page.orchestra.blocked.count.reload_hint": {
        "ru": "Нажмите 'Обновить' для загрузки данных",
        "en": "Click 'Refresh' to load data",
    },
    "page.orchestra.blocked.count.total": {
        "ru": "Всего: {total} ({user_count} пользовательских + {default_count} системных)",
        "en": "Total: {total} ({user_count} custom + {default_count} system)",
    },
    "page.orchestra.blocked.infobar.applied.title": {
        "ru": "Применено",
        "en": "Applied",
    },
    "page.orchestra.blocked.infobar.unblocked": {
        "ru": "Стратегия #{strategy} разблокирована для {domain}. Оркестратор перезапускается.",
        "en": "Strategy #{strategy} unblocked for {domain}. Orchestrator is restarting.",
    },
    "page.orchestra.blocked.infobar.blocked": {
        "ru": "Стратегия #{strategy} заблокирована для {domain}. Оркестратор перезапускается.",
        "en": "Strategy #{strategy} blocked for {domain}. Orchestrator is restarting.",
    },
    "page.orchestra.blocked.infobar.info.title": {
        "ru": "Информация",
        "en": "Information",
    },
    "page.orchestra.blocked.infobar.no_user_blocks": {
        "ru": "Нет пользовательских блокировок для удаления. Системные блокировки не удаляются.",
        "en": "No custom blocks to delete. System blocks are not removed.",
    },
    "page.orchestra.blocked.dialog.clear_user.title": {
        "ru": "Подтверждение",
        "en": "Confirmation",
    },
    "page.orchestra.blocked.dialog.clear_user.body": {
        "ru": "Очистить пользовательский чёрный список ({count} записей)?\n\nСистемные блокировки останутся.",
        "en": "Clear custom blacklist ({count} entries)?\n\nSystem blocks will remain.",
    },
    "page.orchestra.blocked.infobar.cleared": {
        "ru": "Чёрный список очищен. Оркестратор перезапускается.",
        "en": "Blacklist cleared. Orchestrator is restarting.",
    },
    "page.orchestra.locked.title": {
        "ru": "Залоченные стратегии",
        "en": "Locked Strategies",
    },
    "page.orchestra.locked.subtitle": {
        "ru": "Домены с фиксированной стратегией. Оркестратор не будет менять стратегию для этих доменов. Это значит что оркестратор нашёл для этих сайтов наилучшую стратегию. Вы можете зафиксировать свою стратегию для домена здесь.\nЕсли Вас не устраивает текущая стратегия - заблокируйте её здесь и оркестратор начнёт обучение заново при следующем посещении сайта.\nЕсли Вы просто хотите начать обучение заново - разлочьте стратегию.",
        "en": "Domains with fixed strategy. Orchestrator will not change strategy for these domains.\nIf current strategy is not acceptable, block it to force relearning.\nUnlock to start learning from scratch.",
    },
    "page.orchestra.locked.card.add": {
        "ru": "Залочить стратегию вручную",
        "en": "Lock strategy manually",
    },
    "page.orchestra.locked.input.domain.placeholder": {
        "ru": "example.com",
        "en": "example.com",
    },
    "page.orchestra.locked.button.lock.tooltip": {
        "ru": "Залочить стратегию",
        "en": "Lock strategy",
    },
    "page.orchestra.locked.card.list": {
        "ru": "Список залоченных",
        "en": "Locked list",
    },
    "page.orchestra.locked.search.placeholder": {
        "ru": "Поиск по доменам...",
        "en": "Search by domain...",
    },
    "page.orchestra.locked.button.refresh.tooltip": {
        "ru": "Обновить",
        "en": "Refresh",
    },
    "page.orchestra.locked.button.unlock_all": {
        "ru": "Разлочить все",
        "en": "Unlock all",
    },
    "page.orchestra.locked.hint": {
        "ru": "Измените номер стратегии и она автоматически сохранится",
        "en": "Change strategy number and it will be saved automatically",
    },
    "page.orchestra.rows.show_more": {
        "ru": "Показать ещё {count} (скрыто {hidden})",
        "en": "Show {count} more ({hidden} hidden)",
    },
    "page.orchestra.locked.row.unlock.tooltip": {
        "ru": "Разлочить",
        "en": "Unlock",
    },
    "page.orchestra.locked.warning.blocked_strategy": {
        "ru": "Стратегия #{strategy} заблокирована для {domain}. Разблокируйте её на странице 'Заблокированные'.",
        "en": "Strategy #{strategy} is blocked for {domain}. Unblock it on the 'Blocked' page.",
    },
    "page.orchestra.locked.infobar.applied.title": {
        "ru": "Применено",
        "en": "Applied",
    },
    "page.orchestra.locked.infobar.unlocked": {
        "ru": "Стратегия разлочена для {domain}. Оркестратор перезапускается.",
        "en": "Strategy unlocked for {domain}. Orchestrator is restarting.",
    },
    "page.orchestra.locked.count.reload_hint": {
        "ru": "Нажмите 'Обновить' для загрузки данных",
        "en": "Click 'Refresh' to load data",
    },
    "page.orchestra.locked.count.total": {
        "ru": "Всего залочено: {total} (TCP: {tcp_count}, UDP: {udp_count})",
        "en": "Total locked: {total} (TCP: {tcp_count}, UDP: {udp_count})",
    },
    "page.orchestra.locked.dialog.unlock_all.title": {
        "ru": "Подтверждение",
        "en": "Confirmation",
    },
    "page.orchestra.locked.dialog.unlock_all.body": {
        "ru": "Разлочить все {total} стратегий?\nОркестратор начнёт обучение заново.",
        "en": "Unlock all {total} strategies?\nOrchestrator will start learning again.",
    },
    "page.orchestra.locked.infobar.unlocked_all": {
        "ru": "Разлочены все {total} стратегий. Оркестратор перезапускается.",
        "en": "All {total} strategies unlocked. Orchestrator is restarting.",
    },
    "page.orchestra.ratings.title": {
        "ru": "История стратегий (рейтинги)",
        "en": "Strategy History (Ratings)",
    },
    "page.orchestra.ratings.subtitle": {
        "ru": "Рейтинг = успехи / (успехи + провалы). При UNLOCK выбирается лучшая стратегия из истории.",
        "en": "Rating = successes / (successes + failures). On UNLOCK the best strategy from history is selected.",
    },
    "page.orchestra.ratings.card.filter": {
        "ru": "Фильтр",
        "en": "Filter",
    },
    "page.orchestra.ratings.filter.placeholder": {
        "ru": "Поиск по домену...",
        "en": "Search by domain...",
    },
    "page.orchestra.ratings.button.refresh": {
        "ru": "Обновить",
        "en": "Refresh",
    },
    "page.orchestra.ratings.stats.loading": {
        "ru": "Загрузка...",
        "en": "Loading...",
    },
    "page.orchestra.ratings.card.history": {
        "ru": "Рейтинги по доменам",
        "en": "Domain ratings",
    },
    "page.orchestra.ratings.history.placeholder": {
        "ru": "История стратегий появится после обучения...",
        "en": "Strategy history will appear after training...",
    },
    "page.orchestra.ratings.status.not_initialized": {
        "ru": "Оркестратор не инициализирован",
        "en": "Orchestrator is not initialized",
    },
    "page.orchestra.ratings.status.no_history": {
        "ru": "Нет данных истории",
        "en": "No history data",
    },
    "page.orchestra.ratings.status.lock.tls": {
        "ru": " [TLS LOCK]",
        "en": " [TLS LOCK]",
    },
    "page.orchestra.ratings.status.lock.http": {
        "ru": " [HTTP LOCK]",
        "en": " [HTTP LOCK]",
    },
    "page.orchestra.ratings.status.lock.udp": {
        "ru": " [UDP LOCK]",
        "en": " [UDP LOCK]",
    },
    "page.orchestra.ratings.stats.filtered": {
        "ru": "Показано: {shown} из {total} доменов, {records} записей",
        "en": "Shown: {shown} of {total} domains, {records} entries",
    },
    "page.orchestra.ratings.stats.total": {
        "ru": "Всего: {total} доменов, {records} записей",
        "en": "Total: {total} domains, {records} entries",
    },
    "page.orchestra.whitelist.title": {
        "ru": "Белый список",
        "en": "Whitelist",
    },
    "page.orchestra.whitelist.subtitle": {
        "ru": "Домены, которые НЕ обрабатываются оркестратором. Эти сайты работают без DPI bypass.",
        "en": "Domains not processed by orchestrator. These sites run without DPI bypass.",
    },
    "page.orchestra.whitelist.notice.applied_now": {
        "ru": "✅ Изменения применяются сразу, перезапуск оркестратора не нужен",
        "en": "✅ Changes apply immediately, no orchestrator restart needed",
    },
    "page.orchestra.whitelist.card.add": {
        "ru": "Добавить домен",
        "en": "Add domain",
    },
    "page.orchestra.whitelist.input.placeholder": {
        "ru": "example.com",
        "en": "example.com",
    },
    "page.orchestra.whitelist.tooltip.add": {
        "ru": "Добавить в белый список",
        "en": "Add to whitelist",
    },
    "page.orchestra.whitelist.card.list": {
        "ru": "Белый список доменов",
        "en": "Domain whitelist",
    },
    "page.orchestra.whitelist.search.placeholder": {
        "ru": "Поиск по доменам...",
        "en": "Search domains...",
    },
    "page.orchestra.whitelist.button.clear_user": {
        "ru": "Очистить пользовательские",
        "en": "Clear custom",
    },
    "page.orchestra.whitelist.tooltip.clear_user": {
        "ru": "Удалить все пользовательские домены (системные останутся)",
        "en": "Remove all custom domains (system domains stay)",
    },
    "page.orchestra.whitelist.status.init_error": {
        "ru": "Ошибка инициализации",
        "en": "Initialization error",
    },
    "page.orchestra.whitelist.section.user": {
        "ru": "Пользовательские ({count})",
        "en": "Custom ({count})",
    },
    "page.orchestra.whitelist.section.system": {
        "ru": "🔒 Системные ({count}) — нельзя удалить",
        "en": "🔒 System ({count}) — cannot be removed",
    },
    "page.orchestra.whitelist.tooltip.delete": {
        "ru": "Удалить из белого списка",
        "en": "Remove from whitelist",
    },
    "page.orchestra.whitelist.tooltip.system_domain": {
        "ru": "Системный домен (нельзя удалить)",
        "en": "System domain (cannot be removed)",
    },
    "page.orchestra.whitelist.count.total": {
        "ru": "Всего: {total} ({system} системных + {user} пользовательских)",
        "en": "Total: {total} ({system} system + {user} custom)",
    },
    "page.orchestra.whitelist.error.init_runner": {
        "ru": "Не удалось инициализировать оркестратор",
        "en": "Failed to initialize orchestrator",
    },
    "page.orchestra.whitelist.infobar.info_title": {
        "ru": "Информация",
        "en": "Information",
    },
    "page.orchestra.whitelist.info.already_exists": {
        "ru": "Домен {domain} уже в списке",
        "en": "Domain {domain} is already in the list",
    },
    "page.orchestra.whitelist.info.no_user_domains": {
        "ru": "Нет пользовательских доменов для удаления. Системные домены не удаляются.",
        "en": "No custom domains to remove. System domains are not removed.",
    },
    "page.orchestra.whitelist.dialog.clear_user.title": {
        "ru": "Подтверждение",
        "en": "Confirmation",
    },
    "page.orchestra.whitelist.dialog.clear_user.body": {
        "ru": "Удалить все пользовательские домены ({count})?\n\nСистемные домены останутся.",
        "en": "Delete all custom domains ({count})?\n\nSystem domains will remain.",
    },
    "page.premium.subtitle": {
        "ru": "Управление подпиской Zapret Premium (премиум)",
        "en": "Manage Zapret Premium subscription",
    },
    "page.premium.section.subscription_status": {
        "ru": "Статус подписки",
        "en": "Subscription Status",
    },
    "page.premium.section.device_binding": {
        "ru": "Привязка устройства",
        "en": "Device Binding",
    },
    "page.premium.section.device_info": {
        "ru": "Информация об устройстве",
        "en": "Device Information",
    },
    "page.premium.section.actions": {
        "ru": "Действия",
        "en": "Actions",
    },
    "page.premium.instructions": {
        "ru": "1. Нажмите «Создать код»\n2. Отправьте код боту @zapretvpns_bot в Telegram (сообщением)\n3. Вернитесь сюда — приложение обновит статус автоматически",
        "en": "1. Click \"Create code\"\n2. Send the code to @zapretvpns_bot in Telegram (as a message)\n3. Return here — the app will refresh the status automatically",
    },
    "page.premium.placeholder.pair_code": {
        "ru": "ABCD12EF",
        "en": "ABCD12EF",
    },
    "page.premium.button.create_code": {
        "ru": "Создать код",
        "en": "Create code",
    },
    "page.premium.button.create_code.loading": {
        "ru": "Создание...",
        "en": "Creating...",
    },
    "page.premium.button.open_bot": {
        "ru": "Открыть бота",
        "en": "Open bot",
    },
    "page.premium.button.refresh_status": {
        "ru": "Обновить статус",
        "en": "Refresh status",
    },
    "page.premium.button.reset_activation": {
        "ru": "Сбросить активацию",
        "en": "Reset activation",
    },
    "page.premium.button.test_connection": {
        "ru": "Проверить соединение",
        "en": "Test connection",
    },
    "page.premium.button.test_connection.loading": {
        "ru": "Проверка...",
        "en": "Checking...",
    },
    "page.premium.button.extend": {
        "ru": "Продлить подписку",
        "en": "Extend subscription",
    },
    "page.premium.label.device_id.loading": {
        "ru": "ID устройства: загрузка...",
        "en": "Device ID: loading...",
    },
    "page.premium.label.device_id.value": {
        "ru": "ID устройства: {device_id}...",
        "en": "Device ID: {device_id}...",
    },
    "page.premium.label.device_token.none": {
        "ru": "device token: —",
        "en": "device token: -",
    },
    "page.premium.label.device_token.present": {
        "ru": "device token: ✅",
        "en": "device token: ✅",
    },
    "page.premium.label.device_token.absent": {
        "ru": "device token: ❌",
        "en": "device token: ❌",
    },
    "page.premium.label.pair_code.value": {
        "ru": "pair: {pair_code}",
        "en": "pair: {pair_code}",
    },
    "page.premium.label.last_check.none": {
        "ru": "Последняя проверка: —",
        "en": "Last check: -",
    },
    "page.premium.label.last_check.value": {
        "ru": "Последняя проверка: {date}",
        "en": "Last check: {date}",
    },
    "page.premium.label.server.checking": {
        "ru": "Сервер: проверка...",
        "en": "Server: checking...",
    },
    "page.premium.label.server.idle": {
        "ru": "Сервер: нажмите «Проверить соединение»",
        "en": "Server: click \"Test connection\"",
    },
    "page.premium.activation.error.init": {
        "ru": "❌ Ошибка инициализации",
        "en": "❌ Initialization error",
    },
    "page.premium.activation.error.generic": {
        "ru": "❌ Ошибка: {error}",
        "en": "❌ Error: {error}",
    },
    "page.premium.activation.error.invalid_reply": {
        "ru": "Неверный ответ",
        "en": "Invalid response",
    },
    "page.premium.activation.progress.creating_code": {
        "ru": "🔄 Создаю код...",
        "en": "🔄 Creating code...",
    },
    "page.premium.activation.success.code_created": {
        "ru": "✅ Код создан примерно на 10 минут и скопирован. Отправьте его боту — приложение само обновит статус.",
        "en": "✅ Code was created for about 10 minutes and copied. Send it to the bot — the app will refresh automatically.",
    },
    "page.premium.activation.success.linked_active": {
        "ru": "✅ Устройство привязано. Premium активен.",
        "en": "✅ Device linked. Premium is active.",
    },
    "page.premium.activation.success.linked_inactive": {
        "ru": "✅ Устройство привязано. Подписка сейчас не активна.",
        "en": "✅ Device linked. The subscription is currently inactive.",
    },
    "page.premium.connection.progress.testing": {
        "ru": "🔄 Проверка соединения...",
        "en": "🔄 Testing connection...",
    },
    "page.premium.connection.result.template": {
        "ru": "{icon} {message}",
        "en": "{icon} {message}",
    },
    "page.premium.status.checking.title": {
        "ru": "Проверка...",
        "en": "Checking...",
    },
    "page.premium.status.checking.details": {
        "ru": "Подключение к серверу",
        "en": "Connecting to server",
    },
    "page.premium.status.active.title": {
        "ru": "Подписка активна",
        "en": "Subscription active",
    },
    "page.premium.status.expiring_soon.title": {
        "ru": "Скоро истекает!",
        "en": "Expiring soon!",
    },
    "page.premium.status.inactive.title": {
        "ru": "Подписка не активна",
        "en": "Subscription inactive",
    },
    "page.premium.status.inactive.linked_hint": {
        "ru": "Продлите подписку в боте — статус обновится автоматически.",
        "en": "Extend the subscription in the bot — the status will refresh automatically.",
    },
    "page.premium.status.inactive.unlinked_hint": {
        "ru": "Создайте код и привяжите устройство.",
        "en": "Create a code and link this device.",
    },
    "page.premium.status.error.title": {
        "ru": "Ошибка",
        "en": "Error",
    },
    "page.premium.status.error.init_failed": {
        "ru": "Не удалось инициализировать",
        "en": "Initialization failed",
    },
    "page.premium.status.error.invalid_response": {
        "ru": "Неверный ответ сервера",
        "en": "Invalid server response",
    },
    "page.premium.status.error.incomplete_response": {
        "ru": "Неполный ответ",
        "en": "Incomplete response",
    },
    "page.premium.status.error.check_failed": {
        "ru": "Ошибка проверки",
        "en": "Check failed",
    },
    "page.premium.status.reset.title": {
        "ru": "Привязка сброшена",
        "en": "Binding reset",
    },
    "page.premium.status.reset.details": {
        "ru": "Создайте новый код для привязки",
        "en": "Create a new code to link the device",
    },
    "page.premium.days_label.normal": {
        "ru": "Осталось дней: {days}",
        "en": "Days left: {days}",
    },
    "page.premium.days_label.warning": {
        "ru": "⚠️ Осталось дней: {days}",
        "en": "⚠️ Days left: {days}",
    },
    "page.premium.days_label.urgent": {
        "ru": "⚠️ Срочно продлите! Осталось: {days}",
        "en": "⚠️ Renew urgently! Left: {days}",
    },
    "page.premium.dialog.reset.title": {
        "ru": "Подтверждение",
        "en": "Confirmation",
    },
    "page.premium.dialog.reset.body": {
        "ru": "Отвязать это устройство от Premium?\nПриложение сначала закроет локальный доступ, затем сервер отзовёт точную привязку.\nДля восстановления потребуется новое сопряжение через Telegram-бота.",
        "en": "Unlink this device from Premium?\nThe app will close local access first, then the server will revoke the exact binding.\nYou will need to pair again through the Telegram bot.",
    },
    "page.premium.error.open_telegram": {
        "ru": "Не удалось открыть Telegram: {error}",
        "en": "Failed to open Telegram: {error}",
    },
    "page.servers.title": {
        "ru": "Серверы",
        "en": "Servers",
    },
    "page.servers.subtitle": {
        "ru": "Мониторинг серверов обновлений",
        "en": "Update servers monitoring",
    },
    "page.servers.back.about": {
        "ru": "О программе",
        "en": "About",
    },
    "page.servers.section.update_servers": {
        "ru": "Серверы обновлений",
        "en": "Update Servers",
    },
    "page.servers.legend.active": {
        "ru": "активный",
        "en": "active",
    },
    "page.servers.table.header.server": {
        "ru": "Сервер",
        "en": "Server",
    },
    "page.servers.table.header.status": {
        "ru": "Статус",
        "en": "Status",
    },
    "page.servers.table.header.time": {
        "ru": "Время",
        "en": "Time",
    },
    "page.servers.table.header.versions": {
        "ru": "Версии",
        "en": "Versions",
    },
    "page.servers.settings.title": {
        "ru": "Настройки",
        "en": "Settings",
    },
    "page.servers.settings.auto_check": {
        "ru": "Проверять обновления при запуске",
        "en": "Check for updates on startup",
    },
    "page.servers.settings.version_channel_template": {
        "ru": "v{version} · {channel}",
        "en": "v{version} · {channel}",
    },
    "page.servers.telegram.title": {
        "ru": "Проблемы с обновлением?",
        "en": "Update problems?",
    },
    "page.servers.telegram.info": {
        "ru": "Если возникают трудности с автоматическим обновлением, все версии программы выкладываются в Telegram канале.",
        "en": "If automatic update is difficult to use, all app versions are published in the Telegram channel.",
    },
    "page.servers.telegram.button.open_channel": {
        "ru": "Открыть Telegram канал",
        "en": "Open Telegram Channel",
    },
    "page.servers.table.status.online": {
        "ru": "● Онлайн",
        "en": "● Online",
    },
    "page.servers.table.status.blocked": {
        "ru": "● Блок",
        "en": "● Blocked",
    },
    "page.servers.table.status.offline": {
        "ru": "● Офлайн",
        "en": "● Offline",
    },
    "page.servers.table.time.ms_template": {
        "ru": "{ms}мс",
        "en": "{ms}ms",
    },
    "page.servers.table.time.empty": {
        "ru": "—",
        "en": "—",
    },
    "page.servers.table.versions.stable_template": {
        "ru": "S: {version}",
        "en": "S: {version}",
    },
    "page.servers.table.versions.dev_template": {
        "ru": "D: {version}",
        "en": "D: {version}",
    },
    "page.servers.table.versions.both_template": {
        "ru": "S: {stable}, D: {dev}",
        "en": "S: {stable}, D: {dev}",
    },
    "page.servers.table.versions.rate_limit_template": {
        "ru": "Лимит: {remaining}/{limit}",
        "en": "Limit: {remaining}/{limit}",
    },
    "page.servers.error.version_not_found": {
        "ru": "Версия не найдена",
        "en": "Version not found",
    },
    "page.servers.error.bot_not_configured": {
        "ru": "Бот не настроен",
        "en": "Bot is not configured",
    },
    "page.servers.error.blocked_until_template": {
        "ru": "Заблокирован до {time}",
        "en": "Blocked until {time}",
    },
    "page.servers.error.connect_failed": {
        "ru": "Не удалось подключиться",
        "en": "Connection failed",
    },
    "page.servers.update.title.default": {
        "ru": "Проверка обновлений",
        "en": "Update Check",
    },
    "page.servers.update.title.checking": {
        "ru": "Проверка обновлений...",
        "en": "Checking updates...",
    },
    "page.servers.update.title.available_template": {
        "ru": "Доступно обновление v{version}",
        "en": "Update available v{version}",
    },
    "page.servers.update.title.none": {
        "ru": "Обновлений нет",
        "en": "No updates",
    },
    "page.servers.update.title.error": {
        "ru": "Ошибка проверки",
        "en": "Check error",
    },
    "page.servers.update.title.found_template": {
        "ru": "Найдено обновление v{version}",
        "en": "Found update v{version}",
    },
    "page.servers.update.title.download_error": {
        "ru": "Ошибка загрузки",
        "en": "Download error",
    },
    "page.servers.update.title.deferred_template": {
        "ru": "Обновление v{version} отложено",
        "en": "Update v{version} postponed",
    },
    "page.servers.update.subtitle.default": {
        "ru": "Нажмите для проверки доступных обновлений",
        "en": "Click to check available updates",
    },
    "page.servers.update.subtitle.checking": {
        "ru": "Подождите, идёт проверка серверов",
        "en": "Please wait, checking servers",
    },
    "page.servers.update.subtitle.available": {
        "ru": "Нажмите «Подробнее», чтобы посмотреть изменения и установить",
        "en": "Press «Details» to see the changes and install",
    },
    "page.servers.update.subtitle.latest_template": {
        "ru": "Установлена последняя версия {version}",
        "en": "Latest version installed: {version}",
    },
    "page.servers.update.subtitle.source_template": {
        "ru": "Источник: {source}",
        "en": "Source: {source}",
    },
    "page.servers.update.subtitle.try_again": {
        "ru": "Попробуйте снова",
        "en": "Please try again",
    },
    "page.servers.update.subtitle.recheck_hint": {
        "ru": "Нажмите для повторной проверки",
        "en": "Press to recheck",
    },
    "page.servers.update.subtitle.press_button": {
        "ru": "Нажмите кнопку для проверки",
        "en": "Press the button to check",
    },
    "page.servers.update.subtitle.auto_on": {
        "ru": "Автопроверка включена",
        "en": "Auto-check enabled",
    },
    "page.servers.update.subtitle.checked_ago_sec_template": {
        "ru": "Проверено {seconds}с назад",
        "en": "Checked {seconds}s ago",
    },
    "page.servers.update.subtitle.checked_ago_min_sec_template": {
        "ru": "Проверено {minutes}м {seconds}с назад",
        "en": "Checked {minutes}m {seconds}s ago",
    },
    "page.servers.update.button.check": {
        "ru": "Проверить обновления",
        "en": "Check Updates",
    },
    "page.servers.update.button.recheck": {
        "ru": "ПРОВЕРИТЬ СНОВА",
        "en": "CHECK AGAIN",
    },
    "page.servers.update.button.retry": {
        "ru": "Повторить",
        "en": "Retry",
    },
    "page.servers.update.button.manual": {
        "ru": "ПРОВЕРИТЬ ВРУЧНУЮ",
        "en": "CHECK MANUALLY",
    },
    "page.servers.update.button.details": {
        "ru": "Подробнее",
        "en": "Details",
    },
    "page.servers.update.button.show": {
        "ru": "Показать",
        "en": "Show",
    },
    "page.servers.update.title.downloading_template": {
        "ru": "Загрузка обновления v{version}",
        "en": "Downloading update v{version}",
    },
    "page.servers.update.subtitle.downloading": {
        "ru": "Загрузка идёт в фоне",
        "en": "Downloading in the background",
    },
    "update_dialog.accessible_description": {
        "ru": "Окно обновления: список изменений, подробности и кнопки установки.",
        "en": "Update window: list of changes, details and install buttons.",
    },
    "update_dialog.button.browser": {
        "ru": "Открыть в браузере",
        "en": "Open in browser",
    },
    "update_dialog.button.browser_description": {
        "ru": "Открывает страницу выпуска на Forgejo.",
        "en": "Opens the release page on Forgejo.",
    },
    "update_dialog.button.close": {
        "ru": "Закрыть",
        "en": "Close",
    },
    "update_dialog.button.close_description": {
        "ru": "Закрывает окно обновления.",
        "en": "Closes the update window.",
    },
    "update_dialog.button.hide": {
        "ru": "Скрыть",
        "en": "Hide",
    },
    "update_dialog.button.hide_description": {
        "ru": "Загрузка продолжится. Вернуть окно можно на странице «Серверы».",
        "en": "The download continues. You can bring the window back on the Servers page.",
    },
    "update_dialog.button.hide_name": {
        "ru": "Скрыть окно обновления",
        "en": "Hide the update window",
    },
    "update_dialog.button.install": {
        "ru": "Обновить",
        "en": "Update",
    },
    "update_dialog.button.install_description": {
        "ru": "Скачивает новую версию и запускает установщик. Программа закроется и откроется снова.",
        "en": "Downloads the new version and runs the installer. The app will close and reopen.",
    },
    "update_dialog.button.install_name": {
        "ru": "Скачать и установить обновление",
        "en": "Download and install the update",
    },
    "update_dialog.button.later": {
        "ru": "Позже",
        "en": "Later",
    },
    "update_dialog.button.later_description": {
        "ru": "Закрывает окно. Обновление напомнит о себе при следующем запуске.",
        "en": "Closes the window. The update will remind you on the next launch.",
    },
    "update_dialog.button.later_name": {
        "ru": "Отложить обновление",
        "en": "Postpone the update",
    },
    "update_dialog.button.retry": {
        "ru": "Повторить",
        "en": "Retry",
    },
    "update_dialog.button.skip": {
        "ru": "Пропустить версию",
        "en": "Skip this version",
    },
    "update_dialog.button.skip_description": {
        "ru": "При запуске больше не напоминать об этой версии. Следующая версия снова покажет окно.",
        "en": "Do not remind about this version on launch. The next version will show the window again.",
    },
    "update_dialog.button.telegram": {
        "ru": "Telegram",
        "en": "Telegram",
    },
    "update_dialog.button.telegram_description": {
        "ru": "Все версии программы выкладываются в Telegram-канале — можно скачать вручную.",
        "en": "Every version is posted in the Telegram channel — you can download it manually.",
    },
    "update_dialog.button.telegram_name": {
        "ru": "Открыть Telegram-канал",
        "en": "Open the Telegram channel",
    },
    "update_dialog.details.channel": {
        "ru": "Канал обновлений",
        "en": "Update channel",
    },
    "update_dialog.details.count": {
        "ru": "Версий в обновлении",
        "en": "Versions in this update",
    },
    "update_dialog.details.current": {
        "ru": "Установлена",
        "en": "Installed",
    },
    "update_dialog.details.source": {
        "ru": "Источник",
        "en": "Source",
    },
    "update_dialog.details.target": {
        "ru": "Новая версия",
        "en": "New version",
    },
    "update_dialog.history.accessible_description": {
        "ru": "Что изменилось в каждой версии, от новой к старой. Ссылки открываются в браузере.",
        "en": "What changed in each version, newest first. Links open in the browser.",
    },
    "update_dialog.history.accessible_name": {
        "ru": "Список изменений",
        "en": "List of changes",
    },
    "update_dialog.history.earlier": {
        "ru": "Ранее",
        "en": "Earlier",
    },
    "update_dialog.history.new_badge": {
        "ru": "новое",
        "en": "new",
    },
    "update_dialog.history.empty": {
        "ru": "Описание изменений не опубликовано.",
        "en": "No release notes were published.",
    },
    "update_dialog.stage.failed": {
        "ru": "Загрузка прервалась",
        "en": "The download was interrupted",
    },
    "update_dialog.stage.installer_starting": {
        "ru": "Запускаем установщик, программа закроется",
        "en": "Starting the installer, the app will close",
    },
    "update_dialog.stage.preparing": {
        "ru": "Подготовка к загрузке…",
        "en": "Preparing the download…",
    },
    "update_dialog.restart.title_template": {
        "ru": "Обновляем Zapret до v{version}",
        "en": "Updating Zapret to v{version}",
    },
    "update_dialog.restart.subtitle_template": {
        "ru": "v{current}  →  v{target}   ·   программа откроется сама",
        "en": "v{current}  →  v{target}   ·   the app will reopen by itself",
    },
    "update_dialog.restart.stage.closing": {
        "ru": "Закрываем старую версию",
        "en": "Closing the old version",
    },
    "update_dialog.restart.stage.installing_template": {
        "ru": "Устанавливаем v{version}",
        "en": "Installing v{version}",
    },
    "update_dialog.restart.stage.starting": {
        "ru": "Открываем новую версию",
        "en": "Opening the new version",
    },
    "update_dialog.restart.files_template": {
        "ru": "{done} из {total} файлов",
        "en": "{done} of {total} files",
    },
    "update_dialog.restart.status.done": {
        "ru": "Готово",
        "en": "Done",
    },
    "update_dialog.restart.status.active": {
        "ru": "Выполняется",
        "en": "In progress",
    },
    "update_dialog.restart.status.waiting": {
        "ru": "Ожидает",
        "en": "Waiting",
    },
    "update_dialog.restart.footer": {
        "ru": "Окно закроется само, когда откроется новая версия",
        "en": "This window closes by itself when the new version opens",
    },
    "update_dialog.restart.window_title": {
        "ru": "Zapret — обновление",
        "en": "Zapret — update",
    },
    "update_dialog.subtitle.source_template": {
        "ru": "источник: {source}",
        "en": "source: {source}",
    },
    "update_dialog.subtitle.transition_template": {
        "ru": "v{current}  →  v{target}",
        "en": "v{current}  →  v{target}",
    },
    "update_dialog.subtitle.versions_template": {
        "ru": "версий в обновлении: {count}",
        "en": "versions in this update: {count}",
    },
    "update_dialog.tab.accessible_name": {
        "ru": "Разделы окна обновления",
        "en": "Update window sections",
    },
    "update_dialog.tab.changes": {
        "ru": "Что нового",
        "en": "What's new",
    },
    "update_dialog.tab.details": {
        "ru": "Подробности",
        "en": "Details",
    },
    "update_dialog.title.available": {
        "ru": "Доступно обновление",
        "en": "Update available",
    },
    "update_dialog.title.downloading_template": {
        "ru": "Загружаем v{version}",
        "en": "Downloading v{version}",
    },
    "update_dialog.title.failed": {
        "ru": "Не удалось загрузить обновление",
        "en": "Could not download the update",
    },
    "update_dialog.title.installing": {
        "ru": "Устанавливаем…",
        "en": "Installing…",
    },
    "update_dialog.whats_new.load_error_template": {
        "ru": "Не удалось загрузить список изменений: {error}",
        "en": "Could not load the list of changes: {error}",
    },
    "update_dialog.whats_new.loading": {
        "ru": "Загружаем список изменений…",
        "en": "Loading the list of changes…",
    },
    "update_dialog.whats_new.ok": {
        "ru": "Понятно",
        "en": "Got it",
    },
    "update_dialog.whats_new.ok_description": {
        "ru": "Закрывает окно со списком изменений.",
        "en": "Closes the list of changes.",
    },
    "update_dialog.whats_new.ok_name": {
        "ru": "Закрыть «Что нового»",
        "en": "Close «What's new»",
    },
    "update_dialog.whats_new.subtitle": {
        "ru": "Список изменений этого выпуска",
        "en": "Changes in this release",
    },
    "update_dialog.whats_new.subtitle_many_template": {
        "ru": "Изменения за {count} {versions}",
        "en": "Changes in {count} {versions}",
    },
    "update_dialog.whats_new.title_template": {
        "ru": "Что нового в v{version}",
        "en": "What's new in v{version}",
    },
    "page.servers.changelog.progress.speed_unknown": {
        "ru": "Скорость: —",
        "en": "Speed: —",
    },
    "page.servers.changelog.progress.eta_unknown": {
        "ru": "Осталось: —",
        "en": "Remaining: —",
    },
    "page.servers.changelog.progress.downloaded_mb_template": {
        "ru": "Загружено {done:.1f} / {total:.1f} МБ",
        "en": "Downloaded {done:.1f} / {total:.1f} MB",
    },
    "page.servers.changelog.progress.speed_mb_template": {
        "ru": "Скорость: {value:.1f} МБ/с",
        "en": "Speed: {value:.1f} MB/s",
    },
    "page.servers.changelog.progress.speed_kb_template": {
        "ru": "Скорость: {value:.0f} КБ/с",
        "en": "Speed: {value:.0f} KB/s",
    },
    "page.servers.changelog.progress.eta_sec_template": {
        "ru": "Осталось: {seconds} сек",
        "en": "Remaining: {seconds} sec",
    },
    "page.servers.changelog.progress.eta_min_template": {
        "ru": "Осталось: {minutes} мин",
        "en": "Remaining: {minutes} min",
    },
    "page.telegram_proxy_advanced.title": {
        "ru": "Продвинутые настройки",
        "en": "Advanced settings",
    },
    "page.support.title": {
        "ru": "Поддержка",
        "en": "Support",
    },
    "page.support.subtitle": {
        "ru": "Forgejo Issues и каналы сообщества",
        "en": "Forgejo Issues and community channels",
    },
    "page.support.section.discussions": {
        "ru": "Forgejo Issues",
        "en": "Forgejo Issues",
    },
    "page.support.section.community": {
        "ru": "Каналы сообщества",
        "en": "Community Channels",
    },
    "page.support.discussions.title": {
        "ru": "Forgejo Issues",
        "en": "Forgejo Issues",
    },
    "page.support.discussions.description": {
        "ru": "Основной канал поддержки. Здесь можно задать вопрос, описать проблему и приложить нужные материалы вручную.",
        "en": "Main support channel. Ask questions, describe the issue, and attach materials manually.",
    },
    "page.support.discussions.button": {
        "ru": "Открыть",
        "en": "Open",
    },
    "page.support.error.open_discussions": {
        "ru": "Не удалось открыть Forgejo Issues:\n{error}",
        "en": "Failed to open Forgejo Issues:\n{error}",
    },
    "page.support.channel.telegram.title": {
        "ru": "Telegram",
        "en": "Telegram",
    },
    "page.support.channel.telegram.desc": {
        "ru": "Быстрые вопросы и общение с сообществом",
        "en": "Quick questions and community chat",
    },
    "page.support.channel.discord.title": {
        "ru": "Discord",
        "en": "Discord",
    },
    "page.support.channel.discord.desc": {
        "ru": "Обсуждение и живое общение",
        "en": "Discussion and live chat",
    },
    "page.support.channel.open": {
        "ru": "Открыть",
        "en": "Open",
    },
    "page.support.error.title": {
        "ru": "Ошибка",
        "en": "Error",
    },
    "page.support.error.open_telegram": {
        "ru": "Не удалось открыть Telegram:\n{error}",
        "en": "Failed to open Telegram:\n{error}",
    },
    "page.support.error.open_discord": {
        "ru": "Не удалось открыть Discord:\n{error}",
        "en": "Failed to open Discord:\n{error}",
    },
    "page.winws1_control.subtitle": {
        "ru": f"Настройка и запуск Zapret 1 ({EXE_NAME_WINWS1}). В «Мои пресеты» выбирается пресет, а в «Настройка пресета» меняются профили и выбранные для них готовые стратегии.",
        "en": f"Configure and launch Zapret 1 ({EXE_NAME_WINWS1}). My presets selects a preset; preset setup changes profiles and ready strategies.",
    },
    "page.winws1_control.section.presets": {
        "ru": "Пресеты и настройка пресета",
        "en": "Presets and preset setup",
    },
    "page.winws1_pages.title": {
        "ru": "Настройка пресета",
        "en": "Preset setup",
    },
    "page.winws1_pages.back.control": {
        "ru": "\u2190 Управление",
        "en": "\u2190 Control",
    },
    "page.winws1_pages.breadcrumb.control": {
        "ru": "Управление",
        "en": "Control",
    },
    "page.winws1_pages.toolbar.expand": {
        "ru": "Развернуть",
        "en": "Expand",
    },
    "page.winws1_pages.toolbar.collapse": {
        "ru": "Свернуть",
        "en": "Collapse",
    },
    "page.winws1_pages.toolbar.view_menu": {
        "ru": "Вид",
        "en": "View",
    },
    "page.winws1_pages.toolbar.show_added_only": {
        "ru": "Показать только добавленные",
        "en": "Show added only",
    },
    "page.winws1_pages.toolbar.show_all_profiles": {
        "ru": "Показать все профили",
        "en": "Show all profiles",
    },
    "page.winws1_pages.toolbar.info": {
        "ru": "Что это?",
        "en": "What is this?",
    },
    "page.winws1_pages.toolbar.title": {
        "ru": "Профили",
        "en": "Profiles",
    },
    "page.winws1_pages.toolbar.search.placeholder": {
        "ru": "Поиск профиля по имени, портам и т.д.",
        "en": "Search profiles by name, ports, etc.",
    },
    "page.winws1_pages.request.button": {
        "ru": "Предложить профиль",
        "en": "Suggest profile",
    },
    "page.winws1_pages.request.hint": {
        "ru": "Если нужного профиля нет в списке, его можно добавить позже в набор доступных профилей.",
        "en": "If the needed profile is missing, it can be added later to the available profiles.",
    },
    "page.winws1_pages.loading": {
        "ru": "Читаем профили из выбранного пресета...",
        "en": "Reading profiles from the selected preset...",
    },
    "page.winws1_pages.strategy.off": {
        "ru": "Выключено",
        "en": "Off",
    },
    "page.winws1_pages.strategy.custom": {
        "ru": "Свой набор",
        "en": "Custom set",
    },
    "page.winws1_pages.info.title": {
        "ru": "Настройка пресета",
        "en": "Preset setup",
    },
    "page.winws1_pages.info.body": {
        "ru": "Чтобы запустить zapret напрямую, включите нужные профили, выберите для них готовые стратегии и нажмите «Запустить» на странице управления.",
        "en": "To start Zapret directly, enable the needed profiles, choose ready strategies for them, and click Start on the control page.",
    },
    "page.winws1_user_presets.title": {
        "ru": "Мои пресеты",
        "en": "My Presets",
    },
    "page.winws1_user_presets.back.control": {
        "ru": "Управление",
        "en": "Control",
    },
    "page.winws1_user_presets.configs.title": {
        "ru": "Обменивайтесь пресетами и профилями в разделе Forgejo Issues",
        "en": "Share presets and profiles in Forgejo Issues",
    },
    "page.winws1_user_presets.configs.button": {
        "ru": "Получить конфиги",
        "en": "Get configs",
    },
    "page.winws1_user_presets.button.import": {
        "ru": "Импорт",
        "en": "Import",
    },
    "page.winws1_user_presets.button.open_folder": {
        "ru": "Папка пресетов",
        "en": "Presets folder",
    },
    "page.winws1_user_presets.button.reset_all": {
        "ru": "Вернуть встроенные",
        "en": "Restore defaults",
    },
    "page.winws1_user_presets.button.wiki": {
        "ru": "Вики по пресетам",
        "en": "Preset wiki",
    },
    "page.winws1_user_presets.button.what_is_this": {
        "ru": "Что это такое?",
        "en": "What is this?",
    },
    "page.winws1_user_presets.search.placeholder": {
        "ru": "Поиск пресетов по имени...",
        "en": "Search presets by name...",
    },
    "page.winws1_user_presets.tooltip.create": {
        "ru": "Создать новый пресет",
        "en": "Create a new preset",
    },
    "page.winws1_user_presets.tooltip.import": {
        "ru": "Импорт пресета из файла",
        "en": "Import preset from file",
    },
    "page.winws1_user_presets.tooltip.open_folder": {
        "ru": "Открыть папку, где лежат ваши пресеты",
        "en": "Open the folder where your presets are stored",
    },
    "page.winws1_user_presets.tooltip.reset_all": {
        "ru": "Возвращает встроенные пресеты. Ваши изменения во встроенных пресетах будут потеряны.",
        "en": "Restores built-in presets. Your changes to built-in presets will be lost.",
    },
    "page.winws1_user_presets.delegate.tooltip.rename": {
        "ru": "Переименовать",
        "en": "Rename",
    },
    "page.winws1_user_presets.delegate.tooltip.duplicate": {
        "ru": "Дублировать",
        "en": "Duplicate",
    },
    "page.winws1_user_presets.delegate.tooltip.reset": {
        "ru": "Вернуть встроенный",
        "en": "Restore built-in",
    },
    "page.winws1_user_presets.delegate.tooltip.delete": {
        "ru": "Удалить",
        "en": "Delete",
    },
    "page.winws1_user_presets.delegate.tooltip.export": {
        "ru": "Экспорт",
        "en": "Export",
    },
    "page.winws1_user_presets.delegate.tooltip.confirm_again": {
        "ru": "Нажмите ещё раз для подтверждения",
        "en": "Click again to confirm",
    },
    "page.winws1_user_presets.delegate.badge.active": {
        "ru": "Активен",
        "en": "Active",
    },
    "page.winws1_user_presets.dialog.button.cancel": {
        "ru": "Отмена",
        "en": "Cancel",
    },
    "page.winws1_user_presets.dialog.import.title": {
        "ru": "Импортировать пресет",
        "en": "Import preset",
    },
    "page.winws1_user_presets.dialog.import.subtitle": {
        "ru": "Перетащите файл пресета или вставьте ссылку — пресет по ссылке сможет обновляться автоматически.",
        "en": "Drop a preset file or paste a link — a preset imported by link can update automatically.",
    },
    "page.winws1_user_presets.dialog.import.drop.hint": {
        "ru": "Перетащите сюда файл пресета (.txt или .zip)",
        "en": "Drop a preset file here (.txt or .zip)",
    },
    "page.winws1_user_presets.dialog.import.drop.browse": {
        "ru": "Выбрать файл",
        "en": "Browse file",
    },
    "page.winws1_user_presets.dialog.import.or": {
        "ru": "или",
        "en": "or",
    },
    "page.winws1_user_presets.dialog.import.url.label": {
        "ru": "Вставьте ссылку",
        "en": "Paste a link",
    },
    "page.winws1_user_presets.dialog.import.url.placeholder": {
        "ru": "https://…/preset.txt",
        "en": "https://…/preset.txt",
    },
    "page.winws1_user_presets.dialog.import.auto_update.label": {
        "ru": "Автоматически обновлять по ссылке",
        "en": "Update automatically from the link",
    },
    "page.winws1_user_presets.dialog.import.button": {
        "ru": "Импортировать",
        "en": "Import",
    },
    "page.winws1_user_presets.dialog.import.validation.empty": {
        "ru": "Перетащите файл или вставьте ссылку на пресет.",
        "en": "Drop a file or paste a preset link.",
    },
    "page.winws1_user_presets.dialog.import.error.url": {
        "ru": "Некорректная ссылка: {error}",
        "en": "Invalid link: {error}",
    },
    "page.winws1_user_presets.dialog.import.error.ssl": {
        "ru": "Не удалось проверить SSL-сертификат: {error}",
        "en": "SSL certificate check failed: {error}",
    },
    "page.winws1_user_presets.dialog.import.error.timeout": {
        "ru": "Сервер не ответил вовремя. Попробуйте ещё раз.",
        "en": "The server did not respond in time. Try again.",
    },
    "page.winws1_user_presets.dialog.import.error.network": {
        "ru": "Не удалось скачать пресет: {error}",
        "en": "Failed to download the preset: {error}",
    },
    "page.winws1_user_presets.dialog.import.error.http": {
        "ru": "Сервер вернул ошибку: {error}",
        "en": "The server returned an error: {error}",
    },
    "page.winws1_user_presets.dialog.import.error.too_large": {
        "ru": "Файл по ссылке слишком большой.",
        "en": "The file behind the link is too large.",
    },
    "page.winws1_user_presets.dialog.import.error.content": {
        "ru": "Файл по ссылке пуст или не похож на пресет.",
        "en": "The file behind the link is empty or is not a preset.",
    },
    "page.winws1_user_presets.menu.update_remote": {
        "ru": "Обновить из источника",
        "en": "Update from source",
    },
    "page.winws1_user_presets.menu.unlink_remote": {
        "ru": "Отвязать от источника",
        "en": "Unlink from source",
    },
    "page.winws1_user_presets.remote.confirm_overwrite.title": {
        "ru": "Перезаписать локальные правки?",
        "en": "Overwrite local changes?",
    },
    "page.winws1_user_presets.remote.confirm_overwrite.body": {
        "ru": "Этот пресет был изменён локально, поэтому автообновление приостановлено.\nОбновление из источника перезапишет ваши правки и снова включит автообновление.",
        "en": "This preset was modified locally, so automatic updates are paused.\nUpdating from the source will overwrite your changes and re-enable automatic updates.",
    },
    "page.winws1_user_presets.remote.updated.title": {
        "ru": "Пресет обновлён из источника",
        "en": "Preset updated from source",
    },
    "page.winws1_user_presets.remote.updated.content": {
        "ru": "Пресет «{name}» обновлён по ссылке.",
        "en": "Preset '{name}' was updated from its link.",
    },
    "page.winws1_user_presets.remote.up_to_date.title": {
        "ru": "Пресет актуален",
        "en": "Preset is up to date",
    },
    "page.winws1_user_presets.remote.up_to_date.content": {
        "ru": "Пресет «{name}» уже совпадает с источником.",
        "en": "Preset '{name}' already matches the source.",
    },
    "page.winws1_user_presets.remote.detached.title": {
        "ru": "Автообновление приостановлено",
        "en": "Automatic updates paused",
    },
    "page.winws1_user_presets.remote.detached.content": {
        "ru": "Пресет «{name}» изменён локально. Обновите его из источника вручную, чтобы вернуть автообновление.",
        "en": "Preset '{name}' was modified locally. Update it from the source manually to re-enable automatic updates.",
    },
    "page.winws1_user_presets.remote.unlinked.title": {
        "ru": "Автообновление отключено",
        "en": "Automatic updates disabled",
    },
    "page.winws1_user_presets.remote.unlinked.content": {
        "ru": "Пресет «{name}» больше не привязан к ссылке.",
        "en": "Preset '{name}' is no longer linked to a URL.",
    },
    "page.winws1_user_presets.remote.error.generic": {
        "ru": "Не удалось выполнить действие.",
        "en": "The action could not be completed.",
    },
    "page.winws1_user_presets.dialog.reset_single.title": {
        "ru": "Вернуть встроенный пресет?",
        "en": "Restore built-in preset?",
    },
    "page.winws1_user_presets.dialog.reset_single.body": {
        "ru": "Будет удалён ваш изменённый файл пресета «{name}».\nПосле этого снова появится встроенный пресет с тем же именем файла.\nИзменения в этом файле будут потеряны.",
        "en": "User preset file '{name}' will be removed.\nThe built-in preset with the same file name will be used again.\nChanges in the user file will be lost.",
    },
    "page.winws1_user_presets.dialog.reset_single.button": {
        "ru": "Вернуть встроенный",
        "en": "Restore built-in",
    },
    "page.winws1_user_presets.dialog.delete_single.title": {
        "ru": "Удалить пресет?",
        "en": "Delete preset?",
    },
    "page.winws1_user_presets.dialog.delete_single.body": {
        "ru": "Пользовательский пресет «{name}» будет удалён.\nИзменения в нём будут потеряны.\nВернуть его можно только создав новый пресет или импортировав txt-файл.",
        "en": "Preset '{name}' will be removed from the user presets list.\nChanges in this preset will be lost.\nYou can restore it only by creating a new preset or importing a file.",
    },
    "page.winws1_user_presets.dialog.delete_single.button": {
        "ru": "Удалить",
        "en": "Delete",
    },
    "page.winws1_user_presets.dialog.create.title": {
        "ru": "Новый пресет",
        "en": "New preset",
    },
    "page.winws1_user_presets.dialog.create.subtitle": {
        "ru": "Сохраните текущие настройки как отдельный пресет, чтобы быстро переключаться между разными настройками.",
        "en": "Save current settings as a separate preset to switch between configurations quickly.",
    },
    "page.winws1_user_presets.dialog.create.name": {
        "ru": "Название",
        "en": "Name",
    },
    "page.winws1_user_presets.dialog.create.placeholder": {
        "ru": "Например: Игры / YouTube / Дом",
        "en": "For example: Games / YouTube / Home",
    },
    "page.winws1_user_presets.dialog.create.source": {
        "ru": "Создать на основе",
        "en": "Create from",
    },
    "page.winws1_user_presets.dialog.create.source.current": {
        "ru": "Текущего пресета",
        "en": "Current active",
    },
    "page.winws1_user_presets.dialog.create.source.standard": {
        "ru": "Встроенного пресета",
        "en": "Standard preset",
    },
    "page.winws1_user_presets.dialog.create.button.create": {
        "ru": "Создать",
        "en": "Create",
    },
    "page.winws1_user_presets.dialog.rename.title": {
        "ru": "Переименовать",
        "en": "Rename",
    },
    "page.winws1_user_presets.dialog.rename.subtitle": {
        "ru": "Имя пресета отображается в списке и используется для переключения.",
        "en": "Preset name is shown in the list and used for switching.",
    },
    "page.winws1_user_presets.dialog.rename.current_name": {
        "ru": "Текущее имя: {name}",
        "en": "Current name: {name}",
    },
    "page.winws1_user_presets.dialog.rename.new_name": {
        "ru": "Новое имя",
        "en": "New name",
    },
    "page.winws1_user_presets.dialog.rename.placeholder": {
        "ru": "Новое имя...",
        "en": "New name...",
    },
    "page.winws1_user_presets.dialog.rename.button": {
        "ru": "Переименовать",
        "en": "Rename",
    },
    "page.winws1_user_presets.dialog.validation.enter_name": {
        "ru": "Введите название.",
        "en": "Enter a name.",
    },
    "page.winws1_user_presets.dialog.validation.exists": {
        "ru": "Пресет «{name}» уже существует.",
        "en": "Preset '{name}' already exists.",
    },
    "page.winws1_user_presets.dialog.import_exists.title": {
        "ru": "Пресет существует",
        "en": "Preset exists",
    },
    "page.winws1_user_presets.dialog.import_exists.body": {
        "ru": "Пресет «{name}» уже существует. Импортировать с другим именем?",
        "en": "Preset '{name}' already exists. Import with another name?",
    },
    "page.winws1_user_presets.dialog.reset_all.title": {
        "ru": "Вернуть встроенные пресеты",
        "en": "Restore default presets",
    },
    "page.winws1_user_presets.dialog.reset_all.body": {
        "ru": "Мы вернём встроенные пресеты к состоянию после установки.\nЕсли вы меняли встроенный пресет, эти изменения будут потеряны.\nПользовательские пресеты с другими именами останутся.\nТекущий выбранный пресет будет применён заново.",
        "en": "Built-in presets will be restored to their post-install state.\nYour changes to built-in presets will be lost.\nCustom presets with other names will remain.\nCurrent selected preset will be re-applied automatically.",
    },
    "page.winws1_user_presets.dialog.reset_all.button": {
        "ru": "Вернуть встроенные",
        "en": "Restore defaults",
    },
    "page.winws1_user_presets.section.games": {
        "ru": "Игры (game filter)",
        "en": "Games (game filter)",
    },
    "page.winws1_user_presets.section.all_tcp_udp": {
        "ru": "Все сайты и игры (ALL TCP/UDP)",
        "en": "All sites and games (ALL TCP/UDP)",
    },
    "page.winws1_user_presets.empty.not_found": {
        "ru": "По этому поиску пресетов нет. Измените запрос или очистите строку поиска.",
        "en": "No presets match this search. Change the query or clear the search field.",
    },
    "page.winws1_user_presets.empty.none": {
        "ru": "Пресеты не найдены. Создайте новый пресет или импортируйте txt-файл.",
        "en": "No presets. Create a new one or import from file.",
    },
    "page.winws1_user_presets.error.generic": {
        "ru": "Ошибка: {error}",
        "en": "Error: {error}",
    },
    "page.winws1_user_presets.error.create_failed": {
        "ru": "Не удалось создать пресет.",
        "en": "Failed to create preset.",
    },
    "page.winws1_user_presets.error.rename_failed": {
        "ru": "Не удалось переименовать пресет.",
        "en": "Failed to rename preset.",
    },
    "page.winws1_user_presets.error.activate_failed": {
        "ru": "Не удалось активировать пресет '{name}'",
        "en": "Failed to activate preset '{name}'",
    },
    "page.winws1_user_presets.error.duplicate_failed": {
        "ru": "Не удалось дублировать пресет",
        "en": "Failed to duplicate preset",
    },
    "page.winws1_user_presets.error.reset_failed": {
        "ru": "Не удалось вернуть встроенный пресет",
        "en": "Failed to restore built-in preset",
    },
    "page.winws1_user_presets.error.delete_failed": {
        "ru": "Не удалось удалить пресет",
        "en": "Failed to delete preset",
    },
    "page.winws1_user_presets.error.import_failed": {
        "ru": "Не удалось импортировать пресет",
        "en": "Failed to import preset",
    },
    "page.winws1_user_presets.error.import_exception": {
        "ru": "Не удалось импортировать пресет: {error}",
        "en": "Import error: {error}",
    },
    "page.winws1_user_presets.error.open_folder": {
        "ru": "Не удалось открыть папку пресетов: {error}",
        "en": "Could not open presets folder: {error}",
    },
    "page.winws1_user_presets.error.export_failed": {
        "ru": "Не удалось экспортировать пресет",
        "en": "Failed to export preset",
    },
    "page.winws1_user_presets.error.restore_deleted": {
        "ru": "Ошибка восстановления: {error}",
        "en": "Restore error: {error}",
    },
    "page.winws1_user_presets.error.reset_all_exception": {
        "ru": "Ошибка восстановления пресетов: {error}",
        "en": "Preset restore error: {error}",
    },
    "page.winws1_user_presets.error.open_telegram": {
        "ru": "Не удалось открыть страницу пресетов: {error}",
        "en": "Failed to open presets page: {error}",
    },
    "page.winws1_user_presets.file_dialog.import_title": {
        "ru": "Импортировать пресет",
        "en": "Import preset",
    },
    "page.winws1_user_presets.file_dialog.export_title": {
        "ru": "Экспортировать пресет",
        "en": "Export preset",
    },
    "page.winws1_user_presets.infobar.success": {
        "ru": "Успех",
        "en": "Success",
    },
    "page.winws1_user_presets.info.exported": {
        "ru": "Пресет экспортирован: {path}",
        "en": "Preset exported: {path}",
    },
    "page.winws1_user_presets.info.title": {
        "ru": "Что это такое?",
        "en": "What is this?",
    },
    "page.winws1_user_presets.info.body": {
        "ru": (
            "Пресет, или конфиг, — это один или несколько .txt-файлов со списком флагов Zapret. "
            "Формат такой же, как у winws2.exe или winws.exe, поэтому GUI может быстро читать и менять настройки.\n\n"
            "Пресеты доступны с Zapret2 v20.3 для режимов Zapret 1 и Zapret 2. "
            "Они нужны, чтобы быстрее делать новые настройки, проще обмениваться ими и держать GUI и консольный Zapret в одном формате.\n\n"
            "При запуске активный пресет передаётся в winws2.exe для Zapret 2 или в winws.exe для Zapret 1 через @<config_file>. "
            "Это значит: прочитать параметры командной строки из файла. Остальные параметры командной строки при таком запуске не используются.\n\n"
            "Файл %AppData%\\ZapretTwoDev\\preset-zapret2.txt хранит только активный пресет. "
            "Сам по себе он не считается пользовательским пресетом. Ваши пресеты лежат в папке presets. "
            "По умолчанию используется Default, также есть встроенный Gaming.\n\n"
            "Пресетами можно обмениваться напрямую.\n\n"
            "Почему пресеты иногда плохо подходят: в них стратегии часто заранее прописаны под разные фильтры и hostlist. "
            "Из-за этого один сайт может заработать, а другой перестать. Для более точной настройки лучше использовать прямой запуск: "
            "там стратегия подбирается отдельно для нужной категории и hostlist."
        ),
        "en": (
            "A preset, or config, is one or more .txt files with Zapret flags. "
            "It uses the same format as winws2.exe or winws.exe, so the GUI can read and edit these settings.\n\n"
            "The active preset is passed to winws2.exe for Zapret 2 or winws.exe for Zapret 1 through @<config_file>, "
            "which means: read command-line options from a file. Other command-line options are not used in this launch mode.\n\n"
            "%AppData%\\ZapretTwoDev\\preset-zapret2.txt only stores the active preset copy. "
            "User presets are stored in the presets folder. More details are available from the button below."
        ),
    },
    "page.winws1_user_presets.info.open_site.button": {
        "ru": "Открыть сайт с пресетами",
        "en": "Open preset site",
    },
    "page.winws1_user_presets.info.open_site.description": {
        "ru": "Открывает сайт, где можно посмотреть и скачать пресеты.",
        "en": "Opens the site where presets can be viewed and downloaded.",
    },
    "page.winws1_profile_setup.title": {
        "ru": "Настройка профиля",
        "en": "Profile setup",
    },
    "page.winws1_profile_setup.breadcrumb.control": {
        "ru": "Управление",
        "en": "Control",
    },
    "page.winws2_control.subtitle": {
        "ru": "Настройка и запуск Zapret 2. В «Мои пресеты» выбирается пресет, а в «Настройка пресета» меняются профили и выбранные для них готовые стратегии.",
        "en": "Configure and launch Zapret 2. My presets selects a preset; preset setup changes profiles and ready strategies.",
    },
    "page.winws2_control.section.preset_switch": {
        "ru": "Сменить пресет обхода блокировок",
        "en": "Switch Bypass Preset",
    },
    "page.winws2_control.section.profile_tuning": {
        "ru": "Настройка пресета",
        "en": "Preset setup",
    },
    "page.winws2_control.section.additional_settings": {
        "ru": "Дополнительные настройки",
        "en": "Additional Settings",
    },
    "page.winws2_control.status.checking": {
        "ru": "Проверка...",
        "en": "Checking...",
    },
    "page.winws2_control.status.detecting": {
        "ru": "Определение состояния процесса",
        "en": "Detecting process state",
    },
    "page.winws2_control.status.running": {
        "ru": "Zapret работает",
        "en": "Zapret is running",
    },
    "page.winws2_control.status.stopped": {
        "ru": "Zapret остановлен",
        "en": "Zapret stopped",
    },
    "page.winws2_control.status.bypass_active": {
        "ru": "Обход блокировок активен · нажмите на кнопку, чтобы остановить",
        "en": "Bypass is active · click the button to stop",
    },
    "page.winws2_control.status.press_start": {
        "ru": "Нажмите на кнопку, чтобы запустить",
        "en": "Click the button to start",
    },
    "page.winws2_control.button.my_presets": {
        "ru": "Мои пресеты",
        "en": "My presets",
    },
    "page.winws2_control.button.open": {
        "ru": "Открыть",
        "en": "Open",
    },
    "page.winws2_control.preset.not_selected": {
        "ru": "Не выбран",
        "en": "Not selected",
    },
    "page.winws2_control.preset.current": {
        "ru": "Текущий выбранный пресет",
        "en": "Current selected preset",
    },
    "page.winws2_control.advanced.warning": {
        "ru": "Эти параметры лучше менять, только если уверены в результате",
        "en": "Better change these only if you are sure of the result",
    },
    "page.winws2_control.button.connection_test": {
        "ru": "Тест соединения",
        "en": "Connection Test",
    },
    "page.winws2_control.button.open_folder": {
        "ru": "Открыть папку",
        "en": "Open Folder",
    },
    "page.winws2_control.button.documentation": {
        "ru": "Документация",
        "en": "Documentation",
    },
    "page.winws2_pages.title": {
        "ru": "Настройка пресета",
        "en": "Preset setup",
    },
    "page.winws2_pages.back.control": {
        "ru": "Управление",
        "en": "Control",
    },
    "page.winws2_pages.current.not_selected": {
        "ru": "Не выбрана",
        "en": "Not selected",
    },
    "page.winws2_pages.request.button": {
        "ru": "ОТКРЫТЬ ФОРМУ В FORGEJO",
        "en": "OPEN FORGEJO FORM",
    },
    "page.winws2_pages.toolbar.title": {
        "ru": "Профили",
        "en": "Profiles",
    },
    "page.winws2_pages.toolbar.expand": {
        "ru": "Развернуть",
        "en": "Expand",
    },
    "page.winws2_pages.toolbar.collapse": {
        "ru": "Свернуть",
        "en": "Collapse",
    },
    "page.winws2_pages.toolbar.view_menu": {
        "ru": "Вид",
        "en": "View",
    },
    "page.winws2_pages.toolbar.show_added_only": {
        "ru": "Показать только добавленные",
        "en": "Show added only",
    },
    "page.winws2_pages.toolbar.show_all_profiles": {
        "ru": "Показать все профили",
        "en": "Show all profiles",
    },
    "page.winws2_pages.toolbar.info": {
        "ru": "Что это такое?",
        "en": "What is this?",
    },
    "page.winws2_pages.toolbar.search.placeholder": {
        "ru": "Поиск профиля по имени, портам и т.д.",
        "en": "Search profiles by name, ports, etc.",
    },
    "page.winws2_pages.info.title": {
        "ru": "Настройка пресета",
        "en": "Preset setup",
    },
    "page.winws2_user_presets.title": {
        "ru": "Мои пресеты",
        "en": "My Presets",
    },
    "page.winws2_user_presets.back.control": {
        "ru": "Управление",
        "en": "Control",
    },
    "page.winws2_user_presets.configs.title": {
        "ru": "Обменивайтесь пресетами и профилями в разделе Forgejo Issues",
        "en": "Share presets and profiles in Forgejo Issues",
    },
    "page.winws2_user_presets.configs.button": {
        "ru": "Получить конфиги",
        "en": "Get configs",
    },
    "page.winws2_user_presets.button.import": {
        "ru": "Импорт",
        "en": "Import",
    },
    "page.winws2_user_presets.button.open_folder": {
        "ru": "Папка пресетов",
        "en": "Presets folder",
    },
    "page.winws2_user_presets.button.reset_all": {
        "ru": "Вернуть встроенные",
        "en": "Restore defaults",
    },
    "page.winws2_user_presets.button.wiki": {
        "ru": "Вики по пресетам",
        "en": "Preset wiki",
    },
    "page.winws2_user_presets.button.what_is_this": {
        "ru": "Что это такое?",
        "en": "What is this?",
    },
    "page.winws2_user_presets.search.placeholder": {
        "ru": "Поиск пресетов по имени...",
        "en": "Search presets by name...",
    },
    "page.winws2_user_presets.tooltip.create": {
        "ru": "Создать новый пресет",
        "en": "Create a new preset",
    },
    "page.winws2_user_presets.tooltip.import": {
        "ru": "Импорт пресета из файла",
        "en": "Import preset from file",
    },
    "page.winws2_user_presets.tooltip.open_folder": {
        "ru": "Открыть папку, где лежат ваши пресеты",
        "en": "Open the folder where your presets are stored",
    },
    "page.winws2_user_presets.tooltip.reset_all": {
        "ru": "Возвращает встроенные пресеты. Ваши изменения во встроенных пресетах будут потеряны.",
        "en": "Restores built-in presets. Your changes to built-in presets will be lost.",
    },
    "page.winws2_user_presets.delegate.tooltip.rename": {
        "ru": "Переименовать",
        "en": "Rename",
    },
    "page.winws2_user_presets.delegate.tooltip.duplicate": {
        "ru": "Дублировать",
        "en": "Duplicate",
    },
    "page.winws2_user_presets.delegate.tooltip.reset": {
        "ru": "Вернуть встроенный",
        "en": "Restore built-in",
    },
    "page.winws2_user_presets.delegate.tooltip.delete": {
        "ru": "Удалить",
        "en": "Delete",
    },
    "page.winws2_user_presets.delegate.tooltip.export": {
        "ru": "Экспорт",
        "en": "Export",
    },
    "page.winws2_user_presets.delegate.tooltip.confirm_again": {
        "ru": "Нажмите ещё раз для подтверждения",
        "en": "Click again to confirm",
    },
    "page.winws2_user_presets.delegate.badge.active": {
        "ru": "Активен",
        "en": "Active",
    },
    "page.winws2_user_presets.dialog.button.cancel": {
        "ru": "Отмена",
        "en": "Cancel",
    },
    "page.winws2_user_presets.dialog.import.title": {
        "ru": "Импортировать пресет",
        "en": "Import preset",
    },
    "page.winws2_user_presets.dialog.import.subtitle": {
        "ru": "Перетащите файл пресета или вставьте ссылку — пресет по ссылке сможет обновляться автоматически.",
        "en": "Drop a preset file or paste a link — a preset imported by link can update automatically.",
    },
    "page.winws2_user_presets.dialog.import.drop.hint": {
        "ru": "Перетащите сюда файл пресета (.txt или .zip)",
        "en": "Drop a preset file here (.txt or .zip)",
    },
    "page.winws2_user_presets.dialog.import.drop.browse": {
        "ru": "Выбрать файл",
        "en": "Browse file",
    },
    "page.winws2_user_presets.dialog.import.or": {
        "ru": "или",
        "en": "or",
    },
    "page.winws2_user_presets.dialog.import.url.label": {
        "ru": "Вставьте ссылку",
        "en": "Paste a link",
    },
    "page.winws2_user_presets.dialog.import.url.placeholder": {
        "ru": "https://…/preset.txt",
        "en": "https://…/preset.txt",
    },
    "page.winws2_user_presets.dialog.import.auto_update.label": {
        "ru": "Автоматически обновлять по ссылке",
        "en": "Update automatically from the link",
    },
    "page.winws2_user_presets.dialog.import.button": {
        "ru": "Импортировать",
        "en": "Import",
    },
    "page.winws2_user_presets.dialog.import.validation.empty": {
        "ru": "Перетащите файл или вставьте ссылку на пресет.",
        "en": "Drop a file or paste a preset link.",
    },
    "page.winws2_user_presets.dialog.import.error.url": {
        "ru": "Некорректная ссылка: {error}",
        "en": "Invalid link: {error}",
    },
    "page.winws2_user_presets.dialog.import.error.ssl": {
        "ru": "Не удалось проверить SSL-сертификат: {error}",
        "en": "SSL certificate check failed: {error}",
    },
    "page.winws2_user_presets.dialog.import.error.timeout": {
        "ru": "Сервер не ответил вовремя. Попробуйте ещё раз.",
        "en": "The server did not respond in time. Try again.",
    },
    "page.winws2_user_presets.dialog.import.error.network": {
        "ru": "Не удалось скачать пресет: {error}",
        "en": "Failed to download the preset: {error}",
    },
    "page.winws2_user_presets.dialog.import.error.http": {
        "ru": "Сервер вернул ошибку: {error}",
        "en": "The server returned an error: {error}",
    },
    "page.winws2_user_presets.dialog.import.error.too_large": {
        "ru": "Файл по ссылке слишком большой.",
        "en": "The file behind the link is too large.",
    },
    "page.winws2_user_presets.dialog.import.error.content": {
        "ru": "Файл по ссылке пуст или не похож на пресет.",
        "en": "The file behind the link is empty or is not a preset.",
    },
    "page.winws2_user_presets.menu.update_remote": {
        "ru": "Обновить из источника",
        "en": "Update from source",
    },
    "page.winws2_user_presets.menu.unlink_remote": {
        "ru": "Отвязать от источника",
        "en": "Unlink from source",
    },
    "page.winws2_user_presets.remote.confirm_overwrite.title": {
        "ru": "Перезаписать локальные правки?",
        "en": "Overwrite local changes?",
    },
    "page.winws2_user_presets.remote.confirm_overwrite.body": {
        "ru": "Этот пресет был изменён локально, поэтому автообновление приостановлено.\nОбновление из источника перезапишет ваши правки и снова включит автообновление.",
        "en": "This preset was modified locally, so automatic updates are paused.\nUpdating from the source will overwrite your changes and re-enable automatic updates.",
    },
    "page.winws2_user_presets.remote.updated.title": {
        "ru": "Пресет обновлён из источника",
        "en": "Preset updated from source",
    },
    "page.winws2_user_presets.remote.updated.content": {
        "ru": "Пресет «{name}» обновлён по ссылке.",
        "en": "Preset '{name}' was updated from its link.",
    },
    "page.winws2_user_presets.remote.up_to_date.title": {
        "ru": "Пресет актуален",
        "en": "Preset is up to date",
    },
    "page.winws2_user_presets.remote.up_to_date.content": {
        "ru": "Пресет «{name}» уже совпадает с источником.",
        "en": "Preset '{name}' already matches the source.",
    },
    "page.winws2_user_presets.remote.detached.title": {
        "ru": "Автообновление приостановлено",
        "en": "Automatic updates paused",
    },
    "page.winws2_user_presets.remote.detached.content": {
        "ru": "Пресет «{name}» изменён локально. Обновите его из источника вручную, чтобы вернуть автообновление.",
        "en": "Preset '{name}' was modified locally. Update it from the source manually to re-enable automatic updates.",
    },
    "page.winws2_user_presets.remote.unlinked.title": {
        "ru": "Автообновление отключено",
        "en": "Automatic updates disabled",
    },
    "page.winws2_user_presets.remote.unlinked.content": {
        "ru": "Пресет «{name}» больше не привязан к ссылке.",
        "en": "Preset '{name}' is no longer linked to a URL.",
    },
    "page.winws2_user_presets.remote.error.generic": {
        "ru": "Не удалось выполнить действие.",
        "en": "The action could not be completed.",
    },
    "page.winws2_user_presets.dialog.reset_single.title": {
        "ru": "Вернуть встроенный пресет?",
        "en": "Restore built-in preset?",
    },
    "page.winws2_user_presets.dialog.reset_single.body": {
        "ru": "Будет удалён ваш изменённый файл пресета «{name}».\nПосле этого снова появится встроенный пресет с тем же именем файла.\nИзменения в этом файле будут потеряны.",
        "en": "User preset file '{name}' will be removed.\nThe built-in preset with the same file name will be used again.\nChanges in the user file will be lost.",
    },
    "page.winws2_user_presets.dialog.reset_single.button": {
        "ru": "Вернуть встроенный",
        "en": "Restore built-in",
    },
    "page.winws2_user_presets.dialog.delete_single.title": {
        "ru": "Удалить пресет?",
        "en": "Delete preset?",
    },
    "page.winws2_user_presets.dialog.delete_single.body": {
        "ru": "Пользовательский пресет «{name}» будет удалён.\nИзменения в нём будут потеряны.\nВернуть его можно только создав новый пресет или импортировав txt-файл.",
        "en": "Preset '{name}' will be removed from the user presets list.\nChanges in this preset will be lost.\nYou can restore it only by creating a new preset or importing a file.",
    },
    "page.winws2_user_presets.dialog.delete_single.button": {
        "ru": "Удалить",
        "en": "Delete",
    },
    "page.winws2_user_presets.dialog.create.title": {
        "ru": "Новый пресет",
        "en": "New preset",
    },
    "page.winws2_user_presets.dialog.create.subtitle": {
        "ru": "Сохраните текущие настройки как отдельный пресет, чтобы быстро переключаться между разными настройками.",
        "en": "Save current settings as a separate preset to switch between configurations quickly.",
    },
    "page.winws2_user_presets.dialog.create.name": {
        "ru": "Название",
        "en": "Name",
    },
    "page.winws2_user_presets.dialog.create.placeholder": {
        "ru": "Например: Игры / YouTube / Дом",
        "en": "For example: Games / YouTube / Home",
    },
    "page.winws2_user_presets.dialog.create.source": {
        "ru": "Создать на основе",
        "en": "Create from",
    },
    "page.winws2_user_presets.dialog.create.source.current": {
        "ru": "Текущего пресета",
        "en": "Current active",
    },
    "page.winws2_user_presets.dialog.create.source.standard": {
        "ru": "Встроенного пресета",
        "en": "Standard preset",
    },
    "page.winws2_user_presets.dialog.create.button.create": {
        "ru": "Создать",
        "en": "Create",
    },
    "page.winws2_user_presets.dialog.rename.title": {
        "ru": "Переименовать",
        "en": "Rename",
    },
    "page.winws2_user_presets.dialog.rename.subtitle": {
        "ru": "Имя пресета отображается в списке и используется для переключения.",
        "en": "Preset name is shown in the list and used for switching.",
    },
    "page.winws2_user_presets.dialog.rename.current_name": {
        "ru": "Текущее имя: {name}",
        "en": "Current name: {name}",
    },
    "page.winws2_user_presets.dialog.rename.new_name": {
        "ru": "Новое имя",
        "en": "New name",
    },
    "page.winws2_user_presets.dialog.rename.placeholder": {
        "ru": "Новое имя...",
        "en": "New name...",
    },
    "page.winws2_user_presets.dialog.rename.button": {
        "ru": "Переименовать",
        "en": "Rename",
    },
    "page.winws2_user_presets.dialog.validation.enter_name": {
        "ru": "Введите название.",
        "en": "Enter a name.",
    },
    "page.winws2_user_presets.dialog.validation.exists": {
        "ru": "Пресет «{name}» уже существует.",
        "en": "Preset ""{name}"" already exists.",
    },
    "page.winws2_user_presets.dialog.import_exists.title": {
        "ru": "Пресет существует",
        "en": "Preset exists",
    },
    "page.winws2_user_presets.dialog.import_exists.body": {
        "ru": "Пресет «{name}» уже существует. Импортировать с другим именем?",
        "en": "Preset '{name}' already exists. Import with another name?",
    },
    "page.winws2_user_presets.dialog.reset_all.title": {
        "ru": "Вернуть встроенные пресеты",
        "en": "Restore default presets",
    },
    "page.winws2_user_presets.dialog.reset_all.body": {
        "ru": "Мы вернём встроенные пресеты к состоянию после установки.\nЕсли вы меняли встроенный пресет, эти изменения будут потеряны.\nПользовательские пресеты с другими именами останутся.\nТекущий выбранный пресет будет применён заново.",
        "en": "Built-in presets will be restored to their post-install state.\nYour changes to built-in presets will be lost.\nCustom presets with other names will remain.\nCurrent selected preset will be re-applied automatically.",
    },
    "page.winws2_user_presets.dialog.reset_all.button": {
        "ru": "Вернуть встроенные",
        "en": "Restore defaults",
    },
    "page.winws2_user_presets.section.games": {
        "ru": "Игры (game filter)",
        "en": "Games (game filter)",
    },
    "page.winws2_user_presets.section.all_tcp_udp": {
        "ru": "Все сайты и игры(ALL TCP/UDP)",
        "en": "All sites and games (ALL TCP/UDP)",
    },
    "page.winws2_user_presets.empty.not_found": {
        "ru": "По этому поиску пресетов нет. Измените запрос или очистите строку поиска.",
        "en": "No presets match this search. Change the query or clear the search field.",
    },
    "page.winws2_user_presets.empty.none": {
        "ru": "Пресеты не найдены. Создайте новый пресет или импортируйте txt-файл.",
        "en": "No presets. Create a new one or import from file.",
    },
    "page.winws2_user_presets.error.generic": {
        "ru": "Ошибка: {error}",
        "en": "Error: {error}",
    },
    "page.winws2_user_presets.error.create_failed": {
        "ru": "Не удалось создать пресет.",
        "en": "Failed to create preset.",
    },
    "page.winws2_user_presets.error.rename_failed": {
        "ru": "Не удалось переименовать пресет.",
        "en": "Failed to rename preset.",
    },
    "page.winws2_user_presets.error.activate_failed": {
        "ru": "Не удалось активировать пресет '{name}'",
        "en": "Failed to activate preset '{name}'",
    },
    "page.winws2_user_presets.error.duplicate_failed": {
        "ru": "Не удалось дублировать пресет",
        "en": "Failed to duplicate preset",
    },
    "page.winws2_user_presets.error.reset_failed": {
        "ru": "Не удалось вернуть встроенный пресет",
        "en": "Failed to restore built-in preset",
    },
    "page.winws2_user_presets.error.delete_failed": {
        "ru": "Не удалось удалить пресет",
        "en": "Failed to delete preset",
    },
    "page.winws2_user_presets.error.import_failed": {
        "ru": "Не удалось импортировать пресет",
        "en": "Failed to import preset",
    },
    "page.winws2_user_presets.error.import_exception": {
        "ru": "Не удалось импортировать пресет: {error}",
        "en": "Import error: {error}",
    },
    "page.winws2_user_presets.error.open_folder": {
        "ru": "Не удалось открыть папку пресетов: {error}",
        "en": "Could not open presets folder: {error}",
    },
    "page.winws2_user_presets.error.export_failed": {
        "ru": "Не удалось экспортировать пресет",
        "en": "Failed to export preset",
    },
    "page.winws2_user_presets.error.restore_deleted": {
        "ru": "Ошибка восстановления: {error}",
        "en": "Restore error: {error}",
    },
    "page.winws2_user_presets.error.reset_all_exception": {
        "ru": "Ошибка восстановления пресетов: {error}",
        "en": "Preset restore error: {error}",
    },
    "page.winws2_user_presets.error.open_telegram": {
        "ru": "Не удалось открыть страницу пресетов: {error}",
        "en": "Failed to open presets page: {error}",
    },
    "page.winws2_user_presets.file_dialog.import_title": {
        "ru": "Импортировать пресет",
        "en": "Import preset",
    },
    "page.winws2_user_presets.file_dialog.export_title": {
        "ru": "Экспортировать пресет",
        "en": "Export preset",
    },
    "page.winws2_user_presets.infobar.success": {
        "ru": "Успех",
        "en": "Success",
    },
    "page.winws2_user_presets.info.exported": {
        "ru": "Пресет экспортирован: {path}",
        "en": "Preset exported: {path}",
    },
    "page.winws2_user_presets.info.title": {
        "ru": "Что это такое?",
        "en": "What is this?",
    },
    "page.winws2_user_presets.info.body": {
        "ru": (
            "Пресет, или конфиг, — это один или несколько .txt-файлов со списком флагов Zapret. "
            "Формат такой же, как у winws2.exe или winws.exe, поэтому GUI может быстро читать и менять настройки.\n\n"
            "Пресеты доступны с Zapret2 v20.3 для режимов Zapret 1 и Zapret 2. "
            "Они нужны, чтобы быстрее делать новые настройки, проще обмениваться ими и держать GUI и консольный Zapret в одном формате.\n\n"
            "При запуске активный пресет передаётся в winws2.exe для Zapret 2 или в winws.exe для Zapret 1 через @<config_file>. "
            "Это значит: прочитать параметры командной строки из файла. Остальные параметры командной строки при таком запуске не используются.\n\n"
            "Файл %AppData%\\ZapretTwoDev\\preset-zapret2.txt хранит только активный пресет. "
            "Сам по себе он не считается пользовательским пресетом. Ваши пресеты лежат в папке presets. "
            "По умолчанию используется Default, также есть встроенный Gaming.\n\n"
            "Пресетами можно обмениваться напрямую.\n\n"
            "Почему пресеты иногда плохо подходят: в них стратегии часто заранее прописаны под разные фильтры и hostlist. "
            "Из-за этого один сайт может заработать, а другой перестать. Для более точной настройки лучше использовать прямой запуск: "
            "там стратегия подбирается отдельно для нужной категории и hostlist."
        ),
        "en": (
            "A preset, or config, is one or more .txt files with Zapret flags. "
            "It uses the same format as winws2.exe or winws.exe, so the GUI can read and edit these settings.\n\n"
            "The active preset is passed to winws2.exe for Zapret 2 or winws.exe for Zapret 1 through @<config_file>, "
            "which means: read command-line options from a file. Other command-line options are not used in this launch mode.\n\n"
            "%AppData%\\ZapretTwoDev\\preset-zapret2.txt only stores the active preset copy. "
            "User presets are stored in the presets folder. More details are available from the button below."
        ),
    },
    "page.winws2_user_presets.info.open_site.button": {
        "ru": "Открыть сайт с пресетами",
        "en": "Open preset site",
    },
    "page.winws2_user_presets.info.open_site.description": {
        "ru": "Открывает сайт, где можно посмотреть и скачать пресеты.",
        "en": "Opens the site where presets can be viewed and downloaded.",
    },
    "page.winws2_profile_setup.title": {
        "ru": "Настройка профиля",
        "en": "Profile setup",
    },
    "page.winws2_profile_setup.breadcrumb.control": {
        "ru": "Управление",
        "en": "Control",
    },
    "page.winws2_profile_setup.filter.hostlist": {
        "ru": "Hostlist",
        "en": "Hostlist",
    },
    "page.winws2_profile_setup.filter.ipset": {
        "ru": "IPset",
        "en": "IPset",
    },
    "page.ipset.title": {
        "ru": "IPset",
        "en": "IPset",
    },
    "page.ipset.subtitle": {
        "ru": "Управление IP-адресами и подсетями",
        "en": "Manage IP addresses and subnets",
    },
    "page.ipset.description": {
        "ru": "IP-сеты содержат IP-адреса и подсети для обхода блокировок по IP.\nИспользуются когда блокировка происходит на уровне IP-адресов.",
        "en": "IP sets contain IP addresses and subnets for IP-based bypass.\nUsed when blocking happens at the IP-address level.",
    },
    "page.ipset.section.actions": {
        "ru": "Действия",
        "en": "Actions",
    },
    "page.ipset.open_folder.label": {
        "ru": "Открыть папку IP-сетов",
        "en": "Open IP sets folder",
    },
    "page.ipset.button.open": {
        "ru": "Открыть",
        "en": "Open",
    },
    "page.ipset.section.info": {
        "ru": "Информация",
        "en": "Information",
    },
    "page.ipset.files.loading": {
        "ru": "Загрузка информации...",
        "en": "Loading information...",
    },
    "page.ipset.files.not_found": {
        "ru": "Папка не найдена",
        "en": "Folder not found",
    },
    "page.ipset.files.summary": {
        "ru": "📁 Папка: {folder}\n📄 IP-файлов: {files_count}\n🌐 Примерно IP/подсетей: {total_ips}",
        "en": "📁 Folder: {folder}\n📄 IP files: {files_count}\n🌐 Approx. IPs/subnets: {total_ips}",
    },
    "page.ipset.files.error": {
        "ru": "Ошибка загрузки информации: {error}",
        "en": "Failed to load information: {error}",
    },
    "page.ipset.error.open_folder": {
        "ru": "Не удалось открыть папку:\n{error}",
        "en": "Failed to open folder:\n{error}",
    },
    "page.strategy_sort.title": {
        "ru": "Сортировка",
        "en": "Sorting",
    },
    "page.strategy_sort.subtitle": {
        "ru": "Фильтры и сортировка стратегий",
        "en": "Strategy filters and sorting",
    },
    "page.strategy_sort.section.strategy_type.title": {
        "ru": "Тип стратегии",
        "en": "Strategy type",
    },
    "page.strategy_sort.section.strategy_type.desc": {
        "ru": "Выберите тип стратегии для фильтрации",
        "en": "Choose strategy type for filtering",
    },
    "page.strategy_sort.section.desync.title": {
        "ru": "Техника обхода",
        "en": "Bypass technique",
    },
    "page.strategy_sort.section.desync.desc": {
        "ru": "Можно выбрать несколько техник одновременно",
        "en": "You can choose multiple techniques at once",
    },
    "page.strategy_sort.section.sort.title": {
        "ru": "Сортировка",
        "en": "Sorting",
    },
    "page.strategy_sort.section.sort.desc": {
        "ru": "Порядок отображения стратегий в списке",
        "en": "Display order of strategies in the list",
    },
    "page.strategy_sort.option.all": {
        "ru": "Все",
        "en": "All",
    },
    "page.strategy_sort.option.recommended": {
        "ru": "Рекоменд.",
        "en": "Recomm.",
    },
    "page.strategy_sort.option.experimental": {
        "ru": "Эксперим.",
        "en": "Experim.",
    },
    "page.strategy_sort.option.game": {
        "ru": "Игровые",
        "en": "Gaming",
    },
    "page.strategy_sort.option.fake": {
        "ru": "Fake",
        "en": "Fake",
    },
    "page.strategy_sort.option.split": {
        "ru": "Split",
        "en": "Split",
    },
    "page.strategy_sort.option.syn": {
        "ru": "SYN",
        "en": "SYN",
    },
    "page.strategy_sort.option.http": {
        "ru": "HTTP",
        "en": "HTTP",
    },
    "page.strategy_sort.option.rst": {
        "ru": "RST",
        "en": "RST",
    },
    "page.strategy_sort.option.wsize": {
        "ru": "WSize",
        "en": "WSize",
    },
    "page.strategy_sort.option.default": {
        "ru": "По умолчанию",
        "en": "Default",
    },
    "page.strategy_sort.option.name_asc": {
        "ru": "А-Я",
        "en": "A-Z",
    },
    "page.strategy_sort.option.name_desc": {
        "ru": "Я-А",
        "en": "Z-A",
    },
    "page.strategy_sort.option.rating": {
        "ru": "Рейтинг",
        "en": "Rating",
    },
    "tab.diagnostics.connection": {
        "ru": "Диагностика соединения",
        "en": "Connection Diagnostics",
    },
    "tab.diagnostics.dns": {
        "ru": "DNS подмена",
        "en": "DNS Spoofing",
    },
    "tab.orchestra.locked": {
        "ru": "Залоченные",
        "en": "Locked",
    },
    "tab.orchestra.blocked": {
        "ru": "Заблокированные",
        "en": "Blocked",
    },
    "tab.orchestra.whitelist": {
        "ru": "Белый список",
        "en": "Whitelist",
    },
    "tab.orchestra.ratings": {
        "ru": "Рейтинги",
        "en": "Ratings",
    },
    "appearance.language.section": {
        "ru": "Язык интерфейса",
        "en": "Interface Language",
    },
    "appearance.language.desc": {
        "ru": "Язык бокового меню и глобального поиска. Полный перевод страниц расширяется поэтапно.",
        "en": "Language for sidebar and global search. Full page translation is being expanded step by step.",
    },
    "appearance.language.label": {
        "ru": "Язык",
        "en": "Language",
    },
}


TEXTS_EXTRA: dict[str, dict[str, str]] = {
    "page.control.strategy.more_template": {
        "ru": "+{count} ещё",
        "en": "+{count} more",
    },
    "page.strategy_scan.title": {
        "ru": "Подбор стратегии",
        "en": "Strategy Scanner",
    },
    "page.strategy_scan.subtitle": {
        "ru": "Найдёт стратегию обхода DPI, которая работает у вашего провайдера",
        "en": "Finds a DPI bypass strategy that works with your ISP",
    },
    "page.strategy_scan.back": {
        "ru": "Назад",
        "en": "Back",
    },
    "page.strategy_scan.protocol": {
        "ru": "Что должно заработать?",
        "en": "What should work?",
    },
    "page.strategy_scan.protocol_tcp": {
        "ru": "Сайты и приложения",
        "en": "Sites and apps",
    },
    "page.strategy_scan.protocol_stun": {
        "ru": "Голосовые звонки",
        "en": "Voice calls",
    },
    "page.strategy_scan.protocol_games": {
        "ru": "Онлайн-игры",
        "en": "Online games",
    },
    "page.strategy_scan.udp_scope": {
        "ru": "Какие адреса игр:",
        "en": "Game addresses:",
    },
    "page.strategy_scan.udp_scope_all": {
        "ru": "Все списки адресов (по умолчанию)",
        "en": "All address lists (default)",
    },
    "page.strategy_scan.udp_scope_games_only": {
        "ru": "Только игровые списки",
        "en": "Game lists only",
    },
    "page.strategy_scan.mode": {
        "ru": "Тщательность:",
        "en": "Thoroughness:",
    },
    "page.strategy_scan.mode_quick": {
        "ru": "Быстро · 30",
        "en": "Quick · 30",
    },
    "page.strategy_scan.mode_standard": {
        "ru": "Тщательно · 80",
        "en": "Thorough · 80",
    },
    "page.strategy_scan.mode_full": {
        "ru": "Все стратегии",
        "en": "All strategies",
    },
    "page.strategy_scan.target": {
        "ru": "Какой сайт проверить:",
        "en": "Which site to check:",
    },
    "page.strategy_scan.target.default": {
        "ru": "discord.com",
        "en": "discord.com",
    },
    "page.strategy_scan.target.placeholder": {
        "ru": "discord.com",
        "en": "discord.com",
    },
    "page.strategy_scan.quick_domains": {
        "ru": "Выбрать из списка",
        "en": "Pick from list",
    },
    "page.strategy_scan.quick_domains_hint": {
        "ru": "Готовые адреса: Discord, YouTube, Telegram и другие",
        "en": "Ready addresses: Discord, YouTube, Telegram and more",
    },
    "page.strategy_scan.start": {
        "ru": "Найти рабочую стратегию",
        "en": "Find a working strategy",
    },
    "page.strategy_scan.stop": {
        "ru": "Остановить",
        "en": "Stop",
    },
    "page.strategy_scan.ready": {
        "ru": "Zapret на время поиска выключится",
        "en": "Zapret is off while searching",
    },
    "page.strategy_scan.col_strategy": {
        "ru": "Стратегия",
        "en": "Strategy",
    },
    "page.strategy_scan.col_status": {
        "ru": "Результат",
        "en": "Result",
    },
    "page.strategy_scan.col_time": {
        "ru": "Ответ, мс",
        "en": "Response, ms",
    },
    "page.strategy_scan.col_action": {
        "ru": "Применить",
        "en": "Apply",
    },
    "page.strategy_scan.log": {
        "ru": "Подробный лог",
        "en": "Detailed log",
    },
    "page.strategy_scan.starting": {
        "ru": "Запуск сканирования...",
        "en": "Starting scan...",
    },
    "page.strategy_scan.stopping": {
        "ru": "Остановка...",
        "en": "Stopping...",
    },
    "page.strategy_scan.apply": {
        "ru": "Применить",
        "en": "Apply",
    },
    "page.strategy_scan.error": {
        "ru": "Ошибка сканирования",
        "en": "Scan error",
    },
    "page.strategy_scan.baseline_ok_title_stun": {
        "ru": "STUN/UDP уже доступен",
        "en": "STUN/UDP already reachable",
    },
    "page.strategy_scan.baseline_ok_text_stun": {
        "ru": "Цель отвечает и без обхода: результаты подбора — только для сведения",
        "en": "The target answers without bypass: scan results are for reference only",
    },
    "page.strategy_scan.baseline_ok_title": {
        "ru": "Домен уже доступен",
        "en": "Domain is already reachable",
    },
    "page.strategy_scan.baseline_ok_text": {
        "ru": "Сайт открывается и без обхода: результаты подбора — только для сведения",
        "en": "The site opens without bypass: scan results are for reference only",
    },
    "page.strategy_scan.found": {
        "ru": "Найдены надёжные стратегии",
        "en": "Reliable strategies found",
    },
    "page.strategy_scan.not_found": {
        "ru": "Рабочих стратегий не найдено",
        "en": "No working strategies found",
    },
    "page.strategy_scan.try_full": {
        "ru": "Запустите подбор ещё раз: проверятся следующие стратегии, или выберите «Все стратегии»",
        "en": "Run the scan again to test the next strategies, or choose «All strategies»",
    },
    "page.strategy_scan.protocol_tcp.hint": {
        "ru": "YouTube, Discord, Instagram — всё, что в браузере",
        "en": "YouTube, Discord, Instagram — anything in a browser",
    },
    "page.strategy_scan.protocol_stun.hint": {
        "ru": "Звонки в Discord и Telegram",
        "en": "Discord and Telegram calls",
    },
    "page.strategy_scan.protocol_games.hint": {
        "ru": "Roblox, Steam, Amazon и другие",
        "en": "Roblox, Steam, Amazon and more",
    },
    "page.strategy_scan.mode_quick.hint": {
        "ru": "≈ 1–3 минуты",
        "en": "≈ 1–3 minutes",
    },
    "page.strategy_scan.mode_standard.hint": {
        "ru": "≈ 3–7 минут",
        "en": "≈ 3–7 minutes",
    },
    "page.strategy_scan.mode_full.hint": {
        "ru": "долго — самое время для чая ☕",
        "en": "long — perfect time for tea ☕",
    },
    "page.strategy_scan.baseline_question_title": {
        "ru": "Подбирать нечего",
        "en": "Nothing to find",
    },
    "page.strategy_scan.baseline_question_text": {
        "ru": "Всё равно проверить стратегии? Результаты будут только для сведения.",
        "en": "Test the strategies anyway? Results will be for reference only.",
    },
    "page.strategy_scan.baseline_question_yes": {
        "ru": "Всё равно проверить",
        "en": "Test anyway",
    },
    "page.strategy_scan.baseline_question_no": {
        "ru": "Не проверять",
        "en": "Don't test",
    },
    "page.strategy_scan.resume_question_title": {
        "ru": "Подбор уже начинался",
        "en": "The search was started before",
    },
    "page.strategy_scan.resume_question_text": {
        "ru": "Для {target} уже проверено стратегий: {count} — они не сработали (подбор помнит их 14 дней).\n\n«Продолжить» — проверить следующие, ещё не проверенные стратегии.\n«Начать заново» — проверить список с самого начала, как в первый раз.",
        "en": "Strategies already tested for {target}: {count} — they did not work (the search remembers them for 14 days).\n\n\"Continue\" tests the next strategies that have not been tried yet.\n\"Start over\" tests the list from the very beginning, like the first time.",
    },
    "page.strategy_scan.resume_question_continue": {
        "ru": "Продолжить с места остановки",
        "en": "Continue where it stopped",
    },
    "page.strategy_scan.resume_question_restart": {
        "ru": "Начать заново",
        "en": "Start over",
    },
    "page.strategy_scan.resume_question_cancel": {
        "ru": "Отмена",
        "en": "Cancel",
    },
    "page.strategy_scan.geo_site.notice": {
        "ru": "{service} сам ограничивает доступ из России — стратегия Zapret его не чинит, подбор ничего не найдёт. Включите для него DNS-профиль в «Редакторе hosts» или смените DNS в «Настройке DNS».",
        "en": "{service} restricts access from Russia on its own side — a Zapret strategy cannot fix it and the search will find nothing. Turn on a DNS profile for it in the Hosts editor or change DNS in DNS settings.",
    },
    "page.strategy_scan.geo_site.open_hosts": {
        "ru": "Открыть «Редактор hosts»",
        "en": "Open the Hosts editor",
    },
    "page.strategy_scan.geo_site.open_dns": {
        "ru": "Настройка DNS",
        "en": "DNS settings",
    },
    "page.strategy_scan.geo_site.question_title": {
        "ru": "Стратегия здесь не поможет",
        "en": "A strategy will not help here",
    },
    "page.strategy_scan.geo_site.question_text": {
        "ru": "{target} — это {service}. Сервис сам ограничивает доступ из России: провайдер тут ни при чём, поэтому ни одна стратегия Zapret его не откроет, а подбор зря займёт время.\n\nЧто помогает: DNS-профиль для этого сервиса в «Редакторе hosts» или другой DNS в «Настройке DNS».",
        "en": "{target} belongs to {service}. The service restricts access from Russia on its own side: your provider is not the cause, so no Zapret strategy will open it and the search would only waste time.\n\nWhat helps: a DNS profile for this service in the Hosts editor or another DNS in DNS settings.",
    },
    "page.strategy_scan.geo_site.question_scan_anyway": {
        "ru": "Всё равно подобрать",
        "en": "Search anyway",
    },
    "page.strategy_scan.applied": {
        "ru": "Стратегия добавлена",
        "en": "Strategy added",
    },
    "page.winws1_pages.empty.no_categories": {
        "ru": "В выбранном пресете нет профилей, которые можно показать на этой странице. Попробуйте другой пресет или добавьте нужный профиль.",
        "en": "The selected preset has no profiles to show on this page. Try another preset or add the needed profile.",
    },
    "page.winws2_pages.request.hint": {
        "ru": "Хотите добавить новый сайт или сервис в Zapret 2? Откройте готовую форму в Forgejo и опишите, что нужно добавить в hostlist или ipset.",
        "en": "Want to add a new site or service to Zapret 2? Open the Forgejo form and describe what should be added to the hostlist or ipset.",
    },
    "page.winws2_pages.empty.no_presets": {
        "ru": "Пресеты Zapret 2 не найдены. Импортируйте пресет или переустановите приложение, чтобы вернуть встроенные пресеты.",
        "en": "Zapret 2 presets were not found. Import a preset or reinstall the app to restore built-in presets.",
    },
    "page.winws2_pages.empty.no_selected_preset": {
        "ru": "Не удалось понять, какой пресет выбран. Откройте «Мои пресеты» и выберите preset заново.",
        "en": "Could not determine which preset is selected. Open My Presets and choose a preset again.",
    },
    "page.winws2_pages.empty.preset_read_error": {
        "ru": "Не удалось открыть выбранный пресет «{preset_name}». Файл мог быть удалён, очищен или повреждён. Выберите другой пресет или верните встроенный.",
        "en": "Could not open the selected preset \"{preset_name}\". The file may have been deleted, emptied, or corrupted. Choose another preset or restore the built-in one.",
    },
    "page.winws2_pages.empty.unknown_error": {
        "ru": "Не удалось показать profile-ы preset-а «{preset_name}». Если ошибка повторится, выберите другой preset.",
        "en": "Could not show profiles from preset \"{preset_name}\". If the error repeats, choose another preset.",
    },
    "page.winws2_pages.empty.no_categories": {
        "ru": "В выбранном пресете «{preset_name}» нет профилей, которые можно показать на этой странице. Попробуйте другой пресет или добавьте нужный профиль.",
        "en": "The selected preset \"{preset_name}\" has no profiles to show on this page. Try another preset or add the needed profile.",
    },
    "page.winws2_pages.current.active_count": {
        "ru": "{count} активных",
        "en": "{count} active",
    },
    "page.winws2_pages.info.body": {
        "ru": "Здесь вы можете тонко изменить стратегию для каждого профиля, который найден в выбранном пресете. Всего существует несколько фаз дурения (send, syndata, fake, multisplit и т.д.). Последовательность сама определяется программой.\n\nВы можете править пресет вручную через txt-файл или выбрать готовую стратегию в этом меню. В интерфейсе готовая стратегия означает заранее собранный набор аргументов, который программа подставляет в профиль. Это не отдельный синтаксис winws2, а удобный способ выбрать техники дурения или фуллинга для TCP/IP-пакетов.",
        "en": "Here you can finely tune the strategy for each profile found in the selected preset. There are several obfuscation phases (send, syndata, fake, multisplit, etc.). Their sequence is determined by the app.\n\nYou can edit the preset manually in a txt file or choose a ready strategy in this menu. In the interface, a ready strategy means a prebuilt set of arguments that the app inserts into a profile. It is not separate winws2 syntax, but a convenient way to choose obfuscation or fooling techniques for TCP/IP packets.",
    },
}

TEXTS.update(TEXTS_EXTRA)


TEXTS_PAGES_FINAL: dict[str, dict[str, str]] = {
    "page.about.app_name": {
        "ru": "Zapret 2 GUI",
        "en": "Zapret 2 GUI",
    },
    "common.badge.premium": {
        "ru": "⭐ Premium",
        "en": "⭐ Premium",
    },
    "common.premium.tier.free": {
        "ru": "Free",
        "en": "Free",
    },
    "common.premium.tier.premium": {
        "ru": "Premium",
        "en": "Premium",
    },
    "common.premium.days_left": {
        "ru": "Осталось {days} {unit}",
        "en": "{days} {unit} left",
    },
    "common.premium.days_unit.one": {
        "ru": "день",
        "en": "day",
    },
    "common.premium.days_unit.few": {
        "ru": "дня",
        "en": "days",
    },
    "common.premium.days_unit.many": {
        "ru": "дней",
        "en": "days",
    },
    "launch.action.start": {
        "ru": "Запустить Zapret",
        "en": "Start Zapret",
    },
    "launch.action.stop": {
        "ru": "Остановить Zapret",
        "en": "Stop Zapret",
    },
    "launch.action.stopping": {
        "ru": "Zapret останавливается",
        "en": "Zapret is stopping",
    },
    "launch.action.close_app": {
        "ru": "Закрыть программу",
        "en": "Close app",
    },
    "launch.action.close_app.description": {
        "ru": "Остановить Zapret и закрыть программу",
        "en": "Stop Zapret and close the app",
    },
    "launch.dot.description": {
        "ru": "Нажмите на кнопку, чтобы запустить или остановить Zapret",
        "en": "Click the button to start or stop Zapret",
    },
    "launch.badge.running": {
        "ru": "Работает",
        "en": "Running",
    },
    "launch.badge.starting": {
        "ru": "Запуск…",
        "en": "Starting…",
    },
    "launch.badge.stopping": {
        "ru": "Остановка…",
        "en": "Stopping…",
    },
    "launch.badge.stopped": {
        "ru": "Остановлен",
        "en": "Stopped",
    },
    "launch.badge.failed": {
        "ru": "Ошибка",
        "en": "Error",
    },
    "launch.badge.tooltip.running": {
        "ru": "{mode} работает · нажмите, чтобы остановить",
        "en": "{mode} is running · click to stop",
    },
    "launch.badge.tooltip.starting": {
        "ru": "{mode} запускается · нажмите, чтобы остановить",
        "en": "{mode} is starting · click to stop",
    },
    "launch.badge.tooltip.stopping": {
        "ru": "{mode} останавливается…",
        "en": "{mode} is stopping…",
    },
    "launch.badge.tooltip.stopped": {
        "ru": "{mode} остановлен · нажмите, чтобы запустить",
        "en": "{mode} is stopped · click to start",
    },
    "launch.badge.tooltip.failed": {
        "ru": "Ошибка запуска {mode} · нажмите, чтобы попробовать снова",
        "en": "{mode} failed to start · click to try again",
    },
    "tray.status.running": {
        "ru": "работает",
        "en": "running",
    },
    "tray.status.starting": {
        "ru": "запускается",
        "en": "starting",
    },
    "tray.status.stopping": {
        "ru": "останавливается",
        "en": "stopping",
    },
    "tray.status.stopped": {
        "ru": "остановлен",
        "en": "stopped",
    },
    "tray.status.failed": {
        "ru": "ошибка запуска",
        "en": "failed to start",
    },
    "tray.tooltip.preset": {
        "ru": "Пресет: {preset}",
        "en": "Preset: {preset}",
    },
    "tray.menu.start": {
        "ru": "Запустить Zapret",
        "en": "Start Zapret",
    },
    "tray.menu.stop": {
        "ru": "Остановить Zapret",
        "en": "Stop Zapret",
    },
    "tray.menu.starting": {
        "ru": "Zapret запускается…",
        "en": "Zapret is starting…",
    },
    "tray.menu.stopping": {
        "ru": "Zapret останавливается…",
        "en": "Zapret is stopping…",
    },
    "tray.menu.restart": {
        "ru": "Перезапустить",
        "en": "Restart",
    },
    "tray.menu.preset": {
        "ru": "Пресет",
        "en": "Preset",
    },
    "tray.menu.presets_empty": {
        "ru": "Пресетов пока нет",
        "en": "No presets yet",
    },
    "tray.menu.show": {
        "ru": "Показать окно",
        "en": "Show window",
    },
    "tray.menu.hide": {
        "ru": "Скрыть в трей",
        "en": "Hide to tray",
    },
    "tray.menu.opacity": {
        "ru": "Прозрачность окна",
        "en": "Window transparency",
    },
    "tray.menu.acrylic": {
        "ru": "Эффект акрилика окна",
        "en": "Window acrylic effect",
    },
    "tray.menu.console": {
        "ru": "Консоль",
        "en": "Console",
    },
    "tray.menu.exit": {
        "ru": "Выход",
        "en": "Exit",
    },
    "tray.menu.exit_stop": {
        "ru": "Выход и остановить",
        "en": "Stop and exit",
    },
    "tray.notify.started.title": {
        "ru": "Zapret запущен",
        "en": "Zapret started",
    },
    "tray.notify.started.body": {
        "ru": "Обход блокировок активен · пресет: {preset}",
        "en": "Bypass is active · preset: {preset}",
    },
    "tray.notify.started.body_no_preset": {
        "ru": "Обход блокировок активен",
        "en": "Bypass is active",
    },
    "tray.notify.stopped.title": {
        "ru": "Zapret остановлен",
        "en": "Zapret stopped",
    },
    "tray.notify.stopped.body": {
        "ru": "Обход блокировок выключен",
        "en": "Bypass is off",
    },
    "nav.history.back.tooltip": {
        "ru": "Назад. Удерживайте, чтобы увидеть историю",
        "en": "Back. Hold to see history",
    },
    "nav.history.forward.tooltip": {
        "ru": "Вперёд. Удерживайте, чтобы увидеть историю",
        "en": "Forward. Hold to see history",
    },
    "titlebar.subscription.free": {
        "ru": "FREE",
        "en": "FREE",
    },
    "titlebar.subscription.premium": {
        "ru": "PREMIUM",
        "en": "PREMIUM",
    },
    "titlebar.subscription.premium_days": {
        "ru": "PREMIUM · {days} дн.",
        "en": "PREMIUM · {days} d",
    },
    "titlebar.subscription.free.tooltip": {
        "ru": "Бесплатная версия. Нажмите, чтобы узнать о Premium",
        "en": "Free version. Click to learn about Premium",
    },
    "titlebar.subscription.premium.tooltip": {
        "ru": "Premium активен. Нажмите, чтобы открыть страницу подписки",
        "en": "Premium is active. Click to open the subscription page",
    },
    "titlebar.subscription.premium_days.tooltip": {
        "ru": "Premium активен. {days_left}. Нажмите, чтобы открыть страницу подписки",
        "en": "Premium is active: {days_left}. Click to open the subscription page",
    },
    "common.toggle.on_off": {
        "ru": "Вкл/Выкл",
        "en": "On/Off",
    },
    "page.appearance.display_mode.description": {
        "ru": "Выберите светлый или тёмный режим интерфейса.",
        "en": "Choose light or dark interface mode.",
    },
    "page.appearance.display_mode.option.dark": {
        "ru": "🌙 Тёмный",
        "en": "🌙 Dark",
    },
    "page.appearance.display_mode.option.light": {
        "ru": "☀️ Светлый",
        "en": "☀️ Light",
    },
    "page.appearance.display_mode.option.system": {
        "ru": "⚙ Авто",
        "en": "⚙ Auto",
    },
    "page.appearance.background.description": {
        "ru": "Стандартный фон соответствует режиму отображения. AMOLED и РКН Тян доступны подписчикам Premium. Для РКН Тян можно выбрать готовый фон из списка.",
        "en": "The default background follows display mode. AMOLED and RKN Chan are available for Premium subscribers. For RKN Chan you can choose a ready background from the list.",
    },
    "page.appearance.background.option.standard": {
        "ru": "Стандартный",
        "en": "Standard",
    },
    "page.appearance.background.option.amoled": {
        "ru": "AMOLED — чёрный",
        "en": "AMOLED - black",
    },
    "page.appearance.background.option.rkn_chan": {
        "ru": "РКН Тян",
        "en": "RKN Chan",
    },
    "page.appearance.background.rkn.label": {
        "ru": "Фон РКН Тян",
        "en": "RKN Chan Background",
    },
    "page.appearance.background.rkn.none": {
        "ru": "Фоны не найдены",
        "en": "No backgrounds found",
    },
    "page.appearance.holiday.garland.description": {
        "ru": "Праздничная гирлянда с мерцающими огоньками в верхней части окна. Доступно только для подписчиков Premium.",
        "en": "Festive garland with blinking lights at the top of the window. Available only for Premium subscribers.",
    },
    "page.appearance.holiday.garland.title": {
        "ru": "Новогодняя гирлянда",
        "en": "Holiday Garland",
    },
    "page.appearance.holiday.snowflakes.description": {
        "ru": "Мягко падающие снежинки по всему окну. Создаёт уютную зимнюю атмосферу.",
        "en": "Soft falling snowflakes across the window. Creates a cozy winter atmosphere.",
    },
    "page.appearance.holiday.snowflakes.title": {
        "ru": "Снежинки",
        "en": "Snowflakes",
    },
    "page.appearance.opacity.win11.title": {
        "ru": "Эффект акрилика окна",
        "en": "Window Acrylic Effect",
    },
    "page.appearance.opacity.win11.description": {
        "ru": "Настройка интенсивности акрилового эффекта всего окна приложения. При 0% эффект минимальный, при 100% — максимальный.",
        "en": "Adjust acrylic effect intensity for the entire app window. At 0% the effect is minimal, at 100% maximal.",
    },
    "page.appearance.opacity.standard.title": {
        "ru": "Прозрачность окна",
        "en": "Window Opacity",
    },
    "page.appearance.opacity.standard.description": {
        "ru": "Настройка прозрачности всего окна приложения. При 0% окно полностью прозрачное, при 100% — непрозрачное.",
        "en": "Adjust opacity of the entire app window. At 0% the window is fully transparent, at 100% fully opaque.",
    },
    "page.appearance.accent.description": {
        "ru": "Цвет акцентных элементов интерфейса: кнопок, иконок, индикаторов. Изменяет цвет нативных компонентов WinUI.",
        "en": "Accent color for interface elements: buttons, icons, indicators. Changes native WinUI component color.",
    },
    "page.appearance.accent.color.title": {
        "ru": "Цвет акцента",
        "en": "Accent Color",
    },
    "page.appearance.accent.color.pick": {
        "ru": "Выбрать цвет",
        "en": "Pick Color",
    },
    "page.appearance.accent.windows.title": {
        "ru": "Акцент из Windows",
        "en": "Use Windows Accent",
    },
    "page.appearance.accent.windows.description": {
        "ru": "Автоматически использовать системный акцентный цвет Windows",
        "en": "Automatically use the system Windows accent color",
    },
    "page.appearance.accent.tint_background.title": {
        "ru": "Тонировать фон акцентным цветом",
        "en": "Tint Background with Accent",
    },
    "page.appearance.accent.tint_background.description": {
        "ru": "Фон окна окрашивается в оттенок акцентного цвета",
        "en": "Window background is tinted by accent color",
    },
    "page.appearance.accent.tint_intensity.label": {
        "ru": "Интенсивность тонировки:",
        "en": "Tint Intensity:",
    },
    "page.appearance.performance.animations.title": {
        "ru": "Анимации интерфейса",
        "en": "Interface Animations",
    },
    "page.appearance.performance.animations.description": {
        "ru": "Анимации кнопок, переходов и элементов WinUI",
        "en": "Animations for buttons, transitions, and WinUI elements",
    },
    "page.appearance.performance.scroll.title": {
        "ru": "Плавная прокрутка",
        "en": "Smooth Scrolling",
    },
    "page.appearance.performance.scroll.description": {
        "ru": "Инерционная прокрутка страниц настроек",
        "en": "Inertial scrolling on settings pages",
    },
    "page.appearance.performance.live_animations.title": {
        "ru": "Живые анимации",
        "en": "Live animations",
    },
    "page.appearance.performance.live_animations.description": {
        "ru": "Логотип, сцена статуса и сводка на главной, кнопки запуска оживают при изменениях. Почти не нагружает процессор",
        "en": "The logo, the home status scene and summary, and start buttons come alive on changes. Almost no CPU load",
    },
    "page.appearance.performance.editor_scroll.title": {
        "ru": "Плавная прокрутка редакторов",
        "en": "Editor Smooth Scrolling",
    },
    "page.appearance.performance.editor_scroll.description": {
        "ru": "Плавная прокрутка внутри больших текстовых полей и редакторов. Работает только при включённых анимациях интерфейса.",
        "en": "Smooth scrolling inside large text fields and editors. Works only when interface animations are enabled.",
    },
    "page.control.dialog.defender_disable.title": {
        "ru": "Отключение Windows Defender",
        "en": "Disable Windows Defender",
    },
    "page.control.dialog.defender_enable.title": {
        "ru": "Включение Windows Defender",
        "en": "Enable Windows Defender",
    },
    "page.control.dialog.max_block_enable.title": {
        "ru": "Блокировка MAX",
        "en": "Enable MAX Blocking",
    },
    "page.control.dialog.max_block_disable.title": {
        "ru": "Отключение блокировки MAX",
        "en": "Disable MAX Blocking",
    },
    "page.control.dialog.state_media_block_enable.title": {
        "ru": "Блокировка государственных СМИ РФ",
        "en": "Block Russian State Media",
    },
    "page.control.dialog.state_media_block_disable.title": {
        "ru": "Отключение блокировки государственных СМИ РФ",
        "en": "Disable Russian State Media Blocking",
    },
    "page.logs.info.copied": {
        "ru": "✅ Скопировано в буфер обмена",
        "en": "✅ Copied to clipboard",
    },
    "page.logs.info.empty": {
        "ru": "⚠️ Лог пуст",
        "en": "⚠️ Log is empty",
    },
    "page.logs.info.view_cleared": {
        "ru": "🧹 Вид очищен",
        "en": "🧹 View cleared",
    },
    "page.logs.info.errors_cleared": {
        "ru": "🧹 Ошибки очищены",
        "en": "🧹 Errors cleared",
    },
    "page.winws2_control.setting.autostart.title": {
        "ru": "Автозапуск DPI после старта программы",
        "en": "Auto-start DPI after app launch",
    },
    "page.winws2_control.setting.autostart.desc": {
        "ru": "После запуска ZapretGUI автоматически запускать текущий DPI-режим",
        "en": "Automatically start the current DPI mode after ZapretGUI launches",
    },
    "page.winws2_control.strategy.autostart_disabled": {
        "ru": "Автозапуск DPI после старта программы отключён",
        "en": "Auto-start DPI after app launch is disabled",
    },
}

TEXTS.update(TEXTS_PAGES_FINAL)


# Обучающий тур первого запуска (ui/onboarding) и карточка его повтора.
TEXTS_ONBOARDING: dict[str, dict[str, str]] = {
    "onboarding.accessible_name": {
        "ru": "Обучающий тур по программе",
        "en": "Guided tour of the app",
    },
    "onboarding.counter": {
        "ru": "Шаг {current} из {total}",
        "en": "Step {current} of {total}",
    },
    "onboarding.button.start": {"ru": "Начнём", "en": "Let's go"},
    "onboarding.button.next": {"ru": "Далее", "en": "Next"},
    "onboarding.button.back": {"ru": "Назад", "en": "Back"},
    "onboarding.button.skip": {"ru": "Пропустить", "en": "Skip"},
    "onboarding.button.done": {"ru": "Готово", "en": "Done"},
    "onboarding.button.wiki": {"ru": "Подробнее в вики", "en": "Read more in the wiki"},
    "onboarding.step.welcome.title": {
        "ru": "Добро пожаловать в Zapret!",
        "en": "Welcome to Zapret!",
    },
    "onboarding.step.welcome.body": {
        "ru": (
            "Zapret помогает открыть сайты и приложения, которые провайдер блокирует или замедляет: "
            "YouTube, Discord и многие другие.\n\n"
            "За пару минут покажем, как программа устроена и где что находится. Листать можно кнопкой "
            "«Далее» или стрелками на клавиатуре, закрыть — клавишей Esc."
        ),
        "en": (
            "Zapret helps you open sites and apps that your provider blocks or slows down: "
            "YouTube, Discord and many others.\n\n"
            "In a couple of minutes we will show how the app works and where everything is. Use "
            "Next or the arrow keys to move on, press Esc to close the tour."
        ),
    },
    "onboarding.step.how_it_works.title": {
        "ru": "Как работает обход",
        "en": "How the bypass works",
    },
    "onboarding.step.how_it_works.body": {
        "ru": (
            "Провайдер следит за трафиком с помощью фильтра — DPI. Фильтр читает начало каждого "
            "соединения, видит в нём адрес сайта и, если сайт в чёрном списке, обрывает соединение "
            "или замедляет его.\n\n"
            "Zapret запускает у вас на компьютере движок winws2 (в режиме Zapret 1 — winws). Движок "
            "пропускает через себя сетевые пакеты и для нужных сайтов слегка их меняет: делит на части, "
            "переставляет, отправляет перед настоящим пакетом поддельный — фейк. Фильтр путается и "
            "пропускает соединение, а сайт получает обычный запрос.\n\n"
            "Это не VPN: трафик идёт напрямую через ваш интернет, без сервера в другой стране."
        ),
        "en": (
            "Your provider watches traffic with a filter called DPI. It reads the start of every "
            "connection, sees the site address there and, if the site is blacklisted, drops or slows "
            "the connection.\n\n"
            "Zapret runs the winws2 engine on your computer (winws in Zapret 1 mode). The engine passes "
            "network packets through itself and slightly changes them for selected sites: splits them, "
            "reorders them, sends a fake packet before the real one. The filter gets confused and lets "
            "the connection through, while the site receives a normal request.\n\n"
            "This is not a VPN: traffic goes directly through your own connection, with no server abroad."
        ),
    },
    "onboarding.step.building_blocks.title": {
        "ru": "Пресет, профиль, стратегия",
        "en": "Preset, profile, strategy",
    },
    "onboarding.step.building_blocks.body": {
        "ru": (
            "Три главных слова в программе:\n\n"
            "• Пресет — вся настройка целиком. Это обычный текстовый файл, и запускается ровно то, "
            "что в нём записано.\n"
            "• Профиль — правило внутри пресета: какой трафик обрабатывать. Например, YouTube, "
            "Discord или игры.\n"
            "• Стратегия — способ обхода для профиля: как именно менять пакеты, чтобы фильтр "
            "не узнал сайт.\n\n"
            "Пресет состоит из профилей, у каждого профиля своя стратегия. Дальше покажем всё это "
            "прямо в программе."
        ),
        "en": (
            "Three key words in the app:\n\n"
            "• Preset — the whole configuration. It is a plain text file, and exactly what is written "
            "in it is what runs.\n"
            "• Profile — a rule inside the preset: which traffic to handle. For example YouTube, "
            "Discord or games.\n"
            "• Strategy — the bypass method for a profile: how exactly to change packets so the filter "
            "does not recognize the site.\n\n"
            "A preset is made of profiles, and each profile has its own strategy. Next we will show all "
            "of this right in the app."
        ),
    },
    "onboarding.step.control_nav.title": {
        "ru": "Главная страница",
        "en": "Main page",
    },
    "onboarding.step.control_nav.body": {
        "ru": (
            "«Управление» — главная страница программы: запуск, текущее состояние и основные "
            "настройки. Если запутались в разделах, возвращайтесь сюда."
        ),
        "en": (
            "Control is the main page of the app: start button, current state and basic settings. "
            "If you get lost, come back here."
        ),
    },
    "onboarding.step.start.title": {
        "ru": "Выключатель обхода",
        "en": "Bypass switch",
    },
    "onboarding.step.start.body": {
        "ru": (
            "Круглая кнопка в стене — это выключатель. Пока обход выключен, пакеты разбиваются о стену "
            "блокировки. Нажмите на кнопку, и программа запустит движок с выбранным пресетом: пакеты пойдут "
            "сквозь стену к сайтам. "
            "Пока движок работает, обход действует не только в браузере, а во всей системе — в Discord, "
            "играх и других программах, но только для того трафика, который описан в профилях пресета.\n\n"
            "Повторное нажатие останавливает обход. То же самое можно сделать меткой статуса в заголовке "
            "окна — она видна в любом разделе — или из меню значка в трее."
        ),
        "en": (
            "The round button in the wall is a switch. While the bypass is off, packets crash into the "
            "blocking wall. Click the button and the app starts the engine with the selected preset: packets "
            "go through the wall to the sites. While the "
            "engine runs, the bypass works system-wide, not only in the browser — in Discord, games and other "
            "programs, but only for the traffic described in the preset profiles.\n\n"
            "Click it again to stop the bypass. You can also do this with the status badge in the window "
            "title — it is visible in every section — or from the tray icon menu."
        ),
    },
    "onboarding.step.status.title": {
        "ru": "Статус работы",
        "en": "Status",
    },
    "onboarding.step.status.body": {
        "ru": "Показывает, запущен ли обход прямо сейчас. Если что-то пошло не так, здесь появится подсказка.",
        "en": "Shows whether the bypass is running right now. If something goes wrong, a hint appears here.",
    },
    "onboarding.step.preset.title": {
        "ru": "Какой пресет выбран",
        "en": "Selected preset",
    },
    "onboarding.step.preset.body": {
        "ru": "Здесь видно, какой пресет сейчас выбран. Нажмите на плашку — откроется список пресетов.",
        "en": "Shows which preset is selected now. Click it to open the list of presets.",
    },
    "onboarding.step.presets_list.title": {
        "ru": "Что такое пресеты",
        "en": "What presets are",
    },
    "onboarding.step.presets_list.body": {
        "ru": (
            "Это страница «Мои пресеты». Пресет — текстовый файл (.txt) с настройками движка: в нём "
            "перечислены профили и их стратегии. Программа запускает его как есть, ничего не "
            "добавляя от себя.\n\n"
            "Готовые пресеты уже есть в программе. Выбранный отмечен в списке, чтобы сменить его, "
            "просто нажмите на другой. Провайдеры блокируют по-разному, поэтому, если сайт не открывается, "
            "первым делом попробуйте другой пресет."
        ),
        "en": (
            "This is the My presets page. A preset is a text file (.txt) with engine settings: it lists "
            "profiles and their strategies. The app runs it as is and adds nothing on its own.\n\n"
            "Ready presets are already included. The selected one is marked in the list; click another "
            "one to switch. Providers block in different ways, so if a site does not open, try another "
            "preset first."
        ),
    },
    "onboarding.step.preset_menu.title": {
        "ru": "Что можно делать с пресетом",
        "en": "What you can do with a preset",
    },
    "onboarding.step.preset_menu.body": {
        "ru": (
            "Это меню открывается, если нажать на пресет правой кнопкой мыши.\n\n"
            "Открыть — посмотреть и поправить текст пресета. Рейтинг — оценить пресет, чтобы помнить, "
            "какой работает лучше. Выше и ниже — переставить его в списке. Дублировать — сделать "
            "копию и менять её, не трогая оригинал. Экспорт — сохранить файл пресета, например чтобы "
            "поделиться им.\n\n"
            "Переименовать и Удалить есть только у ваших пресетов, а выбранный сейчас удалить нельзя. "
            "Вернуть встроенный появляется, если вы меняли встроенный пресет, — он вернёт исходный "
            "вид. У пресетов, добавленных по ссылке, есть ещё пункты, чтобы обновить их из источника "
            "или отвязать от него."
        ),
        "en": (
            "This menu opens when you right-click a preset.\n\n"
            "Open shows the preset text so you can edit it. Rating lets you score a preset to "
            "remember which one works best. Up and down move it in the list. Duplicate makes a copy "
            "you can change without touching the original. Export saves the preset file, for example "
            "to share it.\n\n"
            "Rename and Delete exist only for your own presets, and the selected preset cannot be "
            "deleted. Restore built-in appears when you have changed a built-in preset and brings "
            "back its original version. Presets added from a link also have items to update them from "
            "the source or unlink them."
        ),
    },
    "onboarding.step.preset_file.title": {
        "ru": "Пресет — это обычный текстовый файл",
        "en": "A preset is a plain text file",
    },
    "onboarding.step.preset_file.body": {
        "ru": (
            "Любой пресет — просто файл .txt. Здесь он открыт целиком: каждая строка — одна настройка "
            "движка, и запускается ровно то, что здесь написано. Где лежит файл, видно в карточке сверху.\n\n"
            "Править пресет можно прямо здесь. Изменения сохраняются сами через секунду, а если этот "
            "пресет сейчас запущен, программа сразу применит их. Открыть это окно можно пунктом "
            "«Открыть» в меню пресета."
        ),
        "en": (
            "Any preset is just a .txt file. Here it is open in full: every line is one engine "
            "setting, and exactly what is written here is what runs. The card at the top shows where "
            "the file is.\n\n"
            "You can edit the preset right here. Changes save on their own after a second, and if "
            "this preset is running, the app applies them right away. You can open this view with "
            "Open in the preset menu."
        ),
    },
    "onboarding.step.preset_header.title": {
        "ru": "Служебные строки",
        "en": "Service lines",
    },
    "onboarding.step.preset_header.body": {
        "ru": (
            "Файл начинается со строк с #. Для движка это комментарии — он их пропускает. Их читает "
            "программа: имя пресета («{name}»), версия встроенного пресета и цвет значка в списке."
        ),
        "en": (
            "The file starts with lines beginning with #. The engine treats them as comments and "
            "skips them. The app reads them: the preset name (\"{name}\"), the built-in preset "
            "version and the icon colour in the list."
        ),
    },
    "onboarding.step.preset_lua_init.title": {
        "ru": "Подключение техник: --lua-init",
        "en": "Loading techniques: --lua-init",
    },
    "onboarding.step.preset_lua_init.body": {
        "ru": (
            "Строки --lua-init подключают Lua-файлы, в которых записаны сами техники обхода — fake, "
            "multisplit и другие. Здесь их {count}.\n\n"
            "Для Zapret 2 этот блок обязателен: без него стратегии не найдут нужных функций. "
            "Трогать его не нужно."
        ),
        "en": (
            "The --lua-init lines load the Lua files that contain the bypass techniques themselves "
            "— fake, multisplit and others. There are {count} of them here.\n\n"
            "This block is required for Zapret 2: without it strategies cannot find their "
            "functions. Leave it as is."
        ),
    },
    "onboarding.step.preset_engine_options.title": {
        "ru": "Общие настройки движка",
        "en": "Engine-wide settings",
    },
    "onboarding.step.preset_engine_options.body": {
        "ru": "Эти настройки действуют на весь пресет сразу, а не на один профиль. Здесь: {lines}.",
        "en": "These settings apply to the whole preset, not to a single profile. Here: {lines}.",
    },
    "onboarding.step.preset_interception.title": {
        "ru": "Какой трафик перехватывается: --wf-…",
        "en": "Which traffic is captured: --wf-…",
    },
    "onboarding.step.preset_interception.body": {
        "ru": (
            "Эти строки решают, какие соединения Windows вообще отдаёт программе. Перехватом "
            "занимается драйвер WinDivert. TCP-порты: {tcp}. UDP-порты: {udp}. Строки --wf-raw-part "
            "добавляют особые случаи, например голосовые звонки Discord.\n\n"
            "Если порта здесь нет, его не увидит ни один профиль ниже."
        ),
        "en": (
            "These lines decide which connections Windows hands to the app at all. Capturing is "
            "done by the WinDivert driver. TCP ports: {tcp}. UDP ports: {udp}. The --wf-raw-part "
            "lines add special cases, such as Discord voice calls.\n\n"
            "If a port is not listed here, no profile below will ever see it."
        ),
    },
    "onboarding.step.preset_blobs.title": {
        "ru": "Фейки: --blob",
        "en": "Fakes: --blob",
    },
    "onboarding.step.preset_blobs.body": {
        "ru": (
            "Здесь объявлены фейки — готовые поддельные пакеты из папки bin, всего {count}. Строка "
            "даёт фейку короткое имя, например {example}, и стратегии зовут его по этому имени.\n\n"
            "Фейк должен быть объявлен здесь: при запуске программа сама ничего не подставляет."
        ),
        "en": (
            "Fakes are declared here — ready-made decoy packets from the bin folder, {count} in "
            "total. Each line gives a fake a short name, such as {example}, and strategies refer to "
            "it by that name.\n\n"
            "A fake must be declared here: the app adds nothing on its own at launch."
        ),
    },
    "onboarding.step.preset_profile.title": {
        "ru": "Профили",
        "en": "Profiles",
    },
    "onboarding.step.preset_profile.body": {
        "ru": (
            "Дальше идут профили — правила: какой трафик и какой стратегией обходить. В этом "
            "пресете их {count}. Разберём один — «{name}», остальные устроены так же."
        ),
        "en": (
            "Profiles come next — rules saying which traffic to handle and with which strategy. "
            "This preset has {count} of them. Let's look at one — \"{name}\"; the rest are built the "
            "same way."
        ),
    },
    "onboarding.step.preset_profile_name.title": {
        "ru": "--name — имя профиля",
        "en": "--name — the profile name",
    },
    "onboarding.step.preset_profile_name.body": {
        "ru": (
            "Так профиль называется в программе: «{name}». Если рядом стоит строка --skip, профиль "
            "выключен и движок его пропускает."
        ),
        "en": (
            "This is how the profile is named in the app: \"{name}\". If there is a --skip line next "
            "to it, the profile is turned off and the engine skips it."
        ),
    },
    "onboarding.step.preset_profile_match.title": {
        "ru": "Когда срабатывает профиль",
        "en": "When the profile applies",
    },
    "onboarding.step.preset_profile_match.body": {
        "ru": (
            "Здесь условия: {match}. --filter-tcp и --filter-udp задают протокол и порты, "
            "--hostlist — список сайтов, --ipset — список IP-адресов. Профиль берёт соединение, "
            "только если подходят все условия сразу."
        ),
        "en": (
            "These are the conditions: {match}. --filter-tcp and --filter-udp set the protocol and "
            "ports, --hostlist is a list of sites, --ipset is a list of IP addresses. The profile "
            "takes a connection only if all conditions match."
        ),
    },
    "onboarding.step.preset_profile_packets.title": {
        "ru": "К каким пакетам применять",
        "en": "Which packets to handle",
    },
    "onboarding.step.preset_profile_packets.body": {
        "ru": (
            "Здесь: {packets}. --payload говорит, какие данные обрабатывать — например, начало "
            "защищённого соединения (tls_client_hello). --out-range и --in-range — какие по счёту "
            "пакеты.\n\n"
            "Порядок важен: эти строки должны стоять перед --lua-desync, к которому относятся."
        ),
        "en": (
            "Here: {packets}. --payload says which data to handle — for example the start of a "
            "secure connection (tls_client_hello). --out-range and --in-range say which packets by "
            "number.\n\n"
            "Order matters: these lines must come before the --lua-desync they apply to."
        ),
    },
    "onboarding.step.preset_profile_strategy.title": {
        "ru": "Стратегия: --lua-desync",
        "en": "The strategy: --lua-desync",
    },
    "onboarding.step.preset_profile_strategy.body": {
        "ru": (
            "Это и есть стратегия: техника «{technique}» и её настройки. Когда вы выбираете для "
            "профиля готовую стратегию, программа меняет именно эти строки. Строк --lua-desync "
            "может быть несколько — тогда техники работают вместе."
        ),
        "en": (
            "This is the strategy itself: the \"{technique}\" technique and its settings. When you "
            "pick a ready strategy for a profile, the app changes exactly these lines. There can be "
            "several --lua-desync lines — then the techniques work together."
        ),
    },
    "onboarding.step.preset_profile_new.title": {
        "ru": "--new — граница профилей",
        "en": "--new — the profile boundary",
    },
    "onboarding.step.preset_profile_new.body": {
        "ru": (
            "Строка --new закрывает профиль и начинает следующий — «{next}». Так до конца файла.\n\n"
            "Порядок профилей важен: если к соединению подходят несколько, сработает верхний."
        ),
        "en": (
            "The --new line closes a profile and starts the next one — \"{next}\". And so on until "
            "the end of the file.\n\n"
            "The order of profiles matters: if several match a connection, the upper one wins."
        ),
    },
    "onboarding.step.presets_toolbar.title": {
        "ru": "Свои пресеты",
        "en": "Your own presets",
    },
    "onboarding.step.presets_toolbar.body": {
        "ru": (
            "Кнопки над списком: создать свой пресет, импортировать его из файла или по ссылке и "
            "открыть папку с файлами пресетов. Пресет по ссылке может обновляться сам, когда автор "
            "его поменяет."
        ),
        "en": (
            "Buttons above the list: create your own preset, import one from a file or a link, and open "
            "the preset folder. A preset imported from a link can update itself when its author "
            "changes it."
        ),
    },
    "onboarding.step.profiles_list.title": {
        "ru": "Что такое профили",
        "en": "What profiles are",
    },
    "onboarding.step.profiles_list.body": {
        "ru": (
            "Это «Профили пресета» — из чего состоит выбранный пресет. Профиль — правило: какой трафик "
            "обрабатывать и какой стратегией. Профили собраны в плитки по сайтам и сервисам: YouTube, "
            "Discord и так далее.\n\n"
            "В готовом пресете уже включено то, что обычно нужно. Включать все профили подряд не надо: "
            "чаще всего достаточно менять стратегию у тех, что уже включены."
        ),
        "en": (
            "This is Preset profiles — what the selected preset is made of. A profile is a rule: which "
            "traffic to handle and with which strategy. Profiles are grouped into tiles by site or "
            "service: YouTube, Discord and so on.\n\n"
            "A ready preset already has what is usually needed turned on. Do not turn every profile "
            "on: most of the time it is enough to change the strategy of the ones that are on."
        ),
    },
    "onboarding.step.profile_group.title": {
        "ru": "Плитка сайта и счётчик «3 из 5»",
        "en": "A site tile and the “3 of 5” counter",
    },
    "onboarding.step.profile_group.body": {
        "ru": (
            "В шапке плитки — название сайта или сервиса. Справа счётчик: «3 из 5» значит, что из пяти "
            "профилей этой группы включены три. Полоска рядом показывает, какие именно: её деления идут "
            "в том же порядке, что строки.\n\n"
            "«3 из 5» — это нормально, а не недоделка. Остальные профили нужны редко, и включать их все "
            "обычно бесполезно.\n\n"
            "Наведите мышь на любой элемент плитки — появится подсказка, за что он отвечает."
        ),
        "en": (
            "The tile header shows the site or service name. The counter on the right: “3 of 5” means "
            "three of the five profiles in this group are on. The bar next to it shows which ones: its "
            "segments follow the order of the rows.\n\n"
            "“3 of 5” is normal, not something unfinished. The other profiles are rarely needed, and "
            "turning all of them on is usually pointless.\n\n"
            "Hover any part of a tile to see a hint about what it does."
        ),
    },
    "onboarding.step.profile_row.title": {
        "ru": "Профиль и его стратегия",
        "en": "A profile and its strategy",
    },
    "onboarding.step.profile_row.body": {
        "ru": (
            "Слева точка: закрашенная — профиль включён и работает, кольцо — не включён. Там, где в "
            "группе собраны разные сервисы, вместо точки стоит значок: цветной — включён, серый — нет. "
            "Дальше название профиля, а справа — выбранная стратегия.\n\n"
            "Нажмите на профиль, чтобы выбрать для него другую готовую стратегию. Если сервис не "
            "открывается, попробуйте несколько стратегий по очереди — какая-то подойдёт вашему "
            "провайдеру."
        ),
        "en": (
            "On the left is a dot: filled means the profile is on and working, a ring means it is off. "
            "Where a group mixes different services, an icon stands there instead: coloured is on, "
            "grey is off. Then comes the profile name, and on the right is the selected strategy.\n\n"
            "Click a profile to choose another ready strategy for it. If a service does not open, try "
            "several strategies one by one — one of them will suit your provider."
        ),
    },
    "onboarding.step.profile_menu.title": {
        "ru": "Что можно делать с профилем",
        "en": "What you can do with a profile",
    },
    "onboarding.step.profile_menu.body": {
        "ru": (
            "Это меню открывается, если нажать на профиль правой кнопкой мыши.\n\n"
            "Открыть — настройки профиля: список, стратегия, диапазоны. Выключить — профиль останется "
            "в пресете, но движок будет его пропускать: в текст пресета добавится строка --skip. "
            "Дублировать — копия профиля, например чтобы попробовать другую стратегию. Удалить из "
            "preset — убрать профиль из файла пресета совсем.\n\n"
            "У профилей, которые вы создали сами, в меню есть ещё пункты, чтобы изменить или удалить "
            "их."
        ),
        "en": (
            "This menu opens when you right-click a profile.\n\n"
            "Open shows the profile settings: list, strategy, ranges. Turn off keeps the profile in "
            "the preset, but the engine skips it: a --skip line is added to the preset text. "
            "Duplicate makes a copy of the profile, for example to try another strategy. Remove from "
            "preset deletes the profile from the preset file.\n\n"
            "Profiles you created yourself also have menu items to edit or delete them."
        ),
    },
    "onboarding.step.profiles_toolbar.title": {
        "ru": "Новые профили",
        "en": "More profiles",
    },
    "onboarding.step.profiles_toolbar.body": {
        "ru": (
            "Здесь можно добавить в пресет ещё профиль, найти нужный поиском и поменять вид списка. "
            "Кнопка «Порядок в пресете» показывает, в каком порядке профили записаны в файле. Если "
            "нужного сервиса нет, отправьте запрос на новый профиль."
        ),
        "en": (
            "Here you can add another profile to the preset, search for one and change the list view. "
            "The Order in preset button shows the order of profiles in the file. If the service you "
            "need is missing, send a request for a new profile."
        ),
    },
    "onboarding.step.profile_order.title": {
        "ru": "Порядок в пресете важен",
        "en": "Order in the preset matters",
    },
    "onboarding.step.profile_order.body": {
        "ru": (
            "Это страница «Порядок в пресете», она открывается кнопкой над списком профилей. Здесь "
            "профили стоят так же, как записаны в файле пресета.\n\n"
            "Движок проверяет профили сверху вниз. Если к одному сайту или IP подходят два профиля, "
            "сработает тот, что выше. Поэтому исключения и узкие правила ставьте выше общих. Чтобы "
            "поменять порядок, перетащите профиль мышью."
        ),
        "en": (
            "This is the Order in preset page, opened with a button above the profile list. Profiles "
            "here are in the same order as in the preset file.\n\n"
            "The engine checks profiles from top to bottom. If two profiles match the same site or "
            "IP, the upper one wins. So put exceptions and narrow rules above general ones. Drag a "
            "profile with the mouse to change the order."
        ),
    },
    "onboarding.step.list_type.title": {
        "ru": "Hostlist или IPset",
        "en": "Hostlist or IPset",
    },
    "onboarding.step.list_type.body": {
        "ru": (
            "Это страница одного профиля. Строка вверху коротко говорит, что он ловит, а кнопка "
            "«Условия» открывает панель, где выбирают тип списка и файл.\n\n"
            "Hostlist — список имён сайтов, например youtube.com. Движок узнаёт сайт по имени, "
            "которое программа сообщает при подключении. Подходит для сайтов и большинства приложений.\n\n"
            "IPset — список IP-адресов и подсетей. Он нужен, когда имени сайта в трафике не видно: "
            "игры, голосовые звонки, некоторые приложения. Минус в том, что на одном адресе бывает "
            "много сайтов, а адреса сервиса могут меняться."
        ),
        "en": (
            "This is the page of a single profile. The line at the top says briefly what it catches, "
            "and the Conditions button opens a panel where you pick the list type and file.\n\n"
            "A hostlist is a list of site names such as youtube.com. The engine recognises the site "
            "by the name an app sends when it connects. It suits sites and most apps.\n\n"
            "An IPset is a list of IP addresses and subnets. It is needed when the site name is not "
            "visible in the traffic: games, voice calls, some apps. The downside is that one address "
            "can host many sites, and a service's addresses can change."
        ),
    },
    "onboarding.step.ranges.title": {
        "ru": "К каким пакетам применять",
        "en": "Which packets to handle",
    },
    "onboarding.step.ranges.body": {
        "ru": (
            "В той же панели «Условия» задают, к каким пакетам соединения применять стратегию: "
            "отдельно для пакетов от вас к сайту (--out-range) и от сайта к вам (--in-range).\n\n"
            "a — всегда, x — никогда, n — первые пакеты, d — первые пакеты с данными. Например, "
            "d и 8 — стратегия работает только на первых 8 пакетах с данными. Блокировка обычно "
            "смотрит на начало соединения, а дальше обработка только нагружает процессор."
        ),
        "en": (
            "The same Conditions panel sets which packets of a connection the strategy handles: "
            "separately for packets from you to the site (--out-range) and from the site to you "
            "(--in-range).\n\n"
            "a means always, x never, n the first packets, d the first packets with data. For "
            "example, d and 8 means the strategy works only on the first 8 data packets. Blocking "
            "usually looks at the start of a connection, and handling the rest only loads the CPU."
        ),
    },
    "onboarding.step.profile_tabs.title": {
        "ru": "Разделы профиля",
        "en": "Profile sections",
    },
    "onboarding.step.profile_tabs.body": {
        "ru": (
            "Страница профиля сразу показывает готовые стратегии: здесь выбирают способ обхода и "
            "отмечают, работает ли он.\n\n"
            "Кнопки в шапке открывают остальные разделы. «Список сайтов» — ваши записи списка сайтов "
            "или адресов. «Текст профиля» — строки профиля так, как они записаны в пресете.\n\n"
            "Открытый раздел появляется в строке пути вверху; по ней же возвращаются к стратегиям."
        ),
        "en": (
            "The profile page shows the ready strategies right away: here you choose the bypass "
            "method and mark whether it works.\n\n"
            "The buttons in the header open the other sections. \"Site list\" holds your entries of "
            "the site or address list. \"Profile text\" shows the profile lines as they are written "
            "in the preset.\n\n"
            "The open section appears in the path line at the top; use it to return to the strategies."
        ),
    },
    "onboarding.scene.you": {"ru": "Вы", "en": "You"},
    "onboarding.scene.provider": {"ru": "Провайдер", "en": "Provider"},
    "onboarding.scene.site": {"ru": "Сайт", "en": "Site"},
    "onboarding.scene.check": {"ru": "проверка", "en": "inspection"},
    "onboarding.scene.junk": {"ru": "мусор", "en": "junk"},
    "onboarding.scene.one_packet": {"ru": "один пакет", "en": "one packet"},
    "onboarding.scene.log.packet": {"ru": "пакет", "en": "packet"},
    "onboarding.scene.log.length": {"ru": "длина", "en": "length"},
    "onboarding.scene.log.gate": {"ru": "проверка провайдера", "en": "provider's inspection"},
    "onboarding.scene.log.site": {"ru": "сайт", "en": "site"},
    "onboarding.scene.log.passed": {"ru": "пропустил", "en": "let through"},
    "onboarding.scene.log.fooled": {"ru": "принял за настоящий", "en": "took for real"},
    "onboarding.scene.log.blocked": {"ru": "узнал имя — блок", "en": "name recognised — blocked"},
    "onboarding.scene.log.accepted": {"ru": "принят", "en": "accepted"},
    "onboarding.scene.log.dropped": {"ru": "отброшен", "en": "dropped"},
    "onboarding.scene.log.not_taken": {"ru": "не принят", "en": "not taken"},
    "onboarding.scene.pause": {"ru": "Остановить анимацию", "en": "Pause the animation"},
    "onboarding.scene.resume": {"ru": "Продолжить анимацию", "en": "Resume the animation"},
    "onboarding.scene.syn_data": {"ru": "SYN + данные", "en": "SYN + data"},
    "onboarding.scene.bubble.blocked": {"ru": "Узнал — блок!", "en": "Recognised — blocked!"},
    "onboarding.scene.bubble.fake": {"ru": "google.com — пропущу", "en": "google.com — let it pass"},
    "onboarding.scene.bubble.unknown": {"ru": "Не узнал…", "en": "Can't tell…"},
    "onboarding.scene.bubble.which_real": {"ru": "Где тут настоящее?", "en": "Which one is real?"},
    "onboarding.scene.bubble.fake_host": {"ru": "abc.ru — пропущу", "en": "abc.ru — let it pass"},
    "onboarding.scene.bubble.junk": {"ru": "Мусор — пропущу", "en": "Junk — let it pass"},
    "onboarding.scene.bubble.odd": {"ru": "Странно, но пропущу", "en": "Odd, but let it pass"},
    "onboarding.step.strategy_try.title": {
        "ru": "Не помогло — следующая",
        "en": "Did not help — next one",
    },
    "onboarding.step.strategy_try.body": {
        "ru": (
            "Панель над списком показывает, какая стратегия выбрана сейчас. Откройте сайт и "
            "проверьте.\n\n"
            "Открылся — нажмите «Работает». Нет — «Не работает — следующая»: программа запомнит "
            "оценку и сразу включит следующую стратегию из советуемых для этого сервиса. Так можно "
            "пройти всю очередь, не разбираясь в названиях.\n\n"
            "Кружок слева от стратегии показывает вашу оценку: пустой — ещё не пробовали, зелёная "
            "галочка — работает, красный крестик — не работает."
        ),
        "en": (
            "The panel above the list shows which strategy is selected now. Open the site and "
            "check.\n\n"
            "If it opened, press \"Works\". If not, press \"Does not work — next\": the program "
            "remembers your mark and immediately switches to the next strategy recommended for this "
            "service. This way you can go through the whole queue without studying the names.\n\n"
            "The circle to the left of a strategy shows your mark: empty — not tried yet, green "
            "check — works, red cross — does not work."
        ),
    },
    "onboarding.step.strategy_find.title": {
        "ru": "Отборы и поиск",
        "en": "Filters and search",
    },
    "onboarding.step.strategy_find.body": {
        "ru": (
            "Кнопки над списком оставляют в нём только нужное: советуемые, отмеченные вами как "
            "рабочие, ещё не опробованные или избранные. Справа выбирается, по чему сгруппировать список.\n\n"
            "Поиск открывается по Ctrl+F и находит стратегию по названию, способу обхода словами "
            "(«нарезка», «подделка»), параметру или автору. Esc его закрывает.\n\n"
            "Метка справа на стратегии говорит, где она стоит в готовых пресетах: «в 13 пресетах» — "
            "на этом же сервисе, «на 28 сервисах» — на других, «у вас работает» — вы сами отмечали её "
            "рабочей на других профилях. Метка — это кнопка: она открывает подробности о стратегии. "
            "Щелчок по названию применяет стратегию.\n\n"
            "Стратегии с одинаковым названием сложены в одну строку, кнопка «ещё 2» раскрывает варианты."
        ),
        "en": (
            "The buttons above the list keep only what you need in it: recommended, marked by you as "
            "working, not tried yet, or favourites. On the right you choose how to group the list.\n\n"
            "Search opens with Ctrl+F and finds a strategy by name, by bypass method in plain words, "
            "by parameter or by author. Esc closes it.\n\n"
            "The badge on the right of a strategy says where the ready-made presets use it: on this "
            "same service or on other services, or that you marked it as working on other profiles. "
            "The badge is a button: it opens the details of the strategy. A click on the name applies "
            "the strategy.\n\n"
            "Strategies with the same name are folded into one row, and the \"more\" button expands "
            "the variants."
        ),
    },
    "onboarding.step.strategy_choice.title": {
        "ru": "Какую стратегию выбрать? Лучшей нет",
        "en": "Which strategy to pick? There is no best one",
    },
    "onboarding.step.strategy_choice.body": {
        "ru": (
            "Универсальной хорошей стратегии не существует: провайдеры блокируют по-разному, и что "
            "помогает одному, у другого не работает.\n\n"
            "Сверху — как обычно работает блокировка: проверка у провайдера читает имя сайта в "
            "первом пакете и обрывает соединение. Стратегия — это набор техник, которые мешают ей "
            "прочитать имя. Где-то помогает нарезка, где-то подсунутый фейк, где-то их сочетание.\n\n"
            "Первой в списке стоит группа «Советуем для этого сервиса»: в ней стратегии, которые в "
            "готовых пресетах уже стоят на этом же сервисе, самые частые выше. С них и стоит начинать. "
            "Ниже остальные стратегии собраны в группы по техникам: не помогла одна группа — "
            "попробуйте другую, а не соседнюю строку.\n\n"
            "Дальше — коротко о главных техниках. Разберитесь, что делает каждая, и оставляйте то, "
            "что работает у вашего провайдера: поменяли стратегию — проверили сайт."
        ),
        "en": (
            "There is no universal good strategy: providers block in different ways, and what helps "
            "one person does not work for another.\n\n"
            "Above is how blocking usually works: the provider's inspection reads the site name in "
            "the first packet and cuts the connection. A strategy is a set of techniques that stop "
            "it from reading the name. Sometimes splitting helps, sometimes a fake, sometimes both.\n\n"
            "The first group in the list is \"Recommended for this service\": strategies that the "
            "ready-made presets already use for this same service, the most frequent on top. Start "
            "with them. Below, the remaining strategies are grouped by technique: if one group did "
            "not help, try another group rather than the next row.\n\n"
            "Next is a short look at the main techniques. Learn what each one does and keep what "
            "works for your provider: change the strategy, then check the site."
        ),
    },
    "onboarding.step.technique_fake.title": {
        "ru": "fake — подсунуть фейк",
        "en": "fake — slip in a decoy",
    },
    "onboarding.step.technique_fake.body": {
        "ru": (
            "Перед настоящим пакетом уходит поддельный — с именем другого, разрешённого сайта. "
            "Поддельный специально «испорчен», поэтому до сайта не доходит, а проверка видит его "
            "первым и пропускает соединение. Следом спокойно проходит настоящий.\n\n"
            "В названиях стратегий: fake."
        ),
        "en": (
            "A fake packet with the name of another, allowed site goes before the real one. The "
            "fake is deliberately \"broken\", so it never reaches the site, but the inspection sees "
            "it first and lets the connection through. The real packet follows.\n\n"
            "In strategy names: fake."
        ),
    },
    "onboarding.step.technique_multisplit.title": {
        "ru": "multisplit — нарезка",
        "en": "multisplit — splitting",
    },
    "onboarding.step.technique_multisplit.body": {
        "ru": (
            "Первый пакет режется на части — например «you» и «tube.com», — и они уходят по "
            "отдельности. Проверка видит обрывки и не узнаёт имя сайта, а сайт сам склеивает части.\n\n"
            "В названиях стратегий: multisplit, split."
        ),
        "en": (
            "The first packet is cut into pieces — for example \"you\" and \"tube.com\" — sent "
            "separately. The inspection sees fragments and does not recognise the site name, while "
            "the site glues the pieces back.\n\n"
            "In strategy names: multisplit, split."
        ),
    },
    "onboarding.step.technique_multidisorder.title": {
        "ru": "multidisorder — нарезка задом наперёд",
        "en": "multidisorder — splitting in reverse",
    },
    "onboarding.step.technique_multidisorder.body": {
        "ru": (
            "Та же нарезка, но части уходят в обратном порядке: сначала вторая, потом первая. "
            "Некоторые проверки ждут части по порядку и сбиваются, а сайт всё равно расставит их по "
            "местам.\n\n"
            "В названиях стратегий: multidisorder, disorder."
        ),
        "en": (
            "The same splitting, but the pieces go in reverse order: the second one first. Some "
            "inspections expect pieces in order and get confused, while the site still puts them "
            "back in place.\n\n"
            "In strategy names: multidisorder, disorder."
        ),
    },
    "onboarding.step.technique_fakedsplit.title": {
        "ru": "fakedsplit — нарезка с фейками",
        "en": "fakedsplit — splitting with fakes",
    },
    "onboarding.step.technique_fakedsplit.body": {
        "ru": (
            "Настоящие части идут вперемешку с поддельными такого же размера. Проверка не может "
            "понять, где настоящие данные, а поддельные до сайта не доходят. fakeddisorder — то же "
            "самое в обратном порядке.\n\n"
            "В названиях стратегий: fakedsplit, fakeddisorder."
        ),
        "en": (
            "Real pieces go mixed with fake ones of the same size. The inspection cannot tell which "
            "data is real, and the fakes never reach the site. fakeddisorder does the same in "
            "reverse order.\n\n"
            "In strategy names: fakedsplit, fakeddisorder."
        ),
    },
    "onboarding.step.technique_hostfakesplit.title": {
        "ru": "hostfakesplit — чужие имена вокруг",
        "en": "hostfakesplit — decoy names around",
    },
    "onboarding.step.technique_hostfakesplit.body": {
        "ru": (
            "Пакет режется точно по границам имени сайта, а вокруг настоящего имени уходят "
            "поддельные. Проверка видит чужие имена, сайт получает только настоящее.\n\n"
            "В названиях стратегий: hostfakesplit."
        ),
        "en": (
            "The packet is cut exactly at the edges of the site name, and fake names are sent "
            "around the real one. The inspection sees other names, the site gets only the real one.\n\n"
            "In strategy names: hostfakesplit."
        ),
    },
    "onboarding.step.technique_tcpseg.title": {
        "ru": "tcpseg — мусор спереди",
        "en": "tcpseg — junk in front",
    },
    "onboarding.step.technique_tcpseg.body": {
        "ru": (
            "Кусок данных уходит отдельным пакетом, а с приёмом seqovl в этот же пакет спереди "
            "приклеивается мусор. Сайт мусор отбрасывает, а проверка может принять его за начало "
            "сообщения и пропустить.\n\n"
            "В готовых стратегиях этот приём встречается как seqovl — например, «multisplit seqovl700»."
        ),
        "en": (
            "A piece of data is sent as a separate packet, and with seqovl some junk is glued to "
            "the front of that same packet. The site drops the junk, while the inspection may take "
            "it for the start of the message and let it pass.\n\n"
            "In ready strategies this trick shows up as seqovl — for example, \"multisplit seqovl700\"."
        ),
    },
    "onboarding.step.technique_oob.title": {
        "ru": "oob — лишний байт",
        "en": "oob — an extra byte",
    },
    "onboarding.step.technique_oob.body": {
        "ru": (
            "В сообщение вставляется один «срочный» байт. Система на стороне сайта его выкидывает, "
            "а проверка видит испорченное имя — например «you#tube.com» — и не узнаёт сайт.\n\n"
            "В готовых стратегиях его пока нет — его можно дописать вручную в тексте профиля."
        ),
        "en": (
            "One \"urgent\" byte is inserted into the message. The system on the site's side throws "
            "it away, while the inspection sees a broken name — for example \"you#tube.com\" — and "
            "does not recognise the site.\n\n"
            "Ready strategies do not use it yet — you can add it by hand in the profile text."
        ),
    },
    "onboarding.step.technique_syndata.title": {
        "ru": "syndata — данные в первом пакете",
        "en": "syndata — data in the first packet",
    },
    "onboarding.step.technique_syndata.body": {
        "ru": (
            "Данные кладутся прямо в самый первый пакет соединения — тот, которым компьютер только "
            "«стучится» к сайту. Это сбивает часть проверок, а соединение устанавливается как "
            "обычно.\n\n"
            "В названиях стратегий: syndata."
        ),
        "en": (
            "Data is put right into the very first packet of the connection — the one the computer "
            "only \"knocks\" with. This confuses some inspections, while the connection is set up as "
            "usual.\n\n"
            "In strategy names: syndata."
        ),
    },
    "onboarding.step.list_entries.title": {
        "ru": "Системные и ваши записи",
        "en": "Built-in and your entries",
    },
    "onboarding.step.list_entries.body": {
        "ru": (
            "Сверху — «База»: системные записи. Они приходят с программой и обновляются вместе с ней.\n\n"
            "Снизу — «Ваши записи». Добавляйте сюда свои сайты или адреса, по одному на строку. Они "
            "лежат в отдельном файле в папке lists/user и не пропадут при обновлении.\n\n"
            "Движок получает общий список: базу плюс ваши записи."
        ),
        "en": (
            "At the top is Base: the built-in entries. They come with the app and are updated with it.\n\n"
            "Below are Your entries. Add your own sites or addresses here, one per line. They are "
            "kept in a separate file in the lists/user folder and survive updates.\n\n"
            "The engine gets one combined list: the base plus your entries."
        ),
    },
    "onboarding.step.fakes.title": {
        "ru": "Фейки",
        "en": "Fakes",
    },
    "onboarding.step.fakes.body": {
        "ru": (
            "Фейк — пакет-обманка, который движок отправляет перед настоящим. Фильтр провайдера "
            "принимает его за начало соединения и пропускает остальное, а сам сайт такой пакет "
            "не получает или отбрасывает.\n\n"
            "Содержимое фейков лежит в .bin-файлах — встроенных и ваших. Здесь их можно посмотреть, "
            "а какие фейки использовать, указано в стратегиях."
        ),
        "en": (
            "A fake is a decoy packet the engine sends before the real one. The provider filter takes it "
            "for the start of the connection and lets the rest through, while the site itself never "
            "gets that packet or drops it.\n\n"
            "Fake contents live in .bin files — built-in and your own. You can view them here; which "
            "fakes to use is set in the strategies."
        ),
    },
    "onboarding.step.dpi_mode.title": {
        "ru": "Режим работы",
        "en": "Operating mode",
    },
    "onboarding.step.dpi_mode.body": {
        "ru": (
            "Здесь выбирается движок:\n"
            "• Zapret 2 (winws2) — основной режим с готовыми пресетами, поддерживает свои стратегии "
            "на Lua;\n"
            "• Zapret 1 (winws) — более старый и простой движок;\n"
            "• Оркестратор — сам подбирает рабочие стратегии для каждого сайта и запоминает удачные.\n\n"
            "Если не знаете, что выбрать, оставьте Zapret 2."
        ),
        "en": (
            "Choose the engine here:\n"
            "• Zapret 2 (winws2) — the main mode with ready presets, supports custom Lua strategies;\n"
            "• Zapret 1 (winws) — an older and simpler engine;\n"
            "• Orchestra — picks working strategies for each site by itself and remembers good ones.\n\n"
            "If unsure, keep Zapret 2."
        ),
    },
    "onboarding.step.program_settings.title": {
        "ru": "Запуск и поведение",
        "en": "Startup and behavior",
    },
    "onboarding.step.program_settings.body": {
        "ru": (
            "Можно запускать программу вместе с Windows, сразу включать обход после её старта и прятать "
            "окно в трей — к значку возле часов. Так обход будет работать сам, без лишних нажатий."
        ),
        "en": (
            "Start the app with Windows, turn the bypass on right after it starts and hide the window to "
            "the tray next to the clock. This way the bypass works on its own."
        ),
    },
    "onboarding.step.tools.title": {
        "ru": "Инструменты",
        "en": "Tools",
    },
    "onboarding.step.tools.body": {
        "ru": (
            "Помощь в особых случаях:\n"
            "• Настройка DNS — сменить DNS-серверы, если провайдер подменяет адреса сайтов;\n"
            "• Редактор hosts — открыть отдельные сервисы через системный файл hosts;\n"
            "• Telegram Proxy — прокси прямо на компьютере, чтобы Telegram работал, когда его замедляют."
        ),
        "en": (
            "Help for special cases:\n"
            "• DNS settings — change DNS servers if your provider spoofs site addresses;\n"
            "• Hosts editor — unblock individual services through the system hosts file;\n"
            "• Telegram Proxy — a proxy right on your computer so Telegram works when it is throttled."
        ),
    },
    "onboarding.step.geo_blocks.title": {
        "ru": "Гео-ограничения стратегиями не обойти",
        "en": "Strategies cannot bypass geo-restrictions",
    },
    "onboarding.step.geo_blocks.body": {
        "ru": (
            "Важно: если сервис пишет «недоступно в вашей стране» или «not available in your region», "
            "это не блокировка провайдера. Сам сервис закрыл доступ для России — так делают ChatGPT, "
            "Gemini и другие ИИ-сервисы, некоторые игры и магазины. Стратегии Zapret такие ограничения "
            "не обходят, сколько их ни перебирай: они помогают только против блокировок провайдера.\n\n"
            "Гео-ограничения обходят по-другому:\n"
            "• Редактор hosts — отметьте сервис, и программа пропишет в файл hosts адреса, через которые "
            "он откроется;\n"
            "• Настройка DNS — в группе «Для ИИ» есть DNS-серверы, которые открывают ChatGPT и похожие "
            "сервисы."
        ),
        "en": (
            "Important: if a service says \"not available in your country\" or \"not available in your "
            "region\", it is not your provider blocking it. The service itself has closed access for "
            "Russia — ChatGPT, Gemini and other AI services, some games and stores do this. Zapret "
            "strategies do not bypass such restrictions no matter how many you try: they only help "
            "against provider blocks.\n\n"
            "Geo-restrictions are bypassed differently:\n"
            "• Hosts editor — tick a service and the app writes addresses to the hosts file through "
            "which it opens;\n"
            "• DNS settings — the For AI group has DNS servers that open ChatGPT and similar services."
        ),
    },
    "onboarding.step.diagnostics.title": {
        "ru": "Диагностика",
        "en": "Diagnostics",
    },
    "onboarding.step.diagnostics.body": {
        "ru": (
            "BlockCheck в один клик проверит, какие сайты блокируются и каким способом, — так проще "
            "понять, что происходит с вашим интернетом. В режиме Zapret 2 здесь же есть разбор "
            "подробного журнала движка."
        ),
        "en": (
            "BlockCheck checks in one click which sites are blocked and how, so it is easier to see what "
            "is happening with your connection. In Zapret 2 mode there is also a detailed engine log "
            "analyzer here."
        ),
    },
    "onboarding.step.appearance.title": {
        "ru": "Оформление и помощь",
        "en": "Appearance and help",
    },
    "onboarding.step.appearance.body": {
        "ru": (
            "Темы и цвета окна, поддержка проекта, логи программы и страница «О программе» с версией и "
            "обновлениями. Если пишете в поддержку, приложите логи — так проблему найдут быстрее."
        ),
        "en": (
            "Window themes and colors, project support, app logs and the About page with version and "
            "updates. If you contact support, attach the logs so the problem is found faster."
        ),
    },
    "onboarding.step.finish.title": {
        "ru": "Всё готово!",
        "en": "All set!",
    },
    "onboarding.step.finish.body": {
        "ru": (
            "Нажмите «Запустить Zapret» и откройте нужный сайт. Не открылся — попробуйте другой пресет "
            "или другую стратегию в профилях.\n\n"
            "Эту экскурсию можно пройти ещё раз: нажмите на эту плитку."
        ),
        "en": (
            "Press Start Zapret and open the site you need. If it does not open, try another preset or "
            "another strategy in the profiles.\n\n"
            "You can take this tour again: just click this tile."
        ),
    },
    "onboarding.step.finish.body_no_target": {
        "ru": (
            "Запустите обход и откройте нужный сайт. Не открылся — попробуйте другой пресет или другую "
            "стратегию в профилях.\n\n"
            "Эту экскурсию можно пройти ещё раз плиткой «Как пользоваться программой» на главной "
            "странице режимов Zapret 1 и Zapret 2."
        ),
        "en": (
            "Start the bypass and open the site you need. If it does not open, try another preset or "
            "another strategy in the profiles.\n\n"
            "You can take this tour again with the “How to use the app” tile on the main page in "
            "Zapret 1 and Zapret 2 modes."
        ),
    },
    "page.control.onboarding_tour.title": {
        "ru": "Как пользоваться программой",
        "en": "How to use the app",
    },
    "page.control.onboarding_tour.desc": {
        "ru": "Пошаговая экскурсия: пресеты, профили и стратегии",
        "en": "A step-by-step tour: presets, profiles and strategies",
    },
    "page.control.onboarding_tour.accessible_name": {
        "ru": "Показать обучающий тур",
        "en": "Show the guided tour",
    },
}

TEXTS.update(TEXTS_ONBOARDING)


NAV_PAGE_TEXT_KEYS: dict[PageName, str] = {
    PageName.ZAPRET2_MODE_CONTROL: "nav.page.zapret2_mode_control",
    PageName.ZAPRET1_MODE_CONTROL: "nav.page.zapret1_mode_control",
    PageName.ORCHESTRA: "nav.page.orchestra",
    PageName.ORCHESTRA_SETTINGS: "nav.page.orchestra_settings",
    PageName.DPI_SETTINGS: "nav.page.dpi_settings",
    PageName.NETWORK: "nav.page.network",
    PageName.NETWORK_CUSTOM_DNS: "page.network.custom_server.title",
    PageName.HOSTS: "nav.page.hosts",
    PageName.HOSTS_FILE: "page.hosts_file.title",
    PageName.BLOCKCHECK: "nav.page.blockcheck",
    PageName.WINWS_LOG_ANALYZER: "nav.page.winws_log_analyzer",
    PageName.APPEARANCE: "nav.page.appearance",
    PageName.PREMIUM: "nav.page.premium",
    PageName.LOGS: "nav.page.logs",
    PageName.SERVERS: "page.servers.title",
    PageName.ABOUT: "nav.page.about",
    PageName.SUPPORT: "page.support.title",
    PageName.TELEGRAM_PROXY_ADVANCED: "page.telegram_proxy_advanced.title",
    PageName.FAKES: "nav.page.fakes",
    PageName.ZAPRET2_PRESET_SETUP: "nav.page.zapret2_mode",
    PageName.ZAPRET2_USER_PRESETS: "nav.page.zapret2_user_presets",
    PageName.ZAPRET2_PROFILE_SETUP: "page.winws2_profile_setup.title",
    PageName.ZAPRET1_PRESET_SETUP: "nav.page.zapret1_mode",
    PageName.ZAPRET1_USER_PRESETS: "nav.page.zapret1_user_presets",
    PageName.ZAPRET1_PROFILE_SETUP: "page.winws1_profile_setup.title",
}


def normalize_language(language: str | None) -> str:
    candidate = (language or DEFAULT_UI_LANGUAGE).strip().lower()
    if candidate in SUPPORTED_UI_LANGUAGES:
        return candidate
    return DEFAULT_UI_LANGUAGE


def tr(key: str, language: str | None = None, default: str | None = None) -> str:
    lang = normalize_language(language)
    values = TEXTS.get(key)
    if not values:
        return default if default is not None else key

    value = values.get(lang)
    if isinstance(value, str) and value:
        return value

    fallback = values.get(DEFAULT_UI_LANGUAGE)
    if isinstance(fallback, str) and fallback:
        return fallback

    for candidate in values.values():
        if isinstance(candidate, str) and candidate:
            return candidate

    return default if default is not None else key


def _text_variants(key: str | None) -> tuple[str, ...]:
    if not key:
        return ()

    values = TEXTS.get(key) or {}
    result: list[str] = []
    for raw in values.values():
        if isinstance(raw, str) and raw:
            result.append(raw)
    return tuple(dict.fromkeys(result))


def get_nav_page_label(page_name: PageName, language: str | None = None, fallback: str | None = None) -> str:
    key = NAV_PAGE_TEXT_KEYS.get(page_name)
    if key is None:
        if fallback is not None:
            return fallback
        return page_name.name
    return tr(key, language=language, default=fallback or page_name.name)
