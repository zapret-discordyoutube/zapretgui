"""Тексты обучающей экскурсии: названия глав и шаги по разделам программы.

Шаги про пресеты, профили и стратегии лежат в ``app.ui_texts`` рядом с
остальными текстами. Здесь — главы экскурсии и шаги, которые ведут по
главной странице, фейкам, инструментам, диагностике и оформлению.

Ключ шага — ``onboarding.step.<ключ шага из ui.onboarding.steps>.title/body``.
"""

from __future__ import annotations


TEXTS_TOUR: dict[str, dict[str, str]] = {
    # ── главы: название стоит в счётчике шагов ──────────────────────────
    "onboarding.chapter.intro": {"ru": "Знакомство", "en": "Getting started"},
    "onboarding.chapter.control": {"ru": "Главная страница", "en": "Main page"},
    "onboarding.chapter.presets": {"ru": "Пресеты", "en": "Presets"},
    "onboarding.chapter.profiles": {"ru": "Профили", "en": "Profiles"},
    "onboarding.chapter.strategies": {"ru": "Профиль и стратегии", "en": "Profile and strategies"},
    "onboarding.chapter.fakes": {"ru": "Фейки", "en": "Fakes"},
    "onboarding.chapter.mode": {"ru": "Режим работы", "en": "Operating mode"},
    "onboarding.chapter.tools": {"ru": "Инструменты", "en": "Tools"},
    "onboarding.chapter.diagnostics": {"ru": "Диагностика", "en": "Diagnostics"},
    "onboarding.chapter.appearance": {"ru": "Оформление и помощь", "en": "Appearance and help"},
    "onboarding.chapter.finish": {"ru": "Финиш", "en": "Finish"},
    # Подсказка у отрезка главы в строке глав на карточке.
    "onboarding.chapter.hint": {"ru": "{chapter} · шагов: {count}", "en": "{chapter} · steps: {count}"},
    # ── главная страница ────────────────────────────────────────────────
    "onboarding.step.quick_actions.title": {"ru": "Быстрые действия", "en": "Quick actions"},
    "onboarding.step.quick_actions.body": {
        "ru": (
            "Плитки под сводкой — то, что нужно чаще всего:\n"
            "• «Как пользоваться программой» — эта экскурсия;\n"
            "• «Тест соединения» — открывает BlockCheck: проверку, какие сайты открываются;\n"
            "• «Сбросить сеть Windows» — сбрасывает TCP/IP, кэш DNS, прокси и Winsock, если интернет ведёт себя "
            "странно. После этого понадобится перезагрузка;\n"
            "• «Открыть папку» — папка программы с пресетами, списками и логами;\n"
            "• «Документация» — вики с подробными инструкциями."
        ),
        "en": (
            "The tiles under the summary are what you need most often:\n"
            "• How to use the app — this tour;\n"
            "• Connection test — opens BlockCheck, which checks which sites open;\n"
            "• Reset Windows network — resets TCP/IP, the DNS cache, proxy and Winsock when the connection "
            "behaves oddly. A reboot is needed afterwards;\n"
            "• Open folder — the app folder with presets, lists and logs;\n"
            "• Documentation — the wiki with detailed guides."
        ),
    },
    "onboarding.step.windows_settings.title": {"ru": "Windows и блокировки", "en": "Windows and blocks"},
    "onboarding.step.windows_settings.body": {
        "ru": (
            "Три выключателя, которые к самому обходу не относятся:\n"
            "• «Отключить Windows Defender» — если встроенный антивирус мешает движку. Нужны права "
            "администратора;\n"
            "• «Блокировать установку MAX» — запрещает запуск и установку мессенджера MAX и закрывает его "
            "адреса через файл hosts;\n"
            "• «Блокировать государственные СМИ РФ» — добавляет в hosts список государственных новостных "
            "сайтов.\n\n"
            "Каждый включается отдельно и так же выключается."
        ),
        "en": (
            "Three switches that are not about the bypass itself:\n"
            "• Disable Windows Defender — if the built-in antivirus gets in the engine's way. Needs "
            "administrator rights;\n"
            "• Block MAX installation — forbids running and installing the MAX messenger and closes its "
            "addresses through the hosts file;\n"
            "• Block Russian state media — adds a list of state news sites to hosts.\n\n"
            "Each one is turned on and off separately."
        ),
    },
    "onboarding.step.fine_tuning.title": {"ru": "Тонкая настройка обхода", "en": "Fine tuning"},
    "onboarding.step.fine_tuning.body": {
        "ru": (
            "Параметры для опытных — менять их стоит, только если понимаете зачем:\n"
            "• «Включить --wssize» — добавляет движку параметр размера окна TCP;\n"
            "• «Включить лог-файл (--debug)» — движок пишет подробный журнал в папку logs. Он нужен, "
            "чтобы разобраться, почему стратегия не срабатывает, и для обращения в поддержку.\n\n"
            "В режиме Zapret 2 здесь же вход в «Фейки» — к ним экскурсия ещё вернётся."
        ),
        "en": (
            "Settings for advanced users — change them only if you know why:\n"
            "• Enable --wssize — adds the TCP window size option to the engine;\n"
            "• Enable log file (--debug) — the engine writes a detailed log to the logs folder. It is "
            "needed to find out why a strategy does not work and for a support request.\n\n"
            "In Zapret 2 mode the entry to Fakes is here too — the tour will come back to them."
        ),
    },
    # ── страница профиля: подробности о стратегии и гео-сервисы ─────────
    "onboarding.step.strategy_details.title": {"ru": "Подробности о стратегии", "en": "Strategy details"},
    "onboarding.step.strategy_details.body": {
        "ru": (
            "Выбирать вслепую не обязательно. Клавиша F1 на строке стратегии или пункт меню по правой "
            "кнопке открывают её разбор — экскурсия открыла пример.\n\n"
            "Схема показывает, что стратегия делает с пакетами. Под ней шаги по порядку: что происходит "
            "и зачем. Нажмите на шаг — схема покажет именно его.\n\n"
            "Назад к списку — клавиша Esc или строка пути сверху."
        ),
        "en": (
            "You do not have to choose blindly. The F1 key on a strategy row or the right-click menu "
            "item opens its breakdown — the tour opened an example.\n\n"
            "The scheme shows what the strategy does with packets. Under it are the steps in order: what "
            "happens and why. Click a step and the scheme shows exactly that one.\n\n"
            "Esc or the path row at the top leads back to the list."
        ),
    },
    "onboarding.step.strategy_details_places.title": {"ru": "Где стратегия уже стоит", "en": "Where the strategy is used"},
    "onboarding.step.strategy_details_places.body": {
        "ru": (
            "Карточки показывают, на каких сервисах и в скольких готовых пресетах эта стратегия стоит: "
            "чем их больше, тем она проверенней. Нажатие на карточку открывает профиль этого сервиса.\n\n"
            "Ниже на странице — ваш опыт с этой стратегией, сведения о ней и строки запуска: ровно то, "
            "что запишется в пресет."
        ),
        "en": (
            "The cards show on which services and in how many ready presets this strategy is used: the "
            "more of them, the better it is proven. Clicking a card opens the profile of that service.\n\n"
            "Further down the page are your experience with this strategy, facts about it and the launch "
            "lines: exactly what is written to the preset."
        ),
    },
    "onboarding.step.profile_geo_notice.title": {"ru": "Сервис закрыт для России", "en": "Service closed for Russia"},
    "onboarding.step.profile_geo_notice.body": {
        "ru": (
            "Такая карточка всплывает в углу страницы, если профиль относится к сервису, который сам не "
            "пускает посетителей из России, — Gemini, ChatGPT, Claude и другим. Экскурсия показывает её "
            "на примере.\n\n"
            "Перебирать стратегии для такого сервиса бесполезно: блокирует не провайдер. Кнопки на карточке "
            "ведут туда, где это решается, — на страницы «Редактор hosts» и «Настройка DNS»."
        ),
        "en": (
            "Such a card pops up in the corner of the page when the profile belongs to a service that "
            "itself does not let visitors from Russia in — Gemini, ChatGPT, Claude and others. The tour "
            "shows it as an example.\n\n"
            "Trying strategies for such a service is useless: it is not your provider blocking it. The "
            "buttons on the card lead to where it is solved — the Hosts editor and DNS settings."
        ),
    },
    # ── фейки ───────────────────────────────────────────────────────────
    "onboarding.step.fakes_table.title": {"ru": "Реестр фейков", "en": "Fake registry"},
    "onboarding.step.fakes_table.body": {
        "ru": (
            "Здесь собраны все фейки, которые знает программа: встроенные и ваши. По столбцам видно имя, "
            "файл, тип — под какой протокол подделка, — имя сайта внутри (SNI) и сколько готовых стратегий "
            "этим фейком пользуются.\n\n"
            "Поиск над таблицей ищет по любому из этих полей."
        ),
        "en": (
            "All fakes the app knows are here: built-in and your own. The columns show the name, the "
            "file, the type — which protocol it imitates — the site name inside (SNI) and how many ready "
            "strategies use this fake.\n\n"
            "The search above the table looks in any of these fields."
        ),
    },
    "onboarding.step.fakes_blob.title": {"ru": "Строка для пресета", "en": "Line for the preset"},
    "onboarding.step.fakes_blob.body": {
        "ru": (
            "Выберите фейк в таблице — здесь появится готовая строка --blob= для него. Экскурсия выбрала "
            "первый фейк для примера.\n\n"
            "Фейк должен быть объявлен в пресете явно, такой строкой. Обычно вписывать её вручную не нужно: "
            "когда вы выбираете готовую стратегию, программа сама дописывает строки --blob= для нужных ей "
            "фейков. Строка пригодится, если вы правите текст пресета сами."
        ),
        "en": (
            "Pick a fake in the table and the ready --blob= line for it appears here. The tour picked the "
            "first fake as an example.\n\n"
            "A fake must be declared in the preset explicitly, with such a line. Usually you do not need "
            "to type it by hand: when you choose a ready strategy, the app adds the --blob= lines for the "
            "fakes it needs. The line is useful when you edit the preset text yourself."
        ),
    },
    "onboarding.step.fakes_own.title": {"ru": "Свои фейки", "en": "Your own fakes"},
    "onboarding.step.fakes_own.body": {
        "ru": (
            "«Добавить свой фейк…» — выберите .bin-файл и дайте ему имя: по этому имени на него будут "
            "ссылаться стратегии. Файлы своих фейков лежат в папке user/fakes и не пропадают при обновлении "
            "программы.\n\n"
            "Удалить можно только свой фейк — встроенные остаются всегда."
        ),
        "en": (
            "Add your own fake… — choose a .bin file and give it a name: strategies will refer to it by "
            "this name. Your fake files are kept in the user/fakes folder and survive app updates.\n\n"
            "Only your own fake can be deleted — built-in ones always stay."
        ),
    },
    # ── режим работы ────────────────────────────────────────────────────
    "onboarding.step.dpi_modes.title": {"ru": "Три режима", "en": "Three modes"},
    "onboarding.step.dpi_modes.body": {
        "ru": (
            "Нажатие на карточку сразу переключает программу на этот режим: меняются главная страница и "
            "пункты меню. Пресеты у каждого движка свои — при переключении они не теряются.\n\n"
            "Zapret 2 отмечен как рекомендуемый. Оркестратор пока в бета-версии."
        ),
        "en": (
            "Clicking a card switches the app to that mode right away: the main page and the menu items "
            "change. Each engine has its own presets — they are not lost when you switch.\n\n"
            "Zapret 2 is marked as recommended. Orchestra is still in beta."
        ),
    },
    # ── инструменты: DNS ────────────────────────────────────────────────
    "onboarding.step.dns_now.title": {"ru": "Какой DNS сейчас", "en": "Current DNS"},
    "onboarding.step.dns_now.body": {
        "ru": (
            "DNS — служба, которая по имени сайта находит его адрес. Провайдер может подменять её ответы, "
            "и тогда сайт не открывается, даже если обход работает.\n\n"
            "Карточка показывает, какой DNS стоит сейчас. В строке «Применять к» отмечаются сетевые "
            "адаптеры — обычно это ваш Wi‑Fi или кабель. «Замерить скорость» сравнит серверы по времени "
            "ответа, «Сбросить кэш DNS» заставит Windows забыть старые адреса."
        ),
        "en": (
            "DNS is the service that finds a site's address by its name. Your provider may spoof its "
            "answers, and then a site does not open even when the bypass works.\n\n"
            "The card shows which DNS is set now. The Apply to row marks the network adapters — usually "
            "your Wi‑Fi or cable. Measure speed compares servers by response time, Flush DNS cache makes "
            "Windows forget old addresses."
        ),
    },
    "onboarding.step.dns_providers.title": {"ru": "Выбор сервера", "en": "Choosing a server"},
    "onboarding.step.dns_providers.body": {
        "ru": (
            "Нажмите на плитку — этот DNS сразу встанет на отмеченные адаптеры. «Автоматически» возвращает "
            "тот, что выдаёт роутер или провайдер.\n\n"
            "Вкладки над плитками делят серверы на группы. «Шифрованные» — запросы шифрует встроенный "
            "dnscrypt-proxy, их не подменить. «Популярные» и «Безопасные» — известные публичные серверы. "
            "«Малоизвестные» реже попадают под блокировки. Не знаете, что выбрать, — возьмите Quad9 из "
            "группы «Безопасные»."
        ),
        "en": (
            "Click a tile and that DNS is set on the marked adapters right away. Automatic brings back "
            "the one your router or provider gives.\n\n"
            "The tabs above the tiles split servers into groups. Encrypted — requests are encrypted by "
            "the built-in dnscrypt-proxy and cannot be spoofed. Popular and Secure are well-known public "
            "servers. Lesser-known ones are blocked less often. If unsure, take Quad9 from Secure."
        ),
    },
    "onboarding.step.dns_ai.title": {"ru": "DNS для ИИ-сервисов", "en": "DNS for AI services"},
    "onboarding.step.dns_ai.body": {
        "ru": (
            "Экскурсия открыла группу «Для ИИ». Эти серверы отдают для ChatGPT, Gemini и похожих сервисов "
            "особые адреса, через которые те открываются из России. Стратегии Zapret тут не помогут: "
            "доступ закрыл не провайдер, а сам сервис.\n\n"
            "Серверы держит сообщество, поэтому доверия к ним меньше, чем к крупным. Если нужен один-два "
            "сервиса, удобнее «Редактор hosts» — о нём чуть дальше."
        ),
        "en": (
            "The tour opened the For AI group. These servers return special addresses for ChatGPT, Gemini "
            "and similar services, through which they open from Russia. Zapret strategies do not help "
            "here: access is closed by the service itself, not by your provider.\n\n"
            "The servers are run by the community, so they are trusted less than the big ones. If you "
            "need one or two services, the Hosts editor is handier — more on it shortly."
        ),
    },
    "onboarding.step.dns_custom.title": {"ru": "Свой DNS", "en": "Your own DNS"},
    "onboarding.step.dns_custom.body": {
        "ru": (
            "Сюда ведёт плитка «Добавить» в группе «Свои DNS». Достаточно одной строки — адреса DoH вида "
            "https://dns.example.com/dns-query: IP-адреса сервера программа найдёт и проверит сама. Можно "
            "и наоборот — вписать только IP-адреса. Название подставится само, его можно поменять.\n\n"
            "Сохранённый сервер появится плиткой среди остальных."
        ),
        "en": (
            "The Add tile in the Custom DNS group leads here. One line is enough — a DoH address like "
            "https://dns.example.com/dns-query: the app finds and checks the server's IP addresses by "
            "itself. Or the other way round — enter only IP addresses. The name is filled in "
            "automatically and can be changed.\n\n"
            "The saved server appears as a tile among the others."
        ),
    },
    # ── инструменты: hosts ──────────────────────────────────────────────
    "onboarding.step.hosts_summary.title": {"ru": "Редактор hosts", "en": "Hosts editor"},
    "onboarding.step.hosts_summary.body": {
        "ru": (
            "Файл hosts — список Windows «имя сайта → адрес». То, что в нём записано, важнее ответа DNS. "
            "Программа вписывает туда адреса, через которые открываются нужные сервисы.\n\n"
            "Сводка показывает, сколько сервисов включено и сколько строк программа держит в файле. Кнопки "
            "сверху: «Файл hosts» открывает файл целиком, «DNS для всех» ставит один DNS-профиль сразу "
            "всем сервисам, «Выключить все» убирает строки программы."
        ),
        "en": (
            "The hosts file is the Windows list of \"site name → address\". What is written there beats "
            "the DNS answer. The app writes addresses there through which the services you need open.\n\n"
            "The summary shows how many services are on and how many lines the app keeps in the file. "
            "The buttons at the top: Hosts file opens the whole file, DNS for all sets one DNS profile "
            "for all services at once, Turn all off removes the app's lines."
        ),
    },
    "onboarding.step.hosts_direct.title": {"ru": "Сервисы «напрямую»", "en": "Direct services"},
    "onboarding.step.hosts_direct.body": {
        "ru": (
            "В группе «Напрямую» у каждой плитки выключатель: включили — адрес сервиса записан в hosts "
            "как есть. Запись идёт сразу, кнопки «Сохранить» нет.\n\n"
            "Счётчик в заголовке группы — сколько сервисов включено. Поиск по сервисам открывается по Ctrl+F."
        ),
        "en": (
            "In the Direct group every tile has a switch: turn it on and the service address is written "
            "to hosts as is. It is written right away, there is no Save button.\n\n"
            "The counter in the group header shows how many services are on. Ctrl+F opens the search."
        ),
    },
    "onboarding.step.hosts_ai.title": {"ru": "ИИ-сервисы и DNS-профили", "en": "AI services and DNS profiles"},
    "onboarding.step.hosts_ai.body": {
        "ru": (
            "У плиток в группах «ИИ-сервисы» и «Остальные сервисы» вместо выключателя ряд значков. Это "
            "DNS-профили: чьи адреса вписать для сервиса. Нажмите на значок — адреса от этого провайдера "
            "запишутся в hosts, нажмите ещё раз — уберутся. Какой значок чей, подписано справа в заголовке "
            "группы.\n\n"
            "Если с одним профилем сервис не открылся, попробуйте другой."
        ),
        "en": (
            "Tiles in the AI services and Other services groups have a row of icons instead of a switch. "
            "These are DNS profiles: whose addresses to write for the service. Click an icon and addresses "
            "from that provider go to hosts, click again and they are removed. The legend on the right of "
            "the group header says which icon is whose.\n\n"
            "If a service does not open with one profile, try another."
        ),
    },
    "onboarding.step.hosts_file.title": {"ru": "Весь файл hosts", "en": "The whole hosts file"},
    "onboarding.step.hosts_file.body": {
        "ru": (
            "Здесь файл hosts целиком. Строки раскрашены по тому, кто их добавил: ZapretGUI, Telegram Proxy, "
            "блокировки MAX и госСМИ, Adobe и ваши собственные. Рядом с каждым цветом — число строк.\n\n"
            "Файл можно править прямо тут и сохранить, а «Отменить правки» вернёт текст как был. Поиск — "
            "по Ctrl+F."
        ),
        "en": (
            "This is the whole hosts file. Lines are coloured by who added them: ZapretGUI, Telegram Proxy, "
            "the MAX and state media blocks, Adobe and your own. Next to each colour is the line count.\n\n"
            "You can edit the file right here and save it; Revert brings the text back. Ctrl+F searches."
        ),
    },
    # ── инструменты: Telegram Proxy ─────────────────────────────────────
    "onboarding.step.telegram_status.title": {"ru": "Telegram Proxy", "en": "Telegram Proxy"},
    "onboarding.step.telegram_status.body": {
        "ru": (
            "Это небольшой прокси, который работает прямо на вашем компьютере. Telegram подключается к нему, "
            "а он доводит соединение до серверов Telegram обходным путём — так мессенджер работает, когда "
            "его замедляют или блокируют.\n\n"
            "Кружок и подпись показывают, запущен ли прокси. Кнопка справа запускает и останавливает его."
        ),
        "en": (
            "This is a small proxy that runs right on your computer. Telegram connects to it, and it "
            "carries the connection to Telegram servers by a roundabout path — so the messenger works "
            "when it is throttled or blocked.\n\n"
            "The dot and the caption show whether the proxy is running. The button on the right starts "
            "and stops it."
        ),
    },
    "onboarding.step.telegram_connect.title": {"ru": "Подключить Telegram", "en": "Connect Telegram"},
    "onboarding.step.telegram_connect.body": {
        "ru": (
            "Прокси мало запустить — о нём должен узнать сам Telegram. «Открыть» передаёт ему настройки "
            "одной ссылкой: Telegram предложит добавить прокси, согласитесь. «Копировать» кладёт ту же "
            "ссылку в буфер обмена, если Telegram не открылся сам.\n\n"
            "ZaStoGram — запасной вариант: Telegram Desktop для сетей с блокировками, который обходит их "
            "сам, без прокси."
        ),
        "en": (
            "Starting the proxy is not enough — Telegram itself has to learn about it. Open hands it the "
            "settings in one link: Telegram offers to add the proxy, accept it. Copy puts the same link "
            "on the clipboard if Telegram did not open by itself.\n\n"
            "ZaStoGram is a fallback: Telegram Desktop for blocked networks that bypasses blocks by "
            "itself, without a proxy."
        ),
    },
    "onboarding.step.telegram_settings.title": {"ru": "Основные настройки", "en": "Basic settings"},
    "onboarding.step.telegram_settings.body": {
        "ru": (
            "Адрес и порт, на которых прокси ждёт подключений, — менять их обычно не нужно. Режим: SOCKS5 "
            "подходит почти всем, MTProxy — для тех, кому нужен secret и Fake TLS. «Авто-настройка "
            "Telegram» сама открывает ссылку в Telegram при первом запуске прокси.\n\n"
            "Последняя строка ведёт в «Продвинутые настройки» — к ним зайдём через пару шагов."
        ),
        "en": (
            "The address and port the proxy listens on — usually there is no need to change them. Mode: "
            "SOCKS5 suits almost everyone, MTProxy is for those who need a secret and Fake TLS. Telegram "
            "auto-setup opens the link in Telegram on the first proxy start.\n\n"
            "The last row leads to Advanced settings — we will visit them in a couple of steps."
        ),
    },
    "onboarding.step.telegram_hosts.title": {"ru": "Сайты Telegram в hosts", "en": "Telegram sites in hosts"},
    "onboarding.step.telegram_hosts.body": {
        "ru": (
            "Отдельная помощь для сайтов Telegram в браузере — web.telegram.org и t.me. «Прописать» "
            "добавляет их адреса в файл hosts, «Убрать» — удаляет.\n\n"
            "Самому прокси эти записи не нужны: приложение Telegram работает и без них."
        ),
        "en": (
            "Separate help for Telegram sites in the browser — web.telegram.org and t.me. Add writes "
            "their addresses to the hosts file, Remove deletes them.\n\n"
            "The proxy itself does not need these entries: the Telegram app works without them."
        ),
    },
    "onboarding.step.telegram_logs.title": {"ru": "Логи и диагностика", "en": "Logs and diagnostics"},
    "onboarding.step.telegram_logs.body": {
        "ru": (
            "Вкладка «Логи» показывает, что делает прокси: какие подключения пришли и каким путём ушли. "
            "Лог можно скопировать или открыть файлом — пригодится для поддержки.\n\n"
            "На соседней вкладке «Диагностика» одна кнопка проверяет, доступны ли серверы Telegram "
            "с вашего компьютера."
        ),
        "en": (
            "The Logs tab shows what the proxy is doing: which connections came in and which way they "
            "went. The log can be copied or opened as a file — handy for support.\n\n"
            "On the next tab, Diagnostics, one button checks whether Telegram servers are reachable from "
            "your computer."
        ),
    },
    "onboarding.step.telegram_advanced.title": {"ru": "Внешний прокси", "en": "External proxy"},
    "onboarding.step.telegram_advanced.body": {
        "ru": (
            "Продвинутые настройки нужны, когда обычного пути к Telegram нет. Первая группа — внешний "
            "прокси: Telegram Proxy может отправлять трафик через другой SOCKS5-сервер, свой или из "
            "готового списка. Включите выключатель — появятся поля адреса, порта и логина."
        ),
        "en": (
            "Advanced settings are for when the usual path to Telegram is gone. The first group is the "
            "external proxy: Telegram Proxy can send traffic through another SOCKS5 server, your own or "
            "one from the ready list. Turn the switch on and the address, port and login fields appear."
        ),
    },
    "onboarding.step.telegram_cloudflare.title": {"ru": "Cloudflare", "en": "Cloudflare"},
    "onboarding.step.telegram_cloudflare.body": {
        "ru": (
            "Ещё один запасной путь — через сеть Cloudflare: свой домен или Worker. Кнопка «Проверить» "
            "покажет, работает ли он.\n\n"
            "Ниже, в группе «Сеть», — тонкие параметры соединения. Без нужды их лучше не трогать."
        ),
        "en": (
            "One more fallback path goes through the Cloudflare network: your own domain or a Worker. "
            "The Check button shows whether it works.\n\n"
            "Below, in the Network group, are low-level connection settings. Better leave them alone "
            "unless you need them."
        ),
    },
    # ── диагностика: BlockCheck ─────────────────────────────────────────
    "onboarding.step.blockcheck_start.title": {"ru": "BlockCheck: запуск проверки", "en": "BlockCheck: starting a check"},
    "onboarding.step.blockcheck_start.body": {
        "ru": (
            "BlockCheck сам открывает сайты и смотрит, что с ними происходит. Выберите, что проверить: "
            "только Discord и YouTube, только сайты или полную проверку — с хостингами, DNS, звонками и "
            "самим компьютером, около минуты. Затем нажмите «Проверить».\n\n"
            "Проверять полезно дважды: с выключенным Zapret — чтобы увидеть, что блокируется, и "
            "с включённым — чтобы убедиться, что обход помог."
        ),
        "en": (
            "BlockCheck opens sites by itself and watches what happens to them. Choose what to check: "
            "only Discord and YouTube, only sites, or the full check — with hostings, DNS, calls and the "
            "computer itself, about a minute. Then press Check.\n\n"
            "It is useful to check twice: with Zapret off — to see what is blocked, and with it on — to "
            "make sure the bypass helped."
        ),
    },
    "onboarding.step.blockcheck_domains.title": {"ru": "Свои домены", "en": "Your own domains"},
    "onboarding.step.blockcheck_domains.body": {
        "ru": (
            "Нужного сайта нет в проверке — впишите его адрес и нажмите «Добавить». Домен запомнится и "
            "будет проверяться вместе с остальными, пока вы его не уберёте."
        ),
        "en": (
            "If the site you need is not in the check, type its address and press Add. The domain is "
            "remembered and checked with the others until you remove it."
        ),
    },
    "onboarding.step.blockcheck_summary.title": {"ru": "Итог проверки", "en": "Check summary"},
    "onboarding.step.blockcheck_summary.body": {
        "ru": (
            "Проверок у вас могло ещё не быть, поэтому экскурсия показывает пример. После неё он исчезнет.\n\n"
            "Вверху главный вывод и условия: был ли включён Zapret. Полоса и плитки под ней делят сайты "
            "по виду блокировки. Ниже — группы проблем: что это за блокировка простыми словами и что "
            "с ней делать. В примере Discord и X закрыты по имени сайта — это лечится стратегией, — "
            "а у YouTube закрыт только QUIC."
        ),
        "en": (
            "You may not have run any checks yet, so the tour shows an example. It disappears afterwards.\n\n"
            "At the top is the main conclusion and the conditions: whether Zapret was on. The bar and the "
            "tiles under it split sites by the kind of block. Below are problem groups: what this block "
            "is in plain words and what to do about it. In the example Discord and X are blocked by site "
            "name — a strategy fixes that — and YouTube only has QUIC closed."
        ),
    },
    "onboarding.step.blockcheck_cards.title": {"ru": "Карточки сайтов", "en": "Site cards"},
    "onboarding.step.blockcheck_cards.body": {
        "ru": (
            "Числа сверху — сколько всего проверено. Под ними карточка на каждый сайт: кружок и подпись — "
            "открывается ли он; строки — отдельные адреса сайта: сам сайт, картинки, видео; метки внизу — "
            "что прошло и что нет: TLS 1.2, TLS 1.3, HTTP, QUIC.\n\n"
            "Google и Яндекс — контрольные: если не открываются даже они, дело не в блокировке, а в самом "
            "интернете."
        ),
        "en": (
            "The numbers at the top show how much was checked. Under them is a card per site: the dot "
            "and the caption say whether it opens; the lines are separate addresses of the site: the "
            "site itself, images, video; the chips at the bottom say what passed and what did not: "
            "TLS 1.2, TLS 1.3, HTTP, QUIC.\n\n"
            "Google and Yandex are control sites: if even they do not open, the problem is the "
            "connection itself, not a block."
        ),
    },
    "onboarding.step.blockcheck_checks.title": {"ru": "Сеть и компьютер", "en": "Network and computer"},
    "onboarding.step.blockcheck_checks.body": {
        "ru": (
            "Вторая группа карточек — про всё, кроме сайтов: обрывается ли загрузка с зарубежных хостингов, "
            "отвечают ли голосовые серверы, не подменяет ли DNS адреса, есть ли IPv6, между какими узлами "
            "по дороге стоит фильтр и всё ли в порядке на самом компьютере — права администратора, службы "
            "Windows, системный прокси."
        ),
        "en": (
            "The second group of cards is about everything except sites: whether downloads from foreign "
            "hostings break off, whether voice servers answer, whether DNS spoofs addresses, whether "
            "there is IPv6, between which hops on the way the filter sits, and whether the computer "
            "itself is fine — administrator rights, Windows services, system proxy."
        ),
    },
    "onboarding.step.blockcheck_card_detail.title": {"ru": "Подробности карточки", "en": "Card details"},
    "onboarding.step.blockcheck_card_detail.body": {
        "ru": (
            "Нажатие на любую карточку открывает все измерения по ней — экскурсия открыла Discord. "
            "По каждому адресу видно, чем кончилось соединение, какие адреса пробовали, что с TLS 1.2, "
            "TLS 1.3 и HTTP и как именно блокируют. Внизу — «Что делать».\n\n"
            "Назад ведёт строка пути сверху или клавиша Esc. «Скопировать» кладёт отчёт в буфер обмена."
        ),
        "en": (
            "Clicking any card opens all measurements for it — the tour opened Discord. For each address "
            "you see how the connection ended, which addresses were tried, what happened with TLS 1.2, "
            "TLS 1.3 and HTTP and how exactly it is blocked. At the bottom is What to do.\n\n"
            "The path row at the top or the Esc key leads back. Copy puts the report on the clipboard."
        ),
    },
    "onboarding.step.blockcheck_history.title": {"ru": "Прошлые проверки", "en": "Past checks"},
    "onboarding.step.blockcheck_history.body": {
        "ru": (
            "Каждая проверка запоминается. В таблице видно, когда она была, что проверяли, сколько сайтов "
            "открылось и что изменилось с прошлого раза — например, «Снова открываются: Discord». Так "
            "удобно сравнивать «до» и «после» смены стратегии.\n\n"
            "В примере четыре записи. Сразу видны последние шесть, остальные — по кнопке «Показать все»; "
            "всего хранится до 50."
        ),
        "en": (
            "Every check is remembered. The table shows when it ran, what was checked, how many sites "
            "opened and what changed since the last time — for example, \"Open again: Discord\". This "
            "makes it easy to compare before and after changing a strategy.\n\n"
            "The example has four records. The last six are visible at once, the rest open with Show "
            "all; up to 50 are kept."
        ),
    },
    "onboarding.step.blockcheck_past_check.title": {"ru": "Проверка из прошлого", "en": "A check from the past"},
    "onboarding.step.blockcheck_past_check.body": {
        "ru": (
            "Нажатие на строку таблицы открывает ту проверку целиком: тот же итог и те же карточки, что "
            "были тогда. Карточки тоже открываются.\n\n"
            "Вернуться — по слову BlockCheck в строке пути или клавишей Esc."
        ),
        "en": (
            "Clicking a table row opens that check in full: the same summary and the same cards as back "
            "then. The cards open too.\n\n"
            "To return, click BlockCheck in the path row or press Esc."
        ),
    },
    "onboarding.step.blockcheck_report.title": {"ru": "Отчёт и обращение", "en": "Report and support request"},
    "onboarding.step.blockcheck_report.body": {
        "ru": (
            "После проверки внизу появляются две кнопки. «Отчёт» — технические подробности: адреса, "
            "ответы DNS, время ответа серверов. «Подготовить обращение» собирает логи проверки для "
            "поддержки — на случай, если разобраться самому не вышло."
        ),
        "en": (
            "After a check two buttons appear at the bottom. Report gives technical details: addresses, "
            "DNS answers, server response times. Prepare request collects the check logs for support — "
            "in case you could not sort it out yourself."
        ),
    },
    "onboarding.step.blockcheck_tabs.title": {"ru": "Ещё четыре инструмента", "en": "Four more tools"},
    "onboarding.step.blockcheck_tabs.body": {
        "ru": (
            "BlockCheck — первая из пяти вкладок. Остальные решают отдельные задачи: подобрать рабочую "
            "стратегию, изучить один домен, проверить DNS-серверы и найти подмену DNS. Пройдём по ним."
        ),
        "en": (
            "BlockCheck is the first of five tabs. The others solve separate tasks: find a working "
            "strategy, study one domain, check DNS servers and find DNS spoofing. Let's walk through them."
        ),
    },
    "onboarding.step.strategy_scan.title": {"ru": "Подбор стратегии", "en": "Strategy search"},
    "onboarding.step.strategy_scan.body": {
        "ru": (
            "Здесь программа сама ищет стратегию, которая откроет нужный сайт. Выберите, что должно "
            "заработать: сайты и приложения, голосовые звонки или онлайн-игры. Впишите сайт или возьмите "
            "готовый кнопкой «Выбрать из списка», задайте тщательность — «Быстро» перебирает 30 стратегий "
            "за пару минут — и нажмите «Найти рабочую стратегию».\n\n"
            "Пока идёт подбор, программа сама перезапускает движок с каждой стратегией."
        ),
        "en": (
            "Here the app itself looks for a strategy that opens the site you need. Choose what should "
            "work: sites and apps, voice calls or online games. Type a site or take a ready one with "
            "Choose from list, set the thoroughness — Quick tries 30 strategies in a couple of minutes — "
            "and press Find a working strategy.\n\n"
            "While the search runs, the app restarts the engine with each strategy by itself."
        ),
    },
    "onboarding.step.strategy_scan_result.title": {"ru": "Итог подбора", "en": "Search result"},
    "onboarding.step.strategy_scan_result.body": {
        "ru": (
            "Так выглядит законченный подбор — это пример. Сначала программа убеждается, что без обхода "
            "сайт закрыт, потом перебирает стратегии, а каждую удачную перепроверяет трижды.\n\n"
            "В «Надёжно работают» попадают только те, что открыли сайт три раза подряд. «Применить лучшую» "
            "записывает самую быструю в выбранный пресет, «Применить» в строке — любую другую. Остальные "
            "разложены по свёрнутым группам с причиной."
        ),
        "en": (
            "This is what a finished search looks like — it is an example. First the app makes sure the "
            "site is closed without the bypass, then it goes through strategies and rechecks every "
            "successful one three times.\n\n"
            "Only those that opened the site three times in a row get into Reliably working. Apply the "
            "best writes the fastest one to the selected preset, Apply in a row — any other. The rest "
            "are sorted into collapsed groups with the reason."
        ),
    },
    "onboarding.step.domain_lookup.title": {"ru": "Проверка домена", "en": "Domain check"},
    "onboarding.step.domain_lookup.body": {
        "ru": (
            "Всё об одном сайте: пинг, какие адреса отдают ему разные DNS-серверы, узлы по дороге до "
            "сервера и какие ещё домены живут на том же адресе. Впишите домен или IP-адрес и запустите "
            "проверку.\n\n"
            "Галочка под полем разрешает спрашивать соседей по адресу у внешних сервисов. Без неё наружу "
            "ничего не уходит."
        ),
        "en": (
            "Everything about one site: ping, which addresses different DNS servers give for it, the "
            "hops on the way to the server and which other domains live on the same address. Type a "
            "domain or an IP address and start the check.\n\n"
            "The checkbox under the field allows asking external services about neighbours on the "
            "address. Without it nothing leaves your computer."
        ),
    },
    "onboarding.step.dns_servers_check.title": {"ru": "DNS-серверы", "en": "DNS servers"},
    "onboarding.step.dns_servers_check.body": {
        "ru": (
            "Проверка самих DNS-серверов: отвечает ли каждый адрес на пинг, на обычные запросы и на "
            "шифрованные, и не перехватывает ли кто-то запросы по дороге.\n\n"
            "По итогу понятно, какие серверы у вашего провайдера рабочие и какой стоит выбрать на странице "
            "«Настройка DNS»."
        ),
        "en": (
            "A check of the DNS servers themselves: whether each address answers ping, plain requests "
            "and encrypted ones, and whether someone intercepts requests on the way.\n\n"
            "The result shows which servers work with your provider and which one to choose on the DNS "
            "settings page."
        ),
    },
    "onboarding.step.dns_spoofing_check.title": {"ru": "DNS подмена", "en": "DNS spoofing"},
    "onboarding.step.dns_spoofing_check.body": {
        "ru": (
            "Спрашивает адреса YouTube и Discord у нескольких DNS-серверов и сравнивает ответы. Если ваш "
            "DNS отвечает не то же, что остальные, — провайдер подменяет адреса.\n\n"
            "Лечится это сменой DNS на странице «Настройка DNS»."
        ),
        "en": (
            "Asks several DNS servers for the addresses of YouTube and Discord and compares the answers. "
            "If your DNS answers differently from the others, your provider spoofs addresses.\n\n"
            "The fix is to change DNS on the DNS settings page."
        ),
    },
    # ── диагностика: разбор лога ────────────────────────────────────────
    "onboarding.step.log_analyzer_source.title": {"ru": "Анализ лога winws2", "en": "winws2 log analyzer"},
    "onboarding.step.log_analyzer_source.body": {
        "ru": (
            "Когда непонятно, почему стратегия не срабатывает, помогает подробный журнал движка. Включите "
            "«Включить лог-файл (--debug)» на главной странице, запустите обход, откройте проблемный сайт — "
            "и загрузите лог сюда: кнопкой «Открыть файл…», из списка последних или просто перетащив файл "
            "на страницу.\n\n"
            "Поиск и две галочки оставляют в таблице только нужные соединения."
        ),
        "en": (
            "When it is unclear why a strategy does not work, the engine's detailed log helps. Turn on "
            "Enable log file (--debug) on the main page, start the bypass, open the problem site — and "
            "load the log here: with Open file…, from the recent list or simply by dropping the file on "
            "the page.\n\n"
            "The search and the two checkboxes keep only the connections you need in the table."
        ),
    },
    "onboarding.step.log_analyzer_connections.title": {"ru": "Соединения", "en": "Connections"},
    "onboarding.step.log_analyzer_connections.body": {
        "ru": (
            "Если своего лога пока нет, в таблице пример. Каждая строка — одно соединение: сайт, адрес и "
            "порт, протокол, под какой профиль пресета оно попало и что движок сделал с его пакетами: "
            "ok — пропустил как есть, mod — изменил, drop — отбросил. Последний столбец — в каких списках "
            "нашёлся сайт.\n\n"
            "Сайт попал не в тот профиль или его пакеты не меняются — причина найдена."
        ),
        "en": (
            "If you have no log of your own yet, the table shows an example. Each row is one connection: "
            "site, address and port, protocol, which preset profile it fell into and what the engine did "
            "with its packets: ok — passed as is, mod — changed, drop — dropped. The last column shows in "
            "which lists the site was found.\n\n"
            "If a site fell into the wrong profile or its packets are not changed, you have found the cause."
        ),
    },
    "onboarding.step.log_analyzer_packets.title": {"ru": "Пакеты соединения", "en": "Connection packets"},
    "onboarding.step.log_analyzer_packets.body": {
        "ru": (
            "Выберите соединение — ниже появятся его пакеты по порядку: направление, длина, флаги TCP, "
            "что внутри, какой профиль сработал и какая Lua-функция стратегии изменила пакет.\n\n"
            "В примере multisplit сработал на четвёртом пакете — том самом, где браузер называет имя сайта."
        ),
        "en": (
            "Select a connection and its packets appear below in order: direction, length, TCP flags, "
            "what is inside, which profile matched and which Lua function of the strategy changed the "
            "packet.\n\n"
            "In the example multisplit fired on the fourth packet — the one where the browser names the site."
        ),
    },
    # ── оформление и помощь ─────────────────────────────────────────────
    "onboarding.step.appearance_theme.title": {"ru": "Режим отображения", "en": "Display mode"},
    "onboarding.step.appearance_theme.body": {
        "ru": (
            "Тёмная или светлая тема; «Авто» повторяет настройку Windows. Ниже на странице — язык "
            "программы, вид значков в меню, фон окна и его прозрачность."
        ),
        "en": (
            "Dark or light theme; Auto follows the Windows setting. Further down the page are the app "
            "language, menu icon style, window background and its transparency."
        ),
    },
    "onboarding.step.appearance_accent.title": {"ru": "Акцентный цвет", "en": "Accent colour"},
    "onboarding.step.appearance_accent.body": {
        "ru": (
            "Цвет кнопок, значков и подсветки — тот же, что у рамки вокруг этого блока. Можно выбрать свой, "
            "взять системный цвет Windows или слегка тонировать им фон окна."
        ),
        "en": (
            "The colour of buttons, icons and highlights — the same as the frame around this block. Pick "
            "your own, take the Windows system colour or lightly tint the window background with it."
        ),
    },
    "onboarding.step.appearance_performance.title": {"ru": "Производительность", "en": "Performance"},
    "onboarding.step.appearance_performance.body": {
        "ru": (
            "Если компьютер слабый или движение на экране отвлекает, выключите его здесь. «Живые анимации» — "
            "логотип, сцена статуса на главной и подобное; «Анимации интерфейса» — переходы и кнопки; "
            "отдельно выключается плавная прокрутка.\n\n"
            "Эта экскурсия без живых анимаций тоже перестанет двигаться и будет просто переключать шаги."
        ),
        "en": (
            "If the computer is slow or motion on the screen distracts you, turn it off here. Live "
            "animations are the logo, the status scene on the main page and the like; Interface "
            "animations are transitions and buttons; smooth scrolling is switched off separately.\n\n"
            "Without live animations this tour stops moving too and simply switches steps."
        ),
    },
    "onboarding.step.logs_view.title": {"ru": "Логи программы", "en": "App logs"},
    "onboarding.step.logs_view.body": {
        "ru": (
            "Здесь на глазах пишется журнал программы: что запускалось, что изменилось, какие были ошибки. "
            "Ошибки и предупреждения собраны ещё и отдельным блоком ниже.\n\n"
            "Вкладка «Управление» показывает файлы логов: их можно скопировать, открыть папку или удалить."
        ),
        "en": (
            "The app log is written here live: what was started, what changed, which errors happened. "
            "Errors and warnings are also gathered in a separate block below.\n\n"
            "The Manage tab shows the log files: copy them, open the folder or delete them."
        ),
    },
    "onboarding.step.logs_send.title": {"ru": "Обращение в поддержку", "en": "Support request"},
    "onboarding.step.logs_send.body": {
        "ru": (
            "Кнопка «Подготовить обращение» делает всё сразу: собирает архив из свежих логов, копирует "
            "в буфер обмена шаблон обращения и открывает страницу Forgejo Issues. Останется вставить текст, "
            "приложить архив и описать, что не работает."
        ),
        "en": (
            "The Prepare request button does everything at once: packs fresh logs into an archive, copies "
            "a request template to the clipboard and opens the Forgejo Issues page. All that is left is "
            "to paste the text, attach the archive and describe what does not work."
        ),
    },
    "onboarding.step.about_version.title": {"ru": "Версия и обновления", "en": "Version and updates"},
    "onboarding.step.about_version.body": {
        "ru": (
            "Номер установленной версии. «Что нового» покажет, что изменилось в последних выпусках, "
            "«Настройка обновлений» откроет страницу, где обновления проверяются и устанавливаются.\n\n"
            "Ниже — статус подписки и вход в справку."
        ),
        "en": (
            "The installed version number. What's new shows what changed in recent releases, Update "
            "settings opens the page where updates are checked and installed.\n\n"
            "Below are the subscription status and the way to the help."
        ),
    },
    "onboarding.step.about_help.title": {"ru": "Справка", "en": "Help"},
    "onboarding.step.about_help.body": {
        "ru": (
            "Все ссылки в одном месте. «Научиться» — вики и видеокурс. «Спросить» — чаты в Telegram и "
            "Discord и страница для сообщений о проблемах. «Следить за новостями» — каналы проекта и "
            "исходный код."
        ),
        "en": (
            "All links in one place. Learn — the wiki and the video course. Ask — Telegram and Discord "
            "chats and the page for problem reports. Follow the news — project channels and the source code."
        ),
    },
    "onboarding.step.updates.title": {"ru": "Обновления", "en": "Updates"},
    "onboarding.step.updates.body": {
        "ru": (
            "Нажмите «Проверить обновления» — программа опросит серверы и предложит новую версию, если "
            "она есть. В таблице видно, какие серверы ответили и какие версии на них лежат.\n\n"
            "Пока включён выключатель «Обновлять программу автоматически» ниже, нажимать ничего не "
            "нужно: программа сама узнаёт о новой версии, скачивает её и перезапускается. Сразу после "
            "выхода версии обновления раздаются по очереди, поэтому до вас оно может дойти не в ту же минуту."
        ),
        "en": (
            "Press Check for updates and the app polls the servers and offers a new version if there is "
            "one. The table shows which servers answered and which versions they hold.\n\n"
            "While the Update the app automatically switch below is on, you do not need to press "
            "anything: the app learns about a new version, downloads it and restarts by itself. Right "
            "after a release, updates are handed out in a queue, so yours may arrive a little later."
        ),
    },
}


__all__ = ["TEXTS_TOUR"]
