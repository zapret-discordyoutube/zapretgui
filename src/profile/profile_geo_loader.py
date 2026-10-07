"""Фоновая проверка: посвящён ли список сайтов профиля гео-сервису.

Гео-сервис сам не пускает посетителей из России (ChatGPT, Gemini, Spotify).
Стратегия Zapret ему не помогает — помогает DNS-профиль в «Редакторе hosts»
или другой DNS. Страница профиля спрашивает об этом здесь и показывает
всплывающую подсказку, чтобы человек не перебирал стратегии впустую.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QThread, pyqtSignal

from log.log import log


class ProfileGeoServicesWorker(QThread):
    """Читает список сайтов профиля и каталог hosts вне UI-потока."""

    # request_id, ключ профиля, названия гео-сервисов (пусто — профиль обычный).
    loaded = pyqtSignal(int, str, object)

    def __init__(
        self,
        request_id: int,
        load_geo_services: Callable[[str], tuple[str, ...]],
        profile_key: str,
        parent=None,
    ):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._load_geo_services = load_geo_services
        self._profile_key = str(profile_key or "").strip()

    def run(self) -> None:
        try:
            services = tuple(self._load_geo_services(self._profile_key) or ())
        except Exception as exc:
            # Подсказка необязательна: без неё страница профиля работает как раньше.
            log(f"ProfileGeoServicesWorker: не удалось проверить список сайтов profile: {exc}", "WARNING")
            services = ()
        self.loaded.emit(self._request_id, self._profile_key, services)


__all__ = ["ProfileGeoServicesWorker"]
