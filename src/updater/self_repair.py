from __future__ import annotations

"""Восстановление поставки той же версией.

Программа обнаружила, что её собственные файлы не соответствуют манифесту, и
чинит себя тем же установщиком, которым обновляется. Сначала — сохранённый
установщик из ``user\\update_cache`` (без сети: без движка сеть у пользователя как
раз может не работать), и только потом загрузка.

Здесь нет ни Qt, ни окна: модуль запускается из фонового worker-а.
"""

from dataclasses import dataclass
from pathlib import Path
import time

from config.build_info import APP_VERSION
from config.runtime_layout import APPLICATION_PATHS, PACKAGED_RUNTIME
from install_integrity import IntegrityCause, IntegrityReport, verify_fast
from log.log import log
from utils.file_digest import sha256_file

from .update import launch_installer_winapi
from .release_contract import normalize_sha256
from .update_pipeline import (
    CancellationToken,
    UpdatePipeline,
    cached_installer_path,
    installer_arguments,
    read_cached_installer_meta,
)


REPAIR_LOG_LEVEL = "🩹 REPAIR"

# Антивирус может удалять движок сразу после каждой установки. Лимиты
# превращают это из бесконечного цикла переустановок в одну попытку с
# понятным сообщением.
MAX_REPAIRS_PER_DAY = 3
REPAIR_WINDOW_SECONDS = 24 * 60 * 60

_repair_attempted_in_process = False


@dataclass(frozen=True, slots=True)
class RepairOutcome:
    started: bool
    reason: str = ""
    source: str = ""

    @property
    def used_cache(self) -> bool:
        return self.source == "cache"


def _reserve_attempt(*, now: float | None = None) -> tuple[bool, str]:
    """Резервирует попытку восстановления под лимитами."""
    global _repair_attempted_in_process

    if _repair_attempted_in_process:
        return False, "Восстановление уже выполнялось в этом запуске"

    from settings.store import append_self_repair_attempt

    allowed, attempts_in_window = append_self_repair_attempt(
        max_attempts=MAX_REPAIRS_PER_DAY,
        window_seconds=REPAIR_WINDOW_SECONDS,
        now=now,
    )
    if not allowed:
        return False, (
            f"Восстановление уже выполнялось {attempts_in_window} раза за сутки. "
            "Файлы удаляет что-то ещё — проверьте карантин антивируса"
        )

    _repair_attempted_in_process = True
    return True, ""


def _cached_installer_ready(*, expected_sha256: str = "") -> Path | None:
    """Путь к сохранённому установщику, если он годится для починки."""
    path = cached_installer_path()
    if not path.is_file():
        return None

    meta = read_cached_installer_meta()
    meta_sha256 = normalize_sha256(meta.get("sha256"))
    wanted = normalize_sha256(expected_sha256) or meta_sha256
    if not wanted:
        return None

    if not expected_sha256:
        # Офлайн-путь: доверяем кэшу только если он от установленной версии.
        if str(meta.get("version") or "").strip() != str(APP_VERSION).strip():
            return None

    try:
        if path.stat().st_size != int(meta.get("size") or 0) and not expected_sha256:
            return None
        actual = sha256_file(path)
    except (OSError, TypeError, ValueError):
        return None

    if actual != wanted:
        log("Сохранённый установщик не совпал по SHA-256, он не будет использован", REPAIR_LOG_LEVEL)
        return None
    return path


def _launch(installer_path: Path) -> bool:
    return bool(launch_installer_winapi(str(installer_path), installer_arguments(log_name="repair.log")))


def repair_installation(
    report: IntegrityReport | None = None,
    *,
    token: CancellationToken | None = None,
    allow_download: bool = True,
) -> RepairOutcome:
    """Чинит установку и возвращает, был ли запущен установщик.

    Приложение уже работает с правами администратора, поэтому установщик
    стартует без второго запроса UAC. После запуска установщик сам закрывает
    приложение и поднимает его обратно.
    """
    if not PACKAGED_RUNTIME:
        return RepairOutcome(False, "Восстановление доступно только для установленной программы")

    if report is None:
        report = verify_fast()
    if report.cause is IntegrityCause.MANIFEST_ABSENT:
        return RepairOutcome(False, "У этой сборки нет манифеста целостности")
    if report.ok:
        return RepairOutcome(False, "Поставка не повреждена")

    reserved, deny_reason = _reserve_attempt()
    if not reserved:
        return RepairOutcome(False, deny_reason)

    log(
        f"Восстановление поставки {APPLICATION_PATHS.root}: "
        f"причина={report.cause.value}, не хватает={len(report.missing)}, "
        f"повреждено={len(report.corrupted)}",
        REPAIR_LOG_LEVEL,
    )

    cached = _cached_installer_ready()
    if cached is not None:
        log(f"Восстановление из сохранённого установщика: {cached}", REPAIR_LOG_LEVEL)
        if _launch(cached):
            return RepairOutcome(True, "Установщик запущен", "cache")
        log("Не удалось запустить сохранённый установщик", REPAIR_LOG_LEVEL)

    if not allow_download:
        return RepairOutcome(False, "Нет пригодного установщика в кэше")

    active_token = token or CancellationToken()
    pipeline = UpdatePipeline(token=active_token, silent=True)
    try:
        preflight = pipeline.preflight(requested_version=APP_VERSION, allow_same_version=True)
    except Exception as exc:
        log(f"Не удалось получить данные выпуска для восстановления: {exc}", REPAIR_LOG_LEVEL)
        return RepairOutcome(False, f"Не удалось получить установщик: {exc}")

    artifact = preflight.artifact
    cached = _cached_installer_ready(expected_sha256=artifact.expected_sha256)
    if cached is not None:
        log("Сохранённый установщик совпал с выпуском, загрузка не нужна", REPAIR_LOG_LEVEL)
        if _launch(cached):
            return RepairOutcome(True, "Установщик запущен", "cache")

    try:
        handoff = pipeline.download_and_prepare(artifact)
    except Exception as exc:
        log(f"Не удалось скачать установщик для восстановления: {exc}", REPAIR_LOG_LEVEL)
        return RepairOutcome(False, f"Не удалось скачать установщик: {exc}")

    if _launch(Path(handoff.installer_path)):
        return RepairOutcome(True, "Установщик запущен", "download")
    return RepairOutcome(False, "Не удалось запустить установщик")


def reset_repair_process_guard() -> None:
    """Сбрасывает ограничение «одна попытка на запуск». Только для тестов."""
    global _repair_attempted_in_process

    _repair_attempted_in_process = False


def seconds_until_repair_window_reset(*, now: float | None = None) -> float:
    from settings.store import get_self_repair_attempts

    attempts = get_self_repair_attempts()
    if not attempts:
        return 0.0
    current = float(now if now is not None else time.time())
    oldest_in_window = min(
        (stamp for stamp in attempts if current - stamp < REPAIR_WINDOW_SECONDS),
        default=None,
    )
    if oldest_in_window is None:
        return 0.0
    return max(0.0, REPAIR_WINDOW_SECONDS - (current - oldest_in_window))


__all__ = [
    "MAX_REPAIRS_PER_DAY",
    "REPAIR_WINDOW_SECONDS",
    "RepairOutcome",
    "repair_installation",
    "reset_repair_process_guard",
    "seconds_until_repair_window_reset",
]
