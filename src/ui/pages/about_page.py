# ui/pages/about_page.py
"""Страница О программе — версия, подписка, справка со всеми ссылками, Zapret KVN"""

from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QLayout

from .base_page import BasePage
import about.plans as about_page_plans
from ui.pages.about_page_accessibility import apply_about_buttons_accessibility
from ui.pages.about_page_about_build import (
    build_about_page_about_content,
    set_about_version_accessibility,
    set_subscription_description_accessibility,
    set_subscription_status_accessibility,
)
from ui.pages.about_page_help_build import HELP_LINK_GROUPS, build_about_page_help_content
from ui.pages.about_page_kvn_build import build_about_page_kvn_content
from ui.pages.about_page_tabs_build import build_about_page_tabs
from app.state_store import AppUiState, MainWindowStateStore
from donater.premium_display import TIER_UNKNOWN, PremiumDisplay, premium_display_from_ui_state
from app.ui_texts import tr as tr_catalog
from ui.one_shot_worker_runtime import OneShotWorkerRuntime
from ui.queued_worker_state import QueuedWorkerState
from ui.theme import get_cached_qta_pixmap, get_theme_tokens, get_themed_qta_icon
from ui.widgets.stagger_float_in import attach_stagger_float_in
from log.log import log


from qfluentwidgets import (
    StrongBodyLabel,
    InfoBar,
    PrimaryPushSettingCard,
    PushSettingCard,
    SettingCardGroup,
)

PREMIUM_STAR_TWINKLE_MS = 7000


def _make_section_label(text: str, parent: QWidget | None = None) -> QLabel:
    """Создаёт заголовок секции для использования внутри sub-layout."""
    lbl = StrongBodyLabel(text, parent)
    lbl.setProperty("tone", "primary")
    return lbl


