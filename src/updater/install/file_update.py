from __future__ import annotations

"""Обновление по файлам: Zapret 2 GUI качает только изменившееся и ставит себя сама.

Тот же путь, что у Zapret KVN (общее ядро ``zapret_update``):

1. ``plan`` — подписанный список новой версии против файлов на диске;
2. ``fetch`` — изменившиеся файлы и заплатки из хранилища Forgejo и зеркал,
   с докачкой после обрыва и перезапуска;
3. ``stage`` — пробное дерево новой версии в ``<корень>/.update/<версия>/tree``
   (неизменившиеся файлы — жёсткими ссылками);
4. запуск новой версии из дерева с ``--apply-update`` — она ставит себя сама
   (``apply_mode``): замена по файлам с журналом, проверка запуска, откат.

Пока идёт установка, ведётся та же запись ``handoff.json``, что у
установщика Inno: по ней окно-продолжение меняет этапы, повторный запуск
программы не мешает установке, а сорвавшаяся установка при следующем
старте уходит серверу как «не вышло» с кодом причины.

Любой сбой до запуска новой версии — вызывающий код ставит установщиком Inno,
как раньше.
"""

import os
import sys
import time
from pathlib import Path

from log.log import log

from ..release.signed import PRODUCT, TRUSTED_KEYS, SignedRelease
from ..server_config import VPS_SERVERS, should_verify_ssl
from . import handoff as install_handoff

UPDATE_LOG_LEVEL = "🔁 UPDATE"
UPDATE_DIR_NAME = ".update"
APP_EXE = "_internal/Zapret.exe"
APPLY_FLAG = "--apply-update"
INSTALLED_MANIFEST = ("system", "update", "installed-manifest.json")
# Папки данных человека: обновление их не читает и не меняет.
PRESERVE = ("user", "settings", "logs", UPDATE_DIR_NAME)
# Папки, которые установщик Inno каждый раз ставит заново целиком: файл в них,
# которого нет в новой версии, — устаревшая поставка, его убирают и без
# списка установленной версии.
OWNED_DIRS = (
    "_internal",
    "system/strategy_catalogs",
    "system/templates",
    "system/themes",
    "system/ico",
    "presets/winws1_builtin",
    "presets/winws2_builtin",
)
# Движок обхода (winws2/winws) с драйвером и библиотеками лежит в exe/.
# Если обновление не меняет и не убирает там ни одного файла, движок
# переживает обновление: обход не останавливается, а новая версия при
# запуске передаёт работу своему движку без разрыва.
ENGINE_DIR = "exe/"
ENGINE_PROCESSES = ("exe/winws2.exe", "exe/winws.exe")
APPLIER_READY_NAME = "applier.ready"
APPLIER_READY_TIMEOUT_SECONDS = 30.0


class FileUpdateUnavailable(Exception):
    """Этот выпуск не поставить по файлам: нужен установщик целиком."""


def install_root() -> Path | None:
    try:
        from config.runtime_layout import APPLICATION_PATHS

        return Path(APPLICATION_PATHS.root)
    except Exception:
        return None


def is_available(signed: SignedRelease | None) -> bool:
    """Можно ли ставить по файлам: Windows, выпуск с хранилищем, известная папка."""
    if sys.platform != "win32" or signed is None or install_root() is None:
        return False
    files = signed.manifest.files
    return bool(files) and all(entry.zsize for entry in files)


def store_sources():
    """Хранилище выпуска: Forgejo, затем зеркала (то же имя файла везде)."""
    from utils import site_links
    from zapret_update import fetch, store

    # Имя выбирается при каждой ссылке (запомненное отдаётся сразу), а не при
    # сборке списка: качает фоновый поток, и основное имя режут по TLS.
    sources = [
        fetch.Source(
            "Forgejo",
            lambda name: store.forgejo_url(f"https://{site_links.chosen_host('git')}", "zapretdiscordyoutube", PRODUCT, name),
        )
    ]
    for server in VPS_SERVERS:
        base = f"https://{server['host']}:{server['https_port']}"
        label = str(server.get("name") or server["host"])
        sources.append(
            fetch.Source(label, lambda name, base=base: store.mirror_url(base, PRODUCT, name), verify_ssl=bool(should_verify_ssl()))
        )
    return sources


def installed_manifest(root: Path, *, channel: str):
    """Список установленной версии, если программа ставилась по файлам."""
    from zapret_update import manifest

    path = root.joinpath(*INSTALLED_MANIFEST)
    try:
        return manifest.load_signed(
            path.read_bytes(),
            path.with_name(path.name + ".sig").read_bytes(),
            trusted=TRUSTED_KEYS,
            product=PRODUCT,
            channel=channel,
        )
    except (OSError, ValueError):
        return None


