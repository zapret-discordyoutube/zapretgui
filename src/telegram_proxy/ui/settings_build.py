"""Build-helper вкладки «Настройки» основной страницы Telegram Proxy."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    HorizontalSeparator,
    IconWidget,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    SettingCardGroup,
    SpinBox,
    StrongBodyLabel,
)

from ui.accessibility import remove_line_edit_buttons_from_tab_order, set_control_accessibility, set_state_text
from ui.fluent_widgets import SettingsCard, enable_setting_card_group_auto_height, set_tooltip
from ui.widgets.win11_controls import Win11ComboRow, Win11ControlRow, Win11ToggleRow
from telegram_proxy.ui.text_plan import TELEGRAM_PROXY_SETTINGS_TEXT


@dataclass(slots=True)
class TelegramProxySettingsPanelWidgets:
    status_card: object
    status_dot: object
    status_label: object
    btn_toggle: object
    stats_label: object
    setup_title_label: object
    setup_open_btn: object
    setup_copy_btn: object
    setup_zastogram_btn: object
    settings_card: object
    host_port_row: object
    host_edit: object
    port_spin: object
    proxy_mode_row: object
    mtproxy_secret_row: object
    mtproxy_secret_edit: object
    mtproxy_generate_btn: object
    fake_tls_domain_row: object
    fake_tls_domain_edit: object
    fake_tls_nginx_btn: object
    proxy_protocol_toggle: object
    auto_deeplink_toggle: object
    advanced_nav_row: object
    advanced_nav_btn: object


def set_spinbox_value_accessibility(spinbox, *, name: str, description: str) -> None:
    def _sync(value=None) -> None:
        if value is None:
            try:
                value = spinbox.value()
            except Exception:
                value = ""
        state = f"{name}, значение: {value}"
        set_control_accessibility(spinbox, name=state, description=description)
        set_state_text(spinbox, state)

    _sync()
    if bool(getattr(spinbox, "_telegram_proxy_accessibility_value_connected", False)):
        return
    try:
        spinbox.valueChanged.connect(_sync)
        setattr(spinbox, "_telegram_proxy_accessibility_value_connected", True)
    except Exception:
        pass


def build_settings_row(icon_name: str, title: str, description: str) -> Win11ControlRow:
    """Строка настройки: значок, название и описание слева, поля справа."""

    return Win11ControlRow(icon_name, title, description)


def build_line_edit(
    *,
    placeholder: str,
    accessible_name: str,
    description: str,
    width: int,
    line_edit_cls=LineEdit,
) -> object:
    edit = line_edit_cls()
    edit.setFixedWidth(int(width))
    edit.setPlaceholderText(placeholder)
    edit.setClearButtonEnabled(True)
    remove_line_edit_buttons_from_tab_order(edit)
    set_tooltip(edit, description)
    set_control_accessibility(edit, name=accessible_name, description=description)
    return edit


def build_row_button(text: str, icon, *, accessible_name: str, description: str, on_click) -> PushButton:
    button = PushButton(text, icon=icon)
    set_tooltip(button, description)
    set_control_accessibility(button, name=accessible_name, description=description)
    button.clicked.connect(on_click)
    return button


def build_action_row(
    *,
    icon_name: str,
    title: str,
    description: str,
    button_text: str,
    button_icon,
    accessible_name: str,
    on_click,
):
    """Строка с одной кнопкой действия справа, в том же стиле, что и строки полей."""

    row = build_settings_row(icon_name, title, description)
    button = build_row_button(
        button_text,
        button_icon,
        accessible_name=accessible_name,
        description=description,
        on_click=on_click,
    )
    button.setFixedWidth(128)
    row.add_control(button)
    return row, button


def _build_status_card(*, status_dot_cls, on_toggle_proxy, on_open_in_telegram, on_copy_link, on_open_zastogram):
    text = TELEGRAM_PROXY_SETTINGS_TEXT
    status_card = SettingsCard()

    status_header = QHBoxLayout()
    status_header.setSpacing(10)
    status_dot = status_dot_cls()
    status_label = StrongBodyLabel("Остановлен")
    status_header.addWidget(status_dot)
    status_header.addWidget(status_label)
    status_header.addStretch()
    btn_toggle = PushButton("Запустить", icon=FluentIcon.PLAY)
    btn_toggle.setFixedWidth(140)
    btn_toggle.clicked.connect(on_toggle_proxy)
    status_header.addWidget(btn_toggle)
    status_card.add_layout(status_header)

    stats_label = CaptionLabel("")
    stats_label.setWordWrap(True)
    # Длинная строка статистики переносится внутри карточки, а не растягивает её.
    stats_label.setMinimumWidth(0)
    stats_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    stats_label.setVisible(False)
    status_card.add_widget(stats_label)

    status_card.add_widget(HorizontalSeparator())

    setup_row = QHBoxLayout()
    setup_row.setSpacing(8)
    setup_title_label = StrongBodyLabel(text.setup_title)
    set_tooltip(setup_title_label, text.setup_description)
    set_control_accessibility(setup_title_label, name=text.setup_title, description=text.setup_description)
    setup_row.addWidget(setup_title_label)
    setup_row.addStretch()

    setup_open_btn = PrimaryPushButton("Открыть", icon=FluentIcon.SEND)
    setup_open_btn.setMinimumWidth(124)
    set_tooltip(setup_open_btn, "Открыть ссылку для автоматической настройки прокси внутри Telegram.")
    set_control_accessibility(
        setup_open_btn,
        name="Открыть Telegram Proxy в Telegram",
        description="Открывает ссылку для автоматической настройки прокси внутри Telegram.",
    )
    set_state_text(setup_open_btn, "Открыть Telegram Proxy в Telegram")
    setup_open_btn.clicked.connect(on_open_in_telegram)
    setup_row.addWidget(setup_open_btn)

    setup_copy_btn = PushButton("Копировать", icon=FluentIcon.COPY)
    setup_copy_btn.setMinimumWidth(124)
    set_tooltip(setup_copy_btn, "Сохранить ссылку в буфер обмена, если Telegram не открылся автоматически.")
    set_control_accessibility(
        setup_copy_btn,
        name="Копировать ссылку Telegram Proxy",
        description="Сохраняет ссылку Telegram Proxy в буфер обмена, если Telegram не открылся автоматически.",
    )
    set_state_text(setup_copy_btn, "Копировать ссылку Telegram Proxy")
    setup_copy_btn.clicked.connect(on_copy_link)
    setup_row.addWidget(setup_copy_btn)

    status_card.add_layout(setup_row)

    # ZaStoGram — отдельной строкой с пояснением: запасной путь, если
    # прокси не помог, должен быть понятен без наведения мыши.
    status_card.add_widget(HorizontalSeparator())
    zastogram_row = QHBoxLayout()
    zastogram_row.setSpacing(12)
    zastogram_icon = IconWidget(FluentIcon.DOWNLOAD)
    zastogram_icon.setFixedSize(20, 20)
    zastogram_row.addWidget(zastogram_icon)
    zastogram_text = QVBoxLayout()
    zastogram_text.setSpacing(2)
    zastogram_title_label = StrongBodyLabel(text.zastogram_title)
    zastogram_description_label = CaptionLabel(text.zastogram_description)
    zastogram_description_label.setWordWrap(True)
    zastogram_text.addWidget(zastogram_title_label)
    zastogram_text.addWidget(zastogram_description_label)
    zastogram_row.addLayout(zastogram_text, 1)

    setup_zastogram_btn = PushButton("Скачать ZaStoGram", icon=FluentIcon.DOWNLOAD)
    setup_zastogram_btn.setMinimumWidth(124)
    set_tooltip(setup_zastogram_btn, "Открыть страницу последнего выпуска ZaStoGram Desktop.")
    set_control_accessibility(
        setup_zastogram_btn,
        name="Скачать ZaStoGram Desktop",
        description="Открывает в браузере страницу последнего выпуска ZaStoGram Desktop в Forgejo.",
    )
    set_state_text(setup_zastogram_btn, "Скачать ZaStoGram Desktop")
    setup_zastogram_btn.clicked.connect(on_open_zastogram)
    zastogram_row.addWidget(setup_zastogram_btn)
    status_card.add_layout(zastogram_row)

    return (
        status_card,
        status_dot,
        status_label,
        btn_toggle,
        stats_label,
        setup_title_label,
        setup_open_btn,
        setup_copy_btn,
        setup_zastogram_btn,
    )


def build_telegram_proxy_settings_panel(
    layout: QVBoxLayout,
    *,
    content_parent,
    status_dot_cls,
    on_toggle_proxy,
    on_open_in_telegram,
    on_copy_link,
    on_open_zastogram,
    on_generate_mtproxy_secret,
    on_copy_fake_tls_nginx_config,
    on_open_advanced_settings,
) -> TelegramProxySettingsPanelWidgets:
    text = TELEGRAM_PROXY_SETTINGS_TEXT
    (
        status_card,
        status_dot,
        status_label,
        btn_toggle,
        stats_label,
        setup_title_label,
        setup_open_btn,
        setup_copy_btn,
        setup_zastogram_btn,
    ) = _build_status_card(
        status_dot_cls=status_dot_cls,
        on_toggle_proxy=on_toggle_proxy,
        on_open_in_telegram=on_open_in_telegram,
        on_copy_link=on_copy_link,
        on_open_zastogram=on_open_zastogram,
    )
    layout.addWidget(status_card)

    settings_card = SettingCardGroup(text.settings_title, content_parent)

    host_port_row = build_settings_row("fa5s.plug", text.host_port_title, text.host_port_description)
    host_edit = build_line_edit(
        placeholder="127.0.0.1",
        accessible_name="Адрес Telegram Proxy",
        description=(
            "IP-адрес для прослушивания Telegram Proxy. 127.0.0.1 — только локально, "
            "0.0.0.0 или IP вашей сети — доступ с других устройств."
        ),
        width=160,
    )
    host_edit.setText("127.0.0.1")
    host_port_row.add_control(host_edit)
    port_spin = SpinBox()
    port_spin.setRange(1024, 65535)
    port_spin.setValue(1353)
    port_spin.setFixedWidth(130)
    set_spinbox_value_accessibility(
        port_spin,
        name="Порт Telegram Proxy",
        description="Порт, на котором Telegram Proxy принимает подключения.",
    )
    host_port_row.add_control(port_spin)
    settings_card.addSettingCard(host_port_row)

    proxy_mode_row = Win11ComboRow(
        icon_name="fa5s.exchange-alt",
        title=text.proxy_mode_title,
        description=text.proxy_mode_description,
        items=[
            ("SOCKS5 (рекомендуется)", "socks5"),
            ("MTProxy (продвинутый)", "mtproxy"),
        ],
    )
    proxy_mode_row.combo.setFixedWidth(250)
    settings_card.addSettingCard(proxy_mode_row)

    mtproxy_secret_row = build_settings_row(
        "fa5s.key",
        text.mtproxy_secret_title,
        text.mtproxy_secret_description,
    )
    mtproxy_secret_edit = build_line_edit(
        placeholder="32 символа: 0-9 и a-f",
        accessible_name="Secret MTProxy",
        description="Секрет MTProxy. Telegram использует его как ключ подключения.",
        width=330,
    )
    mtproxy_secret_row.add_control(mtproxy_secret_edit)
    mtproxy_generate_btn = build_row_button(
        "Создать",
        FluentIcon.SYNC,
        accessible_name="Создать secret MTProxy",
        description="Создать новый случайный secret для MTProxy.",
        on_click=on_generate_mtproxy_secret,
    )
    mtproxy_generate_btn.setFixedWidth(128)
    mtproxy_secret_row.add_control(mtproxy_generate_btn)
    settings_card.addSettingCard(mtproxy_secret_row)

    fake_tls_domain_row = build_settings_row(
        "fa5s.user-secret",
        text.fake_tls_domain_title,
        text.fake_tls_domain_description,
    )
    fake_tls_domain_edit = build_line_edit(
        placeholder="front.example.com",
        accessible_name="Домен MTProxy Fake TLS",
        description="Домен для MTProxy Fake TLS. Оставьте пустым, если Fake TLS не нужен.",
        width=330,
    )
    fake_tls_domain_row.add_control(fake_tls_domain_edit)
    fake_tls_nginx_btn = build_row_button(
        "Nginx",
        FluentIcon.COPY,
        accessible_name="Скопировать Nginx-конфиг MTProxy Fake TLS",
        description="Скопировать stream-конфиг Nginx для MTProxy Fake TLS.",
        on_click=on_copy_fake_tls_nginx_config,
    )
    fake_tls_nginx_btn.setFixedWidth(128)
    fake_tls_domain_row.add_control(fake_tls_nginx_btn)
    settings_card.addSettingCard(fake_tls_domain_row)

    proxy_protocol_toggle = Win11ToggleRow(
        "fa5s.project-diagram",
        text.proxy_protocol_title,
        text.proxy_protocol_description,
    )
    proxy_protocol_toggle.setChecked(False)
    settings_card.addSettingCard(proxy_protocol_toggle)

    # Строки MTProxy видны только в режиме MTProxy; до загрузки настроек скрыты.
    for mtproxy_widget in (mtproxy_secret_row, fake_tls_domain_row, proxy_protocol_toggle):
        mtproxy_widget.setVisible(False)

    auto_deeplink_toggle = Win11ToggleRow(
        "fa5b.telegram",
        text.auto_setup_title,
        text.auto_setup_description,
    )
    auto_deeplink_toggle.setChecked(True)
    settings_card.addSettingCard(auto_deeplink_toggle)

    advanced_nav_row, advanced_nav_btn = build_action_row(
        icon_name="fa5s.sliders-h",
        title=text.advanced_nav_title,
        description=text.advanced_nav_description,
        button_text="Открыть",
        button_icon=FluentIcon.CHEVRON_RIGHT,
        accessible_name="Открыть продвинутые настройки Telegram Proxy",
        on_click=on_open_advanced_settings,
    )
    settings_card.addSettingCard(advanced_nav_row)
    enable_setting_card_group_auto_height(settings_card)

    layout.addWidget(settings_card)
    layout.addStretch()

    return TelegramProxySettingsPanelWidgets(
        status_card=status_card,
        status_dot=status_dot,
        status_label=status_label,
        btn_toggle=btn_toggle,
        stats_label=stats_label,
        setup_title_label=setup_title_label,
        setup_open_btn=setup_open_btn,
        setup_copy_btn=setup_copy_btn,
        setup_zastogram_btn=setup_zastogram_btn,
        settings_card=settings_card,
        host_port_row=host_port_row,
        host_edit=host_edit,
        port_spin=port_spin,
        proxy_mode_row=proxy_mode_row,
        mtproxy_secret_row=mtproxy_secret_row,
        mtproxy_secret_edit=mtproxy_secret_edit,
        mtproxy_generate_btn=mtproxy_generate_btn,
        fake_tls_domain_row=fake_tls_domain_row,
        fake_tls_domain_edit=fake_tls_domain_edit,
        fake_tls_nginx_btn=fake_tls_nginx_btn,
        proxy_protocol_toggle=proxy_protocol_toggle,
        auto_deeplink_toggle=auto_deeplink_toggle,
        advanced_nav_row=advanced_nav_row,
        advanced_nav_btn=advanced_nav_btn,
    )
