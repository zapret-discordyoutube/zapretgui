from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtCore import QTimer
from qfluentwidgets import BodyLabel, CaptionLabel, ComboBox, LineEdit, SubtitleLabel
from ui.fluent_dialog import MessageBoxBase

from profile.user_profiles import validate_user_profile_filter
from ui.accessibility import (
    remove_line_edit_buttons_from_tab_order,
    set_accessible_description,
    set_control_accessibility,
    set_state_text,
)
from ui.combo_accessibility import set_combo_items_accessibility
from ui.fluent_widgets import style_semantic_caption_label


_PORT_HINTS: dict[str, dict[str, str]] = {
    "tcp": {
        "label": "TCP-порты",
        "placeholder": "TCP-порты: 443, 443-65535, 80,443-65535",
        "hint": "TCP: один порт, диапазон или список через запятую, например 80,443-65535.",
        "description": "TCP-порты. Можно указать один порт, диапазон 443-65535 или список через запятую, например 80,443-65535.",
    },
    "udp": {
        "label": "UDP-порты",
        "placeholder": "UDP-порты: 443, 443-65535, 443,50000-50100",
        "hint": "UDP: один порт, диапазон или список через запятую, например 443,50000-50100.",
        "description": "UDP-порты. Можно указать один порт, диапазон 443-65535 или список через запятую, например 443,50000-50100.",
    },
    "l7": {
        "label": "L7",
        "placeholder": "L7-имена, например stun,discord",
        "hint": "L7: укажите имена протоколов через запятую, например stun,discord.",
        "description": "L7-имена можно указать словами через запятую, например stun,discord.",
    },
}