def removal_list(new, root: Path, installed) -> list[str]:
    """Файлы прежней версии, которых нет в новой: только поставленные программой."""
    owned = {entry.path for entry in installed.files} if installed is not None else set()
    for relative in OWNED_DIRS:
        base = root.joinpath(*relative.split("/"))
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file():
                owned.add(path.relative_to(root).as_posix())
    wanted = {entry.path.casefold() for entry in new.files}
    return sorted(path for path in owned if path.casefold() not in wanted)


FAILURE_FILE_NAME = "failure.txt"
BLOCKER_FILE_NAME = "blocker.txt"


def _previous_failure(work: Path) -> str:
    """Код неудачи прошлой установки этой версии по файлам или пусто."""
    try:
        return (work / FAILURE_FILE_NAME).read_text(encoding="utf-8").strip() or "неизвестно"
    except OSError:
        return ""


def engine_untouched(planned, remove) -> bool:
    """Обновление не меняет и не убирает ни одного файла движка (папка exe/)."""
    touched = [want.entry.path for want in planned.want] + list(remove)
    return not any(str(path).casefold().startswith(ENGINE_DIR) for path in touched)


def keeps_engine(plan_path: Path) -> bool:
    """Движок переживает эту установку — обход останавливать не нужно."""
    from zapret_update import apply

    try:
        return bool(apply.ApplyPlan.load(plan_path).keep_running)
    except Exception:
        return False


def plan_summary(planned, manifest) -> str:
    """Строка журнала: сколько качать и чем — по ней видно, сработали ли заплатки."""
    kinds: dict[str, int] = {}
    for want in planned.want:
        if want.patch is not None:
            kinds[want.patch.kind] = kinds.get(want.patch.kind, 0) + 1
    patches = sum(kinds.values())
    detail = f" ({', '.join(f'{kind} {count}' for kind, count in sorted(kinds.items()))})" if kinds else ""
    return (
        f"Обновление по файлам до v{manifest.version}: скачать {planned.download_size} байт "
        f"(файлов {len(planned.want)}, заплаток {patches}{detail}, "
        f"без изменений {len(planned.keep)}), установщик целиком — "
        f"{manifest.installer.size if manifest.installer else 0} байт"
    )


def prepare(
    signed: SignedRelease,
    *,
    channel: str,
    current_version: str,
    start_in_tray: bool,
    cancelled=lambda: False,
    on_stage=lambda _text: None,
    on_progress=None,
    on_planned=lambda _download_bytes: None,
) -> Path:
    """Скачивает изменения и собирает пробное дерево; возвращает путь плана установки.

    ``on_planned(байт)`` — сколько предстоит скачать: путь и объём уходят в
    отчёт серверу после обновления.
    """
    from zapret_update import apply, fetch, plan, stage

    from ..share import client as share_client

    root = install_root()
    if root is None:
        raise FileUpdateUnavailable("папка программы неизвестна")
    manifest = signed.manifest
    work = root / UPDATE_DIR_NAME / manifest.version
    failed = _previous_failure(work)
    if failed:
        # Новая версия уже пробовала встать по файлам и откатилась (чаще
        # всего файл программы держит другой процесс — код «lock»). Второй
        # раз тот же путь упрётся в то же; установщик умеет закрыть
        # мешающее и показать, кто держит папку. Через сутки уборка уберёт
        # рабочую папку, и путь по файлам снова будет пробоваться.
        raise FileUpdateUnavailable(f"прошлая установка v{manifest.version} по файлам не удалась ({failed})")
    # Соседи по сети ищутся в фоне, пока считается план (см. updater/share).
    neighbours = share_client.start_search(PRODUCT, manifest.version)
    on_stage("Сравнение с установленной версией…")
    try:
        planned = plan.make_plan(manifest, root, hashes=plan.HashCache(root / UPDATE_DIR_NAME / "hashes.json"))
    except plan.PlanError as exc:
        raise FileUpdateUnavailable(str(exc)) from None
    log(plan_summary(planned, manifest), UPDATE_LOG_LEVEL)
    on_planned(planned.download_size)
    share_client.fill_from_neighbours(
        planned,
        work_dir=work,
        search=neighbours,
        cancelled=cancelled,
        on_stage=on_stage,
        report=lambda text: log(text, UPDATE_LOG_LEVEL),
    )
    on_stage("Скачивание изменений…")
    ready = fetch.fetch_plan(
        planned,
        root=root,
        work_dir=work,
        sources=store_sources(),
        cancelled=cancelled,
        progress=(lambda done, all_bytes: on_progress(int(done * 100 / max(1, all_bytes)), done, all_bytes))
        if on_progress is not None
        else (lambda _done, _total: None),
    )
    on_stage("Подготовка новой версии…")
    tree = stage.build_stage(planned, root=root, ready=ready, stage_dir=work / "tree")
    # Список выпуска едет вместе с версией: после установки это список
    # установленной версии — по нему следующее обновление уберёт лишнее.
    listed = tree.joinpath(*INSTALLED_MANIFEST)
    listed.parent.mkdir(parents=True, exist_ok=True)
    listed.write_bytes(signed.raw)
    listed.with_name(listed.name + ".sig").write_bytes(signed.signature)

    remove = removal_list(manifest, root, installed_manifest(root, channel=channel))
    keep_engine = engine_untouched(planned, remove)
    if keep_engine:
        log("Движок обхода в обновлении не меняется: обход работает всё обновление", UPDATE_LOG_LEVEL)
    apply_plan = apply.ApplyPlan(
        product="Zapret 2 GUI",
        version=manifest.version,
        from_version=current_version,
        app_dir=str(root),
        tree_dir=str(tree),
        work_dir=str(work),
        app_pid=os.getpid(),
        app_exe=APP_EXE,
        restart_args=["--tray"] if start_in_tray else [],
        preserve=list(PRESERVE),
        remove=remove,
        keep_running=list(ENGINE_PROCESSES) if keep_engine else [],
        failure_file=str(work / FAILURE_FILE_NAME),
        blocker_file=str(work / BLOCKER_FILE_NAME),
        error_log=str(work / "error.log"),
        rescue_dir=rescue_dir(),
    )
    plan_path = work / "plan.json"
    apply_plan.save(plan_path)
    return plan_path


