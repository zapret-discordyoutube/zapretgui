# ui/pages/support_page.py
"""Страница Поддержка - Forgejo Issues и каналы сообщества"""

from __future__ import annotations

from PyQt6.QtCore import QTimer
from qfluentwidgets import InfoBar, PrimaryPushSettingCard, PushSettingCard, SettingCardGroup

from app.ui_texts import tr as tr_catalog
from ui.one_shot_worker_runtime import OneShotWorkerRuntime
from ui.queued_worker_state import QueuedWorkerState
from ui.pages.support_page_accessibility import apply_support_page_accessibility
from ui.theme import get_theme_tokens, get_themed_qta_icon

from .base_page import BasePage


class SupportPage(BasePage):
    """Страница поддержки с одним основным маршрутом через Forgejo Issues."""

    def __init__(self, parent=None, *, create_open_action_worker):
        super().__init__(
            "Поддержка",
            "Forgejo Issues и каналы сообщества",
            parent,
            title_key="page.support.title",
            subtitle_key="page.support.subtitle",
        )
        self._create_support_open_action_worker = create_open_action_worker
        self._support_open_runtime = OneShotWorkerRuntime()
        self._support_open_state = QueuedWorkerState[tuple[str, str, str]](self._support_open_runtime)

        self._support_card = None
        self._support_group = None

        self._tg_card = None
        self._dc_card = None
        self._community_group = None
        self._build_ui()
        self._apply_page_theme(force=True)

    def _tr(self, key: str, default: str) -> str:
        return tr_catalog(key, language=self._ui_language, default=default)

    def _build_ui(self) -> None:
        if SettingCardGroup is None or PushSettingCard is None or PrimaryPushSettingCard is None:
            raise RuntimeError("Stock qfluentwidgets setting cards недоступны для страницы поддержки")

        tokens = get_theme_tokens()

        self._support_group = SettingCardGroup(
            self._tr("page.support.section.discussions", "Forgejo Issues"),
            self.content,
        )
        self._support_card = PrimaryPushSettingCard(
            self._tr("page.support.discussions.button", "Открыть"),
            get_themed_qta_icon("fa5b.github", color=tokens.accent_hex),
            self._tr("page.support.discussions.title", "Forgejo Issues")
        ,
            self._tr(
                "page.support.discussions.description",
                "Основной канал поддержки. Здесь можно задать вопрос, описать проблему и приложить нужные материалы вручную.",
            ),
        )
        self._support_card.clicked.connect(self._open_support_discussions)
        self._support_group.addSettingCard(self._support_card)
        self.add_widget(self._support_group)

        self.add_spacing(16)

        self._community_group = SettingCardGroup(
            self._tr("page.support.section.community", "Каналы сообщества"),
            self.content,
        )

        self._tg_card = PushSettingCard(
            self._tr("page.support.channel.open", "Открыть"),
            get_themed_qta_icon("fa5b.telegram", color="#229ED9"),
            self._tr("page.support.channel.telegram.title", "Telegram"),
            self._tr("page.support.channel.telegram.desc", "Быстрые вопросы и общение с сообществом"),
        )
        self._tg_card.clicked.connect(self._open_telegram_support)

        self._dc_card = PushSettingCard(
            self._tr("page.support.channel.open", "Открыть"),
            get_themed_qta_icon("fa5b.discord", color="#5865F2"),
            self._tr("page.support.channel.discord.title", "Discord"),
            self._tr("page.support.channel.discord.desc", "Обсуждение и живое общение"),
        )
        self._dc_card.clicked.connect(self._open_discord)

        self._community_group.addSettingCards([self._tg_card, self._dc_card])
        self.add_widget(self._community_group)
        apply_support_page_accessibility(self)

    def _apply_page_theme(self, tokens=None, force: bool = False) -> None:
        _ = force
        tokens = tokens or get_theme_tokens()
        if self._support_card is not None:
            try:
                self._support_card.iconLabel.setIcon(get_themed_qta_icon("fa5b.github", color=tokens.accent_hex))
            except Exception:
                pass
        if self._tg_card is not None:
            try:
                self._tg_card.iconLabel.setIcon(get_themed_qta_icon("fa5b.telegram", color="#229ED9"))
            except Exception:
                pass
        if self._dc_card is not None:
            try:
                self._dc_card.iconLabel.setIcon(get_themed_qta_icon("fa5b.discord", color="#5865F2"))
            except Exception:
                pass

    def set_ui_language(self, language: str) -> None:
        super().set_ui_language(language)

        if self._support_group is not None:
            try:
                self._support_group.titleLabel.setText(
                    self._tr("page.support.section.discussions", "Forgejo Issues")
                )
            except Exception:
                pass
        if self._support_card is not None:
            try:
                self._support_card.setTitle(
                    self._tr("page.support.discussions.title", "Forgejo Issues")
                )
                self._support_card.setContent(
                    self._tr(
                        "page.support.discussions.description",
                        "Основной канал поддержки. Здесь можно задать вопрос, описать проблему и приложить нужные материалы вручную.",
                    )
                )
                self._support_card.button.setText(
                    self._tr("page.support.discussions.button", "Открыть")
                )
            except Exception:
                pass

        if self._community_group is not None:
            try:
                self._community_group.titleLabel.setText(
                    self._tr("page.support.section.community", "Каналы сообщества")
                )
            except Exception:
                pass
        if self._tg_card is not None:
            try:
                self._tg_card.setTitle(self._tr("page.support.channel.telegram.title", "Telegram"))
                self._tg_card.setContent(
                    self._tr("page.support.channel.telegram.desc", "Быстрые вопросы и общение с сообществом")
                )
                self._tg_card.button.setText(self._tr("page.support.channel.open", "Открыть"))
            except Exception:
                pass
        if self._dc_card is not None:
            try:
                self._dc_card.setTitle(self._tr("page.support.channel.discord.title", "Discord"))
                self._dc_card.setContent(
                    self._tr("page.support.channel.discord.desc", "Обсуждение и живое общение")
                )
                self._dc_card.button.setText(self._tr("page.support.channel.open", "Открыть"))
            except Exception:
                pass
        apply_support_page_accessibility(self)

    def _open_support_discussions(self) -> None:
        self._request_support_open_action(
            "discussions",
            error_key="page.support.error.open_discussions",
            error_default="Не удалось открыть Forgejo Issues:\n{error}",
        )

    def _open_telegram_support(self) -> None:
        self._request_support_open_action(
            "telegram",
            error_key="page.support.error.open_telegram",
            error_default="Не удалось открыть Telegram:\n{error}",
        )

    def _open_discord(self) -> None:
        self._request_support_open_action(
            "discord",
            error_key="page.support.error.open_discord",
            error_default="Не удалось открыть Discord:\n{error}",
        )

    def create_support_open_action_worker(self, request_id: int, *, action_name: str):
        return self._create_support_open_action_worker(
            request_id,
            action_name=action_name,
            parent=self,
        )

    def _request_support_open_action(
        self,
        action_name: str,
        *,
        error_key: str,
        error_default: str,
    ) -> None:
        request = (str(action_name or "").strip(), str(error_key), str(error_default))
        if self._support_open_state_obj().is_busy():
            self._queue_support_open_action(request)
            return
        self._start_support_open_action_worker(*request)

    def _queue_support_open_action(self, request) -> None:
        self._support_open_state_obj().append_unique(request, key=lambda item: item)

    def _start_support_open_action_worker(
        self,
        action_name: str,
        error_key: str,
        error_default: str,
    ) -> None:
        request_id, _worker = self._support_open_runtime.start_qthread_worker(
            worker_factory=lambda request_id: self.create_support_open_action_worker(
                request_id,
                action_name=action_name,
            ),
            on_loaded=lambda request_id, action_name, result: self._on_support_open_action_finished(
                request_id,
                action_name,
                result,
                error_key=error_key,
                error_default=error_default,
            ),
            on_failed=lambda request_id, action_name, error: self._on_support_open_action_failed(
                request_id,
                action_name,
                error,
                error_key=error_key,
                error_default=error_default,
            ),
            on_finished=self._on_support_open_action_worker_finished,
        )
        _ = request_id

    def _on_support_open_action_finished(
        self,
        request_id: int,
        _action_name: str,
        result,
        *,
        error_key: str,
        error_default: str,
    ) -> None:
        if not self._support_open_runtime.is_current(request_id):
            return
        if self._support_open_state_obj().has_pending():
            return
        if result.ok:
            return
        self._show_support_open_error(error_key, error_default, str(getattr(result, "message", "") or ""))

    def _on_support_open_action_failed(
        self,
        request_id: int,
        _action_name: str,
        error: str,
        *,
        error_key: str,
        error_default: str,
    ) -> None:
        if not self._support_open_runtime.is_current(request_id):
            return
        if self._support_open_state_obj().has_pending():
            return
        self._show_support_open_error(error_key, error_default, str(error))

    def _on_support_open_action_worker_finished(self, _worker) -> None:
        self._support_open_state_obj().schedule_next_after_finish(
            _worker,
            is_current_worker_finish=self._is_current_worker_finish,
            single_shot=QTimer.singleShot,
            start=lambda request: self._run_scheduled_support_open_action_worker_start(request),
            queue_item=self._queue_support_open_action,
            is_cleanup_in_progress=lambda: self.__dict__.get("_cleanup_in_progress", False),
        )

    def _schedule_support_open_action_worker_start(self, request) -> None:
        self._support_open_state_obj().schedule_start(
            request,
            QTimer.singleShot,
            lambda value: self._run_scheduled_support_open_action_worker_start(value),
            queue_item=self._queue_support_open_action,
            is_cleanup_in_progress=lambda: self.__dict__.get("_cleanup_in_progress", False),
        )

    def _run_scheduled_support_open_action_worker_start(self, request) -> None:
        self._support_open_state_obj().start_scheduled = False
        if request is not None and not self.__dict__.get("_cleanup_in_progress", False):
            self._start_support_open_action_worker(*request)

    def _show_support_open_error(self, error_key: str, error_default: str, error: str) -> None:
        if InfoBar is not None:
            InfoBar.warning(
                title=self._tr("page.support.error.title", "Ошибка"),
                content=self._tr(error_key, error_default).format(error=error),
                parent=self.window(),
            )

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

    def _support_open_state_obj(self) -> QueuedWorkerState[tuple[str, str, str]]:
        state = self.__dict__.get("_support_open_state")
        runtime = self.__dict__.get("_support_open_runtime")
        if state is None:
            pending = self.__dict__.pop("_support_open_pending", None)
            start_scheduled = bool(self.__dict__.pop("_support_open_start_scheduled", False))
            state = QueuedWorkerState(
                runtime,
                pending=list(pending or []),
                start_scheduled=start_scheduled,
            )
            self.__dict__["_support_open_state"] = state
        elif getattr(state, "runtime", None) is None and runtime is not None:
            state.runtime = runtime
        return state

    @property
    def _support_open_pending(self):
        return self._support_open_state_obj().pending

    @_support_open_pending.setter
    def _support_open_pending(self, value) -> None:
        self._support_open_state_obj().pending = list(value or [])

    @property
    def _support_open_start_scheduled(self) -> bool:
        return bool(self._support_open_state_obj().start_scheduled)

    @_support_open_start_scheduled.setter
    def _support_open_start_scheduled(self, value: bool) -> None:
        self._support_open_state_obj().start_scheduled = bool(value)

    def cleanup(self) -> None:
        self._cleanup_in_progress = True
        self._support_open_state_obj().reset()
        self._support_open_runtime.stop(blocking=False, warning_prefix="Support open action worker")
        self._support_open_runtime.cancel()