class AboutPage(BasePage):
    """Страница О программе с вкладками: О программе / Справка / Zapret KVN."""

    def __init__(
        self,
        parent=None,
        *,
        open_premium,
        open_updates,
        create_open_action_worker,
        ui_state_store,
    ):
        super().__init__(
            "О программе",
            "Версия, подписка и информация",
            parent,
            title_key="page.about.title",
            subtitle_key="page.about.subtitle",
        )

        # UI refs (support blocks)
        self._open_premium_callback = open_premium
        self._open_updates_callback = open_updates
        self._create_about_open_action_worker = create_open_action_worker
        self._about_open_runtime = OneShotWorkerRuntime()
        self._about_open_state = QueuedWorkerState[tuple[str, str, str]](self._about_open_runtime)
        self._help_link_cards: dict[str, object] = {}

        # Tab lazy init flags
        self._help_tab_initialized = False
        self._kvn_tab_initialized = False

        self._ui_state_store = None
        self._ui_state_unsubscribe = None
        self._pending_tab_key: str | None = None
        self._cleanup_in_progress = False

        self._build_ui()
        self.set_ui_language(self._ui_language)
        self.bind_ui_state_store(ui_state_store)

    def bind_ui_state_store(self, store: MainWindowStateStore) -> None:
        if self._ui_state_store is store:
            return

        unsubscribe = getattr(self, "_ui_state_unsubscribe", None)
        if callable(unsubscribe):
            try:
                unsubscribe()
            except Exception:
                pass

        self._ui_state_store = store
        self._ui_state_unsubscribe = store.subscribe(
            self._on_ui_state_changed,
            fields={"subscription_known", "subscription_is_premium", "subscription_days_remaining"},
            emit_initial=True,
        )

    def _on_ui_state_changed(self, state: AppUiState, _changed_fields: frozenset[str]) -> None:
        if self._cleanup_in_progress:
            return
        self.update_subscription_status(premium_display_from_ui_state(state))

    # ─────────────────────────────────────────────────────────────────────────
    # UI building
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        tabs_widgets = build_about_page_tabs(
            tr_fn=lambda key, default: tr_catalog(key, language=self._ui_language, default=default),
            on_switch_tab=self._switch_tab,
        )
        self.tabs_pivot = tabs_widgets.tabs_pivot
        self.add_widget(self.tabs_pivot)

        self.stacked_widget = tabs_widgets.stacked_widget
        self._about_tab = tabs_widgets.about_tab
        self._help_tab = tabs_widgets.help_tab
        self._kvn_tab = tabs_widgets.kvn_tab
        # При каждом показе вкладки её карточки выплывают по очереди.
        for tab in (self._about_tab, self._help_tab, self._kvn_tab):
            attach_stagger_float_in(tab, page=self)
        self._about_layout = tabs_widgets.about_layout
        self._help_layout = tabs_widgets.help_layout
        self._kvn_layout = tabs_widgets.kvn_layout
        self._build_about_content(self._about_layout)

        self.add_widget(self.stacked_widget)

    def _apply_pending_tab_if_ready(self) -> None:
        pending_tab_key = str(getattr(self, "_pending_tab_key", "") or "").strip().lower()
        if not pending_tab_key:
            return
        if not self.is_page_ready():
            return
        self._pending_tab_key = None
        index = about_page_plans.resolve_tab_index(pending_tab_key)
        if index is not None:
            self._switch_tab(index)

    def _switch_tab(self, index: int):
        plan = about_page_plans.build_tab_switch_plan(
            index=index,
            help_initialized=self._help_tab_initialized,
            kvn_initialized=self._kvn_tab_initialized,
        )
        if plan.init_help:
            self._help_tab_initialized = True
            try:
                self._build_help_content(self._help_layout)
            except Exception as e:
                log(f"Ошибка построения вкладки справки: {e}", "ERROR")

        if plan.init_kvn:
            self._kvn_tab_initialized = True
            try:
                self._build_kvn_content(self._kvn_layout)
            except Exception as e:
                log(f"Ошибка построения вкладки KVN: {e}", "ERROR")

        self.stacked_widget.setCurrentIndex(plan.current_index)

        try:
            self.tabs_pivot.setCurrentItem(plan.route_key)
        except Exception:
            pass

    def switch_to_tab(self, key: str) -> None:
        """External API: switch to About/Support/Help tab by key."""
        if self._cleanup_in_progress:
            return
        index = about_page_plans.resolve_tab_index(key)
        if index is None:
            return
        if not self.is_page_ready():
            self._pending_tab_key = about_page_plans.TAB_KEYS[index]
            self.run_when_page_ready(self._apply_pending_tab_if_ready)
            return
        self._pending_tab_key = None
        self._switch_tab(index)

    def set_ui_language(self, language: str) -> None:
        super().set_ui_language(language)

        try:
            self.tabs_pivot.setItemText("about", " " + tr_catalog("page.about.tab.about", language=language, default="О ПРОГРАММЕ"))
            self.tabs_pivot.setItemText("help", " " + tr_catalog("page.about.tab.help", language=language, default="СПРАВКА"))
            self.tabs_pivot.setItemText("kvn", " ZAPRET KVN")
        except Exception:
            pass

        self._rebuild_about_tab()
        if self._help_tab_initialized:
            self._rebuild_help_tab()
        if self._kvn_tab_initialized:
            self._rebuild_kvn_tab()

    def _clear_layout(self, layout: QLayout | None) -> None:
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                try:
                    widget.deleteLater()
                except Exception:
                    pass
            elif child_layout is not None:
                try:
                    self._clear_layout(child_layout)
                except Exception:
                    pass

    def _retranslate_about_tab(self) -> None:
        try:
            from config.build_info import APP_VERSION


            self.about_section_version_label.setText(
                tr_catalog("page.about.section.version", language=self._ui_language, default="Версия")
            )
            about_app_name = tr_catalog("page.about.app_name", language=self._ui_language, default="Zapret 2 GUI")
            self.about_app_name_label.setText(about_app_name)
            self.about_version_value_label.setText(
                tr_catalog(
                    "page.about.version.value_template",
                    language=self._ui_language,
                    default="Версия {version}",
                ).format(version=APP_VERSION)
            )
            set_about_version_accessibility(
                self.about_app_name_label,
                self.about_version_value_label,
                app_name=about_app_name,
                app_version=APP_VERSION,
            )
            self.update_btn.setText(
                tr_catalog("page.about.button.update_settings", language=self._ui_language, default="Настройка обновлений")
            )
            apply_about_buttons_accessibility(
                tr_fn=lambda key, default: tr_catalog(key, language=self._ui_language, default=default),
                update_btn=self.update_btn,
            )

            self.about_section_subscription_label.setText(
                tr_catalog("page.about.section.subscription", language=self._ui_language, default="Подписка")
            )
            self.sub_desc_label.setText(
                tr_catalog(
                    "page.about.subscription.desc",
                    language=self._ui_language,
                    default="Подписка Zapret Premium открывает доступ к дополнительным темам, приоритетной поддержке и VPN-сервису.",
                )
            )
            set_subscription_description_accessibility(self.sub_desc_label, self.sub_desc_label.text())
            self.premium_btn.setText(
                tr_catalog("page.about.button.premium_vpn", language=self._ui_language, default="Premium и VPN")
            )
            apply_about_buttons_accessibility(
                tr_fn=lambda key, default: tr_catalog(key, language=self._ui_language, default=default),
                premium_btn=self.premium_btn,
            )
            self.kvn_btn.setText(
                tr_catalog("page.about.button.zapret_kvn", language=self._ui_language, default="Zapret KVN")
            )
            apply_about_buttons_accessibility(
                tr_fn=lambda key, default: tr_catalog(key, language=self._ui_language, default=default),
                kvn_btn=self.kvn_btn,
            )
        except Exception:
            pass

        try:
            self.update_subscription_status(self._current_subscription_display())
        except Exception:
            pass

    def _rebuild_about_tab(self) -> None:
        self._clear_layout(self._about_layout)
        self._build_about_content(self._about_layout)
        try:
            self.update_subscription_status(self._current_subscription_display())
        except Exception:
            pass

    def _rebuild_help_tab(self) -> None:
        self._clear_layout(self._help_layout)
        self._build_help_content(self._help_layout)

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 0: О программе
    # ─────────────────────────────────────────────────────────────────────────

    def _build_about_content(self, layout: QVBoxLayout):
        from config.build_info import APP_VERSION

        tokens = get_theme_tokens()
        widgets = build_about_page_about_content(
            layout,
            tr_fn=lambda key, default: tr_catalog(key, language=self._ui_language, default=default),
            tokens=tokens,
            content_parent=self.content,
            app_version=APP_VERSION,
            make_section_label=lambda text: _make_section_label(text),
            on_open_updates=self._open_updates_callback,
            on_open_premium=self._open_premium_callback,
            on_open_kvn_tab=lambda: self.switch_to_tab("kvn"),
            on_open_help_tab=lambda: self.switch_to_tab("help"),
        )
        self.about_section_version_label = widgets.about_section_version_label
        self.about_app_name_label = widgets.about_app_name_label
        self.about_version_value_label = widgets.about_version_value_label
        self.update_btn = widgets.update_btn
        self.about_section_subscription_label = widgets.about_section_subscription_label
        self.sub_status_icon = widgets.sub_status_icon
        self.sub_status_label = widgets.sub_status_label
        self.sub_desc_label = widgets.sub_desc_label
        self.premium_btn = widgets.premium_btn
        self.kvn_btn = widgets.kvn_btn
        layout.addStretch()

    def update_subscription_status(self, display: PremiumDisplay):
        """Обновляет отображение статуса подписки"""
        tokens = get_theme_tokens()
        plan = about_page_plans.build_subscription_status_plan(
            display,
            language=self._ui_language,
            free_icon_color=tokens.fg_faint,
            premium_icon_color="#ffc107",
        )
        self.sub_status_icon.setPixmap(get_cached_qta_pixmap(plan.icon_name, color=plan.icon_color, size=18))
        self.sub_status_label.setText(plan.label_text)
        set_subscription_status_accessibility(self.sub_status_label, plan.label_text)
        set_twinkle = getattr(self.sub_status_icon, "set_idle_twinkle", None)
        if callable(set_twinkle):
            # Звезда Premium изредка поблёскивает, значок Free стоит спокойно.
            set_twinkle(PREMIUM_STAR_TWINKLE_MS if display.is_premium else 0)

    def _current_subscription_display(self) -> PremiumDisplay:
        store = self._ui_state_store
        if store is None:
            return PremiumDisplay(tier=TIER_UNKNOWN)
        return premium_display_from_ui_state(store.snapshot())

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 2: Справка
    # ─────────────────────────────────────────────────────────────────────────

    def _build_help_content(self, layout: QVBoxLayout):
        widgets = build_about_page_help_content(
            layout,
            tr_fn=lambda key, default: tr_catalog(key, language=self._ui_language, default=default),
            tokens=get_theme_tokens(),
            content_parent=self.content,
            make_section_label=lambda text: _make_section_label(text),
            push_setting_card_cls=PushSettingCard,
            primary_push_setting_card_cls=PrimaryPushSettingCard,
            setting_card_group_cls=SettingCardGroup,
            on_open_link=self._open_help_link,
        )
        self._help_link_cards = widgets.cards

    def _open_help_link(self, action_name: str) -> None:
        self._request_about_open_action(
            action_name,
            error_default="Не удалось открыть ссылку:\n{error}",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Tab 3: Zapret KVN
    # ─────────────────────────────────────────────────────────────────────────

    def _rebuild_kvn_tab(self) -> None:
        self._clear_layout(self._kvn_layout)
        self._build_kvn_content(self._kvn_layout)

    def _build_kvn_content(self, layout: QVBoxLayout):
        build_about_page_kvn_content(
            layout,
            tokens=get_theme_tokens(),
            content_parent=self.content,
            on_open_kvn_channel=self._open_kvn_channel,
            on_open_kvn_bot=self._open_kvn_bot,
            on_open_kvn_github=self._open_kvn_github,
        )

    def _open_kvn_channel(self):
        self._request_about_open_action(
            "kvn_channel",
            error_default="Не удалось открыть Telegram:\n{error}",
        )

    def _open_kvn_bot(self):
        self._request_about_open_action(
            "kvn_bot",
            error_default="Не удалось открыть Telegram:\n{error}",
        )

    def _open_kvn_github(self):
        self._request_about_open_action(
            "kvn_github",
            error_default="Не удалось открыть Forgejo:\n{error}",
        )

    def create_about_open_action_worker(self, request_id: int, *, action_name: str):
        return self._create_about_open_action_worker(
            request_id,
            action_name=action_name,
            parent=self,
        )

    def _request_about_open_action(
        self,
        action_name: str,
        *,
        error_default: str,
        raw_error_message: str = "",
    ) -> None:
        request = (
            str(action_name or "").strip(),
            str(error_default),
            str(raw_error_message or ""),
        )
        if self._about_open_state_obj().is_busy():
            self._queue_about_open_action(request)
            return
        self._start_about_open_action_worker(*request)

    def _queue_about_open_action(self, request) -> None:
        self._about_open_state_obj().append_unique(request, key=lambda item: item)

    def _start_about_open_action_worker(
        self,
        action_name: str,
        error_default: str,
        raw_error_message: str,
    ) -> None:
        self._about_open_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self.create_about_open_action_worker(
                request_id,
                action_name=action_name,
            ),
            on_loaded=lambda request_id, _action_name, result: self._on_about_open_action_finished(
                request_id,
                result,
                error_default=error_default,
                raw_error_message=raw_error_message,
            ),
            on_failed=lambda request_id, _action_name, error: self._on_about_open_action_failed(
                request_id,
                error,
                error_default=error_default,
                raw_error_message=raw_error_message,
            ),
            on_finished=self._on_about_open_action_worker_finished,
        )

    def _on_about_open_action_finished(
        self,
        request_id: int,
        result,
        *,
        error_default: str,
        raw_error_message: str,
    ) -> None:
        if not self._about_open_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        if self._about_open_state_obj().has_pending():
            return
        if result.ok:
            return
        self._show_about_open_error(
            str(getattr(result, "message", "") or ""),
            error_default=error_default,
            raw_error_message=raw_error_message,
        )

    def _on_about_open_action_failed(
        self,
        request_id: int,
        error: str,
        *,
        error_default: str,
        raw_error_message: str,
    ) -> None:
        if not self._about_open_runtime.is_current(request_id, cleanup_in_progress=self._cleanup_in_progress):
            return
        if self._about_open_state_obj().has_pending():
            return
        self._show_about_open_error(str(error), error_default=error_default, raw_error_message=raw_error_message)

    def _on_about_open_action_worker_finished(self, _worker) -> None:
        self._about_open_state_obj().schedule_next_after_finish(
            _worker,
            is_current_worker_finish=self._is_current_worker_finish,
            single_shot=QTimer.singleShot,
            start=lambda request: self._run_scheduled_about_open_action_worker_start(request),
            queue_item=self._queue_about_open_action,
            is_cleanup_in_progress=lambda: self.__dict__.get("_cleanup_in_progress", False),
        )

    def _schedule_about_open_action_worker_start(self, request) -> None:
        self._about_open_state_obj().schedule_start(
            request,
            QTimer.singleShot,
            lambda value: self._run_scheduled_about_open_action_worker_start(value),
            queue_item=self._queue_about_open_action,
            is_cleanup_in_progress=lambda: self.__dict__.get("_cleanup_in_progress", False),
        )

    def _run_scheduled_about_open_action_worker_start(self, request) -> None:
        self._about_open_state_obj().start_scheduled = False
        if self.__dict__.get("_cleanup_in_progress", False):
            return
        if request is not None:
            self._start_about_open_action_worker(*request)

    def _show_about_open_error(self, error: str, *, error_default: str, raw_error_message: str) -> None:
        if not InfoBar:
            return
        content = error if raw_error_message and error == raw_error_message else error_default.format(error=error)
        InfoBar.warning(title="Ошибка", content=content, parent=self.window())

    def _is_current_worker_finish(self, runtime, worker) -> bool:
        if self.__dict__.get("_cleanup_in_progress", False):
            return False
        request_id = getattr(worker, "_request_id", None)
        if request_id is None:
            current_worker = getattr(runtime, "worker", None)
            if current_worker is not None:
                return worker is current_worker
            return True
        try:
            return int(request_id) == int(getattr(runtime, "request_id", -1))
        except (TypeError, ValueError):
            return False

    def _about_open_state_obj(self) -> QueuedWorkerState[tuple[str, str, str]]:
        state = self.__dict__.get("_about_open_state")
        runtime = self.__dict__.get("_about_open_runtime")
        if state is None:
            pending = self.__dict__.pop("_about_open_pending", None)
            start_scheduled = bool(self.__dict__.pop("_about_open_start_scheduled", False))
            state = QueuedWorkerState(
                runtime,
                pending=list(pending or []),
                start_scheduled=start_scheduled,
            )
            self.__dict__["_about_open_state"] = state
        elif getattr(state, "runtime", None) is None and runtime is not None:
            state.runtime = runtime
        return state

    @property
    def _about_open_pending(self):
        return self._about_open_state_obj().pending

    @_about_open_pending.setter
    def _about_open_pending(self, value) -> None:
        self._about_open_state_obj().pending = list(value or [])

    @property
    def _about_open_start_scheduled(self) -> bool:
        return bool(self._about_open_state_obj().start_scheduled)

    @_about_open_start_scheduled.setter
    def _about_open_start_scheduled(self, value: bool) -> None:
        self._about_open_state_obj().start_scheduled = bool(value)

    # ─────────────────────────────────────────────────────────────────────────
    # Theme
    # ─────────────────────────────────────────────────────────────────────────

    def _apply_page_theme(self, tokens=None, force: bool = False) -> None:
        _ = force
        tokens = tokens or get_theme_tokens()
        # Значки без своего фирменного цвета идут акцентным — перекрашиваем.
        for group in HELP_LINK_GROUPS:
            for link in group.links:
                card = self._help_link_cards.get(link.action)
                if card is None or link.icon_color:
                    continue
                try:
                    card.iconLabel.setIcon(get_themed_qta_icon(link.icon, color=tokens.accent_hex))
                except Exception:
                    pass

    def cleanup(self) -> None:
        self._cleanup_in_progress = True
        self._pending_tab_key = None
        self._about_open_state_obj().reset()
        self._about_open_runtime.stop(blocking=False, warning_prefix="About open action worker")
        self._about_open_runtime.cancel()

        unsubscribe = getattr(self, "_ui_state_unsubscribe", None)
        if callable(unsubscribe):
            try:
                unsubscribe()
            except Exception:
                pass
        self._ui_state_unsubscribe = None
        self._ui_state_store = None
