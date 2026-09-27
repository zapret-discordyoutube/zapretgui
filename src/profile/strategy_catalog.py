from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.paths import AppPaths
from log.log import log
from .strategy_visuals import StrategyVisual, describe_strategy_visual


@dataclass(frozen=True)
class StrategyEntry:
    strategy_id: str
    catalog_name: str
    name: str
    args: str
    visual: StrategyVisual


_STRATEGY_CATALOGS_CACHE: dict[
    tuple[str, str],
    tuple[tuple[tuple[str, int, int], ...], dict[str, dict[str, StrategyEntry]]],
] = {}

def strategy_catalog_root(paths: AppPaths) -> Path:
    """Каталог готовых стратегий рядом с программой, подготовленный установщиком."""
    return paths.user_root / "system" / "strategy_catalogs"


def _tree_signature(root: Path, pattern: str = "*.txt") -> tuple[tuple[str, int, int], ...]:
    if not root.exists():
        return ()

    rows: list[tuple[str, int, int]] = []
    for path in sorted(root.rglob(pattern)):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            rel = path.relative_to(root).as_posix()
            rows.append((rel, int(getattr(stat, "st_mtime_ns", 0) or 0), int(getattr(stat, "st_size", 0) or 0)))
        except Exception:
            continue
    return tuple(rows)

def _parse_catalog_file(path: Path, catalog_name: str) -> dict[str, StrategyEntry]:
    strategies: dict[str, StrategyEntry] = {}
    seen_ids: set[str] = set()
    current_id: Optional[str] = None
    current_name = ""
    current_args: list[str] = []

    def _flush() -> None:
        nonlocal current_id, current_name, current_args
        if not current_id:
            return
        args = "\n".join(line for line in current_args if line).strip()
        scoped_lines = _profile_scoped_lines(args.splitlines())
        if scoped_lines:
            log(
                f"StrategyCatalog: {path.name} [{current_id}] пропущена — "
                f"строки профиля не допускаются в готовой стратегии: {', '.join(scoped_lines)}",
                "WARNING",
            )
            return
        strategies[current_id] = StrategyEntry(
            strategy_id=current_id,
            catalog_name=catalog_name,
            name=current_name or current_id,
            args=args,
            visual=describe_strategy_visual(args),
        )

    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            _flush()
            current_id = stripped[1:-1].strip()
            if current_id in seen_ids:
                log(
                    f"StrategyCatalog: {path.name} [{current_id}] повторяется — "
                    "будет использована последняя копия",
                    "WARNING",
                )
            seen_ids.add(current_id)
            current_name = current_id
            current_args = []
            continue
        if current_id is None:
            continue
        if stripped.startswith("--"):
            current_args.append(stripped)
            continue
        if current_args:
            current_args.append(stripped)
            continue
        if "=" in stripped:
            key, _, value = stripped.partition("=")
            if key.strip().lower() == "name":
                current_name = value.strip()

    _flush()
    return strategies


def _profile_scoped_lines(lines: list[str]) -> list[str]:
    """Строки, которые относятся к профилю, а не к готовой стратегии.

    Такие строки (`--new`, `--filter-*`, `--hostlist=` и т.п.) нельзя держать
    в каталоге готовых стратегий: они меняют область действия профиля.
    """
    found: list[str] = []
    for raw in lines:
        stripped = str(raw or "").strip()
        lowered = stripped.lower()
        if not lowered:
            continue
        if lowered == "--new" or lowered.startswith("--new="):
            found.append(stripped)
            continue
        if lowered.startswith("--filter-"):
            found.append(stripped)
            continue
        if lowered.startswith((
            "--name",
            "--template",
            "--import",
            "--skip",
            "--hostlist=",
            "--hostlist-domains=",
            "--hostlist-exclude=",
            "--hostlist-exclude-domains=",
            "--hostlist-auto=",
            "--ipset=",
            "--ipset-exclude=",
            "--ipset-exclude-ip=",
            "--ipset-ip=",
        )):
            found.append(stripped)
    return found


def load_strategy_catalogs(paths: AppPaths, engine: str) -> dict[str, dict[str, StrategyEntry]]:
    _signature, catalogs = load_strategy_catalogs_with_signature(paths, engine)
    return catalogs


def load_strategy_catalogs_with_signature(
    paths: AppPaths,
    engine: str,
) -> tuple[tuple[object, ...], dict[str, dict[str, StrategyEntry]]]:
    """Возвращает каталоги вместе с подписью дерева файлов.

    Подпись стабильна, пока файлы каталогов не менялись, и пригодна как компонент
    ключа для контентных кэшей поверх каталогов.
    """
    engine_key = str(engine or "").strip().lower()
    engine_root = strategy_catalog_root(paths) / engine_key
    cache_key = (str(engine_root.resolve()), engine_key)
    signature = _tree_signature(engine_root)
    full_signature = (cache_key, signature)
    cached = _STRATEGY_CATALOGS_CACHE.get(cache_key)
    if cached is not None and cached[0] == signature:
        return full_signature, cached[1]

    catalogs: dict[str, dict[str, StrategyEntry]] = {}
    for path in sorted(engine_root.glob("*.txt")):
        catalogs[path.stem.lower()] = _parse_catalog_file(path, path.stem.lower())
    _STRATEGY_CATALOGS_CACHE[cache_key] = (signature, catalogs)
    return full_signature, catalogs
