"""Fluent-диалог импорта пресета: файл (drag-and-drop) или ссылка."""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    FluentIcon,
    IconWidget,
    IndeterminateProgressBar,
    LineEdit,
    PushButton,
    SubtitleLabel,
)

from presets.ui.common.user_presets_dialogs import PresetDialogTextMixin, tr_presets_dialog
from ui.accessibility import remove_line_edit_buttons_from_tab_order, set_control_accessibility, set_state_text
from ui.fluent_dialog import MessageBoxBase
from ui.fluent_widgets import style_semantic_caption_label
from ui.theme import get_theme_tokens
from ui.theme_refresh import ThemeRefreshBinding

_ALLOWED_SUFFIXES = (".txt", ".zip")


class _PresetDropZone(CardWidget):
    """Зона перетаскивания файла пресета внутри диалога импорта."""

    def __init__(self, parent, on_file_dropped):
        super().__init__(parent)
        self._on_file_dropped = on_file_dropped
        self.setAcceptDrops(True)
        self.setObjectName("presetImportDropZone")

    @staticmethod
    def _dropped_preset_path(mime_data) -> str:
        if mime_data is None or not mime_data.hasUrls():
            return ""
        for url in mime_data.urls():
            path = url.toLocalFile()
            if path and os.path.isfile(path) and path.lower().endswith(_ALLOWED_SUFFIXES):
                return path
        return ""

    def dragEnterEvent(self, event):  # noqa: N802 (Qt override)
        if self._dropped_preset_path(event.mimeData()):
            event.acceptProposedAction()
            self.setProperty("dropHover", True)
            self._repolish()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):  # noqa: N802 (Qt override)
        self.setProperty("dropHover", False)
        self._repolish()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):  # noqa: N802 (Qt override)
        self.setProperty("dropHover", False)
        self._repolish()
        path = self._dropped_preset_path(event.mimeData())
        if not path:
            event.ignore()
            return
        event.acceptProposedAction()
        self._on_file_dropped(path)

    def _repolish(self) -> None:
        style = self.style()
        style.unpolish(self)
        style.polish(self)