def launch(plan_path: Path, *, timeout: float = APPLIER_READY_TIMEOUT_SECONDS) -> bool:
    """Запускает новую версию в режиме установки; True — она взялась за дело.

    Запись ``handoff.json`` («подготовлено») ставится до запуска: с этой
    минуты повторный запуск программы установке не мешает.
    """
    from zapret_update import apply, processes

    plan = apply.ApplyPlan.load(plan_path)
    exe = Path(plan.tree_dir).joinpath(*APP_EXE.split("/"))
    ready = Path(plan.work_dir) / APPLIER_READY_NAME
    ready.unlink(missing_ok=True)
    install_handoff.write_record(
        install_handoff.UpdateHandoffRecord(
            state=install_handoff.HandoffState.PREPARED,
            version=plan.version,
            target_root=plan.app_dir,
            installer_path="",
            gui_pid=os.getpid(),
        )
    )
    process = processes.start_detached([str(exe), APPLY_FLAG, str(plan_path)], cwd=exe.parent)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready.exists():
            log(f"Новая версия v{plan.version} взялась за установку", UPDATE_LOG_LEVEL)
            return True
        if process.poll() is not None:
            break
        time.sleep(0.1)
    log(f"Новая версия v{plan.version} не взялась за установку (код {process.poll()})", "🔁❌ ERROR")
    install_handoff.clear_record()
    return False


# Замену, оборванную выключением, откатываем только если её журналу больше
# этого: свежий журнал — идущая прямо сейчас установка, и запущенная ею для
# проверки новая версия не должна откатить саму себя.
INTERRUPTED_SWAP_MIN_AGE_SECONDS = 10 * 60
# Рабочие папки прошлых обновлений по файлам живут не дольше суток.
LEFTOVER_MIN_AGE_SECONDS = 24 * 3600


def rescue_dir() -> str:
    """Каталог страховки замены: рядом с состоянием обновления, закрыт от записи."""
    if os.name != "nt":
        return ""
    from .paths import update_state_dir

    return str(update_state_dir() / "rescue")


def housekeeping(*, root: Path | None = None, now: float | None = None) -> None:
    """При запуске: откатить оборванную замену и убрать старые рабочие папки."""
    import shutil

    from zapret_update import handoff as core_handoff
    from zapret_update import rescue, swap

    base = root if root is not None else install_root()
    if base is None:
        return
    updates = base / UPDATE_DIR_NAME
    if not updates.is_dir():
        return
    moment = time.time() if now is None else now
    for work in sorted(path for path in updates.iterdir() if path.is_dir()):
        journal = work / "swap" / swap.JOURNAL_NAME
        try:
            age = moment - journal.stat().st_mtime
        except OSError:
            age = None
        if age is not None:
            if age < INTERRUPTED_SWAP_MIN_AGE_SECONDS or os.environ.get(core_handoff.HOLD_ENV):
                continue
            errors = swap.recover(journal.parent)
            if errors:
                log(f"Оборванная установка v{work.name} откачена не полностью: {'; '.join(errors)}", "🔁❌ ERROR")
                continue
            if errors is not None:
                log(f"Оборванная установка v{work.name} откачена: программа вернулась к прежней версии", UPDATE_LOG_LEVEL)
                rescue.disarm(rescue_dir(), base)
        # Откат не вернул всё — в swap/old единственная копия прежних файлов.
        if swap.pending(work / "swap"):
            continue
        try:
            if moment - work.stat().st_mtime >= LEFTOVER_MIN_AGE_SECONDS:
                shutil.rmtree(work, ignore_errors=True)
        except OSError:
            pass


__all__ = [
    "APPLY_FLAG",
    "housekeeping",
    "FileUpdateUnavailable",
    "installed_manifest",
    "is_available",
    "launch",
    "plan_summary",
    "prepare",
    "removal_list",
    "store_sources",
]