class CreateUserProfileDialog(MessageBoxBase):
    def __init__(
        self,
        parent=None,
        *,
        title: str = "Добавить profile",
        subtitle: str = "Создаёт пользовательский profile и два пустых файла списка: hostlist и ipset.",
        button_text: str = "Добавить",
        name: str = "",
        protocol: str = "tcp",
        ports: str = "",
        create_geo_worker=None,
        open_hosts_editor=None,
        open_dns_settings=None,
    ):
        if parent is not None and not parent.isWindow():
            parent = parent.window()
        super().__init__(parent)

        self.titleLabel = SubtitleLabel(title, self.widget)
        self.subtitleLabel = BodyLabel(subtitle, self.widget)
        self.subtitleLabel.setWordWrap(True)

        self.nameEdit = LineEdit(self.widget)
        self.nameEdit.setPlaceholderText("Название profile")
        self.nameEdit.setClearButtonEnabled(True)

        self.protocolCombo = ComboBox(self.widget)
        self.protocolCombo.addItem("TCP", userData="tcp")
        self.protocolCombo.addItem("UDP", userData="udp")
        self.protocolCombo.addItem("L7", userData="l7")
        self._set_protocol(protocol)

        self.portsEdit = LineEdit(self.widget)
        self.portsEdit.setClearButtonEnabled(True)
        self.nameEdit.setText(str(name or ""))
        self.portsEdit.setText(str(ports or ""))

        self.portsLabel = BodyLabel("", self.widget)
        self.portsHintLabel = CaptionLabel("", self.widget)
        self._update_ports_hint()

        self.warningLabel = CaptionLabel("", self.widget)
        style_semantic_caption_label(self.warningLabel, tone="error")
        self.warningLabel.hide()

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.subtitleLabel)
        self.viewLayout.addWidget(BodyLabel("Название", self.widget))
        self.viewLayout.addWidget(self.nameEdit)
        self.viewLayout.addWidget(BodyLabel("Тип", self.widget))
        self.viewLayout.addWidget(self.protocolCombo)
        self.viewLayout.addWidget(self.portsLabel)
        self.viewLayout.addWidget(self.portsEdit)
        self.viewLayout.addWidget(self.portsHintLabel)
        self.viewLayout.addWidget(self.warningLabel)

        self.yesButton.setText(button_text)
        self.cancelButton.setText("Отмена")
        self.widget.setMinimumWidth(520)
        self._install_accessibility(button_text=button_text)
        self.nameEdit.returnPressed.connect(self._validate_and_accept)
        self.portsEdit.returnPressed.connect(self._validate_and_accept)
        self.protocolCombo.currentIndexChanged.connect(self._update_protocol_ui)
        self._geo_notice = self._build_geo_notice(create_geo_worker, open_hosts_editor, open_dns_settings)

    def _build_geo_notice(self, create_geo_worker, open_hosts_editor, open_dns_settings):
        """Название похоже на гео-сервис («gemini» → Gemini AI): та же карточка, что на странице профиля.

        Окно накрывает всю программу, поэтому карточка встаёт в тот же правый
        нижний угол. Кнопки карточки закрывают окно и открывают нужный раздел.
        """
        if create_geo_worker is None:
            return None
        from profile.ui.profile_geo_notice import ProfileGeoNotice

        notice = ProfileGeoNotice(
            self,
            create_worker=create_geo_worker,
            open_hosts_editor=self._leave_for(open_hosts_editor) if open_hosts_editor is not None else None,
            open_dns_settings=self._leave_for(open_dns_settings) if open_dns_settings is not None else None,
            open_telegram_proxy=self._leave_for(self._telegram_proxy_opener()),
        )
        self.nameEdit.textChanged.connect(self._check_geo_name)
        self._geo_notice = notice
        self._check_geo_name()
        return notice

    def _telegram_proxy_opener(self):
        """Раздел «Telegram Proxy» открывается в главном окне: само окно профиля закроется раньше."""
        owner = self.parent()
        main_window = owner.window() if owner is not None else None

        def _open() -> None:
            from profile.ui.profile_geo_notice import show_telegram_proxy_page

            if main_window is not None:
                show_telegram_proxy_page(main_window)

        return _open

    def _leave_for(self, open_page):
        def _leave() -> None:
            self.reject()
            # Раздел открывается, когда окно уже закрыто.
            QTimer.singleShot(0, open_page)

        return _leave

    def _check_geo_name(self, *_args) -> None:
        name = self.nameEdit.text().strip()
        if not name:
            self._geo_notice.show_services(())
            return
        self._geo_notice.request(name, SimpleNamespace(display_name=name, match_lines=()), keep_shown=True)

    def done(self, code) -> None:  # noqa: N802 (Qt API)
        if self._geo_notice is not None:
            # Окно удаляется сразу после закрытия — проверка в его потоке должна закончиться раньше.
            self._geo_notice.cleanup(blocking=True)
        super().done(code)

    def values(self) -> tuple[str, str, str]:
        protocol = str(self.protocolCombo.itemData(self.protocolCombo.currentIndex()) or "tcp")
        return self.nameEdit.text().strip(), protocol, self.portsEdit.text().strip()

    def _set_protocol(self, protocol: str) -> None:
        wanted = str(protocol or "").strip().lower()
        for index in range(self.protocolCombo.count()):
            if str(self.protocolCombo.itemData(index) or "").strip().lower() == wanted:
                self.protocolCombo.setCurrentIndex(index)
                return

    def _install_accessibility(self, *, button_text: str) -> None:
        set_control_accessibility(
            self.nameEdit,
            name="Название пользовательского profile",
            description="Например YouTube или Discord. Это имя будет использоваться в preset-ах.",
        )
        remove_line_edit_buttons_from_tab_order(self.nameEdit)
        self._update_protocol_accessibility()
        set_accessible_description(
            self.protocolCombo,
            "Выберите, к чему относится profile: TCP, UDP или L7.",
        )
        self._update_ports_hint()
        remove_line_edit_buttons_from_tab_order(self.portsEdit)
        action = str(button_text or "").strip().lower()
        is_save = action.startswith("сохран")
        if is_save:
            yes_name = "Сохранить пользовательский profile"
            yes_description = "Сохраняет изменения profile и обновляет связанные preset-ы."
            cancel_name = "Отменить изменение пользовательского profile"
        else:
            yes_name = "Добавить пользовательский profile"
            yes_description = "Создаёт profile и два пустых файла списка: hostlist и ipset."
            cancel_name = "Отменить создание пользовательского profile"
        set_control_accessibility(self.yesButton, name=yes_name, description=yes_description)
        set_state_text(self.yesButton, yes_name)
        set_control_accessibility(
            self.cancelButton,
            name=cancel_name,
            description="Закрывает окно без изменений.",
        )
        set_state_text(self.cancelButton, cancel_name)

    def _update_protocol_accessibility(self) -> None:
        selected = str(self.protocolCombo.currentText() or "").strip() or "не выбрано"
        state_text = f"Тип пользовательского profile, выбрано: {selected}"
        set_control_accessibility(
            self.protocolCombo,
            name=state_text,
        )
        set_state_text(self.protocolCombo, state_text)
        set_combo_items_accessibility(self.protocolCombo, name="Тип пользовательского profile")

    def _update_protocol_ui(self) -> None:
        self._update_protocol_accessibility()
        self._update_ports_hint()

    def _selected_protocol(self) -> str:
        return str(self.protocolCombo.itemData(self.protocolCombo.currentIndex()) or "tcp").strip().lower() or "tcp"

    def _update_ports_hint(self) -> None:
        hint = _PORT_HINTS.get(self._selected_protocol(), _PORT_HINTS["tcp"])
        self.portsLabel.setText(hint["label"])
        self.portsEdit.setPlaceholderText(hint["placeholder"])
        self.portsHintLabel.setText(hint["hint"])
        set_control_accessibility(
            self.portsEdit,
            name="Порты или L7 для пользовательского profile",
            description=hint["description"],
        )
        set_state_text(self.portsHintLabel, hint["hint"])

    def validate(self) -> bool:
        name, protocol, ports = self.values()
        if not name:
            self._show_warning("Введите название profile.")
            return False
        if not ports:
            self._show_warning("Введите порты или L7.")
            return False
        filter_error = validate_user_profile_filter(protocol, ports)
        if filter_error:
            self._show_warning(filter_error)
            return False
        self.warningLabel.hide()
        return True

    def _validate_and_accept(self) -> None:
        if self.validate():
            self.accept()

    def _show_warning(self, text: str) -> None:
        self.warningLabel.setText(text)
        set_state_text(self.warningLabel, f"Ошибка: {text}")
        self.warningLabel.show()