class ImportPresetDialog(PresetDialogTextMixin, MessageBoxBase):
    """Импорт пресета из файла (перетаскивание/выбор) или по ссылке.

    Результат после accept: result_file_path (локальный или скачанный
    temp-файл), result_source_url (пусто для локального файла),
    result_auto_update (включать ли автообновление по ссылке).
    """

    def __init__(self, parent=None, language: str = "ru"):
        if parent and not parent.isWindow():
            parent = parent.window()
        super().__init__(parent)
        self._ui_language = language

        def _tr(key: str, default: str, **kwargs) -> str:
            return tr_presets_dialog(self._dialog_key(key), self._ui_language, default, **kwargs)

        self._tr = _tr
        self.result_file_path = ""
        self.result_source_url = ""
        self.result_auto_update = False
        self._download_worker = None
        self._download_request_id = 0
        self._downloading = False

        self.titleLabel = SubtitleLabel(
            _tr("dialog.import.title", "Импортировать пресет"), self.widget
        )
        self.subtitleLabel = BodyLabel(
            _tr(
                "dialog.import.subtitle",
                "Перетащите файл пресета или вставьте ссылку — пресет по ссылке сможет обновляться автоматически.",
            ),
            self.widget,
        )
        self.subtitleLabel.setWordWrap(True)

        self.dropZone = _PresetDropZone(self.widget, self._on_file_selected)
        drop_layout = QVBoxLayout(self.dropZone)
        drop_layout.setContentsMargins(16, 16, 16, 12)
        drop_layout.setSpacing(8)
        self.dropIcon = IconWidget(FluentIcon.DOWNLOAD, self.dropZone)
        self.dropIcon.setFixedSize(28, 28)
        drop_layout.addWidget(self.dropIcon, 0, Qt.AlignmentFlag.AlignHCenter)
        self.dropHintLabel = BodyLabel(
            _tr("dialog.import.drop.hint", "Перетащите сюда файл пресета (.txt или .zip)"),
            self.dropZone,
        )
        self.dropHintLabel.setWordWrap(True)
        self.dropHintLabel.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        drop_layout.addWidget(self.dropHintLabel)
        self.browseButton = PushButton(
            _tr("dialog.import.drop.browse", "Выбрать файл"), self.dropZone
        )
        drop_layout.addWidget(self.browseButton, 0, Qt.AlignmentFlag.AlignHCenter)
        self.browseButton.clicked.connect(self._on_browse_clicked)

        or_row = QHBoxLayout()
        self.orLabel = CaptionLabel(_tr("dialog.import.or", "или"), self.widget)
        or_row.addStretch()
        or_row.addWidget(self.orLabel)
        or_row.addStretch()

        url_label = BodyLabel(_tr("dialog.import.url.label", "Вставьте ссылку"), self.widget)
        self.urlEdit = LineEdit(self.widget)
        self.urlEdit.setPlaceholderText(
            _tr("dialog.import.url.placeholder", "https://…/preset.txt")
        )
        self.urlEdit.setClearButtonEnabled(True)
        self.urlEdit.textChanged.connect(self._on_url_changed)

        self.autoUpdateCheck = CheckBox(
            _tr("dialog.import.auto_update.label", "Автоматически обновлять по ссылке"),
            self.widget,
        )
        self.autoUpdateCheck.setChecked(True)
        self.autoUpdateCheck.setEnabled(False)

        self.progressBar = IndeterminateProgressBar(self.widget)
        self.progressBar.hide()

        self.warningLabel = CaptionLabel("", self.widget)
        style_semantic_caption_label(self.warningLabel, tone="error")
        self.warningLabel.hide()

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.subtitleLabel)
        self.viewLayout.addWidget(self.dropZone)
        self.viewLayout.addLayout(or_row)
        self.viewLayout.addWidget(url_label)
        self.viewLayout.addWidget(self.urlEdit)
        self.viewLayout.addWidget(self.autoUpdateCheck)
        self.viewLayout.addWidget(self.progressBar)
        self.viewLayout.addWidget(self.warningLabel)

        self.yesButton.setText(_tr("dialog.import.button", "Импортировать"))
        self.cancelButton.setText(
            tr_presets_dialog(self._dialog_key("dialog.button.cancel"), self._ui_language, "Отмена")
        )
        self.widget.setMinimumWidth(460)
        self._apply_drop_zone_theme()
        self._theme_refresh = ThemeRefreshBinding(self.dropZone, self._apply_drop_zone_theme)
        self._install_accessibility()

    # ------------------------------------------------------------- файл

    def handle_dropped_preset_files(self, file_paths) -> bool:
        """Приём файлов от оконного drop-фильтра (делегация во время диалога)."""
        if self._downloading:
            return False
        for file_path in file_paths or ():
            path = str(file_path or "").strip()
            if path and os.path.isfile(path) and path.lower().endswith(_ALLOWED_SUFFIXES):
                self._on_file_selected(path)
                return True
        return False

    def _on_browse_clicked(self) -> None:
        if self._downloading:
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self._tr("dialog.import.title", "Импортировать пресет"),
            "",
            "Пресеты и архивы (*.txt *.zip);;Все файлы (*.*)",
        )
        if file_path:
            self._on_file_selected(file_path)

    def _on_file_selected(self, file_path: str) -> None:
        if self._downloading:
            return
        self.result_file_path = str(file_path or "")
        self.result_source_url = ""
        self.result_auto_update = False
        self.accept()

    # ------------------------------------------------------------- ссылка

    def _on_url_changed(self, text: str) -> None:
        from presets.preset_url_import import is_https_preset_import_url

        self.autoUpdateCheck.setEnabled(is_https_preset_import_url(text))
        if self.warningLabel.isVisible():
            self.warningLabel.hide()

    def validate(self) -> bool:
        if self._downloading:
            return False
        url = self.urlEdit.text().strip()
        if not url:
            self._show_warning(
                self._tr(
                    "dialog.import.validation.empty",
                    "Перетащите файл или вставьте ссылку на пресет.",
                )
            )
            return False

        from presets.preset_url_import import validate_preset_import_url

        validation_error = validate_preset_import_url(url)
        if validation_error:
            self._show_warning(
                self._tr("dialog.import.error.url", "Некорректная ссылка: {error}", error=validation_error)
            )
            return False
        self._start_download(url)
        return False

    def _start_download(self, url: str) -> None:
        from presets.ui.common.preset_import_download_worker import PresetImportDownloadWorker

        self._download_request_id += 1
        request_id = self._download_request_id
        self._downloading = True
        self.warningLabel.hide()
        self.progressBar.show()
        self._set_inputs_enabled(False)

        worker = PresetImportDownloadWorker(request_id, url)
        worker.succeeded.connect(self._on_download_succeeded)
        worker.failed.connect(self._on_download_failed)
        worker.finished.connect(worker.deleteLater)
        self._download_worker = worker
        worker.start()

    def _on_download_succeeded(self, request_id: int, result) -> None:
        if request_id != self._download_request_id:
            return
        self._finish_download_state()
        self.result_file_path = str(getattr(result, "file_path", "") or "")
        self.result_source_url = str(getattr(result, "source_url", "") or "")
        self.result_auto_update = bool(
            self.autoUpdateCheck.isChecked()
            and self.autoUpdateCheck.isEnabled()
            and str(getattr(result, "suffix", "") or "") == ".txt"
        )
        self.accept()

    def _on_download_failed(self, request_id: int, kind: str, detail: str) -> None:
        if request_id != self._download_request_id:
            return
        self._finish_download_state()
        if kind == "cancelled":
            return
        self._show_warning(self._download_error_text(kind, detail))

    def _download_error_text(self, kind: str, detail: str) -> str:
        defaults = {
            "url": "Некорректная ссылка: {error}",
            "ssl": "Не удалось проверить SSL-сертификат: {error}",
            "timeout": "Сервер не ответил вовремя. Попробуйте ещё раз.",
            "network": "Не удалось скачать пресет: {error}",
            "http": "Сервер вернул ошибку: {error}",
            "too_large": "Файл по ссылке слишком большой.",
            "content": "Файл по ссылке пуст или не похож на пресет.",
        }
        key = f"dialog.import.error.{kind}"
        default = defaults.get(kind, "Не удалось скачать пресет: {error}")
        return self._tr(key, default, error=str(detail or ""))

    def _finish_download_state(self) -> None:
        self._downloading = False
        self._download_worker = None
        self.progressBar.hide()
        self._set_inputs_enabled(True)

    def _set_inputs_enabled(self, enabled: bool) -> None:
        self.yesButton.setEnabled(enabled)
        self.urlEdit.setEnabled(enabled)
        self.browseButton.setEnabled(enabled)
        self.dropZone.setAcceptDrops(enabled)

    def done(self, code: int) -> None:  # noqa: A003 (Qt override)
        worker = self._download_worker
        if worker is not None:
            worker.request_cancel()
            self._download_worker = None
        super().done(code)

    # ------------------------------------------------------------- прочее

    def _show_warning(self, text: str) -> None:
        self.warningLabel.setText(text)
        set_state_text(self.warningLabel, f"Ошибка: {text}")
        self.warningLabel.show()

    def _apply_drop_zone_theme(self, *_args) -> None:
        tokens = get_theme_tokens()
        self.dropZone.setStyleSheet(
            "#presetImportDropZone {"
            f" border: 1px dashed {tokens.surface_border_hover};"
            " border-radius: 6px;"
            f" background-color: {tokens.surface_bg};"
            " }"
            "#presetImportDropZone[dropHover=\"true\"] {"
            f" border: 1px dashed {tokens.accent_hex};"
            f" background-color: {tokens.accent_soft_bg};"
            " }"
        )

    def _install_accessibility(self) -> None:
        set_control_accessibility(
            self.dropZone,
            name="Зона перетаскивания файла пресета",
            description="Перетащите сюда txt или zip файл пресета, либо нажмите кнопку «Выбрать файл».",
        )
        set_control_accessibility(
            self.browseButton,
            name="Выбрать файл пресета",
            description="Открывает диалог выбора txt или zip файла пресета.",
        )
        set_state_text(self.browseButton, "Выбрать файл пресета")
        set_control_accessibility(
            self.urlEdit,
            name="Ссылка на пресет",
            description="Вставьте прямую ссылку на txt-файл пресета. Пресет будет скачан и импортирован.",
        )
        remove_line_edit_buttons_from_tab_order(self.urlEdit)
        set_control_accessibility(
            self.autoUpdateCheck,
            name="Автоматически обновлять по ссылке",
            description="Пресет будет периодически проверять источник и обновляться. Доступно только для https-ссылок.",
        )
        set_control_accessibility(
            self.yesButton,
            name="Импортировать пресет",
            description="Скачивает пресет по ссылке и добавляет его в список.",
        )
        set_state_text(self.yesButton, "Импортировать пресет")
        set_control_accessibility(
            self.cancelButton,
            name="Отменить импорт пресета",
            description="Закрывает окно без импорта.",
        )
        set_state_text(self.cancelButton, "Отменить импорт пресета")
